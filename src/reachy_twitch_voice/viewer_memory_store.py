from __future__ import annotations

import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

LOGGER = logging.getLogger(__name__)

_PREFERRED_NAME_MAX = 32
_NOTE_MAX = 200
_NOTE_MIN_CONFIDENCE = 0.4
_TOPIC_MAX = 60
_DENY_KEYWORDS = (
    "http://",
    "https://",
    "個人情報",
    "住所",
    "電話番号",
    "差別",
    "暴力",
)
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(slots=True)
class ViewerProfile:
    viewer_key: str
    login: str | None
    display_name: str | None
    preferred_name: str | None
    first_seen_at: str
    last_seen_at: str
    updated_at: str
    visit_count: int = 0
    last_topic: str | None = None
    last_topic_at: str | None = None


@dataclass(slots=True)
class ViewerNote:
    id: int
    viewer_key: str
    note: str
    confidence: float
    source: str | None
    created_at: str
    updated_at: str


class ViewerMemoryStoreProtocol(Protocol):
    def upsert_seen(
        self,
        *,
        viewer_key: str,
        login: str | None,
        display_name: str | None,
        seen_at: datetime,
    ) -> None: ...

    def get_profile(self, viewer_key: str) -> ViewerProfile | None: ...

    def set_preferred_name(
        self,
        *,
        viewer_key: str,
        preferred_name: str,
        reason: str | None = None,
    ) -> None: ...

    def add_note(
        self,
        *,
        viewer_key: str,
        note: str,
        confidence: float,
        source: str | None = None,
    ) -> None: ...

    def list_recent_notes(
        self,
        *,
        viewer_key: str,
        limit: int,
    ) -> list[ViewerNote]: ...

    def increment_visit(self, viewer_key: str) -> None: ...

    def set_last_topic(self, viewer_key: str, topic: str) -> None: ...


def _to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _now_iso() -> str:
    return _to_iso(datetime.now(tz=timezone.utc))


def _validate_preferred_name(value: str) -> str:
    s = value.strip()
    if not s:
        raise ValueError("preferred_name is empty")
    if len(s) > _PREFERRED_NAME_MAX:
        raise ValueError(f"preferred_name too long: {len(s)} > {_PREFERRED_NAME_MAX}")
    if _CONTROL_CHARS.search(s):
        raise ValueError("preferred_name contains control characters")
    low = s.lower()
    for kw in _DENY_KEYWORDS:
        if kw.lower() in low:
            raise ValueError(f"preferred_name rejected by keyword: {kw}")
    return s


def _validate_topic(value: str) -> str:
    s = value.strip()
    if not s:
        raise ValueError("topic is empty")
    if len(s) > _TOPIC_MAX:
        raise ValueError(f"topic too long: {len(s)} > {_TOPIC_MAX}")
    if _CONTROL_CHARS.search(s):
        raise ValueError("topic contains control characters")
    low = s.lower()
    for kw in _DENY_KEYWORDS:
        if kw.lower() in low:
            raise ValueError(f"topic rejected by keyword: {kw}")
    return s


def _validate_note(value: str, confidence: float) -> str:
    s = value.strip()
    if not s:
        raise ValueError("note is empty")
    if len(s) > _NOTE_MAX:
        raise ValueError(f"note too long: {len(s)} > {_NOTE_MAX}")
    if confidence < _NOTE_MIN_CONFIDENCE:
        raise ValueError(f"note confidence too low: {confidence}")
    if _CONTROL_CHARS.search(s):
        raise ValueError("note contains control characters")
    low = s.lower()
    for kw in _DENY_KEYWORDS:
        if kw.lower() in low:
            raise ValueError(f"note rejected by keyword: {kw}")
    return s


_SCHEMA = """
CREATE TABLE IF NOT EXISTS viewer_profiles (
  viewer_key TEXT PRIMARY KEY,
  login TEXT,
  display_name TEXT,
  preferred_name TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  visit_count INTEGER NOT NULL DEFAULT 0,
  last_topic TEXT,
  last_topic_at TEXT
);
CREATE TABLE IF NOT EXISTS viewer_notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  viewer_key TEXT NOT NULL,
  note TEXT NOT NULL,
  confidence REAL NOT NULL DEFAULT 1.0,
  source TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(viewer_key) REFERENCES viewer_profiles(viewer_key)
);
CREATE INDEX IF NOT EXISTS idx_viewer_notes_viewer_key_created_at
ON viewer_notes(viewer_key, created_at);
"""


class ViewerMemoryStore:
    """SQLite-backed store for per-viewer preferred names and short notes.

    Uses WAL journal mode plus a process-local Lock so that multiple
    asyncio tasks can safely call into this store via asyncio.to_thread.
    """

    def __init__(self, db_path: str, max_notes: int = 8) -> None:
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._max_notes = max(0, max_notes)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=2.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(_SCHEMA)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                # Some filesystems (e.g. tmpfs) do not support WAL; fall back silently.
                pass
            # Idempotent migration: add new columns if they don't exist yet
            existing_cols = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(viewer_profiles)"
                ).fetchall()
            }
            migrations = [
                ("visit_count", "ALTER TABLE viewer_profiles ADD COLUMN visit_count INTEGER NOT NULL DEFAULT 0"),
                ("last_topic", "ALTER TABLE viewer_profiles ADD COLUMN last_topic TEXT"),
                ("last_topic_at", "ALTER TABLE viewer_profiles ADD COLUMN last_topic_at TEXT"),
            ]
            for col_name, ddl in migrations:
                if col_name not in existing_cols:
                    conn.execute(ddl)
            conn.commit()

    def upsert_seen(
        self,
        *,
        viewer_key: str,
        login: str | None,
        display_name: str | None,
        seen_at: datetime,
    ) -> None:
        if not viewer_key:
            raise ValueError("viewer_key is empty")
        seen_iso = _to_iso(seen_at)
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT viewer_key FROM viewer_profiles WHERE viewer_key=?",
                (viewer_key,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO viewer_profiles
                    (viewer_key, login, display_name, preferred_name,
                     first_seen_at, last_seen_at, updated_at)
                    VALUES (?, ?, ?, NULL, ?, ?, ?)
                    """,
                    (viewer_key, login, display_name, seen_iso, seen_iso, seen_iso),
                )
            else:
                conn.execute(
                    """
                    UPDATE viewer_profiles
                    SET login = COALESCE(?, login),
                        display_name = COALESCE(?, display_name),
                        last_seen_at = ?,
                        updated_at = ?
                    WHERE viewer_key=?
                    """,
                    (login, display_name, seen_iso, seen_iso, viewer_key),
                )
            conn.commit()

    def get_profile(self, viewer_key: str) -> ViewerProfile | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM viewer_profiles WHERE viewer_key=?",
                (viewer_key,),
            ).fetchone()
        if row is None:
            return None
        return ViewerProfile(
            viewer_key=row["viewer_key"],
            login=row["login"],
            display_name=row["display_name"],
            preferred_name=row["preferred_name"],
            first_seen_at=row["first_seen_at"],
            last_seen_at=row["last_seen_at"],
            updated_at=row["updated_at"],
            visit_count=row["visit_count"] if "visit_count" in row.keys() else 0,
            last_topic=row["last_topic"] if "last_topic" in row.keys() else None,
            last_topic_at=row["last_topic_at"] if "last_topic_at" in row.keys() else None,
        )

    def set_preferred_name(
        self,
        *,
        viewer_key: str,
        preferred_name: str,
        reason: str | None = None,
    ) -> None:
        validated = _validate_preferred_name(preferred_name)
        now = _now_iso()
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT viewer_key FROM viewer_profiles WHERE viewer_key=?",
                (viewer_key,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO viewer_profiles
                    (viewer_key, login, display_name, preferred_name,
                     first_seen_at, last_seen_at, updated_at)
                    VALUES (?, NULL, NULL, ?, ?, ?, ?)
                    """,
                    (viewer_key, validated, now, now, now),
                )
            else:
                conn.execute(
                    """
                    UPDATE viewer_profiles
                    SET preferred_name=?, updated_at=?
                    WHERE viewer_key=?
                    """,
                    (validated, now, viewer_key),
                )
            conn.commit()
        LOGGER.info(
            "viewer_memory: set_preferred_name viewer_key=%s len=%d reason=%s",
            viewer_key,
            len(validated),
            (reason or "")[:40],
        )

    def add_note(
        self,
        *,
        viewer_key: str,
        note: str,
        confidence: float,
        source: str | None = None,
    ) -> None:
        validated = _validate_note(note, confidence)
        now = _now_iso()
        with self._lock, self._connect() as conn:
            duplicate = conn.execute(
                """
                SELECT id FROM viewer_notes
                WHERE viewer_key=? AND note=?
                LIMIT 1
                """,
                (viewer_key, validated),
            ).fetchone()
            if duplicate is not None:
                conn.execute(
                    "UPDATE viewer_notes SET updated_at=?, confidence=? WHERE id=?",
                    (now, confidence, duplicate["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO viewer_notes
                    (viewer_key, note, confidence, source, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (viewer_key, validated, confidence, source, now, now),
                )
            if self._max_notes > 0:
                # Trim oldest entries beyond the cap (per viewer).
                conn.execute(
                    """
                    DELETE FROM viewer_notes
                    WHERE viewer_key=? AND id IN (
                        SELECT id FROM viewer_notes
                        WHERE viewer_key=?
                        ORDER BY created_at ASC, id ASC
                        LIMIT max(0, (
                            SELECT COUNT(*) FROM viewer_notes WHERE viewer_key=?
                        ) - ?)
                    )
                    """,
                    (viewer_key, viewer_key, viewer_key, self._max_notes),
                )
            conn.commit()
        LOGGER.info(
            "viewer_memory: add_note viewer_key=%s len=%d confidence=%.2f",
            viewer_key,
            len(validated),
            confidence,
        )

    def list_recent_notes(
        self,
        *,
        viewer_key: str,
        limit: int,
    ) -> list[ViewerNote]:
        if limit <= 0:
            return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM viewer_notes
                WHERE viewer_key=?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (viewer_key, int(limit)),
            ).fetchall()
        return [
            ViewerNote(
                id=row["id"],
                viewer_key=row["viewer_key"],
                note=row["note"],
                confidence=row["confidence"],
                source=row["source"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def increment_visit(self, viewer_key: str) -> None:
        """Increment the visit_count for a viewer (called once per session)."""
        now = _now_iso()
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT viewer_key FROM viewer_profiles WHERE viewer_key=?",
                (viewer_key,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO viewer_profiles
                    (viewer_key, login, display_name, preferred_name,
                     first_seen_at, last_seen_at, updated_at, visit_count)
                    VALUES (?, NULL, NULL, NULL, ?, ?, ?, 1)
                    """,
                    (viewer_key, now, now, now),
                )
            else:
                conn.execute(
                    """
                    UPDATE viewer_profiles
                    SET visit_count = visit_count + 1, updated_at = ?
                    WHERE viewer_key = ?
                    """,
                    (now, viewer_key),
                )
            conn.commit()
        LOGGER.info(
            "viewer_memory: increment_visit viewer_key=%s",
            viewer_key,
        )

    def set_last_topic(self, viewer_key: str, topic: str) -> None:
        """Store the last topic discussed with a viewer (validated)."""
        validated = _validate_topic(topic)
        now = _now_iso()
        with self._lock, self._connect() as conn:
            existing = conn.execute(
                "SELECT viewer_key FROM viewer_profiles WHERE viewer_key=?",
                (viewer_key,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO viewer_profiles
                    (viewer_key, login, display_name, preferred_name,
                     first_seen_at, last_seen_at, updated_at, last_topic, last_topic_at)
                    VALUES (?, NULL, NULL, NULL, ?, ?, ?, ?, ?)
                    """,
                    (viewer_key, now, now, now, validated, now),
                )
            else:
                conn.execute(
                    """
                    UPDATE viewer_profiles
                    SET last_topic = ?, last_topic_at = ?, updated_at = ?
                    WHERE viewer_key = ?
                    """,
                    (validated, now, now, viewer_key),
                )
            conn.commit()
        LOGGER.info(
            "viewer_memory: set_last_topic viewer_key=%s len=%d",
            viewer_key,
            len(validated),
        )


class NoopViewerMemoryStore:
    """No-op store used when VIEWER_MEMORY_ENABLED=false.

    Calls succeed silently; lookups return empty results. This lets the rest
    of the pipeline be unaware of whether persistence is on.
    """

    def upsert_seen(
        self,
        *,
        viewer_key: str,
        login: str | None,
        display_name: str | None,
        seen_at: datetime,
    ) -> None:
        return None

    def get_profile(self, viewer_key: str) -> ViewerProfile | None:
        return None

    def set_preferred_name(
        self,
        *,
        viewer_key: str,
        preferred_name: str,
        reason: str | None = None,
    ) -> None:
        return None

    def add_note(
        self,
        *,
        viewer_key: str,
        note: str,
        confidence: float,
        source: str | None = None,
    ) -> None:
        return None

    def list_recent_notes(
        self,
        *,
        viewer_key: str,
        limit: int,
    ) -> list[ViewerNote]:
        return []

    def increment_visit(self, viewer_key: str) -> None:
        return None

    def set_last_topic(self, viewer_key: str, topic: str) -> None:
        return None
