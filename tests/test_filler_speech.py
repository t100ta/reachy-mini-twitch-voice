"""Tests for filler speech / deferred generation parallelism."""
from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import AsyncMock

from reachy_twitch_voice.config import PipelineConfig, TwitchConfig
from reachy_twitch_voice.orchestrator import AppDeps, AppOrchestrator, FILLER_REPLIES
from reachy_twitch_voice.reachy_adapter import MockReachyAdapter
from reachy_twitch_voice.types import ConversationInputEvent, ConversationOutputEvent


def _make_event(msg_id: str = "test-1") -> ConversationInputEvent:
    return ConversationInputEvent(
        message_id=msg_id,
        user_name="tester",
        display_name="Tester",
        channel="test_channel",
        text="test message",
        received_at=time.time(),
        is_operator=False,
        source="twitch",
    )


def _fast_convo() -> ConversationOutputEvent:
    return ConversationOutputEvent(reply_text="はい！", emotion="joy", tool_calls=[])


def _slow_convo() -> ConversationOutputEvent:
    return ConversationOutputEvent(reply_text="調べた結果です！", emotion="joy", tool_calls=[])


def _make_orchestrator(filler_delay: float = 0.3) -> AppOrchestrator:
    cfg = PipelineConfig(
        twitch=TwitchConfig(channel="test", oauth_token="oauth:test", nick="testbot"),
    )
    cfg.runtime.filler_delay_sec = filler_delay
    adapter = MockReachyAdapter()
    q: asyncio.Queue[str] = asyncio.Queue()
    deps = AppDeps(cfg=cfg, adapter=adapter, irc_messages=q)
    orch = AppOrchestrator(deps)
    return orch


class FillerSpeechTest(unittest.IsolatedAsyncioTestCase):
    async def test_fast_path_no_filler(self) -> None:
        """Fast response arrives before filler_delay — no filler, one speak."""
        orch = _make_orchestrator(filler_delay=0.3)
        event = _make_event()
        speak_calls: list[str] = []

        async def instant_generate(ev: ConversationInputEvent) -> ConversationOutputEvent:
            return _fast_convo()

        orch.conversation.generate = instant_generate  # type: ignore[assignment]
        orch.deps.adapter.speak = AsyncMock(  # type: ignore[assignment]
            side_effect=lambda t: speak_calls.append(t.text_ja)
        )

        await orch._process_event(event)
        await asyncio.sleep(0.05)  # let any spurious background tasks settle

        self.assertEqual(len(speak_calls), 1)
        self.assertEqual(speak_calls[0], "はい！")
        self.assertNotIn(speak_calls[0], FILLER_REPLIES)

    async def test_slow_path_filler_then_real(self) -> None:
        """Slow response triggers filler, then real answer delivered after."""
        filler_delay = 0.2
        orch = _make_orchestrator(filler_delay=filler_delay)
        event = _make_event()
        speak_calls: list[str] = []
        research_extra = 0.25  # delay beyond filler threshold

        async def slow_generate(ev: ConversationInputEvent) -> ConversationOutputEvent:
            await asyncio.sleep(filler_delay + research_extra)
            return _slow_convo()

        orch.conversation.generate = slow_generate  # type: ignore[assignment]
        orch.deps.adapter.speak = AsyncMock(  # type: ignore[assignment]
            side_effect=lambda t: speak_calls.append(t.text_ja)
        )

        await orch._process_event(event)
        # At this point filler was spoken and we returned. Real answer is in background.
        self.assertEqual(len(speak_calls), 1)
        self.assertIn(speak_calls[0], FILLER_REPLIES)

        # Wait for background task to complete
        await asyncio.sleep(research_extra + 0.3)
        self.assertEqual(len(speak_calls), 2)
        self.assertIn("調べた結果です！", speak_calls[1])

    async def test_parallel_light_chat_during_research(self) -> None:
        """Light chat processed while heavy research runs in background."""
        filler_delay = 0.2
        orch = _make_orchestrator(filler_delay=filler_delay)
        slow_event = _make_event("slow-1")
        fast_event = _make_event("fast-1")
        speak_calls: list[str] = []
        research_extra = 0.3

        async def generate_dispatch(ev: ConversationInputEvent) -> ConversationOutputEvent:
            if ev.message_id == "slow-1":
                await asyncio.sleep(filler_delay + research_extra)
                return _slow_convo()
            return _fast_convo()

        orch.conversation.generate = generate_dispatch  # type: ignore[assignment]
        orch.deps.adapter.speak = AsyncMock(  # type: ignore[assignment]
            side_effect=lambda t: speak_calls.append(t.text_ja)
        )

        # Trigger heavy query — returns after emitting filler
        await orch._process_event(slow_event)
        self.assertEqual(len(speak_calls), 1)  # filler only
        self.assertIn(speak_calls[0], FILLER_REPLIES)

        # Now trigger light chat while research is still running
        await orch._process_event(fast_event)
        self.assertEqual(len(speak_calls), 2)  # fast chat responded immediately
        self.assertEqual(speak_calls[1], "はい！")

        # Wait for deferred research to complete
        await asyncio.sleep(research_extra + 0.3)
        self.assertEqual(len(speak_calls), 3)
        self.assertIn("調べた結果です！", speak_calls[2])

    async def test_speech_lock_prevents_concurrent_audio(self) -> None:
        """Two speaks don't overlap — lock ensures serialization."""
        orch = _make_orchestrator()
        speak_times: list[tuple[str, str, float]] = []

        async def slow_speak(task):  # type: ignore[no-untyped-def]
            loop = asyncio.get_event_loop()
            speak_times.append(("start", task.text_ja, loop.time()))
            await asyncio.sleep(0.15)
            speak_times.append(("end", task.text_ja, loop.time()))

        orch.deps.adapter.speak = slow_speak  # type: ignore[assignment]

        from reachy_twitch_voice.types import MotionPlan, SpeechTask

        e = _make_event()
        t1 = SpeechTask(
            message_id="1",
            text_ja="first",
            voice_style="default",
            gesture_preset="nod",
            emotion="joy",
            deadline_ms=5000,
            motion_plan=MotionPlan(fallback_gesture="nod"),
        )
        t2 = SpeechTask(
            message_id="2",
            text_ja="second",
            voice_style="default",
            gesture_preset="nod",
            emotion="joy",
            deadline_ms=5000,
            motion_plan=MotionPlan(fallback_gesture="nod"),
        )

        await asyncio.gather(orch._speak(t1, e), orch._speak(t2, e))

        # Verify non-overlapping: first's end must be <= second's start, or vice versa
        ends = {name: ts for action, name, ts in speak_times if action == "end"}
        starts = {name: ts for action, name, ts in speak_times if action == "start"}
        # One must finish before the other starts
        self.assertTrue(
            ends["first"] <= starts["second"] or ends["second"] <= starts["first"],
            "Audio playbacks overlapped — speech lock not working",
        )

    async def test_cancel_deferred_tasks(self) -> None:
        """cancel_deferred_tasks cancels all in-flight background tasks."""
        filler_delay = 0.1
        orch = _make_orchestrator(filler_delay=filler_delay)
        event = _make_event()

        async def very_slow_generate(ev: ConversationInputEvent) -> ConversationOutputEvent:
            await asyncio.sleep(10.0)
            return _slow_convo()

        async def noop_speak(task):  # type: ignore[no-untyped-def]
            pass

        orch.conversation.generate = very_slow_generate  # type: ignore[assignment]
        orch.deps.adapter.speak = noop_speak  # type: ignore[assignment]

        await orch._process_event(event)
        # One deferred task should be tracked
        self.assertEqual(len(orch._deferred_tasks), 1)
        deferred_task = next(iter(orch._deferred_tasks))

        await orch.cancel_deferred_tasks()
        # All deferred tasks cleared
        self.assertEqual(len(orch._deferred_tasks), 0)
        # The task itself should have been cancelled
        self.assertTrue(deferred_task.cancelled())


if __name__ == "__main__":
    unittest.main()
