import unittest

from reachy_twitch_voice.twitch_parser import parse_privmsg, parse_usernotice


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

    def test_uses_tmi_sent_ts_when_present(self) -> None:
        raw = "@tmi-sent-ts=1000 :alice!alice@alice.tmi.twitch.tv PRIVMSG #mychan :こんにちは"
        msg = parse_privmsg(raw)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertEqual(msg.received_at, 1.0)

    def test_reads_display_name_from_tags(self) -> None:
        raw = "@display-name=Alice;tmi-sent-ts=1000 :alice!alice@alice.tmi.twitch.tv PRIVMSG #mychan :こんにちは"
        msg = parse_privmsg(raw)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertEqual(msg.display_name, "Alice")

    def test_reads_display_name_when_after_tmi_sent_ts(self) -> None:
        # display-name must be parsed even when it appears after tmi-sent-ts
        raw = "@tmi-sent-ts=2000;display-name=Bob :bob!bob@bob.tmi.twitch.tv PRIVMSG #mychan :hello"
        msg = parse_privmsg(raw)
        self.assertIsNotNone(msg)
        assert msg is not None
        self.assertEqual(msg.display_name, "Bob")
        self.assertEqual(msg.received_at, 2.0)


class UsernoticeParserTest(unittest.TestCase):
    def _raid_raw(self) -> str:
        return (
            "@msg-id=raid;login=raider_chan;display-name=RaiderChan;"
            "system-msg=RaiderChan\\sis\\sraiding\\swith\\sa\\sparty\\sof\\s42;"
            "msg-param-viewerCount=42;tmi-sent-ts=2000"
            " :tmi.twitch.tv USERNOTICE #mychan"
        )

    def test_parse_raid(self) -> None:
        ev = parse_usernotice(self._raid_raw())
        self.assertIsNotNone(ev)
        assert ev is not None
        self.assertEqual(ev.event_type, "raid")
        self.assertEqual(ev.user_name, "raider_chan")
        self.assertEqual(ev.display_name, "RaiderChan")
        self.assertEqual(ev.channel, "mychan")
        self.assertEqual(ev.viewer_count, 42)
        self.assertEqual(ev.system_msg, "RaiderChan is raiding with a party of 42")
        self.assertAlmostEqual(ev.received_at, 2.0, places=3)

    def test_parse_sub(self) -> None:
        raw = (
            "@msg-id=sub;login=new_sub;display-name=NewSub;"
            "system-msg=NewSub\\ssubscribed.;tmi-sent-ts=3000"
            " :tmi.twitch.tv USERNOTICE #mychan"
        )
        ev = parse_usernotice(raw)
        self.assertIsNotNone(ev)
        assert ev is not None
        self.assertEqual(ev.event_type, "sub")
        self.assertEqual(ev.user_name, "new_sub")
        self.assertEqual(ev.system_msg, "NewSub subscribed.")
        self.assertIsNone(ev.viewer_count)

    def test_ignore_unsupported_msg_id(self) -> None:
        raw = (
            "@msg-id=charity;login=someone;tmi-sent-ts=1000"
            " :tmi.twitch.tv USERNOTICE #mychan"
        )
        self.assertIsNone(parse_usernotice(raw))

    def test_ignore_non_usernotice(self) -> None:
        raw = ":alice!alice@alice.tmi.twitch.tv PRIVMSG #mychan :hello"
        self.assertIsNone(parse_usernotice(raw))


if __name__ == "__main__":
    unittest.main()
