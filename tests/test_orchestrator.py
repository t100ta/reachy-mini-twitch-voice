import asyncio
import unittest

from reachy_twitch_voice.config import (
    ConversationConfig,
    PipelineConfig,
    RuntimeConfig,
    SafetyConfig,
    TwitchConfig,
)
from reachy_twitch_voice.orchestrator import AppDeps, AppOrchestrator
from reachy_twitch_voice.reachy_adapter import MockReachyAdapter


class SlowAdapter(MockReachyAdapter):
    async def speak(self, task):  # type: ignore[override]
        await asyncio.sleep(0.02)


class FailingAdapter(MockReachyAdapter):
    async def speak(self, task):  # type: ignore[override]
        raise TimeoutError()


class IdleAwareAdapter(MockReachyAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.idle_count = 0

    async def idle_tick(self) -> None:  # type: ignore[override]
        self.idle_count += 1
        await asyncio.sleep(0)


class _CaptureConversation:
    def __init__(self) -> None:
        self.last_event = None

    async def generate(self, event):  # type: ignore[no-untyped-def]
        self.last_event = event
        from reachy_twitch_voice.types import ConversationOutputEvent

        return ConversationOutputEvent(reply_text="ok", emotion="empathy", tool_calls=[])


class OrchestratorTest(unittest.IsolatedAsyncioTestCase):
    async def test_long_reply_deadline_is_conservative(self) -> None:
        cfg = PipelineConfig(
            twitch=TwitchConfig(channel="chan", oauth_token="t", nick="n"),
            runtime=RuntimeConfig(message_timeout_ms=1000, reconnect_max_sec=30),
        )
        adapter = MockReachyAdapter()
        deps = AppDeps(cfg=cfg, adapter=adapter, irc_messages=asyncio.Queue())
        orch = AppOrchestrator(deps)

        deadline_ms = orch._speech_deadline_ms("あ" * 180)

        self.assertGreaterEqual(deadline_ms, 70000)

    async def test_consume_once_speaks(self) -> None:
        cfg = PipelineConfig(
            twitch=TwitchConfig(channel="chan", oauth_token="t", nick="n"),
            safety=SafetyConfig(ng_words=["bad"], max_chars=140, spam_window_sec=5),
            runtime=RuntimeConfig(message_timeout_ms=1000, reconnect_max_sec=30),
        )
        adapter = MockReachyAdapter()
        deps = AppDeps(cfg=cfg, adapter=adapter, irc_messages=asyncio.Queue())
        orch = AppOrchestrator(deps)

        raw = ":alice!alice@alice.tmi.twitch.tv PRIVMSG #chan :hello"
        await orch.consume_once(raw)

        self.assertEqual(len(adapter.spoken), 1)
        self.assertTrue(adapter.spoken[0])
        self.assertEqual(orch.stats.processed, 1)
        self.assertGreaterEqual(orch.stats.p95_latency_ms(), 0.0)

    async def test_consume_once_filtered(self) -> None:
        cfg = PipelineConfig(
            twitch=TwitchConfig(channel="chan", oauth_token="t", nick="n"),
            safety=SafetyConfig(ng_words=["bad"], max_chars=140, spam_window_sec=5),
            runtime=RuntimeConfig(message_timeout_ms=1000, reconnect_max_sec=30),
        )
        adapter = MockReachyAdapter()
        deps = AppDeps(cfg=cfg, adapter=adapter, irc_messages=asyncio.Queue())
        orch = AppOrchestrator(deps)

        raw = ":alice!alice@alice.tmi.twitch.tv PRIVMSG #chan :this is bad"
        await orch.consume_once(raw)

        self.assertEqual(adapter.spoken, [])
        self.assertEqual(orch.stats.filtered, 1)

    async def test_consume_once_timeout_is_failed(self) -> None:
        cfg = PipelineConfig(
            twitch=TwitchConfig(channel="chan", oauth_token="t", nick="n"),
            safety=SafetyConfig(ng_words=["bad"], max_chars=140, spam_window_sec=5),
            runtime=RuntimeConfig(message_timeout_ms=1, reconnect_max_sec=30),
        )
        adapter = FailingAdapter()
        deps = AppDeps(cfg=cfg, adapter=adapter, irc_messages=asyncio.Queue())
        orch = AppOrchestrator(deps)

        raw = ":alice!alice@alice.tmi.twitch.tv PRIVMSG #chan :hello"
        await orch.consume_once(raw)

        self.assertEqual(orch.stats.failed, 1)

    async def test_operator_detection_in_event(self) -> None:
        cfg = PipelineConfig(
            twitch=TwitchConfig(channel="chan", oauth_token="t", nick="n"),
            safety=SafetyConfig(ng_words=["bad"], max_chars=140, spam_window_sec=5),
            runtime=RuntimeConfig(message_timeout_ms=1000, reconnect_max_sec=30),
            conversation=ConversationConfig(operator_usernames=["tom_t100ta"]),
        )
        adapter = MockReachyAdapter()
        convo = _CaptureConversation()
        deps = AppDeps(
            cfg=cfg,
            adapter=adapter,
            irc_messages=asyncio.Queue(),
            conversation=convo,  # type: ignore[arg-type]
        )
        orch = AppOrchestrator(deps)

        raw = ":tom_t100ta!u@u.tmi.twitch.tv PRIVMSG #chan :hello"
        await orch.consume_once(raw)

        self.assertIsNotNone(convo.last_event)
        self.assertTrue(convo.last_event.is_operator)

    async def test_idle_tick_runs_when_queue_silent(self) -> None:
        cfg = PipelineConfig(
            twitch=TwitchConfig(channel="chan", oauth_token="t", nick="n"),
            runtime=RuntimeConfig(
                message_timeout_ms=1000,
                reconnect_max_sec=30,
                idle_motion_enabled=True,
                idle_interval_sec=0.02,
            ),
        )
        adapter = IdleAwareAdapter()
        deps = AppDeps(cfg=cfg, adapter=adapter, irc_messages=asyncio.Queue())
        orch = AppOrchestrator(deps)

        task = asyncio.create_task(orch.run())
        await asyncio.sleep(0.08)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertGreaterEqual(adapter.idle_count, 1)

    async def test_consume_once_stale_message_is_dropped(self) -> None:
        cfg = PipelineConfig(
            twitch=TwitchConfig(channel="chan", oauth_token="t", nick="n"),
            runtime=RuntimeConfig(
                message_timeout_ms=1000,
                reconnect_max_sec=30,
                max_queue_wait_ms=1,
            ),
        )
        adapter = MockReachyAdapter()
        deps = AppDeps(cfg=cfg, adapter=adapter, irc_messages=asyncio.Queue())
        orch = AppOrchestrator(deps)

        raw = "@tmi-sent-ts=1000 :alice!alice@alice.tmi.twitch.tv PRIVMSG #chan :hello"
        await orch.consume_once(raw)

        self.assertEqual(adapter.spoken, [])
        self.assertEqual(orch.stats.dropped, 1)

    async def test_max_queue_wait_zero_disables_stale_drop(self) -> None:
        cfg = PipelineConfig(
            twitch=TwitchConfig(channel="chan", oauth_token="t", nick="n"),
            runtime=RuntimeConfig(
                message_timeout_ms=1000,
                reconnect_max_sec=30,
                max_queue_wait_ms=0,
            ),
        )
        adapter = MockReachyAdapter()
        deps = AppDeps(cfg=cfg, adapter=adapter, irc_messages=asyncio.Queue())
        orch = AppOrchestrator(deps)

        raw = "@tmi-sent-ts=1000 :alice!alice@alice.tmi.twitch.tv PRIVMSG #chan :hello"
        await orch.consume_once(raw)

        self.assertEqual(len(adapter.spoken), 1)
        self.assertEqual(orch.stats.dropped, 0)

    async def test_manual_mode_ignores_twitch_input(self) -> None:
        cfg = PipelineConfig(
            twitch=TwitchConfig(channel="chan", oauth_token="t", nick="n"),
            runtime=RuntimeConfig(message_timeout_ms=1000, reconnect_max_sec=30),
            conversation=ConversationConfig(input_mode="manual_text"),
        )
        adapter = MockReachyAdapter()
        deps = AppDeps(cfg=cfg, adapter=adapter, irc_messages=asyncio.Queue())
        orch = AppOrchestrator(deps)

        raw = ":alice!alice@alice.tmi.twitch.tv PRIVMSG #chan :hello"
        await orch.consume_once(raw)

        self.assertEqual(adapter.spoken, [])
        self.assertEqual(orch.stats.processed, 0)

    async def test_consume_manual_text_speaks(self) -> None:
        cfg = PipelineConfig(
            twitch=TwitchConfig(channel="chan", oauth_token="t", nick="n"),
            runtime=RuntimeConfig(message_timeout_ms=1000, reconnect_max_sec=30),
        )
        adapter = MockReachyAdapter()
        convo = _CaptureConversation()
        deps = AppDeps(
            cfg=cfg,
            adapter=adapter,
            irc_messages=asyncio.Queue(),
            conversation=convo,  # type: ignore[arg-type]
        )
        orch = AppOrchestrator(deps)

        await orch.set_input_mode("manual_text")
        await orch.consume_manual_text("manual hello", user_name="tester")

        self.assertEqual(len(adapter.spoken), 1)
        self.assertIsNotNone(convo.last_event)
        self.assertEqual(convo.last_event.source, "manual")
        self.assertEqual(convo.last_event.channel, "manual")
        self.assertEqual(convo.last_event.user_name, "tester")
        self.assertEqual(convo.last_event.display_name, "tester")

    async def test_channel_event_raid_speaks(self) -> None:
        cfg = PipelineConfig(
            twitch=TwitchConfig(channel="chan", oauth_token="t", nick="n"),
            runtime=RuntimeConfig(message_timeout_ms=1000, reconnect_max_sec=30),
        )
        adapter = MockReachyAdapter()
        convo = _CaptureConversation()
        deps = AppDeps(
            cfg=cfg,
            adapter=adapter,
            irc_messages=asyncio.Queue(),
            conversation=convo,  # type: ignore[arg-type]
        )
        orch = AppOrchestrator(deps)

        raw = (
            "@msg-id=raid;login=raider;display-name=Raider;"
            "msg-param-viewerCount=10;tmi-sent-ts=5000"
            " :tmi.twitch.tv USERNOTICE #chan"
        )
        await orch.consume_once(raw)

        self.assertEqual(len(adapter.spoken), 1)
        self.assertIsNotNone(convo.last_event)
        self.assertEqual(convo.last_event.source, "twitch_event")
        self.assertIn("ライド", convo.last_event.text)

    async def test_channel_event_disabled_ignores(self) -> None:
        cfg = PipelineConfig(
            twitch=TwitchConfig(channel="chan", oauth_token="t", nick="n"),
            runtime=RuntimeConfig(
                message_timeout_ms=1000,
                reconnect_max_sec=30,
                channel_events_enabled=False,
            ),
        )
        adapter = MockReachyAdapter()
        deps = AppDeps(cfg=cfg, adapter=adapter, irc_messages=asyncio.Queue())
        orch = AppOrchestrator(deps)

        raw = (
            "@msg-id=raid;login=raider;tmi-sent-ts=5000"
            " :tmi.twitch.tv USERNOTICE #chan"
        )
        await orch.consume_once(raw)

        self.assertEqual(adapter.spoken, [])

    async def test_channel_event_type_filter(self) -> None:
        cfg = PipelineConfig(
            twitch=TwitchConfig(channel="chan", oauth_token="t", nick="n"),
            runtime=RuntimeConfig(
                message_timeout_ms=1000,
                reconnect_max_sec=30,
                channel_event_types=["sub"],
            ),
        )
        adapter = MockReachyAdapter()
        deps = AppDeps(cfg=cfg, adapter=adapter, irc_messages=asyncio.Queue())
        orch = AppOrchestrator(deps)

        raw = (
            "@msg-id=raid;login=raider;tmi-sent-ts=5000"
            " :tmi.twitch.tv USERNOTICE #chan"
        )
        await orch.consume_once(raw)

        self.assertEqual(adapter.spoken, [])

    async def test_manual_text_operator_detection(self) -> None:
        cfg = PipelineConfig(
            twitch=TwitchConfig(channel="chan", oauth_token="t", nick="n"),
            runtime=RuntimeConfig(message_timeout_ms=1000, reconnect_max_sec=30),
            conversation=ConversationConfig(operator_usernames=["manual_tester"]),
        )
        adapter = MockReachyAdapter()
        convo = _CaptureConversation()
        deps = AppDeps(
            cfg=cfg,
            adapter=adapter,
            irc_messages=asyncio.Queue(),
            conversation=convo,  # type: ignore[arg-type]
        )
        orch = AppOrchestrator(deps)

        await orch.consume_manual_text("hello", user_name="manual_tester")

        self.assertIsNotNone(convo.last_event)
        self.assertTrue(convo.last_event.is_operator)


if __name__ == "__main__":
    unittest.main()
