from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from .config import load_config_from_env
from .dotenv_loader import load_env_file
from .orchestrator import AppDeps, AppOrchestrator
from .reachy_adapter import MockReachyAdapter, ReachyMiniAdapter
from .twitch_irc import TwitchIrcClient

LOGGER = logging.getLogger(__name__)


async def _forward_irc_to_queue(client: TwitchIrcClient, q: asyncio.Queue[str]) -> None:
    async for raw in client.messages():
        await q.put(raw)


async def _replay_file_to_queue(path: Path, q: asyncio.Queue[str]) -> None:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if raw:
                await q.put(raw)


def _log_stats(app: AppOrchestrator) -> None:
    LOGGER.info(
        "stats processed=%s filtered=%s failed=%s p95_reaction_ms=%.1f",
        app.stats.processed,
        app.stats.filtered,
        app.stats.failed,
        app.stats.p95_latency_ms(),
    )


async def run_app(use_mock: bool, reachy_host: str, replay_file: str | None) -> None:
    cfg = load_config_from_env(allow_dummy_twitch=bool(replay_file))
    q: asyncio.Queue[str] = asyncio.Queue()

    if use_mock:
        adapter = MockReachyAdapter()
    else:
        sdk = ReachyMiniAdapter(
            host=reachy_host,
            connection_mode=cfg.reachy.connection_mode,
            tts_engine=cfg.reachy.tts_engine,
            tts_lang=cfg.reachy.tts_lang,
            openai_api_key=cfg.conversation.openai_api_key,
            tts_openai_model=cfg.reachy.tts_openai_model,
            tts_openai_voice=cfg.reachy.tts_openai_voice,
            tts_openai_format=cfg.reachy.tts_openai_format,
            tts_openai_speed=cfg.reachy.tts_openai_speed,
            gesture_enabled=cfg.reachy.gesture_enabled,
            audio_volume=cfg.reachy.audio_volume,
            healthcheck_url=cfg.reachy.healthcheck_url,
            connect_timeout_sec=cfg.reachy.connect_timeout_sec,
            connect_retries=cfg.reachy.connect_retries,
            connect_retry_interval_sec=cfg.reachy.connect_retry_interval_sec,
            idle_use_doa=cfg.reachy.idle_use_doa,
        )
        await sdk.connect()
        adapter = sdk
        LOGGER.info(
            "Reachy adapter configured: mode=%s host=%s tts_engine=%s tts_lang=%s gesture_enabled=%s execution_host=%s input_mode=%s model=%s context_window=%s connect_timeout_sec=%.1f connect_retries=%s idle_use_doa=%s",
            cfg.reachy.connection_mode,
            reachy_host,
            cfg.reachy.tts_engine,
            cfg.reachy.tts_lang,
            cfg.reachy.gesture_enabled,
            cfg.reachy.execution_host,
            cfg.conversation.input_mode,
            cfg.conversation.openai_realtime_model,
            cfg.conversation.context_window_size,
            cfg.reachy.connect_timeout_sec,
            cfg.reachy.connect_retries,
            cfg.reachy.idle_use_doa,
        )

    deps = AppDeps(cfg=cfg, adapter=adapter, irc_messages=q)
    app = AppOrchestrator(deps)

    if replay_file:
        await _replay_file_to_queue(Path(replay_file), q)
        while not q.empty():
            raw = await q.get()
            await app.consume_once(raw)
        _log_stats(app)
        return

    irc = TwitchIrcClient(
        nick=cfg.twitch.nick,
        oauth_token=cfg.twitch.oauth_token,
        channel=cfg.twitch.channel,
    )
    producer = asyncio.create_task(_forward_irc_to_queue(irc, q))
    consumer = asyncio.create_task(app.run())

    done, pending = await asyncio.wait(
        {producer, consumer}, return_when=asyncio.FIRST_EXCEPTION
    )
    for p in pending:
        p.cancel()
    for d in done:
        if err := d.exception():
            raise err
    _log_stats(app)


def main() -> None:
    parser = argparse.ArgumentParser(description="Reachy Mini Twitch voice app")
    parser.add_argument("--mock", action="store_true", help="Use mock Reachy adapter")
    parser.add_argument(
        "--env-file",
        default=".env.local",
        help="Local env file to load before config parsing (default: .env.local)",
    )
    parser.add_argument(
        "--no-env-file",
        action="store_true",
        help="Disable local env file loading",
    )
    parser.add_argument(
        "--reachy-host",
        default=os.getenv("REACHY_HOST", "reachy-mini.local"),
        help="Reachy host for reachy-sdk",
    )
    parser.add_argument(
        "--replay-file",
        default=None,
        help="Path to text file containing raw IRC lines for local replay",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.no_env_file:
        loaded = load_env_file(args.env_file)
        if loaded:
            LOGGER.info("Loaded env file: %s", args.env_file)

    asyncio.run(
        run_app(
            use_mock=args.mock,
            reachy_host=args.reachy_host,
            replay_file=args.replay_file,
        )
    )


if __name__ == "__main__":
    main()
