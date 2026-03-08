from __future__ import annotations

import asyncio
import logging
import ssl
from collections.abc import AsyncIterator

LOGGER = logging.getLogger(__name__)


class TwitchIrcClient:
    def __init__(self, nick: str, oauth_token: str, channel: str) -> None:
        self.nick = nick
        self.oauth_token = oauth_token
        self.channel = channel

    async def messages(self) -> AsyncIterator[str]:
        backoff = 1
        while True:
            try:
                async for msg in self._run_once():
                    yield msg
                backoff = 1
            except Exception as exc:
                LOGGER.exception("IRC connection error: %s", exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _run_once(self) -> AsyncIterator[str]:
        ssl_ctx = ssl.create_default_context()
        reader, writer = await asyncio.open_connection(
            host="irc.chat.twitch.tv", port=6697, ssl=ssl_ctx
        )

        token = self.oauth_token.strip()
        if token.lower().startswith("oauth:"):
            token = token[6:]
        writer.write(f"PASS oauth:{token}\r\n".encode())
        writer.write(f"NICK {self.nick}\r\n".encode())
        writer.write(b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n")
        await writer.drain()

        try:
            authed = False
            while not authed:
                line = await reader.readline()
                if not line:
                    raise ConnectionError("IRC socket closed before auth")
                raw = line.decode(errors="ignore").strip()
                if raw.startswith("PING"):
                    writer.write(raw.replace("PING", "PONG", 1).encode() + b"\r\n")
                    await writer.drain()
                    continue
                if "Login authentication failed" in raw:
                    raise PermissionError(
                        "Twitch IRC auth failed (Login authentication failed). "
                        "Check TWITCH_OAUTH_TOKEN (user access token with chat:read) and TWITCH_NICK."
                    )
                if "Improperly formatted auth" in raw:
                    raise PermissionError(
                        "Twitch IRC auth failed (Improperly formatted auth). "
                        "Use TWITCH_OAUTH_TOKEN=oauth:ACCESS_TOKEN or ACCESS_TOKEN."
                    )
                if f" 001 {self.nick} " in raw:
                    authed = True
                LOGGER.debug("IRC pre-auth: %s", raw)

            writer.write(f"JOIN #{self.channel}\r\n".encode())
            await writer.drain()

            while True:
                line = await reader.readline()
                if not line:
                    raise ConnectionError("IRC socket closed")
                raw = line.decode(errors="ignore").strip()
                if "Login authentication failed" in raw:
                    raise PermissionError(
                        "Twitch IRC auth failed during session (token may be expired)."
                    )
                if raw.startswith("PING"):
                    writer.write(raw.replace("PING", "PONG", 1).encode() + b"\r\n")
                    await writer.drain()
                    continue
                if " RECONNECT " in raw:
                    raise ConnectionError("Twitch requested reconnect")
                yield raw
        finally:
            writer.close()
            await writer.wait_closed()
