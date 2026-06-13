import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from reachy_twitch_voice.viewer_memory_store import (
    NoopViewerMemoryStore,
    ViewerMemoryStore,
)


class ViewerMemoryStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "viewer_memory.sqlite3")
        self.store = ViewerMemoryStore(self.db_path, max_notes=3)
        self.now = datetime(2026, 4, 28, 12, 0, 0, tzinfo=timezone.utc)

    def test_upsert_seen_creates_profile(self) -> None:
        self.store.upsert_seen(
            viewer_key="12345",
            login="alice",
            display_name="Alice",
            seen_at=self.now,
        )
        prof = self.store.get_profile("12345")
        assert prof is not None
        self.assertEqual(prof.login, "alice")
        self.assertEqual(prof.display_name, "Alice")
        self.assertEqual(prof.first_seen_at, prof.last_seen_at)
        self.assertIsNone(prof.preferred_name)

    def test_upsert_seen_updates_last_seen(self) -> None:
        self.store.upsert_seen(
            viewer_key="12345",
            login="alice",
            display_name="Alice",
            seen_at=self.now,
        )
        later = self.now + timedelta(hours=1)
        self.store.upsert_seen(
            viewer_key="12345",
            login="alice",
            display_name="Alice",
            seen_at=later,
        )
        prof = self.store.get_profile("12345")
        assert prof is not None
        self.assertNotEqual(prof.first_seen_at, prof.last_seen_at)
        self.assertGreater(prof.last_seen_at, prof.first_seen_at)

    def test_set_preferred_name_basic(self) -> None:
        self.store.upsert_seen(
            viewer_key="k1",
            login="bob",
            display_name="Bob",
            seen_at=self.now,
        )
        self.store.set_preferred_name(viewer_key="k1", preferred_name="マロ")
        prof = self.store.get_profile("k1")
        assert prof is not None
        self.assertEqual(prof.preferred_name, "マロ")

    def test_set_preferred_name_creates_profile_when_absent(self) -> None:
        # set_preferred_name should still work when called before upsert_seen.
        self.store.set_preferred_name(viewer_key="k_new", preferred_name="ニック")
        prof = self.store.get_profile("k_new")
        assert prof is not None
        self.assertEqual(prof.preferred_name, "ニック")

    def test_preferred_name_too_long_rejected(self) -> None:
        long_name = "あ" * 33
        with self.assertRaises(ValueError):
            self.store.set_preferred_name(viewer_key="k1", preferred_name=long_name)

    def test_preferred_name_with_url_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set_preferred_name(
                viewer_key="k1",
                preferred_name="see http://example.com",
            )

    def test_preferred_name_with_control_chars_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set_preferred_name(
                viewer_key="k1",
                preferred_name="foo\x00bar",
            )

    def test_preferred_name_empty_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set_preferred_name(viewer_key="k1", preferred_name="   ")

    def test_add_note_basic(self) -> None:
        self.store.add_note(
            viewer_key="k1",
            note="Alan Wake 2 が好き",
            confidence=0.8,
        )
        notes = self.store.list_recent_notes(viewer_key="k1", limit=10)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].note, "Alan Wake 2 が好き")
        self.assertAlmostEqual(notes[0].confidence, 0.8)

    def test_add_note_low_confidence_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.add_note(viewer_key="k1", note="hello", confidence=0.1)

    def test_add_note_with_sensitive_keyword_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.add_note(
                viewer_key="k1",
                note="住所は東京都",
                confidence=0.9,
            )

    def test_add_note_with_url_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.add_note(
                viewer_key="k1",
                note="https://example.com is cool",
                confidence=0.9,
            )

    def test_list_recent_notes_limits_and_trims(self) -> None:
        for i in range(5):
            self.store.add_note(
                viewer_key="k1",
                note=f"note-{i}",
                confidence=0.7,
            )
        # max_notes=3 in setUp, oldest two should have been trimmed.
        notes = self.store.list_recent_notes(viewer_key="k1", limit=10)
        self.assertEqual(len(notes), 3)
        kept = {n.note for n in notes}
        self.assertEqual(kept, {"note-2", "note-3", "note-4"})

    def test_list_recent_notes_orders_newest_first(self) -> None:
        self.store.add_note(viewer_key="k1", note="first", confidence=0.7)
        self.store.add_note(viewer_key="k1", note="second", confidence=0.7)
        notes = self.store.list_recent_notes(viewer_key="k1", limit=2)
        self.assertEqual([n.note for n in notes], ["second", "first"])

    def test_list_recent_notes_zero_limit(self) -> None:
        self.store.add_note(viewer_key="k1", note="hello", confidence=0.9)
        self.assertEqual(self.store.list_recent_notes(viewer_key="k1", limit=0), [])

    def test_get_profile_for_unknown_returns_none(self) -> None:
        self.assertIsNone(self.store.get_profile("missing-key"))

    def test_creates_parent_directory(self) -> None:
        nested = os.path.join(self.tmp.name, "deep", "path", "viewer.sqlite3")
        store = ViewerMemoryStore(nested, max_notes=1)
        store.set_preferred_name(viewer_key="x", preferred_name="P")
        self.assertTrue(Path(nested).exists())


class ViewerMemoryStoreMigrationTest(unittest.TestCase):
    """Test idempotent migration from a 7-column (pre-B-1) DB."""

    def test_migrate_old_6col_db(self) -> None:
        """Open a store with the old schema (no visit_count etc), then re-open with new schema."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "old.sqlite3")
            import sqlite3 as _sqlite3
            # Create DB with old schema (no visit_count, last_topic, last_topic_at)
            conn = _sqlite3.connect(db_path)
            conn.execute("""
                CREATE TABLE viewer_profiles (
                  viewer_key TEXT PRIMARY KEY,
                  login TEXT,
                  display_name TEXT,
                  preferred_name TEXT,
                  first_seen_at TEXT NOT NULL,
                  last_seen_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
            """)
            now_str = datetime.now(tz=timezone.utc).isoformat()
            conn.execute(
                "INSERT INTO viewer_profiles VALUES (?,?,?,?,?,?,?)",
                ("user1", "alice", "Alice", None, now_str, now_str, now_str),
            )
            conn.commit()
            conn.close()

            # Open with new ViewerMemoryStore — should migrate without error
            store = ViewerMemoryStore(db_path, max_notes=5)
            prof = store.get_profile("user1")
            assert prof is not None
            self.assertEqual(prof.login, "alice")
            self.assertEqual(prof.visit_count, 0)
            self.assertIsNone(prof.last_topic)

    def test_migrate_is_idempotent(self) -> None:
        """Opening a new-schema DB again should not fail."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "new.sqlite3")
            store1 = ViewerMemoryStore(db_path, max_notes=5)
            store2 = ViewerMemoryStore(db_path, max_notes=5)
            # Should not raise
            self.assertIsNone(store2.get_profile("missing"))


class ViewerMemoryStoreVisitCountTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "viewer_memory.sqlite3")
        self.store = ViewerMemoryStore(self.db_path, max_notes=5)
        self.now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_increment_visit_creates_profile_if_missing(self) -> None:
        self.store.increment_visit("vk1")
        prof = self.store.get_profile("vk1")
        assert prof is not None
        self.assertEqual(prof.visit_count, 1)

    def test_increment_visit_accumulates(self) -> None:
        self.store.upsert_seen(viewer_key="vk1", login="a", display_name=None, seen_at=self.now)
        self.store.increment_visit("vk1")
        self.store.increment_visit("vk1")
        prof = self.store.get_profile("vk1")
        assert prof is not None
        self.assertEqual(prof.visit_count, 2)

    def test_visit_count_zero_by_default(self) -> None:
        self.store.upsert_seen(viewer_key="vk2", login="b", display_name=None, seen_at=self.now)
        prof = self.store.get_profile("vk2")
        assert prof is not None
        self.assertEqual(prof.visit_count, 0)


class ViewerMemoryStoreLastTopicTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "viewer_memory.sqlite3")
        self.store = ViewerMemoryStore(self.db_path, max_notes=5)
        self.now = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_set_last_topic_saves(self) -> None:
        self.store.upsert_seen(viewer_key="vk1", login="a", display_name=None, seen_at=self.now)
        self.store.set_last_topic("vk1", "ポケモン新作の話")
        prof = self.store.get_profile("vk1")
        assert prof is not None
        self.assertEqual(prof.last_topic, "ポケモン新作の話")
        self.assertIsNotNone(prof.last_topic_at)

    def test_set_last_topic_overwrites(self) -> None:
        self.store.upsert_seen(viewer_key="vk1", login="a", display_name=None, seen_at=self.now)
        self.store.set_last_topic("vk1", "最初のトピック")
        self.store.set_last_topic("vk1", "新しいトピック")
        prof = self.store.get_profile("vk1")
        assert prof is not None
        self.assertEqual(prof.last_topic, "新しいトピック")

    def test_set_last_topic_too_long_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set_last_topic("vk1", "あ" * 61)

    def test_set_last_topic_with_url_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set_last_topic("vk1", "https://example.com の話")

    def test_set_last_topic_with_control_chars_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set_last_topic("vk1", "topic\x00bad")

    def test_set_last_topic_empty_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.set_last_topic("vk1", "   ")

    def test_set_last_topic_creates_profile_if_missing(self) -> None:
        self.store.set_last_topic("new_vk", "ゲームの話")
        prof = self.store.get_profile("new_vk")
        assert prof is not None
        self.assertEqual(prof.last_topic, "ゲームの話")


class NoopViewerMemoryStoreTest(unittest.TestCase):
    def test_noop_store_does_not_create_db(self) -> None:
        store = NoopViewerMemoryStore()
        store.upsert_seen(
            viewer_key="k",
            login="a",
            display_name=None,
            seen_at=datetime.now(tz=timezone.utc),
        )
        store.set_preferred_name(viewer_key="k", preferred_name="P")
        store.add_note(viewer_key="k", note="hello", confidence=0.9)
        self.assertIsNone(store.get_profile("k"))
        self.assertEqual(store.list_recent_notes(viewer_key="k", limit=5), [])

    def test_noop_increment_visit_no_error(self) -> None:
        store = NoopViewerMemoryStore()
        store.increment_visit("k")  # should not raise

    def test_noop_set_last_topic_no_error(self) -> None:
        store = NoopViewerMemoryStore()
        store.set_last_topic("k", "some topic")  # should not raise


if __name__ == "__main__":
    unittest.main()
