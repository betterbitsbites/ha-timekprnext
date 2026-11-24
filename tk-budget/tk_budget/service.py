"""BudgetService: sums usage per kid and week, owns settings and adjustments, publishes remaining.

Kids are learned from the agents' usage reports (any timekpr user on any PC) and persisted.
A kid is limited only while its `limit_active` switch is on; otherwise remaining says unlimited.
"""

from __future__ import annotations

import functools
import json
import logging
import threading

from tk_protocol import topics
from tk_protocol.bus import Bus
from tk_protocol.clock import Clock
from tk_protocol.payloads import Cmd, Remaining, Settings, Usage

from . import discovery
from .engine import UsageLedger, remaining_total_s, week_key
from .store import Store

log = logging.getLogger(__name__)


def _locked(fn):
    """Serialize entry points: MQTT callbacks run on paho's thread, tick() on the main thread."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    return wrapper


class BudgetService:
    def __init__(self, bus: Bus, store: Store, clock: Clock) -> None:
        self._bus = bus
        self._store = store
        self._clock = clock
        self._kids: list[str] = []
        self._ledger = UsageLedger()
        self._last: dict[str, tuple[str, str]] = {}  # kid -> (remaining json, state json)
        self._lock = threading.RLock()

    @_locked
    def start(self) -> None:
        for kid, host, week, total_s in self._store.load_usage():
            self._ledger.restore(host, kid, week, total_s)
        self._bus.publish(discovery.STATUS_TOPIC, "online", retain=True)
        for kid in self._store.kids():
            self._announce(kid)
        self._bus.subscribe(f"{topics.PREFIX}/+/+/usage", self._on_usage)
        self._bus.subscribe(topics.cmd("+"), self._on_cmd)
        self._bus.subscribe(discovery.set_topic("+", "+"), self._on_set)

    def _announce(self, kid: str) -> None:
        if kid not in self._kids:
            self._kids.append(kid)
        for topic, payload in discovery.entities(kid):
            self._bus.publish(topic, json.dumps(payload, sort_keys=True), retain=True)
        self._publish_settings(kid)
        self.republish(kid)

    @_locked
    def tick(self) -> None:
        """Call periodically: picks up week and day changes without new messages."""
        for kid in self._kids:
            self._publish(kid, force=False)

    @_locked
    def republish(self, kid: str) -> None:
        self._publish(kid, force=True)

    # --- handlers -------------------------------------------------------------

    @_locked
    def _on_usage(self, topic: str, payload: str) -> None:
        parsed = topics.parse_usage_topic(topic)
        if parsed is None or not discovery.valid_kid(parsed[1]):
            return
        host, kid = parsed
        try:
            u = Usage.from_json(payload)
        except ValueError as e:
            log.warning("ignoring malformed usage on %s: %s", topic, e)
            return
        if u.week != self._week():
            log.info("ignoring usage for %s on %s (current week %s)", u.week, topic, self._week())
            return
        if kid not in self._kids:
            log.info("new kid %s (first reported by %s)", kid, host)
            self._store.add_kid(kid)
            self._announce(kid)
        self._ledger.update(host, kid, u.week, u.total_s, u.day, u.total_day_s)
        self._store.save_usage(kid, host, u.week, u.total_s)
        self._store.record_daily(kid, u.day, host, u.total_day_s)
        self._publish(kid, force=False)

    @_locked
    def _on_cmd(self, topic: str, payload: str) -> None:
        kid = topic.split("/")[1]
        if kid not in self._kids:
            return
        try:
            c = Cmd.from_json(payload)
        except ValueError as e:
            log.warning("ignoring malformed cmd on %s: %s", topic, e)
            return
        if c.op == "adjust_total":
            self._store.add_adjustment_s(kid, self._week(), c.minutes * 60)
        self._publish(kid, force=False)

    @_locked
    def _on_set(self, topic: str, payload: str) -> None:
        parts = topic.split("/")
        kid, key = parts[1], parts[3]
        if kid not in self._kids:
            return
        if key == "weekly_total_min":
            try:
                minutes = int(float(payload))
            except ValueError:
                log.warning("ignoring weekly_total_min %r", payload)
                return
            minutes = max(0, min(discovery.MAX_WEEKLY_MIN, minutes))
            self._store.set_setting(kid, key, str(minutes))
        elif key == "time_up_action":
            if payload not in discovery.TIME_UP_ACTIONS:
                log.warning("ignoring time_up_action %r", payload)
                return
            self._store.set_setting(kid, key, payload)
            self._publish_settings(kid)
        elif key == discovery.LIMIT_KEY:
            if payload not in ("ON", "OFF"):
                log.warning("ignoring limit_active %r", payload)
                return
            self._store.set_setting(kid, key, payload)
        else:
            return
        self._publish(kid, force=False)

    # --- publishing -----------------------------------------------------------

    def _week(self) -> str:
        return week_key(self._clock.now().date())

    def _budget_min(self, kid: str) -> int:
        # Unset budget means 0 while the limit is on: fail closed.
        return int(self._store.get_setting(kid, "weekly_total_min", "0") or 0)

    def _limited(self, kid: str) -> bool:
        return self._store.get_setting(kid, discovery.LIMIT_KEY) == "ON"

    def _time_up_action(self, kid: str) -> str:
        return self._store.get_setting(kid, "time_up_action") or discovery.DEFAULT_TIME_UP_ACTION

    def _publish_settings(self, kid: str) -> None:
        s = Settings(time_up_action=self._time_up_action(kid))
        self._bus.publish(topics.settings(kid), s.to_json(), retain=True)

    def _publish(self, kid: str, force: bool) -> None:
        today = self._clock.now().date()
        week = week_key(today)
        budget = self._budget_min(kid)
        adjustment = self._store.get_adjustment_s(kid, week)
        rem = remaining_total_s(budget, adjustment, self._ledger.used_s(kid, week))
        limited = self._limited(kid)
        rem_json = Remaining(week=week, total_s=rem if limited else 0, limited=limited).to_json()
        state_json = json.dumps({
            "week": week,
            "day": today.isoformat(),
            "weekly_total_min": budget,
            "limited": limited,
            "adjustment_min": adjustment // 60,
            "remaining_s": rem,
            "today_s": sum(self._store.daily_totals(kid, today.isoformat()).values()),
            "time_up_action": self._time_up_action(kid),
        }, sort_keys=True)
        if not force and self._last.get(kid) == (rem_json, state_json):
            return
        self._last[kid] = (rem_json, state_json)
        self._bus.publish(topics.remaining(kid), rem_json, retain=True)
        self._bus.publish(discovery.state_topic(kid), state_json, retain=True)
