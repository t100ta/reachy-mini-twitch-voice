from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import random
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import wave
from abc import ABC, abstractmethod
from typing import Any

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
    """Backwards-compatible alias. Uses Reachy Mini SDK internally."""

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
        audio_volume: int | None = None,
        healthcheck_url: str = "http://localhost:8000/api/state/full",
        connect_timeout_sec: float = 45.0,
        connect_retries: int = 3,
        connect_retry_interval_sec: float = 3.0,
        idle_use_doa: bool = False,
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
        self.audio_volume = audio_volume
        self.healthcheck_url = healthcheck_url
        self.connect_timeout_sec = connect_timeout_sec
        self.connect_retries = max(1, connect_retries)
        self.connect_retry_interval_sec = max(0.1, connect_retry_interval_sec)
        self.idle_use_doa = idle_use_doa
        self._rng = random.Random(time.time_ns())
        self._last_speech_end = 0.0
        self._last_idle_tick = 0.0
        self._ready = False
        self._client = client
        if client is not None:
            self._ready = True

    async def connect(self) -> None:
        if self._client is not None and self._ready:
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
        # v1.5.0: in auto/network mode host is meaningful.
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
        gesture_task: asyncio.Task[None] | None = None
        gesture_stop = asyncio.Event()
        try:
            if self.gesture_enabled:
                wav_duration = self._wav_duration_sec(wav_path)
                # Prefer real audio duration to avoid over-running gestures after speech ends.
                # Fallback to text-based estimate only when wav duration is unavailable.
                min_duration = (
                    wav_duration + 0.10
                    if wav_duration > 0
                    else self._estimate_speech_duration_sec(task.text_ja)
                )
                gesture_task = asyncio.create_task(
                    self._run_gesture_loop(task.gesture_preset, min_duration, gesture_stop)
                )

            # Some media backends block, so offload playback from the event-loop thread.
            await asyncio.to_thread(self._play_sound, wav_path)
        finally:
            self._cleanup_temp_wav(wav_path)

        if self.gesture_enabled and gesture_task is not None:
            gesture_stop.set()
            await gesture_task
        self._last_speech_end = time.monotonic()
        await asyncio.sleep(0)

    async def stop(self) -> None:
        if self._client is None:
            return
        # Best-effort: stop current speech before disconnect.
        media = getattr(self._client, "media", None)
        if media is not None and hasattr(media, "stop_playing"):
            self._call_if_exists([(media, "stop_playing", ())])
        self._call_if_exists([(self._client, "stop_speaking", ())])
        if hasattr(self._client, "audio"):
            self._call_if_exists([(self._client.audio, "stop", ())])
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
        now = time.monotonic()
        # Keep idle motions sparse and only after speech has ended.
        if self._last_speech_end > 0 and (now - self._last_speech_end) < 3.0:
            return
        if (now - self._last_idle_tick) < 3.2:
            return
        self._last_idle_tick = now
        preset = self._rng.choices(
            ["idle_micro", "idle_glance"],
            weights=[0.7, 0.3],
            k=1,
        )[0]
        if self.idle_use_doa:
            if await asyncio.to_thread(self._idle_look_with_doa):
                return
        LOGGER.info("idle motion tick: preset=%s", preset)
        await asyncio.to_thread(
            self._play_synthetic_gesture,
            preset,
            0.18,  # antenna_scale (idle, safety-first)
            0.60,  # motion_scale (idle)
        )

    def _idle_look_with_doa(self) -> bool:
        media = getattr(self._client, "media", None)
        if media is None or not hasattr(media, "get_DoA"):
            return False
        try:
            doa = media.get_DoA()
        except Exception:
            return False
        if not doa or not isinstance(doa, tuple) or len(doa) < 2:
            return False
        angle, speech_detected = doa[0], bool(doa[1])
        if not speech_detected:
            return False
        # Map DOA angle to normalized x for look_at API.
        norm_x = max(min(float(angle) / 1.57, 1.0), -1.0)
        called = self._call_if_exists(
            [
                (self._client, "look_at", (norm_x, 0.0)),
                (getattr(self._client, "head", None), "look_at", (norm_x, 0.0)),
                (getattr(self._client, "motion", None), "look_at", (norm_x, 0.0)),
            ]
        )
        if called:
            LOGGER.info("idle doa glance: angle=%.2f norm_x=%.2f", angle, norm_x)
            return True
        # Fallback to small synthetic glance if look_at is unavailable.
        return self._play_synthetic_gesture("idle_glance", antenna_scale=0.25, motion_scale=0.5)

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
            raise RuntimeError(
                "REACHY_TTS_ENGINE=openai-tts requires OPENAI_API_KEY"
            )

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
            # Backward compatibility: some endpoints may reject unknown fields.
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

    def _play_sound(self, wav_path: str) -> None:
        media = getattr(self._client, "media", None)
        if media is None or not hasattr(media, "play_sound"):
            raise RuntimeError("ReachyMini.media.play_sound is not available")
        media.play_sound(wav_path)

    def _estimate_speech_duration_sec(self, text: str) -> float:
        # Rough JP duration estimate. openai-tts speed>1.0 means faster speech.
        chars = max(len(text), 1)
        base_chars_per_sec = 7.0
        speed = self.tts_openai_speed if self.tts_engine == "openai-tts" else 1.0
        duration = chars / (base_chars_per_sec * max(speed, 0.5))
        return min(max(duration, 0.8), 8.0)

    def _wav_duration_sec(self, path: str) -> float:
        try:
            with wave.open(path, "rb") as w:
                fr = w.getframerate()
                if fr <= 0:
                    return 0.0
                return w.getnframes() / float(fr)
        except Exception:
            return 0.0

    async def _run_gesture_loop(
        self,
        preset: str,
        min_duration_sec: float,
        stop_event: asyncio.Event,
    ) -> None:
        started = asyncio.get_running_loop().time()
        # Keep a coherent emotional style during one utterance.
        # Variation is expressed inside each synthetic move (amplitude/timing jitter),
        # not by changing gesture category every cycle.
        while True:
            await asyncio.to_thread(self._gesture_start, preset)
            await asyncio.sleep(self._gesture_cycle_sec(preset))
            elapsed = asyncio.get_running_loop().time() - started
            if stop_event.is_set() and elapsed >= min_duration_sec:
                break
        await asyncio.to_thread(self._gesture_end, preset)

    def _gesture_cycle_sec(self, preset: str) -> float:
        if preset == "nod":
            return 0.85
        if preset == "look":
            return 1.0
        if preset == "sway":
            return 0.95 + self._rng.uniform(0.00, 0.35)
        if preset == "tilt":
            return 0.85 + self._rng.uniform(0.00, 0.25)
        return 0.75 + self._rng.uniform(0.00, 0.25)

    def _cleanup_temp_wav(self, path: str) -> None:
        try:
            if os.path.exists(path):
                os.unlink(path)
        except OSError as exc:
            LOGGER.warning("Failed to cleanup temp wav: %s", exc)

    def _gesture_start(self, preset: str) -> None:
        try:
            called = False
            if preset == "nod":
                called = self._call_if_exists(
                    [
                        (self._client, "gesture_nod", ()),
                        (self._client, "nod", ()),
                        (getattr(self._client, "head", None), "nod", ()),
                        (getattr(self._client, "motion", None), "nod", ()),
                    ]
                )
            elif preset == "look":
                called = self._call_if_exists(
                    [
                        (self._client, "look_at", (0.0, 0.0)),
                        (getattr(self._client, "head", None), "look_at", (0.0, 0.0)),
                        (getattr(self._client, "motion", None), "look_at", (0.0, 0.0)),
                    ]
                )
            if not called and preset in {"nod", "look", "sway", "tilt"}:
                called = self._play_synthetic_gesture(preset)
            if not called:
                LOGGER.warning(
                    "gesture start skipped: preset=%s no compatible SDK method found",
                    preset,
                )
        except Exception as exc:
            LOGGER.warning("gesture start failed: %s", exc)

    def _gesture_end(self, preset: str) -> None:
        try:
            if preset in {"nod", "look", "sway", "tilt", "idle"}:
                called = self._call_if_exists(
                    [
                        (self._client, "idle", ()),
                        (getattr(self._client, "head", None), "idle", ()),
                        (getattr(self._client, "motion", None), "idle", ()),
                    ]
                )
                if not called:
                    called = self._play_synthetic_gesture("idle")
                if not called:
                    LOGGER.warning(
                        "gesture end skipped: preset=%s no compatible SDK method found",
                        preset,
                    )
        except Exception as exc:
            LOGGER.warning("gesture end failed: %s", exc)

    def _play_synthetic_gesture(
        self,
        preset: str,
        antenna_scale: float = 1.0,
        motion_scale: float = 1.0,
    ) -> bool:
        try:
            import numpy as np
            from reachy_mini.motion.move import Move  # type: ignore
            from reachy_mini.utils import create_head_pose  # type: ignore
            from reachy_mini.utils.interpolation import linear_pose_interpolation  # type: ignore
        except Exception as exc:
            LOGGER.warning("synthetic gesture unavailable: %s", exc)
            return False

        class _LinearMove(Move):  # type: ignore[misc]
            def __init__(
                self,
                keyframes: list[tuple[float, Any]],
                antenna_keyframes: list[tuple[float, tuple[float, float]]],
                duration: float,
            ) -> None:
                self.keyframes = sorted(keyframes, key=lambda x: x[0])
                self.antenna_keyframes = sorted(antenna_keyframes, key=lambda x: x[0])
                self._duration = duration

            @property
            def duration(self) -> float:
                return self._duration

            def evaluate(self, t: float):  # type: ignore[override]
                if t <= 0:
                    pose = self.keyframes[0][1]
                    antennas = self.antenna_keyframes[0][1]
                elif t >= self._duration:
                    pose = self.keyframes[-1][1]
                    antennas = self.antenna_keyframes[-1][1]
                else:
                    r = t / self._duration
                    left = self.keyframes[0]
                    right = self.keyframes[-1]
                    for i in range(1, len(self.keyframes)):
                        if r <= self.keyframes[i][0]:
                            left = self.keyframes[i - 1]
                            right = self.keyframes[i]
                            break
                    span = max(right[0] - left[0], 1e-6)
                    alpha = (r - left[0]) / span
                    pose = linear_pose_interpolation(left[1], right[1], alpha)

                    a_left = self.antenna_keyframes[0]
                    a_right = self.antenna_keyframes[-1]
                    for i in range(1, len(self.antenna_keyframes)):
                        if r <= self.antenna_keyframes[i][0]:
                            a_left = self.antenna_keyframes[i - 1]
                            a_right = self.antenna_keyframes[i]
                            break
                    a_span = max(a_right[0] - a_left[0], 1e-6)
                    a_alpha = (r - a_left[0]) / a_span
                    antennas = (
                        a_left[1][0] + (a_right[1][0] - a_left[1][0]) * a_alpha,
                        a_left[1][1] + (a_right[1][1] - a_left[1][1]) * a_alpha,
                    )

                return (
                    pose,
                    np.array([antennas[0], antennas[1]], dtype=np.float64),
                    0.0,
                )

        center = create_head_pose(0, 0, 0, 0, 0, 0, degrees=True)
        antenna_scale = min(max(antenna_scale, 0.0), 2.0)
        motion_scale = min(max(motion_scale, 0.2), 2.0)
        # Conversation gestures should be more expressive than idle motions.
        if preset in {"nod", "look", "sway", "tilt"}:
            antenna_scale *= 0.25
            motion_scale *= 1.10
            antenna_limit = 0.35
        elif preset in {"idle_micro", "idle_glance", "idle"}:
            antenna_limit = 0.12
        else:
            antenna_limit = 0.20

        def A(v: float) -> float:
            raw = v * antenna_scale
            return max(min(raw, antenna_limit), -antenna_limit)

        def M(v: float) -> float:
            return v * motion_scale

        # Keep antenna motion subtle and tied to gesture semantics.
        antenna_jitter = self._rng.uniform(-0.35, 0.35)
        if preset == "nod":
            down_pitch = M(12 + self._rng.uniform(2.0, 8.0))
            up_pitch = M(-(6 + self._rng.uniform(2.0, 7.0)))
            down = create_head_pose(0, 0, 0, 0, down_pitch, 0, degrees=True)
            up = create_head_pose(0, 0, 0, 0, up_pitch, 0, degrees=True)
            fwd = 2.4 + antenna_jitter
            back = -1.4 + antenna_jitter
            nod_antenna_variants = [
                [
                    (0.0, (0.0, 0.0)),
                    (0.35, (A(fwd), A(fwd))),
                    (0.7, (A(back), A(back))),
                    (1.0, (0.0, 0.0)),
                ],
                [
                    (0.0, (0.0, 0.0)),
                    (0.35, (A(fwd * 0.7), A(fwd * 1.1))),
                    (0.7, (A(back * 1.1), A(back * 0.7))),
                    (1.0, (0.0, 0.0)),
                ],
            ]
            move = _LinearMove(
                keyframes=[(0.0, center), (0.35, down), (0.7, up), (1.0, center)],
                antenna_keyframes=self._rng.choice(nod_antenna_variants),
                duration=0.72 + self._rng.uniform(0.10, 0.35),
            )
        elif preset == "look":
            yaw = M(14 + self._rng.uniform(4.0, 14.0))
            left = create_head_pose(0, 0, 0, 0, 0, yaw, degrees=True)
            right = create_head_pose(0, 0, 0, 0, 0, -yaw, degrees=True)
            split = 2.2 + antenna_jitter
            look_antenna_variants = [
                [
                    (0.0, (0.0, 0.0)),
                    (0.4, (A(split), A(-split))),
                    (0.8, (A(-split), A(split))),
                    (1.0, (0.0, 0.0)),
                ],
                [
                    (0.0, (0.0, 0.0)),
                    (0.4, (A(split * 1.2), A(-split * 0.7))),
                    (0.8, (A(-split * 0.7), A(split * 1.2))),
                    (1.0, (0.0, 0.0)),
                ],
            ]
            move = _LinearMove(
                keyframes=[(0.0, center), (0.4, left), (0.8, right), (1.0, center)],
                antenna_keyframes=self._rng.choice(look_antenna_variants),
                duration=0.85 + self._rng.uniform(0.10, 0.35),
            )
        elif preset == "sway":
            yaw = M(10 + self._rng.uniform(4.0, 12.0))
            roll = M(4 + self._rng.uniform(0.0, 6.0))
            left = create_head_pose(0, 0, 0, -roll, 0, yaw, degrees=True)
            right = create_head_pose(0, 0, 0, roll, 0, -yaw, degrees=True)
            flow = 2.8 + antenna_jitter
            sway_antenna_variants = [
                [
                    (0.0, (0.0, 0.0)),
                    (0.33, (A(-flow), A(flow))),
                    (0.66, (A(flow), A(-flow))),
                    (1.0, (0.0, 0.0)),
                ],
                [
                    (0.0, (0.0, 0.0)),
                    (0.33, (A(-flow * 0.8), A(flow * 0.8))),
                    (0.66, (A(flow * 1.1), A(-flow * 1.1))),
                    (1.0, (0.0, 0.0)),
                ],
            ]
            move = _LinearMove(
                keyframes=[(0.0, center), (0.33, left), (0.66, right), (1.0, center)],
                antenna_keyframes=self._rng.choice(sway_antenna_variants),
                duration=0.95 + self._rng.uniform(0.10, 0.45),
            )
        elif preset == "tilt":
            roll = M(9 + self._rng.uniform(2.0, 10.0))
            yaw = M(4 + self._rng.uniform(1.0, 8.0))
            left_tilt = create_head_pose(0, 0, 0, -roll, 0, yaw, degrees=True)
            right_tilt = create_head_pose(0, 0, 0, roll, 0, -yaw, degrees=True)
            accent = 3.2 + antenna_jitter
            tilt_antenna_variants = [
                [
                    (0.0, (0.0, 0.0)),
                    (0.4, (A(-accent), A(-accent * 0.35))),
                    (0.8, (A(accent * 0.35), A(accent))),
                    (1.0, (0.0, 0.0)),
                ],
                [
                    (0.0, (0.0, 0.0)),
                    (0.4, (A(-accent * 0.5), A(-accent))),
                    (0.8, (A(accent), A(accent * 0.4))),
                    (1.0, (0.0, 0.0)),
                ],
            ]
            move = _LinearMove(
                keyframes=[(0.0, center), (0.4, left_tilt), (0.8, right_tilt), (1.0, center)],
                antenna_keyframes=self._rng.choice(tilt_antenna_variants),
                duration=0.80 + self._rng.uniform(0.10, 0.35),
            )
        elif preset == "idle_micro":
            small = M(0.8 + self._rng.uniform(0.0, 0.8))
            micro = create_head_pose(0, 0, 0, 0, M(2.0), small, degrees=True)
            move = _LinearMove(
                keyframes=[(0.0, center), (0.5, micro), (1.0, center)],
                antenna_keyframes=[
                    (0.0, (0.0, 0.0)),
                    # Idle mode: keep antennas almost still.
                    (0.5, (A(small * 0.10), A(small * 0.10))),
                    (1.0, (0.0, 0.0)),
                ],
                duration=1.1 + self._rng.uniform(0.0, 0.5),
            )
        elif preset == "idle_glance":
            small_yaw = M(4.0 + self._rng.uniform(0.0, 4.0))
            glance = create_head_pose(0, 0, 0, 0, 0, small_yaw, degrees=True)
            move = _LinearMove(
                keyframes=[(0.0, center), (0.45, glance), (1.0, center)],
                antenna_keyframes=[
                    (0.0, (0.0, 0.0)),
                    (0.45, (A(0.25), A(-0.25))),
                    (1.0, (0.0, 0.0)),
                ],
                duration=1.0 + self._rng.uniform(0.0, 0.4),
            )
        else:
            move = _LinearMove(
                keyframes=[(0.0, center), (1.0, center)],
                antenna_keyframes=[(0.0, (0.0, 0.0)), (1.0, (0.0, 0.0))],
                duration=0.4,
            )

        # Prefer async_play_move first so motion and audio can overlap.
        called = self._call_if_exists(
            [
                (self._client, "async_play_move", (move,)),
                (self._client, "play_move", (move,)),
            ]
        )
        if called:
            LOGGER.info("synthetic gesture started: preset=%s", preset)
        return called

    def _call_if_exists(self, targets: list[tuple[Any, str, tuple[Any, ...]]]) -> bool:
        for obj, method, args in targets:
            if obj is None:
                continue
            if hasattr(obj, method):
                fn = getattr(obj, method)
                result = fn(*args)
                if inspect.isawaitable(result):
                    try:
                        asyncio.get_running_loop().create_task(result)
                    except RuntimeError:
                        threading.Thread(
                            target=lambda: asyncio.run(result),
                            daemon=True,
                        ).start()
                return True
        return False


ReachyMiniAdapter = ReachySdkAdapter
