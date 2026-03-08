import asyncio
import tempfile
import unittest

from reachy_twitch_voice.main import run_app


class MainReplayTest(unittest.TestCase):
    def test_run_app_with_replay_completes(self) -> None:
        lines = [
            ":alice!alice@alice.tmi.twitch.tv PRIVMSG #chan :hello\\n",
            ":bob!bob@bob.tmi.twitch.tv PRIVMSG #chan :world\\n",
        ]
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=True) as f:
            f.writelines(lines)
            f.flush()
            asyncio.run(run_app(use_mock=True, reachy_host="127.0.0.1", replay_file=f.name))


if __name__ == "__main__":
    unittest.main()
