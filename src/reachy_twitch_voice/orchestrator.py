from __future__ import annotations

import asyncio
import logging
import random as _random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .app_state_store import AppStateStore
from .config import ConversationConfig, PipelineConfig
from .conversation_session import FALLBACK_REPLY, ConversationSession, create_conversation_session
from .input_adapter import ManualTextInputAdapter, RealtimeInputAdapter
from .normalizer import normalize_comment
from .reachy_adapter import ReachyAdapter
from .safety import SafetyFilter
from .stream_journal_store import NoopStreamJournalStore, StreamJournalStore
from .tool_executor import ToolExecutor
from .twitch_parser import parse_privmsg, parse_usernotice
from .types import ChannelEvent, ConversationInputEvent, ConversationInputSource, ConversationOutputEvent, RuntimeStats, SpeechTask
from .viewer_memory_store import NoopViewerMemoryStore, ViewerMemoryStoreProtocol

LOGGER = logging.getLogger(__name__)

FILLER_REPLIES: tuple[str, ...] = (
    "いい質問だね、ちょっと調べてみる！",
    "おっ、それ気になる。少し待ってね！",
    "ちょっと調べるから待っててね！",
    "むずかしい質問だ、少し考える時間ちょうだい！",
)
_MAX_DEFERRED_CONCURRENT = 3  # cap simultaneous in-flight research tasks


@dataclass(slots=True)
class AppDeps:
    cfg: PipelineConfig
    adapter: ReachyAdapter
    irc_messages: asyncio.Queue[str]
    input_adapter: RealtimeInputAdapter | None = None
    conversation: ConversationSession | None = None
    tool_executor: ToolExecutor | None = None
    viewer_memory: ViewerMemoryStoreProtocol | None = None
    stream_journal: object | None = None  # StreamJournalStore or NoopStreamJournalStore
    state_store: AppStateStore | None = None


class AppOrchestrator:
    def __init__(self, deps: AppDeps) -> None:
        self.deps = deps
        self.stats = RuntimeStats()
        self.filter = SafetyFilter(deps.cfg.safety)
        self.input_adapter = deps.input_adapter or RealtimeInputAdapter()
        self.viewer_memory: ViewerMemoryStoreProtocol = (
            deps.viewer_memory or NoopViewerMemoryStore()
        )
        self.conversation = deps.conversation or create_conversation_session(
            deps.cfg.conversation,
            deps.cfg.safety,
            hermes_cfg=deps.cfg.hermes,
            viewer_memory_cfg=deps.cfg.viewer_memory,
            viewer_store=self.viewer_memory,
            journal_store=deps.stream_journal,
            stream_journal_cfg=deps.cfg.stream_journal,
        )
        self.tool_executor = deps.tool_executor or ToolExecutor()
        self.manual_input_adapter = ManualTextInputAdapter()
        if hasattr(self.conversation, "update_twitch_context"):
            self.conversation.update_twitch_context(deps.cfg.twitch.channel)
        self.operator_usernames = set(deps.cfg.conversation.operator_usernames)
        input_mode = deps.cfg.conversation.input_mode.strip().lower()
        self.input_mode: ConversationInputSource = "manual" if input_mode == "manual_text" else "twitch"
        self.channel_events_enabled: bool = deps.cfg.runtime.channel_events_enabled
        self.channel_event_types: set[str] = set(deps.cfg.runtime.channel_event_types)
        self.audio_output_target: str = deps.cfg.reachy.audio_output_target
        # Stream journal
        journal_cfg = deps.cfg.stream_journal
        if deps.stream_journal is not None:
            self.stream_journal = deps.stream_journal
        elif journal_cfg.enabled:
            try:
                self.stream_journal: object = StreamJournalStore(journal_cfg.db_path)
            except Exception as exc:
                LOGGER.warning("stream_journal: init failed (%s); using noop", exc)
                self.stream_journal = NoopStreamJournalStore()
        else:
            self.stream_journal = NoopStreamJournalStore()
        self._journal_entry_id: int | None = None
        if not isinstance(self.stream_journal, NoopStreamJournalStore):
            try:
                started_at = datetime.now(tz=timezone.utc).isoformat()
                self._journal_entry_id = self.stream_journal.start_entry(started_at)  # type: ignore[attr-defined]
            except Exception as exc:
                LOGGER.warning("stream_journal: start_entry failed: %s", exc)
                self._journal_entry_id = None
        # Token / connection state
        self._twitch_nick: str = deps.cfg.twitch.nick
        self._twitch_token: str = deps.cfg.twitch.oauth_token
        self._twitch_channel: str = deps.cfg.twitch.channel
        self.twitch_connection_status: str = "connecting"
        self.twitch_reconnect_event: asyncio.Event = asyncio.Event()
        self._irc_client: object | None = None
        self._state_store: AppStateStore | None = deps.state_store
        self._current_tts_voice: str = deps.cfg.reachy.tts_openai_voice
        self._speech_lock: asyncio.Lock = asyncio.Lock()
        self._deferred_tasks: set[asyncio.Task] = set()  # keep refs to prevent GC

    def get_twitch_credentials(self) -> tuple[str, str, str]:
        return (self._twitch_nick, self._twitch_token, self._twitch_channel)

    def set_twitch_status(self, status: str) -> None:
        self.twitch_connection_status = status
        LOGGER.info("Twitch connection status: %s", status)

    def attach_irc_client(self, client: object) -> None:
        self._irc_client = client

    def request_reconnect(self) -> None:
        self.twitch_reconnect_event.set()
        if self._irc_client is not None:
            self._irc_client.request_reconnect()  # type: ignore[attr-defined]

    async def set_twitch_token(self, token: str) -> None:
        normalized = token.strip()
        if not normalized.startswith("oauth:"):
            normalized = f"oauth:{normalized}"
        self._twitch_token = normalized
        if self._state_store is not None:
            self._state_store.save_token(normalized)
        self.request_reconnect()

    async def set_tts_voice(self, voice: str) -> None:
        if voice == self._current_tts_voice:
            return
        self._current_tts_voice = voice
        self.deps.adapter.set_tts_voice(voice)
        if self._state_store is not None:
            self._state_store.save_setting("tts_voice", voice)

    async def finalize_session(self) -> None:
        """Generate a stream summary and persist it to the journal. Called at shutdown."""
        if self._journal_entry_id is None:
            return
        ended_at = datetime.now(tz=timezone.utc).isoformat()
        journal_cfg = self.deps.cfg.stream_journal
        summarizer = getattr(self.conversation, "generate_stream_summary", None)
        result: dict | None = None
        if callable(summarizer):
            try:
                result = await asyncio.wait_for(
                    summarizer(min_turns=journal_cfg.min_turns_for_summary),
                    timeout=journal_cfg.summary_timeout_sec,
                )
            except asyncio.TimeoutError:
                LOGGER.warning("stream_journal: summary generation timed out")
            except Exception as exc:
                LOGGER.warning("stream_journal: summary generation failed: %s", exc)

        try:
            if result is not None:
                self.stream_journal.finalize_entry(  # type: ignore[attr-defined]
                    entry_id=self._journal_entry_id,
                    ended_at=ended_at,
                    summary=result.get("summary", ""),
                    highlights=result.get("highlights", []),
                    learnings=result.get("learnings", []),
                    turn_count=len(getattr(self.conversation, "session_log", [])),
                    unique_viewers=len(getattr(self.conversation, "_seen_this_session", set())),
                )
            else:
                # Update ended_at only; summary stays NULL (crash-safe)
                update_fn = getattr(self.stream_journal, "update_ended_at", None)
                if callable(update_fn):
                    update_fn(entry_id=self._journal_entry_id, ended_at=ended_at)
        except Exception as exc:
            LOGGER.warning("stream_journal: finalize_entry failed: %s", exc)

    async def reload_conversation_config(self, cfg: ConversationConfig) -> None:
        self.deps.cfg.conversation = cfg
        self.operator_usernames = set(cfg.operator_usernames)
        await self.conversation.reload_config(cfg)

    async def set_input_mode(self, mode: str) -> None:
        normalized = mode.strip().lower()
        self.input_mode = "manual" if normalized == "manual_text" else "twitch"

    async def set_channel_events_enabled(self, enabled: bool) -> None:
        self.channel_events_enabled = enabled

    async def set_channel_event_types(self, types: list[str]) -> None:
        self.channel_event_types = set(types)

    async def set_audio_output_target(self, target: str) -> None:
        normalized = target.strip().lower()
        self.audio_output_target = normalized if normalized in {"robot", "web"} else "robot"
        self.deps.adapter.set_audio_output_target(self.audio_output_target)

    def register_web_audio_sink(self, cb: Any) -> None:
        self.deps.adapter.register_web_audio_sink(cb)

    async def consume_manual_text(self, text: str, user_name: str = "manual_tester") -> None:
        event = self.manual_input_adapter.build_event(text=text, user_name=user_name)
        event.is_operator = event.user_name.lower() in self.operator_usernames
        await self._process_event(event)

    async def consume_once(self, raw: str) -> None:
        if self.input_mode != "twitch":
            LOGGER.debug("Ignoring Twitch input while input_mode=%s", self.input_mode)
            return
        msg = parse_privmsg(raw)
        if msg is None:
            if self.channel_events_enabled:
                ch_event = parse_usernotice(raw)
                if ch_event and ch_event.event_type in self.channel_event_types:
                    await self._process_channel_event(ch_event)
            return

        normalized = normalize_comment(msg.text)
        decision = self.filter.evaluate(msg, normalized)
        if not decision.allow:
            self.stats.filtered += 1
            LOGGER.info("Filtered message id=%s reason=%s", msg.id, decision.reason)
            return

        event = self.input_adapter.to_conversation_input(msg)
        event.queue_age_ms = max((time.time() - msg.received_at) * 1000.0, 0.0)
        if (
            self.deps.cfg.runtime.max_queue_wait_ms > 0
            and event.queue_age_ms > self.deps.cfg.runtime.max_queue_wait_ms
        ):
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
        await self._process_event(event)

    async def _process_channel_event(self, ch_event: ChannelEvent) -> None:
        display = ch_event.display_name or ch_event.user_name
        if ch_event.system_msg:
            text = ch_event.system_msg
        elif ch_event.event_type == "raid":
            count = ch_event.viewer_count if ch_event.viewer_count is not None else "？"
            text = f"[ライド] {display}が{count}人の視聴者と共にやって来ました！"
        else:
            text = f"[サブスク] {display}がサブスクしてくれました！"

        event = ConversationInputEvent(
            message_id=ch_event.id,
            user_name=ch_event.user_name,
            display_name=ch_event.display_name,
            channel=ch_event.channel,
            text=text,
            received_at=ch_event.received_at,
            is_operator=ch_event.user_name.lower() in self.operator_usernames,
            source="twitch_event",
            user_id=ch_event.user_id,
        )
        await self._process_event(event)

    async def _speak(self, task: SpeechTask, event: ConversationInputEvent) -> None:
        """Serialized speech playback — acquires _speech_lock for the duration of playback."""
        timeout_s = task.deadline_ms / 1000
        reaction_start_ms = (time.time() - event.received_at) * 1000
        async with self._speech_lock:
            try:
                await asyncio.wait_for(self.deps.adapter.speak(task), timeout=timeout_s)
                self.stats.processed += 1
                self.stats.add_latency(reaction_start_ms)
            except Exception as exc:
                self.stats.failed += 1
                LOGGER.warning(
                    "Failed to speak message id=%s error_type=%s error=%r",
                    event.message_id,
                    type(exc).__name__,
                    exc,
                )

    async def _emit_speech(self, convo: ConversationOutputEvent, event: ConversationInputEvent) -> None:
        """Build a SpeechTask from a ConversationOutputEvent and call _speak."""
        if not convo.reply_text.strip():
            LOGGER.info(
                "Skipping speech: empty reply_text id=%s emotion=%s",
                event.message_id,
                convo.emotion,
            )
            return
        motion_plan = self.tool_executor.build_motion_plan(convo)
        gesture = motion_plan.fallback_gesture
        task = SpeechTask(
            message_id=event.message_id,
            text_ja=convo.reply_text,
            voice_style="default",
            gesture_preset=gesture,
            emotion=convo.emotion,
            deadline_ms=self._speech_deadline_ms(convo.reply_text),
            motion_plan=motion_plan,
        )
        await self._speak(task, event)

    def _make_filler_task(self, event: ConversationInputEvent) -> SpeechTask:
        """Build a short filler SpeechTask to speak while research runs in background."""
        from .types import MotionPlan
        text = _random.choice(FILLER_REPLIES)
        return SpeechTask(
            message_id=event.message_id,
            text_ja=text,
            voice_style="default",
            gesture_preset="nod",
            emotion="surprise",
            deadline_ms=self._speech_deadline_ms(text),
            motion_plan=MotionPlan(fallback_gesture="nod"),
        )

    def _spawn_deferred(self, gen_task: asyncio.Task, event: ConversationInputEvent) -> None:
        """Wrap a still-running generation task and deliver its result via _speak when done."""
        async def _complete() -> None:
            try:
                convo = await gen_task
            except Exception as exc:
                LOGGER.warning(
                    "Deferred generation failed id=%s err=%s; skipping speech",
                    event.message_id,
                    exc,
                )
                return
            # Prefix reply with the original asker's display name for clarity
            if convo.reply_text.strip():
                display = (event.display_name or event.user_name or "").strip()
                if display and not convo.reply_text.startswith(display):
                    convo = ConversationOutputEvent(
                        reply_text=f"{display}、{convo.reply_text}",
                        emotion=convo.emotion,
                        tool_calls=convo.tool_calls,
                    )
            await self._emit_speech(convo, event)

        task = asyncio.create_task(_complete())
        self._deferred_tasks.add(task)
        task.add_done_callback(self._deferred_tasks.discard)
        LOGGER.info(
            "Deferred research task spawned id=%s total_deferred=%d",
            event.message_id,
            len(self._deferred_tasks),
        )

    async def cancel_deferred_tasks(self) -> None:
        """Cancel all in-flight deferred research tasks. Called at shutdown."""
        tasks = list(self._deferred_tasks)
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._deferred_tasks.clear()

    async def _process_event(self, event: ConversationInputEvent) -> None:
        # Cap concurrent deferred tasks to avoid runaway resource usage
        if len(self._deferred_tasks) >= _MAX_DEFERRED_CONCURRENT:
            LOGGER.warning(
                "Deferred task cap reached (%d); using fallback for id=%s",
                _MAX_DEFERRED_CONCURRENT,
                event.message_id,
            )
            fallback = ConversationOutputEvent(
                reply_text=FALLBACK_REPLY,
                emotion="empathy",
                tool_calls=[],
            )
            await self._emit_speech(fallback, event)
            return

        gen_task: asyncio.Task = asyncio.create_task(self.conversation.generate(event))
        filler_delay = self.deps.cfg.runtime.filler_delay_sec

        done, _ = await asyncio.wait({gen_task}, timeout=filler_delay)

        if gen_task in done:
            # Fast path: response arrived before filler threshold — no filler needed
            try:
                convo = gen_task.result()
            except Exception as exc:
                LOGGER.warning(
                    "Conversation generation failed id=%s error=%s; using fallback",
                    event.message_id,
                    exc,
                )
                convo = ConversationOutputEvent(
                    reply_text=FALLBACK_REPLY,
                    emotion="empathy",
                    tool_calls=[],
                )
            await self._emit_speech(convo, event)
        else:
            # Slow path: emit filler immediately, defer the real answer
            LOGGER.info(
                "Filler triggered: generation still in-flight after %.1fs id=%s",
                filler_delay,
                event.message_id,
            )
            filler = self._make_filler_task(event)
            await self._speak(filler, event)
            self._spawn_deferred(gen_task, event)
            # Return immediately — main loop is free to handle next message

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
        # Keep playback alive for long-form Japanese replies.
        # The previous estimate was too optimistic and could cancel
        # playback before the WAV finished on long responses.
        estimated = int((max(len(text), 1) / 3.0) * 1000) + 12000
        return min(max(base, estimated), 120000)
