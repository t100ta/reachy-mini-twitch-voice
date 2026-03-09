from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass(slots=True)
class TwitchConfig:
    channel: str
    oauth_token: str
    nick: str


@dataclass(slots=True)
class SafetyConfig:
    ng_words: list[str] = field(default_factory=lambda: ["死ね", "kill yourself"])
    max_chars: int = 140
    spam_window_sec: int = 5


@dataclass(slots=True)
class RuntimeConfig:
    message_timeout_ms: int = 15000
    reconnect_max_sec: int = 30
    idle_motion_enabled: bool = True
    idle_interval_sec: float = 0.3
    max_queue_size: int = 100
    max_queue_wait_ms: int = 15000
    drop_policy: str = "drop_oldest"


@dataclass(slots=True)
class WebConsoleConfig:
    enabled: bool = True
    host: str = "0.0.0.0"
    port: int = 7860


@dataclass(slots=True)
class ReachyConfig:
    tts_engine: str = "espeak-ng"
    tts_lang: str = "ja"
    tts_openai_model: str = "gpt-4o-mini-tts"
    tts_openai_voice: str = "alloy"
    tts_openai_format: str = "wav"
    tts_openai_speed: float = 1.15
    gesture_enabled: bool = True
    speech_motion_enabled: bool = True
    execution_host: str = "on_reachy"
    connection_mode: str = "auto"
    audio_volume: int | None = None
    healthcheck_url: str = "http://localhost:8000/api/state/full"
    connect_timeout_sec: float = 45.0
    connect_retries: int = 3
    connect_retry_interval_sec: float = 3.0
    idle_use_doa: bool = False


@dataclass(slots=True)
class ConversationConfig:
    engine: str = "realtime"
    input_mode: str = "twitch"
    context_window_size: int = 30
    openai_api_key: str = ""
    openai_realtime_model: str = "gpt-4o-mini"
    openai_timeout_sec: float = 10.0
    persona_name: str = "NUVA"
    persona_name_kana: str = "ヌーバ"
    operator_name: str = "にかなとむ(tom_t100ta)"
    persona_style: str = "親しみを保ちつつ、常に適度に礼儀正しく"
    system_prompt_file: str = ""
    system_prompt_text: str = ""
    operator_usernames: list[str] = field(
        default_factory=lambda: ["tom_t100ta", "にかなとむ"]
    )
    profile_storage_dir: str = "~/.config/reachy-mini-twitch-voice/profiles"
    active_profile: str = ""


@dataclass(slots=True)
class PipelineConfig:
    twitch: TwitchConfig
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    reachy: ReachyConfig = field(default_factory=ReachyConfig)
    conversation: ConversationConfig = field(default_factory=ConversationConfig)
    web_console: WebConsoleConfig = field(default_factory=WebConsoleConfig)


def _as_bool(value: str, default: bool = True) -> bool:
    v = value.strip().lower()
    if not v:
        return default
    return v in {"1", "true", "yes", "on"}


def load_config_from_env(allow_dummy_twitch: bool = False) -> PipelineConfig:
    channel = os.getenv("TWITCH_CHANNEL", "").strip().lower()
    token = os.getenv("TWITCH_OAUTH_TOKEN", "").strip()
    nick = os.getenv("TWITCH_NICK", "").strip().lower()

    if allow_dummy_twitch and (not channel or not token or not nick):
        channel = channel or "dummy_channel"
        token = token or "dummy_token"
        nick = nick or "dummy_nick"

    if not channel or not token or not nick:
        raise ValueError(
            "Missing envs: TWITCH_CHANNEL, TWITCH_OAUTH_TOKEN, TWITCH_NICK are required"
        )

    ng_words = [w.strip() for w in os.getenv("NG_WORDS", "").split(",") if w.strip()]
    max_chars = int(os.getenv("MAX_CHARS", "140"))
    spam_window_sec = int(os.getenv("SPAM_WINDOW_SEC", "5"))
    message_timeout_ms = int(os.getenv("MESSAGE_TIMEOUT_MS", "15000"))
    reconnect_max_sec = int(os.getenv("RECONNECT_MAX_SEC", "30"))
    idle_motion_enabled = _as_bool(os.getenv("IDLE_MOTION_ENABLED", "true"), True)
    idle_interval_sec = float(os.getenv("IDLE_INTERVAL_SEC", "0.3"))
    max_queue_size = max(1, int(os.getenv("MAX_QUEUE_SIZE", "100")))
    max_queue_wait_ms = max(0, int(os.getenv("MAX_QUEUE_WAIT_MS", "15000")))
    drop_policy = os.getenv("QUEUE_DROP_POLICY", "drop_oldest").strip().lower() or "drop_oldest"
    if drop_policy not in {"drop_oldest"}:
        drop_policy = "drop_oldest"
    tts_engine = os.getenv("REACHY_TTS_ENGINE", "espeak-ng").strip() or "espeak-ng"
    tts_lang = os.getenv("REACHY_TTS_LANG", "ja").strip() or "ja"
    tts_openai_model = (
        os.getenv("REACHY_TTS_OPENAI_MODEL", "gpt-4o-mini-tts").strip()
        or "gpt-4o-mini-tts"
    )
    tts_openai_voice = (
        os.getenv("REACHY_TTS_OPENAI_VOICE", "alloy").strip() or "alloy"
    )
    tts_openai_format = (
        os.getenv("REACHY_TTS_OPENAI_FORMAT", "wav").strip() or "wav"
    )
    tts_openai_speed = float(os.getenv("REACHY_TTS_OPENAI_SPEED", "1.15"))
    gesture_enabled = _as_bool(os.getenv("REACHY_GESTURE_ENABLED", "true"), True)
    speech_motion_enabled = _as_bool(
        os.getenv("REACHY_SPEECH_MOTION_ENABLED", "true"),
        True,
    )
    execution_host = (
        os.getenv("REACHY_EXECUTION_HOST", "on_reachy").strip() or "on_reachy"
    )
    connection_mode = (
        os.getenv("REACHY_CONNECTION_MODE", "auto").strip().lower() or "auto"
    )
    if connection_mode not in {"auto", "localhost_only", "network"}:
        connection_mode = "auto"
    volume_raw = os.getenv("REACHY_AUDIO_VOLUME", "").strip()
    audio_volume = int(volume_raw) if volume_raw else None
    if audio_volume is not None:
        audio_volume = min(max(audio_volume, 0), 100)
    healthcheck_url = (
        os.getenv("REACHY_HEALTHCHECK_URL", "http://localhost:8000/api/state/full").strip()
        or "http://localhost:8000/api/state/full"
    )
    connect_timeout_sec = float(os.getenv("REACHY_CONNECT_TIMEOUT_SEC", "45.0"))
    connect_retries = int(os.getenv("REACHY_CONNECT_RETRIES", "3"))
    connect_retry_interval_sec = float(
        os.getenv("REACHY_CONNECT_RETRY_INTERVAL_SEC", "3.0")
    )
    idle_use_doa = _as_bool(os.getenv("IDLE_USE_DOA", "false"), False)
    input_mode = os.getenv("CONVERSATION_INPUT_MODE", "twitch").strip() or "twitch"
    conversation_engine = os.getenv("CONVERSATION_ENGINE", "realtime").strip().lower() or "realtime"
    if conversation_engine not in {"http", "realtime"}:
        conversation_engine = "realtime"
    context_window_size = int(os.getenv("TWITCH_MESSAGE_CONTEXT_WINDOW", "30"))
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    openai_realtime_model = (
        os.getenv("OPENAI_REALTIME_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
    )
    openai_timeout_sec = float(os.getenv("OPENAI_TIMEOUT_SEC", "10.0"))
    persona_name = os.getenv("PERSONA_NAME", "NUVA").strip() or "NUVA"
    persona_name_kana = os.getenv("PERSONA_NAME_KANA", "ヌーバ").strip() or "ヌーバ"
    operator_name = (
        os.getenv("OPERATOR_NAME", "にかなとむ(tom_t100ta)").strip()
        or "にかなとむ(tom_t100ta)"
    )
    persona_style = (
        os.getenv("PERSONA_STYLE", "親しみを保ちつつ、常に適度に礼儀正しく").strip()
        or "親しみを保ちつつ、常に適度に礼儀正しく"
    )
    system_prompt_file = os.getenv("SYSTEM_PROMPT_FILE", "").strip()
    profile_storage_dir = (
        os.getenv(
            "PROFILE_STORAGE_DIR",
            "~/.config/reachy-mini-twitch-voice/profiles",
        ).strip()
        or "~/.config/reachy-mini-twitch-voice/profiles"
    )
    active_profile = os.getenv("ACTIVE_PROFILE", "").strip()
    operator_usernames = [
        u.strip().lower()
        for u in os.getenv("OPERATOR_USERNAMES", "tom_t100ta,にかなとむ").split(",")
        if u.strip()
    ]
    web_console_enabled = _as_bool(os.getenv("WEB_CONSOLE_ENABLED", "true"), True)
    web_console_host = os.getenv("WEB_CONSOLE_HOST", "0.0.0.0").strip() or "0.0.0.0"
    web_console_port = int(os.getenv("WEB_CONSOLE_PORT", "7860"))

    return PipelineConfig(
        twitch=TwitchConfig(channel=channel, oauth_token=token, nick=nick),
        safety=SafetyConfig(
            ng_words=ng_words or ["死ね", "kill yourself"],
            max_chars=max_chars,
            spam_window_sec=spam_window_sec,
        ),
        runtime=RuntimeConfig(
            message_timeout_ms=message_timeout_ms,
            reconnect_max_sec=reconnect_max_sec,
            idle_motion_enabled=idle_motion_enabled,
            idle_interval_sec=idle_interval_sec,
            max_queue_size=max_queue_size,
            max_queue_wait_ms=max_queue_wait_ms,
            drop_policy=drop_policy,
        ),
        reachy=ReachyConfig(
            tts_engine=tts_engine,
            tts_lang=tts_lang,
            tts_openai_model=tts_openai_model,
            tts_openai_voice=tts_openai_voice,
            tts_openai_format=tts_openai_format,
            tts_openai_speed=tts_openai_speed,
            gesture_enabled=gesture_enabled,
            speech_motion_enabled=speech_motion_enabled,
            execution_host=execution_host,
            connection_mode=connection_mode,
            audio_volume=audio_volume,
            healthcheck_url=healthcheck_url,
            connect_timeout_sec=connect_timeout_sec,
            connect_retries=connect_retries,
            connect_retry_interval_sec=connect_retry_interval_sec,
            idle_use_doa=idle_use_doa,
        ),
        conversation=ConversationConfig(
            engine=conversation_engine,
            input_mode=input_mode,
            context_window_size=context_window_size,
            openai_api_key=openai_api_key,
            openai_realtime_model=openai_realtime_model,
            openai_timeout_sec=openai_timeout_sec,
            persona_name=persona_name,
            persona_name_kana=persona_name_kana,
            operator_name=operator_name,
            persona_style=persona_style,
            system_prompt_file=system_prompt_file,
            system_prompt_text="",
            operator_usernames=operator_usernames or ["tom_t100ta", "にかなとむ"],
            profile_storage_dir=profile_storage_dir,
            active_profile=active_profile,
        ),
        web_console=WebConsoleConfig(
            enabled=web_console_enabled,
            host=web_console_host,
            port=web_console_port,
        ),
    )
