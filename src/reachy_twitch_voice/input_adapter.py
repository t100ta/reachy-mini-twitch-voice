from __future__ import annotations

from abc import ABC, abstractmethod
import time
import uuid

from .types import ConversationInputEvent, TwitchMessage


class InputAdapter(ABC):
    @abstractmethod
    def to_conversation_input(self, msg: TwitchMessage) -> ConversationInputEvent:
        raise NotImplementedError


class TwitchChatInputAdapter(InputAdapter):
    def to_conversation_input(self, msg: TwitchMessage) -> ConversationInputEvent:
        return ConversationInputEvent(
            message_id=msg.id,
            user_name=msg.user_name,
            channel=msg.channel,
            text=msg.text,
            received_at=msg.received_at,
            is_operator=False,
            source="twitch",
            queue_age_ms=0.0,
        )


class RealtimeInputAdapter(TwitchChatInputAdapter):
    """Semantic alias to make the realtime input path explicit."""


class ManualTextInputAdapter(InputAdapter):
    def to_conversation_input(self, msg: TwitchMessage) -> ConversationInputEvent:
        return ConversationInputEvent(
            message_id=msg.id,
            user_name=msg.user_name,
            channel=msg.channel,
            text=msg.text,
            received_at=msg.received_at,
            is_operator=False,
            source="manual",
            queue_age_ms=0.0,
        )

    def build_event(self, text: str, user_name: str = "manual_tester") -> ConversationInputEvent:
        now = time.time()
        return ConversationInputEvent(
            message_id=str(uuid.uuid4()),
            user_name=user_name.strip() or "manual_tester",
            channel="manual",
            text=text,
            received_at=now,
            is_operator=False,
            source="manual",
            queue_age_ms=0.0,
        )
