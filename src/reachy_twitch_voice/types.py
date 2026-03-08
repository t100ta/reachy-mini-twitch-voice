from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


SafetyReason = Literal["ok", "ng_word", "spam", "too_long", "unsafe_intent"]
GesturePreset = Literal["nod", "look", "sway", "tilt", "idle"]
VoiceStyle = Literal["default"]
EmotionLabel = Literal["joy", "surprise", "empathy"]


@dataclass(slots=True)
class TwitchMessage:
    id: str
    channel: str
    user_id: str
    user_name: str
    text: str
    received_at: float


@dataclass(slots=True)
class SafetyDecision:
    allow: bool
    reason: SafetyReason
    sanitized_text: str | None = None


@dataclass(slots=True)
class SpeechTask:
    message_id: str
    text_ja: str
    voice_style: VoiceStyle
    gesture_preset: GesturePreset
    deadline_ms: int
    emotion: EmotionLabel = "empathy"


@dataclass(slots=True)
class ConversationInputEvent:
    message_id: str
    user_name: str
    channel: str
    text: str
    received_at: float
    is_operator: bool = False


@dataclass(slots=True)
class ConversationTurn:
    user_name: str
    text: str
    assistant_reply: str
    emotion: EmotionLabel


@dataclass(slots=True)
class ConversationOutputEvent:
    reply_text: str
    emotion: EmotionLabel
    tool_calls: list[str]


@dataclass(slots=True)
class RuntimeStats:
    processed: int = 0
    filtered: int = 0
    failed: int = 0
    latency_ms_samples: list[float] | None = None

    def __post_init__(self) -> None:
        if self.latency_ms_samples is None:
            self.latency_ms_samples = []

    def add_latency(self, value_ms: float) -> None:
        if self.latency_ms_samples is not None:
            self.latency_ms_samples.append(value_ms)

    def p95_latency_ms(self) -> float:
        if not self.latency_ms_samples:
            return 0.0
        samples = sorted(self.latency_ms_samples)
        idx = int(0.95 * (len(samples) - 1))
        return samples[idx]
