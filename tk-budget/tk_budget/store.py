"""SQLite store for settings, weekly adjustments, usage and daily statistics.

Counters are cumulative, so writes only ever raise a stored value; a stale or
resent report can never lower or rewrite statistics.
"""

from __future__ import annotations

import sqlite3
import threading

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    kid TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
    PRIMARY KEY (kid, key));
CREATE TABLE IF NOT EXISTS adjustments (
    kid TEXT NOT NULL, week TEXT NOT NULL, adjustment_s INTEGER NOT NULL,
    PRIMARY KEY (kid, week));
CREATE TABLE IF NOT EXISTS usage (
    kid TEXT NOT NULL, host TEXT NOT NULL, week TEXT NOT NULL, total_s INTEGER NOT NULL,
    PRIMARY KEY (kid, host, week));
CREATE TABLE IF NOT EXISTS daily (
    kid TEXT NOT NULL, day TEXT NOT NULL, host TEXT NOT NULL, total_s INTEGER NOT NULL,
    PRIMARY KEY (kid, day, host));
"""


class Store:
    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def get_setting(self, kid: str, key: str, default: str | None = None) -> str | None:
        row = self._one("SELECT value FROM settings WHERE kid=? AND key=?", (kid, key))
        return row[0] if row else default

    def set_setting(self, kid: str, key: str, value: str) -> None:
        self._exec("INSERT INTO settings (kid, key, value) VALUES (?, ?, ?) "
                   "ON CONFLICT (kid, key) DO UPDATE SET value=excluded.value", (kid, key, value))

    def get_adjustment_s(self, kid: str, week: str) -> int:
        row = self._one("SELECT adjustment_s FROM adjustments WHERE kid=? AND week=?", (kid, week))
        return row[0] if row else 0

    def add_adjustment_s(self, kid: str, week: str, delta_s: int) -> int:
        self._exec("INSERT INTO adjustments (kid, week, adjustment_s) VALUES (?, ?, ?) "
                   "ON CONFLICT (kid, week) DO UPDATE SET adjustment_s=adjustment_s+excluded.adjustment_s",
                   (kid, week, delta_s))
        return self.get_adjustment_s(kid, week)

    def record_daily(self, kid: str, day: str, host: str, total_s: int) -> None:
        self._exec("INSERT INTO daily (kid, day, host, total_s) VALUES (?, ?, ?, ?) "
                   "ON CONFLICT (kid, day, host) DO UPDATE SET total_s=MAX(total_s, excluded.total_s)",
                   (kid, day, host, total_s))

    def daily_totals(self, kid: str, day: str) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute("SELECT host, total_s FROM daily WHERE kid=? AND day=?",
                                    (kid, day)).fetchall()
        return dict(rows)

    def save_usage(self, kid: str, host: str, week: str, total_s: int) -> None:
        self._exec("INSERT INTO usage (kid, host, week, total_s) VALUES (?, ?, ?, ?) "
                   "ON CONFLICT (kid, host, week) DO UPDATE SET total_s=MAX(total_s, excluded.total_s)",
                   (kid, host, week, total_s))

    def load_usage(self) -> list[tuple[str, str, str, int]]:
        """All stored (kid, host, week, total_s) rows."""
        with self._lock:
            rows = self._db.execute("SELECT kid, host, week, total_s FROM usage ORDER BY kid, host, week").fetchall()
        return [tuple(r) for r in rows]  # type: ignore[misc]

    def _exec(self, sql: str, args: tuple) -> None:
        with self._lock:
            self._db.execute(sql, args)

    def _one(self, sql: str, args: tuple) -> tuple | None:
        with self._lock:
            return self._db.execute(sql, args).fetchone()
