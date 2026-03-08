import asyncio
import unittest

from reachy_twitch_voice.config import RuntimeConfig
from reachy_twitch_voice.main import _enqueue_with_policy


class QueuePolicyTest(unittest.TestCase):
    def test_drop_oldest_when_queue_full(self) -> None:
        q: asyncio.Queue[str] = asyncio.Queue()
        cfg = RuntimeConfig(max_queue_size=2, drop_policy="drop_oldest")

        self.assertFalse(_enqueue_with_policy(q, "a", cfg))
        self.assertFalse(_enqueue_with_policy(q, "b", cfg))
        self.assertTrue(_enqueue_with_policy(q, "c", cfg))

        self.assertEqual(q.get_nowait(), "b")
        self.assertEqual(q.get_nowait(), "c")


if __name__ == "__main__":
    unittest.main()
