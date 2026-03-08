import unittest

from reachy_twitch_voice.twitch_parser import parse_privmsg


class TwitchParserTest(unittest.TestCase):
    def test_parse_privmsg(self) -> None:
        raw = ":alice!alice@alice.tmi.twitch.tv PRIVMSG #mychan :こんにちは"
        msg = parse_privmsg(raw)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertEqual(msg.channel, "mychan")
        self.assertEqual(msg.user_id, "alice")
        self.assertEqual(msg.text, "こんにちは")

    def test_ignore_non_privmsg(self) -> None:
        self.assertIsNone(parse_privmsg("PING :tmi.twitch.tv"))


if __name__ == "__main__":
    unittest.main()
