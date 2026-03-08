import asyncio
import unittest
from unittest.mock import patch

from reachy_twitch_voice.twitch_irc import TwitchIrcClient


class _FakeReader:
    async def readline(self) -> bytes:
        return b""


class _FakeWriter:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def write(self, b: bytes) -> None:
        self.writes.append(b.decode())

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


class TwitchIrcTokenTest(unittest.IsolatedAsyncioTestCase):
    async def test_oauth_prefix_is_not_duplicated(self) -> None:
        reader = _FakeReader()
        writer = _FakeWriter()

        async def fake_open_connection(*args, **kwargs):
            return reader, writer

        client = TwitchIrcClient(nick="bot", oauth_token="oauth:abc", channel="chan")

        with patch("asyncio.open_connection", fake_open_connection):
            with self.assertRaises(ConnectionError):
                async for _ in client._run_once():
                    pass

        self.assertTrue(any("PASS oauth:abc\r\n" == x for x in writer.writes))


if __name__ == "__main__":
    unittest.main()
