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


if __name__ == "__main__":
    unittest.main()
