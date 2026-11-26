"""Usage meter for one timekpr user.

Usage is never taken from timekpr's spent counters (`--settimeleft` rewrites them). Instead
the meter keeps a baseline — the time left it last saw or wrote — and on each poll counts
`baseline − time_left_now` (never negative). The baseline and the cumulative week/day
counters are persisted atomically after every change, so a crash or restart neither loses
nor double-counts usage. A baseline from another day is discarded: timekpr resets time left
at midnight, so the delta across midnight is not usage.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile

from tk_protocol.clock import Clock, day_key, week_key

log = logging.getLogger(__name__)


class UsageMeter:
    def __init__(self, state_path: str, clock: Clock) -> None:
        self._path = state_path
        self._clock = clock
        self._s = self._load()

    # --- public ---------------------------------------------------------------

    def wrote(self, seconds: int) -> None:
        """Record the time left that is now in timekpr (read back after a write)."""
        self._roll()
        self._s["baseline"] = int(seconds)
        self._s["baseline_day"] = self._s["day"]
        self._save()

    def observe(self, time_left_s: int, count: bool = True) -> int:
        """Count usage since the last baseline; returns it (≥ 0). With `count=False` only the
        baseline moves to `time_left_s` (the drop is known not to be usage)."""
        self._roll()
        base = self._s["baseline"]
        used = 0
        if count and base is not None and self._s["baseline_day"] == self._s["day"]:
            used = max(0, base - int(time_left_s))
        self._s["week_total_s"] += used
        self._s["day_total_s"] += used
        self._s["baseline"] = int(time_left_s)
        self._s["baseline_day"] = self._s["day"]
        self._save()
        return used

    def week_total_s(self, week: str) -> int:
        self._roll()
        return self._s["week_total_s"] if week == self._s["week"] else 0

    def day_total_s(self, day: str) -> int:
        self._roll()
        return self._s["day_total_s"] if day == self._s["day"] else 0

    def current_week(self) -> str:
        self._roll()
        return self._s["week"]

    def current_day(self) -> str:
        self._roll()
        return self._s["day"]

    def seed(self, week: str, total_s: int, day: str, total_day_s: int) -> None:
        """Raise counters to values known elsewhere (e.g. our own retained usage after a lost state file)."""
        self._roll()
        changed = False
        if week == self._s["week"] and total_s > self._s["week_total_s"]:
            self._s["week_total_s"] = int(total_s)
            changed = True
        if day == self._s["day"] and total_day_s > self._s["day_total_s"]:
            self._s["day_total_s"] = int(total_day_s)
            changed = True
        if changed:
            self._save()

    # --- internals ------------------------------------------------------------

    def _roll(self) -> None:
        today = self._clock.now().date()
        if self._s["week"] != week_key(today):
            self._s["week"] = week_key(today)
            self._s["week_total_s"] = 0
        if self._s["day"] != day_key(today):
            self._s["day"] = day_key(today)
            self._s["day_total_s"] = 0

    def _fresh(self) -> dict:
        today = self._clock.now().date()
        return {"baseline": None, "baseline_day": None, "week": week_key(today), "week_total_s": 0,
                "day": day_key(today), "day_total_s": 0}

    def _load(self) -> dict:
        fresh = self._fresh()
        try:
            with open(self._path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return fresh
        except (OSError, ValueError) as e:
            log.error("meter state %s unreadable (%s); starting fresh", self._path, e)
            return fresh
        if not isinstance(data, dict) or set(fresh) - set(data):
            log.error("meter state %s malformed; starting fresh", self._path)
            return fresh
        return {k: data[k] for k in fresh}

    def _save(self) -> None:
        d = os.path.dirname(os.path.abspath(self._path))
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".meter-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._s, f, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
