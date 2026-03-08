import unittest

from reachy_twitch_voice.config import SafetyConfig
from reachy_twitch_voice.safety import SafetyFilter
from reachy_twitch_voice.types import TwitchMessage


class SafetyFilterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.filter = SafetyFilter(SafetyConfig(ng_words=["ngword"], max_chars=10, spam_window_sec=60))
        self.base = TwitchMessage(
            id="1",
            channel="chan",
            user_id="u1",
            user_name="u1",
            text="hello",
            received_at=0,
        )

    def test_block_ng_word(self) -> None:
        d = self.filter.evaluate(self.base, "ngword")
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "ng_word")

    def test_block_too_long(self) -> None:
        d = self.filter.evaluate(self.base, "12345678901")
        self.assertFalse(d.allow)
        self.assertEqual(d.reason, "too_long")

    def test_block_spam_on_third_dup(self) -> None:
        d1 = self.filter.evaluate(self.base, "ok")
        d2 = self.filter.evaluate(self.base, "ok")
        d3 = self.filter.evaluate(self.base, "ok")
        self.assertTrue(d1.allow)
        self.assertTrue(d2.allow)
        self.assertFalse(d3.allow)
        self.assertEqual(d3.reason, "spam")


if __name__ == "__main__":
    unittest.main()
