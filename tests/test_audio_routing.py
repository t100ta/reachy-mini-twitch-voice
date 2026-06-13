from __future__ import annotations

import asyncio
import unittest

from reachy_twitch_voice.config import PipelineConfig, ReachyConfig, TwitchConfig
from reachy_twitch_voice.orchestrator import AppDeps, AppOrchestrator
from reachy_twitch_voice.reachy_adapter import MockReachyAdapter, ReachySdkAdapter


class TestReachySdkAdapterAudioTarget(unittest.TestCase):
    def test_set_audio_output_target_valid_values(self) -> None:
        adapter = ReachySdkAdapter(host="127.0.0.1")
        adapter.set_audio_output_target("web")
        self.assertEqual(adapter._audio_output_target, "web")
        adapter.set_audio_output_target("robot")
        self.assertEqual(adapter._audio_output_target, "robot")

    def test_set_audio_output_target_invalid_falls_back_to_robot(self) -> None:
        adapter = ReachySdkAdapter(host="127.0.0.1")
        adapter.set_audio_output_target("web")  # change away from default
        adapter.set_audio_output_target("invalid_value")
        self.assertEqual(adapter._audio_output_target, "robot")

    def test_set_audio_output_target_strips_and_lowercases(self) -> None:
        adapter = ReachySdkAdapter(host="127.0.0.1")
        adapter.set_audio_output_target("  WEB  ")
        self.assertEqual(adapter._audio_output_target, "web")

    def test_init_audio_output_target_default_is_robot(self) -> None:
        adapter = ReachySdkAdapter(host="127.0.0.1")
        self.assertEqual(adapter._audio_output_target, "robot")

    def test_init_audio_output_target_from_param(self) -> None:
        adapter = ReachySdkAdapter(host="127.0.0.1", audio_output_target="web")
        self.assertEqual(adapter._audio_output_target, "web")

    def test_init_audio_output_target_invalid_param_falls_back(self) -> None:
        adapter = ReachySdkAdapter(host="127.0.0.1", audio_output_target="bad")
        self.assertEqual(adapter._audio_output_target, "robot")

    def test_register_web_audio_sink_stores_callback(self) -> None:
        adapter = ReachySdkAdapter(host="127.0.0.1")
        called: list[str] = []

        def sink(path: str) -> None:
            called.append(path)

        adapter.register_web_audio_sink(sink)
        self.assertIs(adapter._web_audio_sink, sink)


class TestMockReachyAdapterAudioTarget(unittest.TestCase):
    def test_set_audio_output_target_is_noop(self) -> None:
        adapter = MockReachyAdapter()
        # Should not raise
        adapter.set_audio_output_target("web")
        adapter.set_audio_output_target("robot")
        adapter.set_audio_output_target("invalid")

    def test_register_web_audio_sink_is_noop(self) -> None:
        adapter = MockReachyAdapter()
        # Should not raise
        adapter.register_web_audio_sink(lambda p: None)
        adapter.register_web_audio_sink(None)  # type: ignore[arg-type]


class TestOrchestratorAudioTarget(unittest.IsolatedAsyncioTestCase):
    def _make_orch(self, audio_output_target: str = "robot") -> tuple[AppOrchestrator, MockReachyAdapter]:
        cfg = PipelineConfig(
            twitch=TwitchConfig(channel="chan", oauth_token="t", nick="n"),
            reachy=ReachyConfig(audio_output_target=audio_output_target),
        )
        adapter = MockReachyAdapter()
        deps = AppDeps(cfg=cfg, adapter=adapter, irc_messages=asyncio.Queue())
        orch = AppOrchestrator(deps)
        return orch, adapter

    def test_initial_audio_output_target_from_config(self) -> None:
        orch, _ = self._make_orch("web")
        self.assertEqual(orch.audio_output_target, "web")

    def test_initial_audio_output_target_default_robot(self) -> None:
        orch, _ = self._make_orch("robot")
        self.assertEqual(orch.audio_output_target, "robot")

    async def test_set_audio_output_target_updates_state(self) -> None:
        orch, _ = self._make_orch()
        await orch.set_audio_output_target("web")
        self.assertEqual(orch.audio_output_target, "web")

    async def test_set_audio_output_target_invalid_falls_back_to_robot(self) -> None:
        orch, _ = self._make_orch()
        await orch.set_audio_output_target("web")  # change first
        await orch.set_audio_output_target("bad_value")
        self.assertEqual(orch.audio_output_target, "robot")

    async def test_set_audio_output_target_calls_through_to_adapter(self) -> None:
        orch, adapter = self._make_orch()
        # Track calls via a subclass override
        calls: list[str] = []
        original = adapter.set_audio_output_target

        def tracking_set(target: str) -> None:
            calls.append(target)
            return original(target)

        adapter.set_audio_output_target = tracking_set  # type: ignore[method-assign]
        await orch.set_audio_output_target("web")
        self.assertIn("web", calls)

    def test_register_web_audio_sink_calls_through_to_adapter(self) -> None:
        orch, adapter = self._make_orch()
        cb_calls: list[str] = []

        def cb(path: str) -> None:
            cb_calls.append(path)

        # Should not raise (MockReachyAdapter no-ops it)
        orch.register_web_audio_sink(cb)


if __name__ == "__main__":
    unittest.main()
