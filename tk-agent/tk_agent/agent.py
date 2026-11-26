"""The agent loop: measure usage, report it, enforce the shared remaining time.

Enforcement: tk-budget's `remaining` is a snapshot. Between snapshots — and during a broker
outage — the agent subtracts what this PC used since it received the snapshot, so a stale
value is never handed back to the kid again (fail closed). The last snapshot is persisted,
so a reboot without a broker keeps enforcing it.

Users: every timekpr user (`timekpra --userlist`) is a kid with the same id. A kid is enforced
only while tk-budget says `limited`; unlimited kids are measured but never written to, except
once on the change limited -> unlimited, which hands back a full day so a locked kid is free.

Play time (timekpr's PlayTime) is a second, independent part of the same snapshot: its own
meter (`<user>.play`), its own receipt point and its own release. timekpr shows play left
capped by the total time left, so the play target is compared against that cap, and a drop
in play left counts as play only while timekpr reports a running PlayTime activity. If a play
write does not stick (PlayTime off in timekpr), play is left alone until the next day, a
change of play limited/unlimited, or 15 minutes later (so PlayTime switched on later is picked
up); a changed play value alone does not retry, since tk-budget caps play by total and so
changes it with every usage report. Play usage is still measured and reported.

Apply feedback: a `remaining` with a new revision (parent input) or changed `settings` sets
`wake`, so the main loop ticks at once instead of waiting for the interval (handlers only set it;
ticks stay on the main thread). A remaining that changed only through usage reports (same rev)
does not wake: that would tick, report usage and be answered again at once, in a loop. Both
carry tk-budget's revision `rev`; after a tick that finished for a logged-in user, the agent acks
the highest revision it holds for that user with a retained `Applied`. Nothing is acked before a remaining snapshot exists; after that
even rev 0 is acked once (nothing acked yet counts as -1), and any change of rev, also a lower one.
A new rev with unchanged values counts as a change (wake, ack) but keeps the receipt points.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Protocol, TypeVar

from tk_protocol import topics
from tk_protocol.bus import Bus
from tk_protocol.clock import Clock
from tk_protocol.payloads import Action, Applied, Event, Remaining, Settings, Sessions, Usage

from .config import Config
from .meter import UsageMeter
from .sessions import GRAPHICAL, Session, connect_url, inactive, primary
from .timekpra import LOCKOUT_TYPES

log = logging.getLogger(__name__)

MAX_TIME_LEFT_S = 24 * 3600
TIME_LOW_S = 300
WRITE_TOLERANCE_S = 60
ACTION_MAX_AGE_S = 120
PLAY_OFF_RETRY = timedelta(minutes=15)
_ID_RE = re.compile(r"^[a-z0-9_-]+$")
_T = TypeVar("_T")


class TimekpraLike(Protocol):
    def users(self) -> list[str]: ...
    def time_left_s(self, user: str) -> int | None: ...
    def set_time_left_s(self, user: str, seconds: int) -> None: ...
    def set_lockout_type(self, user: str, value: str) -> None: ...
    def play_left_s(self, user: str) -> int | None: ...
    def play_rt(self, user: str) -> tuple[int, int] | None: ...
    def set_play_left_s(self, user: str, seconds: int) -> None: ...


class SessionsLike(Protocol):
    def sessions(self, user: str) -> list[Session]: ...
    def terminate_session(self, session_id: str) -> None: ...
    def terminate_user(self, user: str) -> None: ...


@dataclass
class _Snapshot:
    week: str
    total_s: int
    receipt_week: str  # meter week when received
    receipt_usage_s: int  # meter week total when received
    limited: bool = True
    released: bool = True  # unlimited only: the one release write is done (or was never needed)
    play_s: int = 0
    play_limited: bool = False
    play_receipt_week: str = ""  # play meter week when the play part was received
    play_receipt_usage_s: int = 0  # play meter week total when the play part was received
    play_released: bool = True  # play unlimited only: like `released`
    rev: int = 0  # tk-budget's revision of the last received remaining


def _same_part(old_limited: bool, new_limited: bool, old_key: tuple, new_key: tuple) -> bool:
    """A snapshot part is unchanged if both are unlimited, or both limited with the same (week, seconds)."""
    return old_limited == new_limited and (not new_limited or old_key == new_key)


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    return wrapper


class Agent:
    def __init__(self, cfg: Config, bus: Bus, timekpra: TimekpraLike,
                 meter_factory: Callable[[str], UsageMeter], clock: Clock,
                 sessions: SessionsLike | None = None) -> None:
        self.cfg = cfg
        self._bus = bus
        self._tk = timekpra
        self._clock = clock
        self._sessions = sessions
        self._sess_sent: dict[str, Sessions] = {}  # last published per user (empty after start)
        self._sess_warned: set[str] = set()
        self._lock = threading.RLock()
        self._meter_factory = meter_factory
        self._users: list[str] = []  # last good timekpr user list (valid names only)
        self._warned: set[str] = set()
        # per-user state, created when a user first appears in timekpr's list
        self._meters: dict[str, UsageMeter] = {}
        self._play_meters: dict[str, UsageMeter] = {}
        # (day, when) a play write did not stick; None = fine. Warned once per episode (day / play_limited).
        self._play_off: dict[str, tuple[str, datetime] | None] = {}
        self._play_off_warned: set[str] = set()
        self._play_warned: set[str] = set()  # play_left_s failed (warned once)
        self._snap: dict[str, _Snapshot | None] = {}
        self._action_wanted: dict[str, str | None] = {}
        self._action_applied: dict[str, str | None] = {}
        self._low_sent: dict[str, bool] = {}
        self._up_sent: dict[str, bool] = {}
        # last message per kid not on this PC (yet): replayed when the user appears
        self._pending: dict[tuple[str, str], tuple[str, str]] = {}
        self._settings_rev: dict[str, int] = {}  # rev of the last accepted settings (in memory)
        self._acked: dict[str, int] = {}  # last rev published as Applied (in memory)
        self.wake = threading.Event()  # set by handlers when remaining/settings changed

    @_locked
    def start(self) -> None:
        host = self.cfg.host_id
        self._bus.publish(topics.status(host), "online", retain=True)
        self._bus.subscribe(topics.usage(host, "+"), self._on_own_usage)
        self._bus.subscribe(topics.remaining("+"), self._on_remaining)
        self._bus.subscribe(topics.settings("+"), self._on_settings)
        if self._sessions is not None:
            self._bus.subscribe(topics.action("+"), self._on_action, retained=False)
        self._bus.on_connect(self._on_reconnect)

    @_locked
    def _on_reconnect(self) -> None:
        """After a reconnect: the broker may have fired the last will, and missed sessions reports."""
        self._bus.publish(topics.status(self.cfg.host_id), "online", retain=True)
        self._sess_sent.clear()  # the next tick republishes sessions
        self._acked = dict.fromkeys(self._acked, -1)  # ... and acks, in case one was lost

    @_locked
    def tick(self) -> None:
        self._refresh_users()
        for user in self._users:
            try:
                logged_in = self._tick_user(user)
            except Exception:
                log.exception("tick failed for %s", user)
                continue
            if logged_in:
                self._ack(user)

    def _ack(self, user: str) -> None:
        snap = self._snap[user]
        if snap is None:
            return  # never ack a remaining not seen yet
        want = max(snap.rev, self._settings_rev[user])
        if want != self._acked[user]:  # != also covers a budget whose rev went backwards
            self._bus.publish(topics.applied(self.cfg.host_id, user), Applied(rev=want).to_json(), retain=True)
            self._acked[user] = want

    def _refresh_users(self) -> None:
        try:
            listed = self._tk.users()
        except Exception as e:
            log.error("cannot list timekpr users (%s); keeping %s", e, self._users)
            return
        users = []
        for user in listed:
            if not _ID_RE.match(user) or user == self.cfg.host_id:
                if user not in self._warned:
                    self._warned.add(user)
                    log.warning("skipping timekpr user %r: needs [a-z0-9_-]+ and must differ from host id %r",
                                user, self.cfg.host_id)
                continue
            users.append(user)
            if user not in self._meters:
                self._add_user(user)
        self._users = users

    def _add_user(self, user: str) -> None:
        self._meters[user] = self._meter_factory(user)
        self._play_meters[user] = self._meter_factory(f"{user}.play")
        self._play_off[user] = None
        self._snap[user] = self._load_snapshot(user)
        self._action_wanted[user] = self._action_applied[user] = None
        self._low_sent[user] = self._up_sent[user] = False
        self._settings_rev[user] = 0
        self._acked[user] = -1
        for kind, handler in (("usage", self._on_own_usage), ("remaining", self._on_remaining),
                              ("settings", self._on_settings)):
            msg = self._pending.pop((kind, user), None)
            if msg is not None:
                handler(*msg)

    def _known(self, kind: str, kid: str, topic: str, payload: str) -> bool:
        """True if `kid` is a user here; otherwise keep the message for when it appears."""
        if kid in self._meters:
            return True
        if _ID_RE.match(kid):
            self._pending[(kind, kid)] = (topic, payload)
        return False

    # --- per user -------------------------------------------------------------

    def _tick_user(self, user: str) -> bool:
        """One tick for `user`; returns whether the user is logged in (timekpr has real-time info)."""
        kid = user
        meter, pmeter = self._meters[user], self._play_meters[user]
        left = self._tk.time_left_s(user)
        play_left = None
        if left is not None:
            meter.observe(left)
            rt = self._read_play(user, self._tk.play_rt)
            if rt is not None:
                # play left also falls with the total cap: it is play only while an activity runs
                play_left, activities = rt
                pmeter.observe(play_left, count=activities > 0)
        week, day = meter.current_week(), meter.current_day()
        pweek, pday = pmeter.current_week(), pmeter.current_day()
        u = Usage(week=week, total_s=meter.week_total_s(week), day=day, total_day_s=meter.day_total_s(day),
                  play_s=pmeter.week_total_s(pweek), play_day_s=pmeter.day_total_s(pday))
        self._bus.publish(topics.usage(self.cfg.host_id, kid), u.to_json(), retain=True)
        self._report_sessions(user)
        if left is None:
            return False  # not logged in: uses no time, and timekpr ignores writes until login

        snap = self._snap[user]
        if snap is None:
            return True  # nothing known yet: leave timekpr untouched
        cap = self._enforce_total(user, kid, snap, left, u)
        if play_left is not None:
            self._enforce_play(user, snap, play_left, cap)
        return True

    def _enforce_total(self, user: str, kid: str, snap: _Snapshot, left: int, u: Usage) -> int:
        """Enforce the total part; returns the total time left expected in timekpr after this tick."""
        if not snap.limited:
            if not snap.released:
                self._tk.set_time_left_s(user, MAX_TIME_LEFT_S)  # limit lifted: free a locked kid once
                back = self._wrote(user)
                snap.released = True
                self._save_snapshot(user, snap)
                return left if back is None else back
            return left
        wanted = self._action_wanted[user]  # timekpr settings are only touched while limited
        if wanted is not None and wanted != self._action_applied[user]:
            self._tk.set_lockout_type(user, wanted)
            self._action_applied[user] = wanted
        since = u.total_s - snap.receipt_usage_s if snap.receipt_week == u.week else u.total_s
        target = min(max(0, snap.total_s - max(0, since)), MAX_TIME_LEFT_S)
        expected = min(target, self._seconds_to_midnight())  # timekpr caps time left at midnight
        cap = left
        if abs(expected - left) >= WRITE_TOLERANCE_S or (target == 0 and left > 0):
            self._tk.set_time_left_s(user, target)
            back = self._wrote(user)
            cap = expected if back is None else back
        self._events(user, kid, target)
        return cap

    def _enforce_play(self, user: str, snap: _Snapshot, play_left: int, cap: int) -> None:
        """Enforce the play part; `cap` is the total time left timekpr caps play left with."""
        pmeter = self._play_meters[user]
        if not snap.play_limited:
            if not snap.play_released:
                self._tk.set_play_left_s(user, MAX_TIME_LEFT_S)  # play limit lifted: once, even if PlayTime is off
                self._play_wrote(user)
                snap.play_released = True
                self._save_snapshot(user, snap)
            return
        today = pmeter.current_day()
        now = self._clock.now()
        off = self._play_off[user]
        if off is not None:
            if off[0] == today and now - off[1] < PLAY_OFF_RETRY:
                return  # a play write did not stick: leave PlayTime alone for now
            if off[0] != today:
                self._play_off_warned.discard(user)  # new day: a new episode
            self._play_off[user] = None
        week = pmeter.current_week()
        used = pmeter.week_total_s(week)
        since = used - snap.play_receipt_usage_s if snap.play_receipt_week == week else used
        target = min(max(0, snap.play_s - max(0, since)), MAX_TIME_LEFT_S)
        expected = min(target, cap)
        if abs(expected - play_left) >= WRITE_TOLERANCE_S or (target == 0 and play_left > 0):
            self._tk.set_play_left_s(user, target)
            back = self._play_wrote(user)
            if back is not None and abs(back - expected) >= WRITE_TOLERANCE_S:
                self._play_off[user] = (today, now)
                if user in self._play_off_warned:
                    log.info("PlayTime still off in timekpr for %s (read back %d s)", user, back)
                else:
                    self._play_off_warned.add(user)
                    log.warning("PlayTime seems off in timekpr for %s: wrote %d s play left, read back %d s; "
                                "not enforcing play, retrying every %d min", user, target, back,
                                int(PLAY_OFF_RETRY.total_seconds() // 60))

    def _read_play(self, user: str, read: Callable[[str], _T]) -> _T | None:
        try:
            value = read(user)
        except Exception as e:
            if user not in self._play_warned:
                self._play_warned.add(user)
                log.warning("cannot read play time left for %s (%s); not measuring play", user, e)
            return None
        self._play_warned.discard(user)
        return value

    def _play_wrote(self, user: str) -> int | None:
        back = self._read_play(user, self._tk.play_left_s)  # read back: exit codes prove nothing
        if back is not None:
            self._play_meters[user].wrote(back)
        return back

    def _report_sessions(self, user: str) -> None:
        if self._sessions is None:
            return
        try:
            found = self._sessions.sessions(user)
        except Exception as e:
            if user not in self._sess_warned:
                self._sess_warned.add(user)
                log.warning("loginctl failed for %s (%s); not reporting sessions", user, e)
            return
        self._sess_warned.discard(user)
        live = [s for s in found if s.state != "closing"]  # closing: already ending, not counted
        p = primary(live)
        if p is None:
            state, kind = "none", "none"
        else:
            state = ("locked" if p.locked else "active" if p.state == "active"
                     else "away" if p.state == "online" else "none")
            kind = p.type if p.type in GRAPHICAL else "tty"
        report = Sessions(count=len(live), locked=sum(1 for s in live if s.locked), state=state,  # type: ignore[arg-type]
                          kind=kind, connect=connect_url(self.cfg, user, p))  # type: ignore[arg-type]
        if self._sess_sent.get(user) != report:
            self._bus.publish(topics.sessions(self.cfg.host_id, user), report.to_json(), retain=True)
            self._sess_sent[user] = report

    def _wrote(self, user: str) -> int | None:
        back = self._tk.time_left_s(user)  # read back: exit codes prove nothing
        if back is not None:  # None: logged out in between; the next login tick observes afresh
            self._meters[user].wrote(back)
        return back

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
        user = topic.split("/")[1]
        if not self._known("remaining", user, topic, payload):
            return
        try:
            r = Remaining.from_json(payload)
        except ValueError as e:
            log.warning("ignoring malformed remaining on %s: %s", topic, e)
            return
        old = self._snap[user]
        snap = _Snapshot(**old.__dict__) if old is not None else _Snapshot(r.week, 0, "", 0)
        snap.week = r.week
        if old is None or not _same_part(old.limited, r.limited, (old.week, old.total_s), (r.week, r.total_s)):
            # new total part (a re-delivered retained value keeps the original receipt point)
            meter = self._meters[user]
            snap.receipt_week = meter.current_week()
            snap.receipt_usage_s = meter.week_total_s(snap.receipt_week)
            snap.limited = r.limited
            snap.total_s = r.total_s if r.limited else 0
            # release only if this PC enforced a limit before; never touch an always-unlimited user
            snap.released = r.limited or old is None or not old.limited
        if old is None or not _same_part(old.play_limited, r.play_limited, (old.week, old.play_s),
                                         (r.week, r.play_s)):
            pmeter = self._play_meters[user]
            snap.play_receipt_week = pmeter.current_week()
            snap.play_receipt_usage_s = pmeter.week_total_s(snap.play_receipt_week)
            snap.play_limited = r.play_limited
            snap.play_s = r.play_s if r.play_limited else 0
            snap.play_released = r.play_limited or old is None or not old.play_limited
            if old is None or old.play_limited != r.play_limited:
                self._play_off[user] = None  # play limited/unlimited changed: try enforcing play again
                self._play_off_warned.discard(user)
        snap.rev = r.rev  # a new rev alone moves no receipt point, but is acked
        if snap != old:
            self._snap[user] = snap
            self._save_snapshot(user, snap)
            if old is None or snap.rev != old.rev:
                # parent input (new rev) or first snapshot: apply now. Not on usage-driven
                # republishes (same rev), which would tick, report usage and wake again in a loop.
                self.wake.set()

    @_locked
    def _on_settings(self, topic: str, payload: str) -> None:
        user = topic.split("/")[1]
        if not self._known("settings", user, topic, payload):
            return
        try:
            s = Settings.from_json(payload)
        except ValueError as e:
            log.warning("ignoring malformed settings on %s: %s", topic, e)
            return
        if s.time_up_action not in LOCKOUT_TYPES:
            log.warning("ignoring unsupported time_up_action %r", s.time_up_action)
            return
        if s.time_up_action != self._action_wanted[user] or s.rev != self._settings_rev[user]:
            self._action_wanted[user] = s.time_up_action
            self._settings_rev[user] = s.rev
            self.wake.set()

    @_locked
    def _on_own_usage(self, topic: str, payload: str) -> None:
        parsed = topics.parse_usage_topic(topic)
        if parsed is None or parsed[0] != self.cfg.host_id:
            return
        user = parsed[1]
        if not self._known("usage", user, topic, payload):
            return
        try:
            u = Usage.from_json(payload)
        except ValueError:
            return
        self._meters[user].seed(u.week, u.total_s, u.day, u.total_day_s)
        self._play_meters[user].seed(u.week, u.play_s, u.day, u.play_day_s)

    @_locked
    def _on_action(self, topic: str, payload: str) -> None:
        if self._sessions is None:
            return
        user = topic.split("/")[1]
        if user not in self._users:
            return  # not a kid on this PC: ignore, never park
        try:
            a = Action.from_json(payload)
        except ValueError as e:
            log.warning("ignoring malformed action on %s: %s", topic, e)
            return
        try:
            age = (self._clock.now() - datetime.fromisoformat(a.ts)).total_seconds()
        except (ValueError, TypeError) as e:
            log.warning("ignoring action on %s with unusable ts %r: %s", topic, a.ts, e)
            return
        if abs(age) > ACTION_MAX_AGE_S:
            log.warning("ignoring stale action %s for %s (ts %s)", a.op, user, a.ts)
            return
        try:
            if a.op == "logout":
                self._sessions.terminate_user(user)
                log.info("logged out %s on request", user)
            else:
                ended = [s.id for s in self._sessions.sessions(user) if inactive(s)]
                for sid in ended:
                    self._sessions.terminate_session(sid)
                log.info("ended inactive sessions %s of %s on request", ended, user)
        except Exception as e:
            log.warning("action %s for %s failed: %s", a.op, user, e)
        self._sess_sent.pop(user, None)  # republish on the next tick

    # --- snapshot persistence ---------------------------------------------------

    def _snapshot_path(self, user: str) -> str:
        return os.path.join(self.cfg.state_dir, f"{user}.remaining.json")

    def _load_snapshot(self, user: str) -> _Snapshot | None:
        try:
            with open(self._snapshot_path(user), encoding="utf-8") as f:
                d = json.load(f)
            return _Snapshot(str(d["week"]), int(d["total_s"]), str(d["receipt_week"]), int(d["receipt_usage_s"]),
                             bool(d.get("limited", True)), bool(d.get("released", True)),
                             int(d.get("play_s", 0)), bool(d.get("play_limited", False)),
                             str(d.get("play_receipt_week", "")), int(d.get("play_receipt_usage_s", 0)),
                             bool(d.get("play_released", True)), int(d.get("rev", 0)))
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

