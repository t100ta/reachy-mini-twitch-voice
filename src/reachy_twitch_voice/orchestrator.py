from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from .config import ConversationConfig, PipelineConfig
from .conversation_session import FALLBACK_REPLY, ConversationSession, create_conversation_session
from .input_adapter import RealtimeInputAdapter
from .normalizer import normalize_comment
from .reachy_adapter import ReachyAdapter
from .safety import SafetyFilter
from .tool_executor import ToolExecutor
from .twitch_parser import parse_privmsg
from .types import ConversationOutputEvent, RuntimeStats, SpeechTask

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class AppDeps:
    cfg: PipelineConfig
    adapter: ReachyAdapter
    irc_messages: asyncio.Queue[str]
    input_adapter: RealtimeInputAdapter | None = None
    conversation: ConversationSession | None = None
    tool_executor: ToolExecutor | None = None


class AppOrchestrator:
    def __init__(self, deps: AppDeps) -> None:
        self.deps = deps
        self.stats = RuntimeStats()
        self.filter = SafetyFilter(deps.cfg.safety)
        self.input_adapter = deps.input_adapter or RealtimeInputAdapter()
        self.conversation = deps.conversation or create_conversation_session(
            deps.cfg.conversation,
            deps.cfg.safety,
        )
        self.tool_executor = deps.tool_executor or ToolExecutor()
        self.operator_usernames = set(deps.cfg.conversation.operator_usernames)

    async def reload_conversation_config(self, cfg: ConversationConfig) -> None:
        self.deps.cfg.conversation = cfg
        self.operator_usernames = set(cfg.operator_usernames)
        await self.conversation.reload_config(cfg)

    async def consume_once(self, raw: str) -> None:
        msg = parse_privmsg(raw)
        if msg is None:
            return

        normalized = normalize_comment(msg.text)
        decision = self.filter.evaluate(msg, normalized)
        if not decision.allow:
            self.stats.filtered += 1
            LOGGER.info("Filtered message id=%s reason=%s", msg.id, decision.reason)
            return

        event = self.input_adapter.to_conversation_input(msg)
        event.queue_age_ms = max((time.time() - msg.received_at) * 1000.0, 0.0)
        if event.queue_age_ms > self.deps.cfg.runtime.max_queue_wait_ms:
            self.stats.dropped += 1
            LOGGER.info(
                "Dropped stale message id=%s queue_age_ms=%.1f limit_ms=%s",
                msg.id,
                event.queue_age_ms,
                self.deps.cfg.runtime.max_queue_wait_ms,
            )
            return
        event.text = decision.sanitized_text or normalized
        event.is_operator = msg.user_name.lower() in self.operator_usernames
        try:
            convo = await self.conversation.generate(event)
        except Exception as exc:
            LOGGER.warning(
                "Conversation generation failed id=%s error=%s; using fallback",
                msg.id,
                exc,
            )
            convo = ConversationOutputEvent(
                reply_text=FALLBACK_REPLY,
                emotion="empathy",
                tool_calls=[],
            )
        gesture = self.tool_executor.pick_gesture(convo)

        task = SpeechTask(
            message_id=msg.id,
            text_ja=convo.reply_text,
            voice_style="default",
            gesture_preset=gesture,
            emotion=convo.emotion,
            deadline_ms=self._speech_deadline_ms(convo.reply_text),
        )

        timeout_s = task.deadline_ms / 1000
        reaction_start_ms = (time.time() - msg.received_at) * 1000
        try:
            await asyncio.wait_for(self.deps.adapter.speak(task), timeout=timeout_s)
            self.stats.processed += 1
            self.stats.add_latency(reaction_start_ms)
        except Exception as exc:
            self.stats.failed += 1
            LOGGER.warning(
                "Failed to speak message id=%s error_type=%s error=%r",
                msg.id,
                type(exc).__name__,
                exc,
            )

    async def run(self) -> None:
        while True:
            try:
                raw = await asyncio.wait_for(
                    self.deps.irc_messages.get(),
                    timeout=self.deps.cfg.runtime.idle_interval_sec,
                )
                await self.consume_once(raw)
            except asyncio.TimeoutError:
                if self.deps.cfg.runtime.idle_motion_enabled:
                    await self.deps.adapter.idle_tick()

    def _speech_deadline_ms(self, text: str) -> int:
        base = self.deps.cfg.runtime.message_timeout_ms
        # Approximate JP speech duration + safety margin.
        estimated = int((max(len(text), 1) / 6.0) * 1000) + 7000
        return min(max(base, estimated), 45000)
