from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


SafetyReason = Literal["ok", "ng_word", "spam", "too_long", "unsafe_intent"]
GesturePreset = Literal["nod", "look", "sway", "tilt", "idle"]
VoiceStyle = Literal["default"]
EmotionLabel = Literal["joy", "surprise", "empathy"]
MotionStyle = Literal["official", "legacy"]
IdleStyle = Literal["calm", "attentive"]
BaselineMode = Literal["neutral", "attentive_idle", "breathing_idle"]
ConversationInputSource = Literal["twitch", "manual", "twitch_event"]


@dataclass(slots=True)
class MotionPlan:
    fallback_gesture: GesturePreset = "idle"
    speech_opening_emotion: str | None = None
    post_speech_settle: str | None = "settle"
    idle_profile: IdleStyle = "attentive"
    baseline_mode: BaselineMode = "attentive_idle"
    speech_motion_scale: float = 0.65
    allow_antenna_follow_during_speech: bool = True
    dance_move: str | None = None
    idle_phrase_candidates: list[GesturePreset] = field(default_factory=list)


@dataclass(slots=True)
class ChannelEvent:
    id: str
    event_type: str          # "raid", "sub", "resub", "subgift", "submysterygift"
    channel: str
    user_name: str
    display_name: str | None
    system_msg: str | None   # Twitch の system-msg タグ（\s → スペース変換済み）
    viewer_count: int | None # raid の msg-param-viewerCount
    received_at: float


@dataclass(slots=True)
class TwitchMessage:
    id: str
    channel: str
    user_id: str
    user_name: str
    display_name: str | None
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
    motion_plan: MotionPlan = field(default_factory=MotionPlan)


@dataclass(slots=True)
class ConversationInputEvent:
    message_id: str
    user_name: str
    display_name: str | None
    channel: str
    text: str
    received_at: float
    is_operator: bool = False
    source: ConversationInputSource = "twitch"
    queue_age_ms: float = 0.0


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
    dropped: int = 0
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
