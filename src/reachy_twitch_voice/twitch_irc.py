from __future__ import annotations

import asyncio
import logging
import ssl
from collections.abc import AsyncIterator, Callable

LOGGER = logging.getLogger(__name__)


class TwitchIrcClient:
    def __init__(
        self,
        nick: str,
        oauth_token: str,
        channel: str,
        credentials_provider: Callable[[], tuple[str, str, str]] | None = None,
        reconnect_event: asyncio.Event | None = None,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        self.nick = nick
        self.oauth_token = oauth_token
        self.channel = channel
        self._credentials_provider = credentials_provider
        self._reconnect_event = reconnect_event
        self._status_callback = status_callback
        self._active_writer: asyncio.StreamWriter | None = None

    def _notify_status(self, status: str) -> None:
        if self._status_callback:
            try:
                self._status_callback(status)
            except Exception:
                pass

    def request_reconnect(self) -> None:
        if self._reconnect_event is not None:
            self._reconnect_event.set()
        writer = self._active_writer
        if writer is not None:
            try:
                writer.close()
            except Exception:
                pass

    async def messages(self) -> AsyncIterator[str]:
        backoff = 1
        while True:
            try:
                self._notify_status("connecting")
                async for msg in self._run_once():
                    yield msg
                backoff = 1
            except PermissionError as exc:
                LOGGER.exception("IRC auth error: %s", exc)
                self._notify_status("auth_failed")
                if self._reconnect_event is not None:
                    self._reconnect_event.clear()
                    try:
                        await asyncio.wait_for(self._reconnect_event.wait(), timeout=backoff)
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            except Exception as exc:
                LOGGER.exception("IRC connection error: %s", exc)
                self._notify_status("reconnecting")
                if self._reconnect_event is not None:
                    self._reconnect_event.clear()
                    try:
                        await asyncio.wait_for(self._reconnect_event.wait(), timeout=backoff)
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _run_once(self) -> AsyncIterator[str]:
        if self._credentials_provider is not None:
            nick, oauth_token, channel = self._credentials_provider()
        else:
            nick, oauth_token, channel = self.nick, self.oauth_token, self.channel

        ssl_ctx = ssl.create_default_context()
        reader, writer = await asyncio.open_connection(
            host="irc.chat.twitch.tv", port=6697, ssl=ssl_ctx
        )
        self._active_writer = writer

        token = oauth_token.strip()
        if token.lower().startswith("oauth:"):
            token = token[6:]
        writer.write(f"PASS oauth:{token}\r\n".encode())
        writer.write(f"NICK {nick}\r\n".encode())
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
                if f" 001 {nick} " in raw:
                    authed = True
                    self._notify_status("connected")
                LOGGER.debug("IRC pre-auth: %s", raw)

            writer.write(f"JOIN #{channel}\r\n".encode())
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
            self._active_writer = None
            writer.close()
            await writer.wait_closed()
