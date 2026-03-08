import unittest

from reachy_twitch_voice.normalizer import normalize_comment


class NormalizerTest(unittest.TestCase):
    def test_normalize_url_whitespace_and_repeat(self) -> None:
        text = "  hello   https://example.com  woooooooow  "
        got = normalize_comment(text)
        self.assertEqual(got, "hello [link] wooow")


if __name__ == "__main__":
    unittest.main()
