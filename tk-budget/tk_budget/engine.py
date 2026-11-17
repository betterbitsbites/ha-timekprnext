"""Pure budget logic: week keys, remaining time, and the usage ledger. No I/O."""

from __future__ import annotations

from tk_protocol.clock import week_key

__all__ = ["UsageLedger", "remaining_total_s", "week_key"]


def remaining_total_s(weekly_budget_min: int, adjustment_s: int, used_s: int) -> int:
    return max(0, weekly_budget_min * 60 + adjustment_s - used_s)


class UsageLedger:
    """Cumulative usage per host/kid/week and host/kid/day.

    Reports are cumulative, so the ledger keeps the maximum seen per key: resent
    or stale (lower) values never decrease a total and are never added twice.
    """

    def __init__(self) -> None:
        self._week: dict[tuple[str, str, str], int] = {}  # (host, kid, week) -> s
        self._day: dict[tuple[str, str, str], int] = {}  # (host, kid, day) -> s

    def update(self, host: str, kid: str, week: str, total_s: int, day: str, total_day_s: int) -> bool:
        changed = _raise_to(self._week, (host, kid, week), total_s)
        changed = _raise_to(self._day, (host, kid, day), total_day_s) or changed
        return changed

    def restore(self, host: str, kid: str, week: str, total_s: int) -> None:
        """Load a persisted weekly total (after a restart); day totals live in the store."""
        _raise_to(self._week, (host, kid, week), total_s)

    def used_s(self, kid: str, week: str) -> int:
        return sum(v for (_, k, w), v in self._week.items() if k == kid and w == week)

    def day_used_s(self, kid: str, day: str) -> dict[str, int]:
        return {h: v for (h, k, d), v in self._day.items() if k == kid and d == day}


def _raise_to(table: dict[tuple[str, str, str], int], key: tuple[str, str, str], value: int) -> bool:
    if value > table.get(key, -1):
        table[key] = value
        return True
    return False
