import os
import tempfile
import unittest
from datetime import datetime, timezone

from reachy_twitch_voice.stream_journal_store import (
    NoopStreamJournalStore,
    StreamJournalStore,
)
from reachy_twitch_voice.viewer_memory_store import ViewerMemoryStore


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class StreamJournalStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "viewer_memory.sqlite3")
        self.store = StreamJournalStore(self.db_path)

    def test_start_returns_positive_id(self) -> None:
        entry_id = self.store.start_entry(_now())
        self.assertGreater(entry_id, 0)

    def test_start_finalize_list(self) -> None:
        started = "2026-06-12T10:00:00+00:00"
        ended = "2026-06-12T12:00:00+00:00"
        entry_id = self.store.start_entry(started)
        self.store.finalize_entry(
            entry_id=entry_id,
            ended_at=ended,
            summary="楽しい配信でした",
            highlights=["レイドが来た", "ゲームでボス撃破"],
            learnings=["コメント速度は遅めがよい"],
            turn_count=42,
            unique_viewers=7,
        )
        entries = self.store.list_recent_finalized(limit=10)
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e.id, entry_id)
        self.assertEqual(e.summary, "楽しい配信でした")
        self.assertEqual(e.turn_count, 42)
        self.assertEqual(e.unique_viewers, 7)
        self.assertIn("レイドが来た", e.highlights)
        self.assertIn("コメント速度は遅めがよい", e.learnings)

    def test_unfinalized_entry_not_listed(self) -> None:
        # start_entry without finalize → summary=NULL → should not appear
        self.store.start_entry(_now())
        entries = self.store.list_recent_finalized(limit=10)
        self.assertEqual(entries, [])

    def test_list_recent_returns_newest_first(self) -> None:
        e1 = self.store.start_entry("2026-06-10T10:00:00+00:00")
        self.store.finalize_entry(
            entry_id=e1,
            ended_at="2026-06-10T12:00:00+00:00",
            summary="古い配信",
            highlights=[],
            learnings=[],
            turn_count=5,
            unique_viewers=2,
        )
        e2 = self.store.start_entry("2026-06-12T10:00:00+00:00")
        self.store.finalize_entry(
            entry_id=e2,
            ended_at="2026-06-12T12:00:00+00:00",
            summary="新しい配信",
            highlights=[],
            learnings=[],
            turn_count=10,
            unique_viewers=4,
        )
        entries = self.store.list_recent_finalized(limit=10)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].summary, "新しい配信")
        self.assertEqual(entries[1].summary, "古い配信")

    def test_list_recent_limit_respected(self) -> None:
        for i in range(5):
            eid = self.store.start_entry(f"2026-06-{10+i:02d}T10:00:00+00:00")
            self.store.finalize_entry(
                entry_id=eid,
                ended_at=f"2026-06-{10+i:02d}T12:00:00+00:00",
                summary=f"配信{i}",
                highlights=[],
                learnings=[],
                turn_count=i,
                unique_viewers=i,
            )
        entries = self.store.list_recent_finalized(limit=2)
        self.assertEqual(len(entries), 2)

    def test_list_recent_zero_limit(self) -> None:
        eid = self.store.start_entry(_now())
        self.store.finalize_entry(
            entry_id=eid,
            ended_at=_now(),
            summary="test",
            highlights=[],
            learnings=[],
            turn_count=1,
            unique_viewers=1,
        )
        entries = self.store.list_recent_finalized(limit=0)
        self.assertEqual(entries, [])

    def test_summary_too_long_raises(self) -> None:
        eid = self.store.start_entry(_now())
        with self.assertRaises(ValueError):
            self.store.finalize_entry(
                entry_id=eid,
                ended_at=_now(),
                summary="あ" * 501,
                highlights=[],
                learnings=[],
                turn_count=1,
                unique_viewers=1,
            )

    def test_summary_with_url_raises(self) -> None:
        eid = self.store.start_entry(_now())
        with self.assertRaises(ValueError):
            self.store.finalize_entry(
                entry_id=eid,
                ended_at=_now(),
                summary="https://example.com 配信",
                highlights=[],
                learnings=[],
                turn_count=1,
                unique_viewers=1,
            )

    def test_highlight_too_long_is_silently_skipped(self) -> None:
        eid = self.store.start_entry(_now())
        self.store.finalize_entry(
            entry_id=eid,
            ended_at=_now(),
            summary="ok summary",
            highlights=["あ" * 121],  # too long — silently skipped
            learnings=[],
            turn_count=1,
            unique_viewers=1,
        )
        entries = self.store.list_recent_finalized(limit=1)
        self.assertEqual(entries[0].highlights, [])

    def test_highlights_capped_at_5(self) -> None:
        eid = self.store.start_entry(_now())
        many = [f"highlight{i}" for i in range(8)]
        self.store.finalize_entry(
            entry_id=eid,
            ended_at=_now(),
            summary="ok",
            highlights=many,
            learnings=[],
            turn_count=1,
            unique_viewers=1,
        )
        entries = self.store.list_recent_finalized(limit=1)
        self.assertLessEqual(len(entries[0].highlights), 5)

    def test_update_ended_at_only(self) -> None:
        eid = self.store.start_entry(_now())
        ended = "2026-06-12T12:00:00+00:00"
        self.store.update_ended_at(entry_id=eid, ended_at=ended)
        # Entry should still have summary=NULL
        entries = self.store.list_recent_finalized(limit=10)
        self.assertEqual(entries, [])

    def test_coexists_with_viewer_memory_same_db(self) -> None:
        """viewer_memory and stream_journal can share the same SQLite file."""
        viewer_store = ViewerMemoryStore(self.db_path, max_notes=5)
        viewer_store.set_preferred_name(viewer_key="u1", preferred_name="テスト")
        eid = self.store.start_entry(_now())
        self.store.finalize_entry(
            entry_id=eid,
            ended_at=_now(),
            summary="共存テスト",
            highlights=[],
            learnings=[],
            turn_count=1,
            unique_viewers=1,
        )
        # Both should work independently
        prof = viewer_store.get_profile("u1")
        assert prof is not None
        self.assertEqual(prof.preferred_name, "テスト")
        entries = self.store.list_recent_finalized(limit=1)
        self.assertEqual(entries[0].summary, "共存テスト")

    def test_creates_parent_directory(self) -> None:
        nested = os.path.join(self.tmp.name, "deep", "path", "viewer.sqlite3")
        store = StreamJournalStore(nested)
        eid = store.start_entry(_now())
        self.assertGreater(eid, 0)


class NoopStreamJournalStoreTest(unittest.TestCase):
    def test_start_returns_minus_one(self) -> None:
        store = NoopStreamJournalStore()
        self.assertEqual(store.start_entry(_now()), -1)

    def test_finalize_does_not_raise(self) -> None:
        store = NoopStreamJournalStore()
        store.finalize_entry(
            entry_id=-1,
            ended_at=_now(),
            summary="test",
            highlights=[],
            learnings=[],
            turn_count=1,
            unique_viewers=1,
        )

    def test_update_ended_at_does_not_raise(self) -> None:
        store = NoopStreamJournalStore()
        store.update_ended_at(entry_id=-1, ended_at=_now())

    def test_list_recent_returns_empty(self) -> None:
        store = NoopStreamJournalStore()
        self.assertEqual(store.list_recent_finalized(limit=5), [])


if __name__ == "__main__":
    unittest.main()
