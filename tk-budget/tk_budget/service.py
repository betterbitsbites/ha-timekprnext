"""BudgetService: sums usage per kid and week, owns settings and adjustments, publishes remaining.

Kids are learned from the agents' usage reports (any timekpr user on any PC) and persisted.
A kid is limited only while its `limit_active` switch is on; otherwise remaining says unlimited.
"""

from __future__ import annotations

import functools
import json
import logging
import threading
from collections.abc import Collection
from datetime import timedelta

from tk_protocol import topics
from tk_protocol.bus import Bus
from tk_protocol.clock import Clock
from tk_protocol.payloads import Action, Cmd, Remaining, Sessions, Settings, Usage

from . import discovery
from .engine import UsageLedger, effective, limit, week_key
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
    def __init__(self, bus: Bus, store: Store, clock: Clock, phone_kids: Collection[str] = ()) -> None:
        self._phone_kids = frozenset(phone_kids)
        self._bus = bus
        self._store = store
        self._clock = clock
        self._kids: list[str] = []
        self._ledger = UsageLedger()
        self._play_ledger = UsageLedger()
        self._sessions: dict[tuple[str, str], Sessions] = {}  # (host, kid) -> latest report
        self._offline: set[str] = set()  # hosts whose last status is "offline": their reports count 0
        self._last: dict[str, tuple[str, str]] = {}  # kid -> (remaining json, state json)
        self._lock = threading.RLock()

    @_locked
    def start(self) -> None:
        for kid, host, week, total_s in self._store.load_usage():
            self._ledger.restore(host, kid, week, total_s)
        for kid, host, week, play_s in self._store.load_play_usage():
            self._play_ledger.restore(host, kid, week, play_s)
        self._bus.publish(discovery.STATUS_TOPIC, "online", retain=True)
        for kid in self._store.kids():
            self._announce(kid)
        self._bus.subscribe(f"{topics.PREFIX}/+/+/usage", self._on_usage)
        self._bus.subscribe(f"{topics.PREFIX}/+/+/sessions", self._on_sessions)
        self._bus.subscribe(f"{topics.PREFIX}/+/status", self._on_status)
        self._bus.subscribe(topics.cmd("+"), self._on_cmd, retained=False)  # a button press, never a replay
        self._bus.subscribe(discovery.set_topic("+", "+"), self._on_set)
        self._bus.on_connect(self._on_reconnect)

    @_locked
    def _on_reconnect(self) -> None:
        # the broker may have fired our last will meanwhile, making every HA entity unavailable
        self._bus.publish(discovery.STATUS_TOPIC, "online", retain=True)

    def _announce(self, kid: str) -> None:
        if kid not in self._kids:
            self._kids.append(kid)
        for topic, payload in discovery.entities(kid):
            self._bus.publish(topic, json.dumps(payload, sort_keys=True), retain=True)
        if kid in self._phone_kids:
            for topic, payload in discovery.combined_entities(kid):
                self._bus.publish(topic, json.dumps(payload, sort_keys=True), retain=True)
        else:  # remove entities left over from when the kid had a Family Link sensor
            for topic in discovery.combined_topics(kid):
                self._bus.publish(topic, "", retain=True)
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

    @_locked
    def set_phone_minutes(self, kid: str, minutes: float) -> None:
        """Record today's phone screen time (Family Link, minutes over all devices)."""
        if kid not in self._phone_kids or kid not in self._kids:
            return
        now = self._clock.now()
        day = now.date().isoformat()
        yesterday = (now.date() - timedelta(days=1)).isoformat()
        s = int(round(minutes * 60))
        if "_familylink" not in self._store.daily_totals(kid, day):
            # The sensor resets at its first refresh after midnight; until then it still shows yesterday.
            prev = self._store.daily_totals(kid, yesterday).get("_familylink")
            if prev is not None and s >= prev and (now.hour, now.minute) < (0, 15):
                return
        self._store.record_daily(kid, day, "_familylink", s)
        self._publish(kid, force=False)

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
            if (self._store.get_setting(kid, discovery.LIMIT_KEY) is None
                    and self._store.get_setting(kid, "weekly_total_min") is not None):
                self._store.set_setting(kid, discovery.LIMIT_KEY, "ON")  # kid limited before 0.3.0
            self._announce(kid)
        self._ledger.update(host, kid, u.week, u.total_s, u.day, u.total_day_s)
        self._store.save_usage(kid, host, u.week, u.total_s)
        self._store.record_daily(kid, u.day, host, u.total_day_s)
        self._play_ledger.update(host, kid, u.week, u.play_s, u.day, u.play_day_s)
        self._store.save_play_usage(kid, host, u.week, u.play_s)
        self._store.record_play_daily(kid, u.day, host, u.play_day_s)
        self._publish(kid, force=False)

    @_locked
    def _on_sessions(self, topic: str, payload: str) -> None:
        parsed = topics.parse_sessions_topic(topic)
        if parsed is None:
            return
        host, kid = parsed
        if kid not in self._kids:
            return  # kids are learned from usage reports only
        try:
            self._sessions[(host, kid)] = Sessions.from_json(payload)
        except ValueError as e:
            log.warning("ignoring malformed sessions on %s: %s", topic, e)
            return
        self._publish(kid, force=False)

    @_locked
    def _on_status(self, topic: str, payload: str) -> None:
        # Retained replay order is random, so a stale sessions report may arrive after "offline":
        # reports are kept, but an offline host's count as 0 until it is online again.
        host = topics.parse_status_topic(topic)
        if host is None or payload not in ("online", "offline"):
            return
        if payload == "offline":
            self._offline.add(host)
        else:
            self._offline.discard(host)
        for kid in {k for h, k in self._sessions if h == host}:
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
            self._store.add_day_adjustment_s(kid, self._clock.now().date().isoformat(), c.minutes * 60)
        elif c.op == "adjust_play":
            self._store.add_play_adjustment_s(kid, self._week(), c.minutes * 60)
            self._store.add_play_day_adjustment_s(kid, self._clock.now().date().isoformat(), c.minutes * 60)
        elif c.op in ("end_inactive", "logout"):
            ts = self._clock.now().isoformat(timespec="seconds")
            self._bus.publish(topics.action(kid), Action(op=c.op, ts=ts).to_json(), retain=False)
            return
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
        elif key == "daily_total_min":
            try:
                minutes = int(float(payload))
            except ValueError:
                log.warning("ignoring daily_total_min %r", payload)
                return
            minutes = max(0, min(discovery.MAX_DAILY_MIN, minutes))
            self._store.set_setting(kid, key, str(minutes))
        elif key in ("weekly_play_min", "daily_play_min"):
            try:
                minutes = int(float(payload))
            except ValueError:
                log.warning("ignoring %s %r", key, payload)
                return
            top = discovery.MAX_WEEKLY_MIN if key == "weekly_play_min" else discovery.MAX_DAILY_MIN
            self._store.set_setting(kid, key, str(max(0, min(top, minutes))))
        elif key == "combined_total_min" and kid in self._phone_kids:
            try:
                minutes = int(float(payload))
            except ValueError:
                log.warning("ignoring combined_total_min %r", payload)
                return
            minutes = max(0, min(discovery.MAX_DAILY_MIN, minutes))
            self._store.set_setting(kid, key, str(minutes))
        elif key in (discovery.LIMIT_KEY, discovery.DAILY_KEY, discovery.PLAY_WEEK_KEY,
                     discovery.PLAY_DAY_KEY) or (
                key == discovery.COMBINED_KEY and kid in self._phone_kids):
            if payload not in ("ON", "OFF"):
                log.warning("ignoring %s %r", key, payload)
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
        day = today.isoformat()
        week = week_key(today)
        budget = self._budget_min(kid)
        daily_min = int(self._store.get_setting(kid, "daily_total_min", "0") or 0)  # unset = 0: fail closed
        week_adj = self._store.get_adjustment_s(kid, week)
        day_adj = self._store.get_day_adjustment_s(kid, day)
        week_on = self._limited(kid)
        day_on = self._store.get_setting(kid, discovery.DAILY_KEY) == "ON"
        totals = self._store.daily_totals(kid, day)
        today_s = sum(v for h, v in totals.items() if h != "_familylink")
        phone_s = totals.get("_familylink", 0) if kid in self._phone_kids else 0
        combined_min = int(self._store.get_setting(kid, "combined_total_min", "0") or 0)
        combined_on = (kid in self._phone_kids
                       and self._store.get_setting(kid, discovery.COMBINED_KEY) == "ON")
        week_lim = limit("week", week_on, budget, week_adj, self._ledger.used_s(kid, week))
        day_lim = limit("day", day_on, daily_min, day_adj, today_s)
        comb_lim = limit("combined", combined_on, combined_min, day_adj, today_s + phone_s)
        limited, eff = effective([week_lim, day_lim, comb_lim])
        rem = eff if limited else week_lim.remaining_s
        play_week_min = int(self._store.get_setting(kid, "weekly_play_min", "0") or 0)  # unset = 0: fail closed
        play_day_min = int(self._store.get_setting(kid, "daily_play_min", "0") or 0)
        play_week_on = self._store.get_setting(kid, discovery.PLAY_WEEK_KEY) == "ON"
        play_day_on = self._store.get_setting(kid, discovery.PLAY_DAY_KEY) == "ON"
        play_today_s = sum(self._store.play_daily_totals(kid, day).values())
        play_week_lim = limit("play_week", play_week_on, play_week_min,
                              self._store.get_play_adjustment_s(kid, week), self._play_ledger.used_s(kid, week))
        play_day_lim = limit("play_day", play_day_on, play_day_min,
                             self._store.get_play_day_adjustment_s(kid, day), play_today_s)
        play_limited, play_rem = effective([play_week_lim, play_day_lim])
        if play_limited and limited:
            play_rem = min(play_rem, eff)
        play_s = play_rem if play_limited else 0
        rem_json = Remaining(week=week, total_s=eff if limited else 0, limited=limited,
                         play_s=play_s, play_limited=play_limited).to_json()
        live = {h: v for (h, k), v in sorted(self._sessions.items()) if k == kid and h not in self._offline}
        by_host = {h: v.count for h, v in live.items() if v.count > 0}
        state_json = json.dumps({
            "sessions": sum(by_host.values()),
            "sessions_locked": sum(v.locked for v in live.values()),
            "sessions_by_host": by_host,
            "week": week,
            "day": day,
            "weekly_total_min": budget,
            "daily_total_min": daily_min,
            "limited": limited,
            "week_limited": week_on,
            "daily_limited": day_on,
            "adjustment_min": week_adj // 60,
            "remaining_s": rem,
            "combined_limited": combined_on,
            "combined_total_min": combined_min,
            "today_s": today_s,
            "phone_today_s": phone_s,
            "limits": {lim.key: {"on": lim.active, "allowance_s": lim.allowance_s,
                                 "remaining_s": lim.remaining_s, "pct": lim.pct}
                       for lim in (week_lim, day_lim, comb_lim, play_week_lim, play_day_lim)},
            "play_limited": play_limited,
            "play_week_limited": play_week_on,
            "play_daily_limited": play_day_on,
            "weekly_play_min": play_week_min,
            "daily_play_min": play_day_min,
            "play_today_s": play_today_s,
            "play_remaining_s": play_s,
            "time_up_action": self._time_up_action(kid),
        }, sort_keys=True)
        if not force and self._last.get(kid) == (rem_json, state_json):
            return
        self._last[kid] = (rem_json, state_json)
        self._bus.publish(topics.remaining(kid), rem_json, retain=True)
        self._bus.publish(discovery.state_topic(kid), state_json, retain=True)
