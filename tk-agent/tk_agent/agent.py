"""The agent loop: measure usage, report it, enforce the shared remaining time.

Enforcement: tk-budget's `remaining` is a snapshot. Between snapshots — and during a broker
outage — the agent subtracts what this PC used since it received the snapshot, so a stale
value is never handed back to the kid again (fail closed). The last snapshot is persisted,
so a reboot without a broker keeps enforcing it.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Protocol

from tk_protocol import topics
from tk_protocol.bus import Bus
from tk_protocol.clock import Clock
from tk_protocol.payloads import Event, Remaining, Settings, Usage

from .config import Config
from .meter import UsageMeter
from .timekpra import LOCKOUT_TYPES

log = logging.getLogger(__name__)

MAX_TIME_LEFT_S = 24 * 3600
TIME_LOW_S = 300
WRITE_TOLERANCE_S = 60


class TimekpraLike(Protocol):
    def time_left_s(self, user: str) -> int: ...
    def set_time_left_s(self, user: str, seconds: int) -> None: ...
    def set_lockout_type(self, user: str, value: str) -> None: ...


@dataclass
class _Snapshot:
    week: str
    total_s: int
    receipt_week: str  # meter week when received
    receipt_usage_s: int  # meter week total when received


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    return wrapper


class Agent:
    def __init__(self, cfg: Config, bus: Bus, timekpra: TimekpraLike,
                 meter_factory: Callable[[str], UsageMeter], clock: Clock) -> None:
        self.cfg = cfg
        self._bus = bus
        self._tk = timekpra
        self._clock = clock
        self._lock = threading.RLock()
        self._kid_user = {kid: user for user, kid in cfg.kids.items()}
        self._meters = {user: meter_factory(user) for user in cfg.kids}
        self._snap: dict[str, _Snapshot | None] = {u: self._load_snapshot(u) for u in cfg.kids}
        self._action_wanted: dict[str, str | None] = {u: None for u in cfg.kids}
        self._action_applied: dict[str, str | None] = {u: None for u in cfg.kids}
        self._low_sent = {u: False for u in cfg.kids}
        self._up_sent = {u: False for u in cfg.kids}

    @_locked
    def start(self) -> None:
        host = self.cfg.host_id
        self._bus.publish(topics.status(host), "online", retain=True)
        self._bus.publish(f"homeassistant/binary_sensor/tk_{host}/online/config",
                          json.dumps(_online_discovery(host), sort_keys=True), retain=True)
        for user, kid in self.cfg.kids.items():
            self._bus.subscribe(topics.usage(host, kid), self._on_own_usage)
            self._bus.subscribe(topics.remaining(kid), self._on_remaining)
            self._bus.subscribe(topics.settings(kid), self._on_settings)

    @_locked
    def tick(self) -> None:
        for user in self.cfg.kids:
            try:
                self._tick_user(user)
            except Exception:
                log.exception("tick failed for %s", user)

    # --- per user -------------------------------------------------------------

    def _tick_user(self, user: str) -> None:
        kid = self.cfg.kids[user]
        meter = self._meters[user]
        left = self._tk.time_left_s(user)
        meter.observe(left)
        week, day = meter.current_week(), meter.current_day()
        u = Usage(week=week, total_s=meter.week_total_s(week), day=day, total_day_s=meter.day_total_s(day))
        self._bus.publish(topics.usage(self.cfg.host_id, kid), u.to_json(), retain=True)

        wanted = self._action_wanted[user]
        if wanted is not None and wanted != self._action_applied[user]:
            self._tk.set_lockout_type(user, wanted)
            self._action_applied[user] = wanted

        snap = self._snap[user]
        if snap is None:
            return  # nothing known yet: leave timekpr untouched
        since = u.total_s - snap.receipt_usage_s if snap.receipt_week == week else u.total_s
        target = min(max(0, snap.total_s - max(0, since)), MAX_TIME_LEFT_S)
        expected = min(target, self._seconds_to_midnight())  # timekpr caps time left at midnight
        if abs(expected - left) >= WRITE_TOLERANCE_S or (target == 0 and left > 0):
            self._tk.set_time_left_s(user, target)
            meter.wrote(self._tk.time_left_s(user))  # read back: exit codes prove nothing
        self._events(user, kid, target)

    def _events(self, user: str, kid: str, target: int) -> None:
        if target > TIME_LOW_S:
            self._low_sent[user] = False
        if target > 0:
            self._up_sent[user] = False
        if 0 < target <= TIME_LOW_S and not self._low_sent[user]:
            self._low_sent[user] = True
            self._event(kid, "time_low")
        if target == 0 and not self._up_sent[user]:
            self._up_sent[user] = True
            self._event(kid, "time_up")

    def _event(self, kid: str, kind: str) -> None:
        e = Event(type=kind, ts=self._clock.now().isoformat(timespec="seconds"))  # type: ignore[arg-type]
        self._bus.publish(topics.event(self.cfg.host_id, kid), e.to_json())

    def _seconds_to_midnight(self) -> int:
        now = self._clock.now()
        midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
        return int((midnight - now).total_seconds())

    # --- handlers -------------------------------------------------------------

    @_locked
    def _on_remaining(self, topic: str, payload: str) -> None:
        user = self._kid_user.get(topic.split("/")[1])
        if user is None:
            return
        try:
            r = Remaining.from_json(payload)
        except ValueError as e:
            log.warning("ignoring malformed remaining on %s: %s", topic, e)
            return
        old = self._snap[user]
        if old is not None and (old.week, old.total_s) == (r.week, r.total_s):
            return  # re-delivered retained value (reconnect): keep the original receipt point
        meter = self._meters[user]
        week = meter.current_week()
        snap = _Snapshot(r.week, r.total_s, week, meter.week_total_s(week))
        self._snap[user] = snap
        self._save_snapshot(user, snap)

    @_locked
    def _on_settings(self, topic: str, payload: str) -> None:
        user = self._kid_user.get(topic.split("/")[1])
        if user is None:
            return
        try:
            s = Settings.from_json(payload)
        except ValueError as e:
            log.warning("ignoring malformed settings on %s: %s", topic, e)
            return
        if s.time_up_action not in LOCKOUT_TYPES:
            log.warning("ignoring unsupported time_up_action %r", s.time_up_action)
            return
        self._action_wanted[user] = s.time_up_action

    @_locked
    def _on_own_usage(self, topic: str, payload: str) -> None:
        parsed = topics.parse_usage_topic(topic)
        if parsed is None or parsed[0] != self.cfg.host_id:
            return
        user = self._kid_user.get(parsed[1])
        if user is None:
            return
        try:
            u = Usage.from_json(payload)
        except ValueError:
            return
        self._meters[user].seed(u.week, u.total_s, u.day, u.total_day_s)

    # --- snapshot persistence ---------------------------------------------------

    def _snapshot_path(self, user: str) -> str:
        return os.path.join(self.cfg.state_dir, f"{user}.remaining.json")

    def _load_snapshot(self, user: str) -> _Snapshot | None:
        try:
            with open(self._snapshot_path(user), encoding="utf-8") as f:
                d = json.load(f)
            return _Snapshot(str(d["week"]), int(d["total_s"]), str(d["receipt_week"]), int(d["receipt_usage_s"]))
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as e:
            log.error("remaining snapshot for %s unreadable (%s); ignoring", user, e)
            return None

    def _save_snapshot(self, user: str, snap: _Snapshot) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.cfg.state_dir, prefix=".remaining-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(snap.__dict__, f, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._snapshot_path(user))
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise


def _online_discovery(host: str) -> dict:
    return {
        "name": f"{host} online",
        "unique_id": f"tk_{host}_online",
        "object_id": f"tk_{host}_online",
        "state_topic": topics.status(host),
        "payload_on": "online",
        "payload_off": "offline",
        "device_class": "connectivity",
        "device": {"identifiers": [f"tk_agent_{host}"], "name": f"PC {host}",
                   "manufacturer": "ha-timekprnext", "model": "tk-agent"},
    }
