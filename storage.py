"""SQLite storage for an authorized, one-time link distribution bot."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

@dataclass(frozen=True)
class UploadResult:
    added: int
    invalid: int
    duplicates: int

@dataclass(frozen=True)
class ClaimResult:
    status: str
    url: str | None
    used: int
    remaining: int

class LinkStore:
    """Persist links and issue at most one unassigned link per successful claim."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS links (
                    link_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL UNIQUE,
                    batch_name TEXT NOT NULL,
                    uploaded_at_utc TEXT NOT NULL,
                    assigned_to INTEGER,
                    assigned_at_utc TEXT,
                    CHECK (
                        (assigned_to IS NULL AND assigned_at_utc IS NULL)
                        OR
                        (assigned_to IS NOT NULL AND assigned_at_utc IS NOT NULL)
                    )
                );

                CREATE TABLE IF NOT EXISTS daily_usage (
                    user_id INTEGER NOT NULL,
                    usage_date TEXT NOT NULL,
                    issued_count INTEGER NOT NULL DEFAULT 0 CHECK (issued_count >= 0),
                    PRIMARY KEY (user_id, usage_date)
                );

                CREATE INDEX IF NOT EXISTS idx_links_unassigned
                    ON links (assigned_to, link_id);
                """
            )

    @staticmethod
    def normalize_url(raw_value: str) -> str | None:
        """Return a canonical HTTP(S) URL, or None for a disallowed/invalid row."""
        value = raw_value.strip()
        if not value or len(value) > 2048:
            return None

        try:
            parsed = urlsplit(value)
        except ValueError:
            return None

        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return None
        if parsed.username or parsed.password:
            return None

        return urlunsplit(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path or "",
                parsed.query,
                "",
            )
        )

    def add_links(self, lines: Iterable[str], batch_name: str) -> UploadResult:
        """Insert valid, unique links from a text file without overwriting inventory."""
        seen_in_file: set[str] = set()
        candidates: list[str] = []
        invalid = 0
        duplicates = 0

        for raw_line in lines:
            url = self.normalize_url(raw_line)
            if url is None:
                if raw_line.strip():
                    invalid += 1
                continue
            if url in seen_in_file:
                duplicates += 1
                continue
            seen_in_file.add(url)
            candidates.append(url)

        added = 0
        uploaded_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for url in candidates:
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO links (url, batch_name, uploaded_at_utc)
                        VALUES (?, ?, ?)
                        """,
                        (url, batch_name, uploaded_at),
                    )
                    if cursor.rowcount == 1:
                        added += 1
                    else:
                        duplicates += 1
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        return UploadResult(added=added, invalid=invalid, duplicates=duplicates)

    def claim_link(self, user_id: int, usage_date: str, daily_limit: int) -> ClaimResult:
        """Atomically claim one unassigned link after enforcing a per-day limit."""
        if daily_limit < 1:
            raise ValueError("daily_limit must be at least 1")

        assigned_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                usage_row = connection.execute(
                    """
                    SELECT issued_count
                    FROM daily_usage
                    WHERE user_id = ? AND usage_date = ?
                    """,
                    (user_id, usage_date),
                ).fetchone()
                used = int(usage_row["issued_count"]) if usage_row else 0

                if used >= daily_limit:
                    connection.rollback()
                    return ClaimResult("quota_reached", None, used, 0)

                link_row = connection.execute(
                    """
                    SELECT link_id, url
                    FROM links
                    WHERE assigned_to IS NULL
                    ORDER BY link_id ASC
                    LIMIT 1
                    """
                ).fetchone()

                if link_row is None:
                    connection.rollback()
                    return ClaimResult("inventory_empty", None, used, daily_limit - used)

                updated = connection.execute(
                    """
                    UPDATE links
                    SET assigned_to = ?, assigned_at_utc = ?
                    WHERE link_id = ? AND assigned_to IS NULL
                    """,
                    (user_id, assigned_at, int(link_row["link_id"])),
                )
                if updated.rowcount != 1:
                    connection.rollback()
                    return ClaimResult("retry", None, used, daily_limit - used)

                new_used = used + 1
                if usage_row:
                    connection.execute(
                        """
                        UPDATE daily_usage
                        SET issued_count = ?
                        WHERE user_id = ? AND usage_date = ?
                        """,
                        (new_used, user_id, usage_date),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO daily_usage (user_id, usage_date, issued_count)
                        VALUES (?, ?, ?)
                        """,
                        (user_id, usage_date, new_used),
                    )
                connection.commit()
                return ClaimResult("claimed", str(link_row["url"]), new_used, daily_limit - new_used)
            except Exception:
                connection.rollback()
                raise

    def usage(self, user_id: int, usage_date: str, daily_limit: int) -> tuple[int, int]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT issued_count
                FROM daily_usage
                WHERE user_id = ? AND usage_date = ?
                """,
                (user_id, usage_date),
            ).fetchone()
        used = int(row["issued_count"]) if row else 0
        return used, max(0, daily_limit - used)

    def stats(self) -> dict[str, int]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN assigned_to IS NULL THEN 1 ELSE 0 END) AS available,
                    SUM(CASE WHEN assigned_to IS NOT NULL THEN 1 ELSE 0 END) AS assigned
                FROM links
                """
            ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "available": int(row["available"] or 0),
            "assigned": int(row["assigned"] or 0),
        }
