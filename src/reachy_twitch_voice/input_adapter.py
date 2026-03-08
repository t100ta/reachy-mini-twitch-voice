from __future__ import annotations

from abc import ABC, abstractmethod

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
        )
