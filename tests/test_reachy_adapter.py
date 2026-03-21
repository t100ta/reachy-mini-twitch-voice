import io
import os
import tempfile
import time
import wave
import unittest
from unittest.mock import patch

from reachy_twitch_voice.reachy_adapter import ReachySdkAdapter
from reachy_twitch_voice.types import SpeechTask


class _FakeMedia:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.doa = None

    def play_sound(self, path: str) -> None:
        self.calls.append(("media.play_sound", path))

    def stop_playing(self) -> None:
        self.calls.append(("media.stop_playing", ""))

    def get_DoA(self):
        return self.doa


class _FakeClient:
    def __init__(self) -> None:
        self.media = _FakeMedia()
        self.calls: list[tuple[str, str]] = []
        self.connected = True

    def disconnect(self) -> None:
        self.calls.append(("disconnect", ""))

    def nod(self) -> None:
        self.calls.append(("nod", ""))


class _FakeMotionManager:
    def __init__(self) -> None:
        self.last_offsets = None
        self.queued_moves = []

    def set_speech_offsets(self, offsets):  # type: ignore[no-untyped-def]
        self.last_offsets = offsets

    def queue_move(self, move):  # type: ignore[no-untyped-def]
        self.queued_moves.append(move)


class ReachyAdapterTest(unittest.IsolatedAsyncioTestCase):
    def test_build_connect_kwargs_modes(self) -> None:
        a_auto = ReachySdkAdapter(host="reachy-mini.local", connection_mode="auto")
        self.assertEqual(
            a_auto._build_connect_kwargs(),
            {"connection_mode": "auto", "timeout": 45.0, "host": "reachy-mini.local"},
        )
        a_local = ReachySdkAdapter(host="reachy-mini.local", connection_mode="localhost_only")
        self.assertEqual(
            a_local._build_connect_kwargs(),
            {"connection_mode": "localhost_only", "timeout": 45.0},
        )
        a_network = ReachySdkAdapter(host="192.168.1.20", connection_mode="network")
        self.assertEqual(
            a_network._build_connect_kwargs(),
            {"connection_mode": "network", "timeout": 45.0, "host": "192.168.1.20"},
        )

    async def test_speak_uses_media_play_sound(self) -> None:
        client = _FakeClient()
        adapter = ReachySdkAdapter(
            host="127.0.0.1", tts_engine="espeak-ng", gesture_enabled=False, client=client
        )
        adapter._synthesize_to_wav = lambda text: "/tmp/fake.wav"  # type: ignore[method-assign]
        adapter._cleanup_temp_wav = lambda path: None  # type: ignore[method-assign]
        task = SpeechTask(
            message_id="m1",
            text_ja="hello",
            voice_style="default",
            gesture_preset="nod",
            deadline_ms=1000,
        )
        await adapter.speak(task)
        self.assertEqual(client.media.calls[0], ("media.play_sound", "/tmp/fake.wav"))

    async def test_gesture_best_effort(self) -> None:
        client = _FakeClient()
        adapter = ReachySdkAdapter(
            host="127.0.0.1",
            tts_engine="espeak-ng",
            gesture_enabled=True,
            speech_motion_enabled=False,
            client=client,
        )
        adapter._synthesize_to_wav = lambda text: "/tmp/fake.wav"  # type: ignore[method-assign]
        adapter._cleanup_temp_wav = lambda path: None  # type: ignore[method-assign]
        async def _noop_gesture_loop(*args, **kwargs):  # type: ignore[no-untyped-def]
            return None
        adapter._run_gesture_loop = _noop_gesture_loop  # type: ignore[method-assign]
        task = SpeechTask(
            message_id="m1",
            text_ja="hello",
            voice_style="default",
            gesture_preset="nod",
            deadline_ms=1000,
        )
        await adapter.speak(task)
        # Gesture methods may be unavailable on the fake client; speak should still proceed.
        self.assertEqual(client.media.calls[0][0], "media.play_sound")

    async def test_health_and_stop(self) -> None:
        client = _FakeClient()
        adapter = ReachySdkAdapter(host="127.0.0.1", client=client)
        with patch("urllib.request.urlopen") as m:
            m.return_value.__enter__.return_value.status = 200
            self.assertEqual(await adapter.health(), "ok")
        await adapter.stop()
        self.assertEqual(await adapter.health(), "down")
        self.assertIn(("disconnect", ""), client.calls)
        self.assertIn(("media.stop_playing", ""), client.media.calls)

    async def test_tts_engine_failure_raises(self) -> None:
        client = _FakeClient()
        adapter = ReachySdkAdapter(
            host="127.0.0.1", tts_engine="unsupported", gesture_enabled=False, client=client
        )
        task = SpeechTask(
            message_id="m1",
            text_ja="hello",
            voice_style="default",
            gesture_preset="nod",
            deadline_ms=1000,
        )
        with self.assertRaises(RuntimeError):
            await adapter.speak(task)

    def test_openai_tts_wav_is_normalized_for_playback(self) -> None:
        adapter = ReachySdkAdapter(
            host="127.0.0.1",
            tts_engine="openai-tts",
            openai_api_key="sk-test",
        )

        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(24000)
            w.writeframes(b"\x00\x00\x10\x00" * 2400)

        adapter._request_openai_tts = lambda payload: buf.getvalue()  # type: ignore[method-assign]
        wav_path = adapter._synthesize_with_openai_tts("hello")
        try:
            with wave.open(wav_path, "rb") as w:
                self.assertEqual(w.getnchannels(), 1)
                self.assertEqual(w.getsampwidth(), 2)
                self.assertEqual(w.getframerate(), 16000)
                self.assertGreater(w.getnframes(), 0)
        finally:
            adapter._cleanup_temp_wav(wav_path)

    def test_wav_duration_sec_reads_frames(self) -> None:
        adapter = ReachySdkAdapter(host="127.0.0.1")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name
        try:
            with wave.open(wav_path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"\x00\x00" * 8000)
            self.assertAlmostEqual(adapter._wav_duration_sec(wav_path), 0.5, places=2)
        finally:
            if os.path.exists(wav_path):
                os.unlink(wav_path)

    def test_apply_speech_frame_adds_visible_base_motion(self) -> None:
        adapter = ReachySdkAdapter(host="127.0.0.1")
        manager = _FakeMotionManager()
        adapter._motion_manager = manager  # type: ignore[assignment]
        with patch("reachy_twitch_voice.reachy_adapter.time.monotonic", return_value=1.0):
            ok = adapter._apply_speech_frame(0.0, 0.0, 0.0, 0.0, 1.0)
        self.assertTrue(ok)
        self.assertIsNotNone(manager.last_offsets)
        self.assertNotEqual(manager.last_offsets, (0.0, 0.0, 0.0, 0.0, 0.0, 0.0))

    def test_queue_speaking_phrase_enqueues_small_move(self) -> None:
        adapter = ReachySdkAdapter(host="127.0.0.1")
        manager = _FakeMotionManager()
        adapter._motion_manager = manager  # type: ignore[assignment]
        adapter._resolve_gesture_fallback = lambda preset, **kwargs: ("move", preset, kwargs)  # type: ignore[method-assign]
        adapter._rng.seed(1)
        adapter._queue_speaking_phrase()
        self.assertEqual(len(manager.queued_moves), 1)

    async def test_speak_waits_for_wav_duration(self) -> None:
        client = _FakeClient()
        adapter = ReachySdkAdapter(
            host="127.0.0.1", tts_engine="espeak-ng", gesture_enabled=False, client=client
        )
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name
        try:
            with wave.open(wav_path, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"\x00\x00" * 1600)
            adapter._synthesize_to_wav = lambda text: wav_path  # type: ignore[method-assign]
            adapter._cleanup_temp_wav = lambda path: None  # type: ignore[method-assign]
            task = SpeechTask(
                message_id="m2",
                text_ja="hello",
                voice_style="default",
                gesture_preset="nod",
                deadline_ms=1000,
            )
            started = time.monotonic()
            await adapter.speak(task)
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 0.09)
        finally:
            if os.path.exists(wav_path):
                os.unlink(wav_path)


if __name__ == "__main__":
    unittest.main()
