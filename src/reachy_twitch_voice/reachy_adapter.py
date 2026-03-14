from __future__ import annotations

import audioop
import asyncio
import json
import logging
import math
import os
import random
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import wave
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .movement_manager import MovementManager, build_gesture_move
from .speech_tapper import frames_from_wav
from .types import SpeechTask

LOGGER = logging.getLogger(__name__)


class ReachyAdapter(ABC):
    @abstractmethod
    async def speak(self, task: SpeechTask) -> None:
        raise NotImplementedError

    @abstractmethod
    async def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    async def health(self) -> str:
        raise NotImplementedError

    @abstractmethod
    async def idle_tick(self) -> None:
        raise NotImplementedError


class MockReachyAdapter(ReachyAdapter):
    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def speak(self, task: SpeechTask) -> None:
        self.spoken.append(task.text_ja)
        await asyncio.sleep(0)

    async def stop(self) -> None:
        await asyncio.sleep(0)

    async def health(self) -> str:
        return "ok"

    async def idle_tick(self) -> None:
        await asyncio.sleep(0)


class ReachySdkAdapter(ReachyAdapter):
    """Reachy Mini adapter with official-app-style motion management."""

    def __init__(
        self,
        host: str,
        connection_mode: str = "auto",
        tts_engine: str = "espeak-ng",
        tts_lang: str = "ja",
        openai_api_key: str = "",
        tts_openai_model: str = "gpt-4o-mini-tts",
        tts_openai_voice: str = "alloy",
        tts_openai_format: str = "wav",
        tts_openai_speed: float = 1.15,
        gesture_enabled: bool = True,
        speech_motion_enabled: bool = True,
        audio_volume: int | None = None,
        healthcheck_url: str = "http://localhost:8000/api/state/full",
        connect_timeout_sec: float = 45.0,
        connect_retries: int = 3,
        connect_retry_interval_sec: float = 3.0,
        idle_use_doa: bool = False,
        idle_inactivity_delay_sec: float = 0.3,
        motion_style: str = "official",
        idle_style: str = "attentive",
        idle_first_delay_sec: float = 3.0,
        idle_glance_interval_sec: float = 10.0,
        speech_motion_scale: float = 0.65,
        emotion_motion_enabled: bool = True,
        client: Any | None = None,
    ) -> None:
        self.host = host
        self.connection_mode = connection_mode
        self.tts_engine = tts_engine
        self.tts_lang = tts_lang
        self.openai_api_key = openai_api_key
        self.tts_openai_model = tts_openai_model
        self.tts_openai_voice = tts_openai_voice
        self.tts_openai_format = tts_openai_format
        self.tts_openai_speed = tts_openai_speed
        self.gesture_enabled = gesture_enabled
        self.speech_motion_enabled = speech_motion_enabled
        self.audio_volume = audio_volume
        self.healthcheck_url = healthcheck_url
        self.connect_timeout_sec = connect_timeout_sec
        self.connect_retries = max(1, connect_retries)
        self.connect_retry_interval_sec = max(0.1, connect_retry_interval_sec)
        self.idle_use_doa = idle_use_doa
        self.idle_inactivity_delay_sec = max(0.0, idle_inactivity_delay_sec)
        self.motion_style = motion_style
        self.idle_style = idle_style
        self.idle_first_delay_sec = max(0.0, idle_first_delay_sec)
        self.idle_glance_interval_sec = max(1.0, idle_glance_interval_sec)
        self.speech_motion_scale = min(max(speech_motion_scale, 0.1), 1.5)
        self.emotion_motion_enabled = emotion_motion_enabled
        self._rng = random.Random(time.time_ns())
        self._last_speech_end = 0.0
        self._ready = False
        self._client = client
        self._motion_manager: MovementManager | None = None
        self._emotion_moves: Any | None = None
        self._dance_move_cls: Any | None = None
        if client is not None:
            self._ready = True

    async def connect(self) -> None:
        if self._client is not None and self._ready:
            self._ensure_motion_manager_started()
            return
        try:
            from reachy_mini import ReachyMini  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("reachy-mini SDK is not installed") from exc

        last_error: Exception | None = None
        for attempt in range(1, self.connect_retries + 1):
            try:
                self._client = ReachyMini(**self._build_connect_kwargs())
                if hasattr(self._client, "__enter__"):
                    self._client.__enter__()
                self._apply_audio_volume_if_configured()
                self._ready = True
                self._ensure_motion_manager_started()
                return
            except Exception as exc:  # pragma: no cover
                last_error = exc
                LOGGER.warning(
                    "Reachy connection attempt %s/%s failed: %s",
                    attempt,
                    self.connect_retries,
                    exc,
                )
                if attempt < self.connect_retries:
                    await asyncio.sleep(self.connect_retry_interval_sec)

        raise ConnectionError(
            "Failed to connect Reachy Mini daemon after retries. "
            "Check 'systemctl status reachy-mini-daemon' and "
            "'curl -i http://localhost:8000/api/state/full'."
        ) from last_error

    def _build_connect_kwargs(self) -> dict[str, Any]:
        mode = self.connection_mode.strip().lower()
        if mode not in {"auto", "localhost_only", "network"}:
            mode = "auto"
        kwargs: dict[str, Any] = {
            "connection_mode": mode,
            "timeout": self.connect_timeout_sec,
        }
        if mode in {"auto", "network"}:
            kwargs["host"] = self.host
        return kwargs

    def _apply_audio_volume_if_configured(self) -> None:
        if self.audio_volume is None:
            return
        payload = json.dumps({"volume": self.audio_volume}).encode("utf-8")
        attempts = [
            ("POST", "http://localhost:8000/api/volume"),
            ("PUT", "http://localhost:8000/api/volume"),
            ("POST", "http://localhost:8000/api/volume/set"),
            ("PUT", "http://localhost:8000/api/volume/set"),
        ]
        for method, url in attempts:
            req = urllib.request.Request(
                url,
                data=payload,
                method=method,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    if 200 <= resp.status < 300:
                        LOGGER.info("Applied Reachy volume=%s via %s %s", self.audio_volume, method, url)
                        return
            except Exception:
                continue
        LOGGER.warning("Failed to apply REACHY_AUDIO_VOLUME=%s (no known volume endpoint matched)", self.audio_volume)

    async def speak(self, task: SpeechTask) -> None:
        if not self._ready or self._client is None:
            raise RuntimeError("ReachySdkAdapter is not connected")

        wav_path = self._synthesize_to_wav(task.text_ja)
        speech_motion_task: asyncio.Task[None] | None = None
        speech_motion_stop = asyncio.Event()
        manager = self._ensure_motion_manager_started()
        try:
            if manager is not None:
                manager.set_idle_phrase_candidates(task.motion_plan.idle_phrase_candidates)
                manager.mark_activity()
                manager.set_speaking(True)
                opening_move = self._resolve_opening_move(task)
                if opening_move is not None:
                    manager.queue_move(opening_move)
                elif self.gesture_enabled:
                    move = build_gesture_move(task.gesture_preset, self._rng)
                    if move is not None:
                        manager.queue_move(move)
                if self.speech_motion_enabled:
                    speech_motion_task = asyncio.create_task(
                        self._run_speech_motion_loop(
                            wav_path,
                            task.gesture_preset,
                            speech_motion_stop,
                            task.motion_plan.speech_motion_scale,
                        )
                    )
            playback_duration = await asyncio.to_thread(self._play_sound, wav_path)
            if playback_duration > 0.0:
                await asyncio.sleep(playback_duration)
        finally:
            speech_motion_stop.set()
            if speech_motion_task is not None:
                await speech_motion_task
            if manager is not None:
                settle_move = self._resolve_settle_move(task)
                if settle_move is not None:
                    manager.queue_move(settle_move)
                manager.set_speech_offsets((0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
                manager.mark_activity()
                manager.set_speaking(False)
            self._cleanup_temp_wav(wav_path)

        self._last_speech_end = time.monotonic()
        await asyncio.sleep(0)

    async def stop(self) -> None:
        if self._client is None:
            return
        media = getattr(self._client, "media", None)
        if media is not None and hasattr(media, "stop_playing"):
            try:
                media.stop_playing()
            except Exception:
                pass
        if self._motion_manager is not None:
            self._motion_manager.stop()
            self._motion_manager = None
        if hasattr(self._client, "stop_speaking"):
            try:
                self._client.stop_speaking()
            except Exception:
                pass
        if hasattr(self._client, "audio") and hasattr(self._client.audio, "stop"):
            try:
                self._client.audio.stop()
            except Exception:
                pass
        if hasattr(self._client, "disconnect"):
            self._client.disconnect()
        elif hasattr(self._client, "client") and hasattr(self._client.client, "disconnect"):
            self._client.client.disconnect()
        elif hasattr(self._client, "__exit__"):
            self._client.__exit__(None, None, None)
        self._ready = False
        await asyncio.sleep(0)

    async def health(self) -> str:
        if not self._ready:
            return "down"
        req = urllib.request.Request(self.healthcheck_url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                return "ok" if resp.status == 200 else "down"
        except (urllib.error.URLError, TimeoutError):
            return "down"

    async def idle_tick(self) -> None:
        if not self._ready or self._client is None or not self.gesture_enabled:
            return
        manager = self._ensure_motion_manager_started()
        if manager is None:
            return
        if self.idle_use_doa:
            if await asyncio.to_thread(self._idle_look_with_doa, manager):
                return
        manager.set_idle_phrase_candidates(["look", "nod"] if self.idle_style == "attentive" else ["nod", "tilt"])
        await asyncio.sleep(0)

    def _idle_look_with_doa(self, manager: MovementManager) -> bool:
        media = getattr(self._client, "media", None)
        if media is None or not hasattr(media, "get_DoA"):
            return False
        try:
            doa = media.get_DoA()
        except Exception:
            return False
        if not doa or not isinstance(doa, tuple) or len(doa) < 2:
            return False
        _, speech_detected = doa[0], bool(doa[1])
        if not speech_detected:
            return False
        move = build_gesture_move("look", self._rng, antenna_scale=0.25, motion_scale=0.5)
        official = self._resolve_gesture_fallback("look", antenna_scale=0.25, motion_scale=0.5)
        if official is not None:
            move = official
        if move is None:
            return False
        manager.mark_activity()
        manager.queue_move(move)
        LOGGER.info("idle doa glance triggered")
        return True

    def _ensure_motion_manager_started(self) -> MovementManager | None:
        if self._client is None or not self._supports_motion_control():
            return None
        if self._motion_manager is None:
            self._motion_manager = MovementManager(
                self._client,
                idle_inactivity_delay=self.idle_inactivity_delay_sec,
                idle_style=self.idle_style,
                idle_first_delay=self.idle_first_delay_sec,
                idle_glance_interval=self.idle_glance_interval_sec,
            )
            self._motion_manager.start()
        return self._motion_manager

    def _load_motion_libraries(self) -> None:
        if self.motion_style != "official":
            return
        if self._emotion_moves is None:
            try:
                from reachy_mini.motion.recorded_move import RecordedMoves  # type: ignore

                self._emotion_moves = RecordedMoves("pollen-robotics/reachy-mini-emotions-library")
            except Exception as exc:
                LOGGER.warning("Emotion moves unavailable, falling back to legacy gestures: %s", exc)
                self._emotion_moves = False
        if self._dance_move_cls is None:
            try:
                from reachy_mini_dances_library.dance_move import DanceMove  # type: ignore

                self._dance_move_cls = DanceMove
            except Exception as exc:
                LOGGER.warning("Dance moves unavailable, continuing without dance support: %s", exc)
                self._dance_move_cls = False

    def _resolve_emotion_move(self, emotion_name: str | None) -> Any | None:
        if not emotion_name or not self.emotion_motion_enabled:
            return None
        self._load_motion_libraries()
        if not self._emotion_moves or self._emotion_moves is False:
            return None
        candidates = [emotion_name]
        alias_map = {
            "happy": ["happy", "joy", "celebration"],
            "celebration": ["celebration", "happy", "joy"],
            "surprised": ["surprised", "surprise", "curious"],
            "agree": ["agree", "listening", "empathy"],
            "listening": ["listening", "agree", "empathy"],
            "settle": ["settle", "calm", "neutral"],
        }
        candidates = alias_map.get(emotion_name, [emotion_name])
        for candidate in candidates:
            try:
                return self._emotion_moves.get(candidate)
            except Exception:
                continue
        return None

    def _resolve_dance_move(self, move_name: str | None) -> Any | None:
        if not move_name:
            return None
        self._load_motion_libraries()
        if not self._dance_move_cls or self._dance_move_cls is False:
            return None
        try:
            return self._dance_move_cls(move_name)
        except Exception:
            return None

    def _resolve_gesture_fallback(self, preset: str, *, antenna_scale: float = 1.0, motion_scale: float = 1.0) -> Any | None:
        return build_gesture_move(preset, self._rng, antenna_scale=antenna_scale, motion_scale=motion_scale)

    def _resolve_opening_move(self, task: SpeechTask) -> Any | None:
        move = self._resolve_emotion_move(task.motion_plan.speech_opening_emotion)
        if move is not None:
            return move
        dance_move = self._resolve_dance_move(task.motion_plan.dance_move)
        if dance_move is not None:
            return dance_move
        if self.gesture_enabled:
            return self._resolve_gesture_fallback(task.motion_plan.fallback_gesture)
        return None

    def _resolve_settle_move(self, task: SpeechTask) -> Any | None:
        move = self._resolve_emotion_move(task.motion_plan.post_speech_settle)
        if move is not None:
            return move
        return self._resolve_gesture_fallback("idle", antenna_scale=0.3, motion_scale=0.5)

    def _supports_motion_control(self) -> bool:
        if self._client is None:
            return False
        return any(
            hasattr(self._client, name)
            for name in (
                "set_target",
                "set_target_head_pose",
                "set_target_antenna_joint_positions",
                "goto_target",
                "get_current_head_pose",
            )
        )

    def _synthesize_to_wav(self, text: str) -> str:
        if self.tts_engine == "espeak-ng":
            return self._synthesize_with_espeak(text)
        if self.tts_engine == "openai-tts":
            return self._synthesize_with_openai_tts(text)
        raise RuntimeError(f"Unsupported REACHY_TTS_ENGINE: {self.tts_engine}")

    def _synthesize_with_espeak(self, text: str) -> str:
        with tempfile.NamedTemporaryFile(prefix="reachy_tts_", suffix=".wav", delete=False) as f:
            wav_path = f.name
        cmd = ["espeak-ng", "-v", self.tts_lang, "-w", wav_path, text]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"espeak-ng failed: {proc.stderr.strip()}")
        if not os.path.exists(wav_path) or os.path.getsize(wav_path) == 0:
            raise RuntimeError("espeak-ng did not generate a valid wav file")
        return wav_path

    def _synthesize_with_openai_tts(self, text: str) -> str:
        if not self.openai_api_key:
            raise RuntimeError("REACHY_TTS_ENGINE=openai-tts requires OPENAI_API_KEY")

        with tempfile.NamedTemporaryFile(prefix="reachy_tts_", suffix=".wav", delete=False) as f:
            wav_path = f.name

        payload = {
            "model": self.tts_openai_model,
            "voice": self.tts_openai_voice,
            "input": text,
            "response_format": self.tts_openai_format,
            "speed": self.tts_openai_speed,
        }
        try:
            audio_bytes = self._request_openai_tts(payload)
        except urllib.error.HTTPError as exc:
            if exc.code == 400 and "speed" in payload:
                payload.pop("speed", None)
                audio_bytes = self._request_openai_tts(payload)
            else:
                self._cleanup_temp_wav(wav_path)
                raise RuntimeError(f"OpenAI TTS request failed: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            self._cleanup_temp_wav(wav_path)
            raise RuntimeError(f"OpenAI TTS request failed: {exc}") from exc

        with open(wav_path, "wb") as f:
            f.write(audio_bytes)
        if not os.path.exists(wav_path) or os.path.getsize(wav_path) == 0:
            self._cleanup_temp_wav(wav_path)
            raise RuntimeError("OpenAI TTS did not return a valid wav file")
        self._normalize_wav_for_playback(wav_path)
        return wav_path

    def _request_openai_tts(self, payload: dict[str, Any]) -> bytes:
        req = urllib.request.Request(
            "https://api.openai.com/v1/audio/speech",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.openai_api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=20.0) as resp:
            return resp.read()

    def _play_sound(self, wav_path: str) -> float:
        media = getattr(self._client, "media", None)
        if media is None or not hasattr(media, "play_sound"):
            raise RuntimeError("ReachyMini.media.play_sound is not available")
        media.play_sound(wav_path)
        return self._wav_duration_sec(wav_path)

    def _wav_duration_sec(self, wav_path: str) -> float:
        try:
            with wave.open(wav_path, "rb") as wav_file:
                frame_rate = max(wav_file.getframerate(), 1)
                frame_count = wav_file.getnframes()
                duration = frame_count / frame_rate
        except Exception:
            return 0.0
        return max(duration, 0.0)

    def _normalize_wav_for_playback(self, wav_path: str) -> None:
        try:
            with wave.open(wav_path, "rb") as src:
                channels = max(src.getnchannels(), 1)
                sample_width = src.getsampwidth()
                frame_rate = max(src.getframerate(), 1)
                frames = src.readframes(src.getnframes())
        except Exception as exc:
            raise RuntimeError(f"OpenAI TTS returned an unreadable wav file: {exc}") from exc

        if sample_width not in {1, 2, 4}:
            raise RuntimeError(
                f"OpenAI TTS returned unsupported wav sample width: {sample_width}"
            )

        converted = frames
        width = sample_width
        if channels > 1:
            converted = audioop.tomono(converted, width, 0.5, 0.5)
            channels = 1
        if width != 2:
            converted = audioop.lin2lin(converted, width, 2)
            width = 2
        if frame_rate != 16000:
            converted, _ = audioop.ratecv(converted, width, channels, frame_rate, 16000, None)
            frame_rate = 16000

        with wave.open(wav_path, "wb") as dst:
            dst.setnchannels(channels)
            dst.setsampwidth(width)
            dst.setframerate(frame_rate)
            dst.writeframes(converted)

    async def _run_speech_motion_loop(
        self,
        wav_path: str,
        base_preset: str,
        stop_event: asyncio.Event,
        motion_scale: float,
    ) -> None:
        frames = self._extract_sway_frames_from_wav(wav_path)
        if not frames:
            frames = [{"pitch_deg": 0.6, "yaw_deg": 0.0, "roll_deg": 0.0, "gain": 0.2}]
        sign = -1.0 if base_preset in {"look", "tilt"} else 1.0
        idx = 0
        smooth_pitch = 0.0
        smooth_yaw = 0.0
        smooth_roll = 0.0
        smooth_gain = 0.0
        while not stop_event.is_set():
            window = frames[idx : min(idx + 3, len(frames))]
            if not window:
                window = [frames[-1]]
            pitch = sum(float(item["pitch_deg"]) for item in window) / len(window)
            yaw = sum(float(item["yaw_deg"]) for item in window) / len(window)
            roll = sum(float(item["roll_deg"]) for item in window) / len(window)
            gain = sum(float(item["gain"]) for item in window) / len(window)
            yaw *= sign
            roll *= sign
            smooth_pitch = (smooth_pitch * 0.55) + (pitch * 0.45)
            smooth_yaw = (smooth_yaw * 0.55) + (yaw * 0.45)
            smooth_roll = (smooth_roll * 0.55) + (roll * 0.45)
            smooth_gain = (smooth_gain * 0.50) + (gain * 0.50)
            await asyncio.to_thread(
                self._apply_speech_frame,
                smooth_pitch,
                smooth_yaw,
                smooth_roll,
                smooth_gain,
                motion_scale,
            )
            await asyncio.sleep(0.18)
            idx += 3
        await asyncio.to_thread(self._apply_speech_frame, 0.0, 0.0, 0.0, 0.0, motion_scale)

    def _extract_sway_frames_from_wav(self, wav_path: str) -> list[dict[str, float]]:
        try:
            with wave.open(wav_path, "rb") as w:
                frame_rate = max(w.getframerate(), 1)
                channels = max(w.getnchannels(), 1)
                sample_width = w.getsampwidth()
                if sample_width != 2:
                    return []
                raw = w.readframes(w.getnframes())
        except Exception:
            return []
        return frames_from_wav(raw, frame_rate, channels)

    def _apply_speech_frame(
        self,
        pitch_deg: float,
        yaw_deg: float,
        roll_deg: float,
        gain: float,
        motion_scale: float,
    ) -> bool:
        manager = self._motion_manager
        if manager is None:
            return False
        g = min(max(gain, 0.0), 1.0)
        scale = min(max(self.speech_motion_scale * motion_scale, 0.1), 1.5)
        roll_rad = math.radians(roll_deg * (0.8 + 0.2 * g) * scale)
        pitch_rad = math.radians(pitch_deg * (0.85 + 0.25 * g) * scale)
        yaw_rad = math.radians(yaw_deg * (0.7 + 0.2 * g) * scale)
        manager.set_speech_offsets((0.0, 0.0, 0.0, roll_rad, pitch_rad, yaw_rad))
        return True

    def _cleanup_temp_wav(self, path: str) -> None:
        try:
            if os.path.exists(path):
                os.unlink(path)
        except OSError as exc:
            LOGGER.warning("Failed to cleanup temp wav: %s", exc)


ReachyMiniAdapter = ReachySdkAdapter
