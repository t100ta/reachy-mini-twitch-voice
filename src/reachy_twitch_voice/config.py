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
    idle_interval_sec: float = 3.0
    max_queue_size: int = 100
    max_queue_wait_ms: int = 60000
    drop_policy: str = "drop_oldest"
    channel_events_enabled: bool = True
    channel_event_types: list[str] = field(
        default_factory=lambda: ["raid", "sub", "resub", "subgift"]
    )
    filler_delay_sec: float = 3.5  # trigger filler speech after this many seconds


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
    motion_style: str = "official"
    idle_style: str = "attentive"
    idle_first_delay_sec: float = 3.0
    idle_glance_interval_sec: float = 10.0
    speech_motion_scale: float = 0.65
    emotion_motion_enabled: bool = True
    audio_output_target: str = "robot"


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
    enable_tools: bool = True
    enable_web_search: bool = False


@dataclass(slots=True)
class HermesConfig:
    base_url: str = "http://127.0.0.1:8642/v1"
    api_key: str = ""
    model: str = "hermes-agent"
    timeout_sec: float = 30.0
    stream: bool = False
    use_responses_api: bool = False
    conversation_prefix: str = "reachy-twitch"
    system_prompt_file: str = ""
    retry_count: int = 1


@dataclass(slots=True)
class ViewerMemoryConfig:
    enabled: bool = True
    db_path: str = "~/.config/reachy-mini-twitch-voice/viewer_memory.sqlite3"
    max_notes: int = 8
    save_source_message: bool = False


@dataclass(slots=True)
class StreamJournalConfig:
    enabled: bool = True
    db_path: str = "~/.config/reachy-mini-twitch-voice/viewer_memory.sqlite3"
    inject_recent_count: int = 2
    summary_timeout_sec: float = 20.0
    min_turns_for_summary: int = 3


@dataclass(slots=True)
class PipelineConfig:
    twitch: TwitchConfig
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    reachy: ReachyConfig = field(default_factory=ReachyConfig)
    conversation: ConversationConfig = field(default_factory=ConversationConfig)
    web_console: WebConsoleConfig = field(default_factory=WebConsoleConfig)
    hermes: HermesConfig = field(default_factory=HermesConfig)
    viewer_memory: ViewerMemoryConfig = field(default_factory=ViewerMemoryConfig)
    stream_journal: StreamJournalConfig = field(default_factory=StreamJournalConfig)


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
    idle_interval_sec = float(os.getenv("IDLE_INTERVAL_SEC", "3.0"))
    max_queue_size = max(1, int(os.getenv("MAX_QUEUE_SIZE", "100")))
    max_queue_wait_ms = max(0, int(os.getenv("MAX_QUEUE_WAIT_MS", "60000")))
    drop_policy = os.getenv("QUEUE_DROP_POLICY", "drop_oldest").strip().lower() or "drop_oldest"
    if drop_policy not in {"drop_oldest"}:
        drop_policy = "drop_oldest"
    filler_delay_sec = float(os.getenv("FILLER_DELAY_SEC", "3.5"))
    channel_events_enabled = _as_bool(os.getenv("CHANNEL_EVENTS_ENABLED", "true"), True)
    channel_event_types_raw = os.getenv("CHANNEL_EVENT_TYPES", "").strip()
    if channel_event_types_raw:
        channel_event_types = [t.strip() for t in channel_event_types_raw.split(",") if t.strip()]
    else:
        channel_event_types = ["raid", "sub", "resub", "subgift"]
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
    motion_style = os.getenv("REACHY_MOTION_STYLE", "official").strip().lower() or "official"
    if motion_style not in {"official", "legacy"}:
        motion_style = "official"
    idle_style = os.getenv("REACHY_IDLE_STYLE", "attentive").strip().lower() or "attentive"
    if idle_style not in {"calm", "attentive"}:
        idle_style = "attentive"
    idle_first_delay_sec = float(os.getenv("REACHY_IDLE_FIRST_DELAY_SEC", "3.0"))
    idle_glance_interval_sec = float(os.getenv("REACHY_IDLE_GLANCE_INTERVAL_SEC", "10.0"))
    speech_motion_scale = float(os.getenv("REACHY_SPEECH_MOTION_SCALE", "0.65"))
    emotion_motion_enabled = _as_bool(
        os.getenv("REACHY_EMOTION_MOTION_ENABLED", "true"),
        True,
    )
    audio_output_target = os.getenv("REACHY_AUDIO_OUTPUT_TARGET", "robot").strip().lower() or "robot"
    if audio_output_target not in {"robot", "web"}:
        audio_output_target = "robot"
    input_mode = os.getenv("CONVERSATION_INPUT_MODE", "twitch").strip().lower() or "twitch"
    if input_mode not in {"twitch", "manual_text"}:
        input_mode = "twitch"
    conversation_engine = os.getenv("CONVERSATION_ENGINE", "realtime").strip().lower() or "realtime"
    if conversation_engine not in {"http", "realtime", "hermes"}:
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
    enable_tools = _as_bool(os.getenv("ENABLE_TOOLS", "true"), True)
    enable_web_search = _as_bool(os.getenv("ENABLE_WEB_SEARCH", "false"), False)
    operator_usernames = [
        u.strip().lower()
        for u in os.getenv("OPERATOR_USERNAMES", "tom_t100ta,にかなとむ").split(",")
        if u.strip()
    ]
    web_console_enabled = _as_bool(os.getenv("WEB_CONSOLE_ENABLED", "true"), True)
    web_console_host = os.getenv("WEB_CONSOLE_HOST", "0.0.0.0").strip() or "0.0.0.0"
    web_console_port = int(os.getenv("WEB_CONSOLE_PORT", "7860"))

    hermes_base_url = (
        os.getenv("HERMES_BASE_URL", "http://127.0.0.1:8642/v1").strip()
        or "http://127.0.0.1:8642/v1"
    )
    hermes_api_key = os.getenv("HERMES_API_KEY", "").strip()
    hermes_model = os.getenv("HERMES_MODEL", "hermes-agent").strip() or "hermes-agent"
    hermes_timeout_sec = float(os.getenv("HERMES_TIMEOUT_SEC", "30.0"))
    hermes_stream = _as_bool(os.getenv("HERMES_STREAM", "false"), False)
    hermes_use_responses_api = _as_bool(
        os.getenv("HERMES_USE_RESPONSES_API", "false"), False
    )
    hermes_conversation_prefix = (
        os.getenv("HERMES_CONVERSATION_PREFIX", "reachy-twitch").strip()
        or "reachy-twitch"
    )
    hermes_system_prompt_file = os.getenv("HERMES_SYSTEM_PROMPT_FILE", "").strip()
    hermes_retry_count = max(0, int(os.getenv("HERMES_RETRY_COUNT", "1")))

    viewer_memory_enabled = _as_bool(os.getenv("VIEWER_MEMORY_ENABLED", "true"), True)
    viewer_memory_db_path = (
        os.getenv(
            "VIEWER_MEMORY_DB_PATH",
            "~/.config/reachy-mini-twitch-voice/viewer_memory.sqlite3",
        ).strip()
        or "~/.config/reachy-mini-twitch-voice/viewer_memory.sqlite3"
    )
    viewer_memory_max_notes = max(0, int(os.getenv("VIEWER_MEMORY_MAX_NOTES", "8")))
    viewer_memory_save_source = _as_bool(
        os.getenv("VIEWER_MEMORY_SAVE_SOURCE_MESSAGE", "false"), False
    )

    stream_journal_enabled = _as_bool(os.getenv("STREAM_JOURNAL_ENABLED", "true"), True)
    stream_journal_db_path = (
        os.getenv(
            "STREAM_JOURNAL_DB_PATH",
            "~/.config/reachy-mini-twitch-voice/viewer_memory.sqlite3",
        ).strip()
        or "~/.config/reachy-mini-twitch-voice/viewer_memory.sqlite3"
    )
    stream_journal_inject_recent_count = max(
        0, int(os.getenv("STREAM_JOURNAL_INJECT_RECENT_COUNT", "2"))
    )
    stream_journal_summary_timeout_sec = float(
        os.getenv("STREAM_JOURNAL_SUMMARY_TIMEOUT_SEC", "20.0")
    )
    stream_journal_min_turns = max(
        0, int(os.getenv("STREAM_JOURNAL_MIN_TURNS_FOR_SUMMARY", "3"))
    )

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
            channel_events_enabled=channel_events_enabled,
            channel_event_types=channel_event_types,
            filler_delay_sec=filler_delay_sec,
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
            motion_style=motion_style,
            idle_style=idle_style,
            idle_first_delay_sec=idle_first_delay_sec,
            idle_glance_interval_sec=idle_glance_interval_sec,
            speech_motion_scale=speech_motion_scale,
            emotion_motion_enabled=emotion_motion_enabled,
            audio_output_target=audio_output_target,
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
            enable_tools=enable_tools,
            enable_web_search=enable_web_search,
        ),
        web_console=WebConsoleConfig(
            enabled=web_console_enabled,
            host=web_console_host,
            port=web_console_port,
        ),
        hermes=HermesConfig(
            base_url=hermes_base_url,
            api_key=hermes_api_key,
            model=hermes_model,
            timeout_sec=hermes_timeout_sec,
            stream=hermes_stream,
            use_responses_api=hermes_use_responses_api,
            conversation_prefix=hermes_conversation_prefix,
            system_prompt_file=hermes_system_prompt_file,
            retry_count=hermes_retry_count,
        ),
        viewer_memory=ViewerMemoryConfig(
            enabled=viewer_memory_enabled,
            db_path=viewer_memory_db_path,
            max_notes=viewer_memory_max_notes,
            save_source_message=viewer_memory_save_source,
        ),
        stream_journal=StreamJournalConfig(
            enabled=stream_journal_enabled,
            db_path=stream_journal_db_path,
            inject_recent_count=stream_journal_inject_recent_count,
            summary_timeout_sec=stream_journal_summary_timeout_sec,
            min_turns_for_summary=stream_journal_min_turns,
        ),
    )
