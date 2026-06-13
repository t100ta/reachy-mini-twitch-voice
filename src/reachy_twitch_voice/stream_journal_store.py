from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

LOGGER = logging.getLogger(__name__)

_SUMMARY_MAX = 500
_HIGHLIGHT_MAX = 120
_LEARNING_MAX = 120
_HIGHLIGHTS_MAX_COUNT = 5
_LEARNINGS_MAX_COUNT = 5

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


def _to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _now_iso() -> str:
    return _to_iso(datetime.now(tz=timezone.utc))


def _validate_text(value: str, max_len: int, field_name: str) -> str:
    s = value.strip()
    if not s:
        raise ValueError(f"{field_name} is empty")
    if len(s) > max_len:
        raise ValueError(f"{field_name} too long: {len(s)} > {max_len}")
    if _CONTROL_CHARS.search(s):
        raise ValueError(f"{field_name} contains control characters")
    low = s.lower()
    for kw in _DENY_KEYWORDS:
        if kw.lower() in low:
            raise ValueError(f"{field_name} rejected by keyword: {kw}")
    return s


@dataclass(slots=True)
class StreamJournalEntry:
    id: int
    started_at: str
    ended_at: str | None
    summary: str | None
    highlights: list[str]
    learnings: list[str]
    turn_count: int
    unique_viewers: int
    created_at: str
    updated_at: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS stream_journal (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  ended_at TEXT,
  summary TEXT,
  highlights TEXT,
  learnings TEXT,
  turn_count INTEGER NOT NULL DEFAULT 0,
  unique_viewers INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


class StreamJournalStoreProtocol(Protocol):
    def start_entry(self, started_at: str) -> int: ...

    def finalize_entry(
        self,
        *,
        entry_id: int,
        ended_at: str,
        summary: str,
        highlights: list[str],
        learnings: list[str],
        turn_count: int,
        unique_viewers: int,
    ) -> None: ...

    def list_recent_finalized(self, limit: int) -> list[StreamJournalEntry]: ...


class StreamJournalStore:
    """SQLite-backed store for per-stream journal entries (summaries, highlights, learnings).

    Co-resides in the same DB file as ViewerMemoryStore (viewer_memory.sqlite3).
    Uses WAL mode and a process-local Lock for thread safety.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
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
                pass
            conn.commit()

    def start_entry(self, started_at: str) -> int:
        """Create an open (summary=NULL) journal entry and return its id."""
        now = _now_iso()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO stream_journal
                (started_at, ended_at, summary, highlights, learnings,
                 turn_count, unique_viewers, created_at, updated_at)
                VALUES (?, NULL, NULL, NULL, NULL, 0, 0, ?, ?)
                """,
                (started_at, now, now),
            )
            entry_id = cursor.lastrowid
            conn.commit()
        LOGGER.info("stream_journal: start_entry id=%s started_at=%s", entry_id, started_at)
        return entry_id  # type: ignore[return-value]

    def finalize_entry(
        self,
        *,
        entry_id: int,
        ended_at: str,
        summary: str,
        highlights: list[str],
        learnings: list[str],
        turn_count: int,
        unique_viewers: int,
    ) -> None:
        """Validate and finalize a journal entry with summary and metadata."""
        validated_summary = _validate_text(summary, _SUMMARY_MAX, "summary")

        validated_highlights: list[str] = []
        for h in highlights[: _HIGHLIGHTS_MAX_COUNT]:
            try:
                validated_highlights.append(_validate_text(h, _HIGHLIGHT_MAX, "highlight"))
            except ValueError as exc:
                LOGGER.info("stream_journal: highlight rejected: %s", exc)

        validated_learnings: list[str] = []
        for lr in learnings[: _LEARNINGS_MAX_COUNT]:
            try:
                validated_learnings.append(_validate_text(lr, _LEARNING_MAX, "learning"))
            except ValueError as exc:
                LOGGER.info("stream_journal: learning rejected: %s", exc)

        now = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE stream_journal
                SET ended_at=?, summary=?, highlights=?, learnings=?,
                    turn_count=?, unique_viewers=?, updated_at=?
                WHERE id=?
                """,
                (
                    ended_at,
                    validated_summary,
                    json.dumps(validated_highlights, ensure_ascii=False),
                    json.dumps(validated_learnings, ensure_ascii=False),
                    turn_count,
                    unique_viewers,
                    now,
                    entry_id,
                ),
            )
            conn.commit()
        LOGGER.info(
            "stream_journal: finalize_entry id=%s turn_count=%d unique_viewers=%d",
            entry_id,
            turn_count,
            unique_viewers,
        )

    def update_ended_at(self, *, entry_id: int, ended_at: str) -> None:
        """Update ended_at only (for crash/timeout cases where summary is not available)."""
        now = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE stream_journal SET ended_at=?, updated_at=? WHERE id=?",
                (ended_at, now, entry_id),
            )
            conn.commit()
        LOGGER.info("stream_journal: update_ended_at id=%s", entry_id)

    def list_recent_finalized(self, limit: int) -> list[StreamJournalEntry]:
        """Return finalized (summary NOT NULL) entries, newest first."""
        if limit <= 0:
            return []
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM stream_journal
                WHERE summary IS NOT NULL
                ORDER BY started_at DESC, id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    @staticmethod
    def _row_to_entry(row: sqlite3.Row) -> StreamJournalEntry:
        highlights: list[str] = []
        learnings: list[str] = []
        try:
            if row["highlights"]:
                highlights = json.loads(row["highlights"])
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            if row["learnings"]:
                learnings = json.loads(row["learnings"])
        except (json.JSONDecodeError, TypeError):
            pass
        return StreamJournalEntry(
            id=row["id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            summary=row["summary"],
            highlights=highlights,
            learnings=learnings,
            turn_count=row["turn_count"],
            unique_viewers=row["unique_viewers"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


class NoopStreamJournalStore:
    """No-op store used when stream journal is disabled or unavailable."""

    def start_entry(self, started_at: str) -> int:
        return -1

    def finalize_entry(
        self,
        *,
        entry_id: int,
        ended_at: str,
        summary: str,
        highlights: list[str],
        learnings: list[str],
        turn_count: int,
        unique_viewers: int,
    ) -> None:
        return None

    def update_ended_at(self, *, entry_id: int, ended_at: str) -> None:
        return None

    def list_recent_finalized(self, limit: int) -> list[StreamJournalEntry]:
        return []
