import dataclasses
import json
from datetime import datetime, timedelta

from pathlib import Path

import pytest

from tk_agent.agent import Agent
from tk_agent.config import Config
from tk_agent.meter import UsageMeter
from tk_protocol import topics
from tk_protocol.bus import InMemoryBus
from tk_protocol.clock import FixedClock
from tk_agent.sessions import Loginctl, LoginctlError, Session
from tk_protocol.payloads import Action, Applied, Event, Remaining, Settings, Sessions, Usage

USER = KID = "kid-a"  # the timekpr user name is the kid id
HOST = "pc1"
WEEK = "2026-W47"
MON_10 = datetime(2026, 11, 16, 10, 0)


class FakeTimekpra:
    """Simulates timekpr: time left ticks down while the user is active and is capped at midnight."""

    def __init__(self, clock: FixedClock, left: int = 86400) -> None:
        self.clock = clock
        self.left = left
        self.play_left = 86400  # PlayTime left; timekpr shows it capped by the total time left
        self.play_writes: list[tuple[str, int]] = []
        self.play_enabled = True  # False: PlayTime off in timekpr, play left reads 0, writes ignored
        self.play_consumed = 0
        self.playing = False  # a PlayTime activity runs (ACTUAL_ACTIVE_PLAYTIME_ACTIVITY_COUNT > 0)
        self.rt_calls = 0  # play_rt() calls (one --getuserinfort each)
        self.writes: list[tuple[str, int]] = []
        self.lockouts: list[tuple[str, str]] = []
        self.consumed = 0
        self.user_list = [USER]
        self.fail_users = 0  # next n users() calls raise
        self.logged_out: set[str] = set()  # no session: no real-time info, writes have no effect

    def users(self) -> list[str]:
        if self.fail_users:
            self.fail_users -= 1
            raise RuntimeError("timekpra --userlist failed")
        return list(self.user_list)

    def _cap(self) -> int:
        now = self.clock.now()
        midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
        return int((midnight - now).total_seconds())

    def time_left_s(self, user: str) -> int | None:
        if user in self.logged_out:
            return None
        return min(self.left, self._cap())

    def set_time_left_s(self, user: str, seconds: int) -> None:
        self.writes.append((user, seconds))
        if user not in self.logged_out:
            self.left = seconds

    def set_lockout_type(self, user: str, value: str) -> None:
        self.lockouts.append((user, value))

    def play_left_s(self, user: str) -> int | None:
        if user in self.logged_out:
            return None
        if not self.play_enabled:
            return 0
        return min(self.play_left, self.time_left_s(user))

    def play_rt(self, user: str) -> tuple[int, int] | None:
        self.rt_calls += 1
        left = self.play_left_s(user)
        return None if left is None else (left, 1 if self.playing else 0)

    def set_play_left_s(self, user: str, seconds: int) -> None:
        self.play_writes.append((user, seconds))
        if user not in self.logged_out and self.play_enabled:
            self.play_left = seconds

    def run(self, seconds: int, playing: bool = True) -> None:
        """User active for `seconds` (playing: a PlayTime activity runs); the clock advances too."""
        used = min(seconds, self.time_left_s(USER))
        self.playing = playing  # the game is still running when the agent reads next
        if playing and self.play_enabled:
            played = min(seconds, self.play_left_s(USER))
            self.play_left -= played
            self.play_consumed += played
        self.left -= used
        self.consumed += used
        self.clock.set(self.clock.now() + timedelta(seconds=seconds))


class FakeSessions:
    def __init__(self) -> None:
        self.by_user: dict[str, list[Session]] = {}
        self.error: Exception | None = None
        self.ended_sessions: list[str] = []
        self.ended_users: list[str] = []

    def sessions(self, user: str) -> list[Session]:
        if self.error:
            raise self.error
        return list(self.by_user.get(user, []))

    def terminate_session(self, session_id: str) -> None:
        self.ended_sessions.append(session_id)

    def terminate_user(self, user: str) -> None:
        self.ended_users.append(user)


def sess(id, state="active", locked=False):
    return Session(id=id, cls="user", type="wayland", state=state, locked=locked, remote=False)


@pytest.fixture
def senv(env):
    agent, bus, tk, clock = env
    fs = FakeSessions()
    agent = Agent(agent.cfg, bus, tk, agent._meter_factory, clock, fs)
    agent.start()
    return agent, bus, tk, clock, fs


def published_sessions(bus):
    """(count, locked) of every sessions report; presence fields are tested separately."""
    return [(r.count, r.locked) for r in (Sessions.from_json(p) for t, p, _ in bus.published
                                          if t == topics.sessions(HOST, KID))]


@pytest.fixture
def env(tmp_path):
    clock = FixedClock(MON_10)
    bus = InMemoryBus()
    tk = FakeTimekpra(clock)
    cfg = Config(broker_host="b", broker_port=1883, username="u", password="p", host_id=HOST,
                 interval_s=45, state_dir=str(tmp_path))
    agent = Agent(cfg, bus, tk, lambda user: UsageMeter(str(tmp_path / f"{user}.json"), clock), clock)
    return agent, bus, tk, clock


def usage(bus) -> Usage:
    return Usage.from_json(bus.retained[topics.usage(HOST, KID)])


def events(bus) -> list[str]:
    return [Event.from_json(p).type for t, p, _ in bus.published if t == topics.event(HOST, KID)]


def send_remaining(bus, total_s, week=WEEK, kid=KID, limited=True):
    bus.publish(topics.remaining(kid), Remaining(week=week, total_s=total_s, limited=limited).to_json(),
                retain=True)


def new_agent(agent, bus, tk, clock, tmp_path):
    return Agent(agent.cfg, bus, tk, lambda u: UsageMeter(str(tmp_path / f"{u}.json"), clock), clock)


def test_start_publishes_online_without_discovery(env):
    agent, bus, *_ = env
    agent.start()
    assert bus.retained[topics.status(HOST)] == "online"
    assert not any(t.startswith("homeassistant/") for t in bus.retained)


def test_unlimited_never_writes(env):
    agent, bus, tk, _ = env
    agent.start()
    send_remaining(bus, 0, limited=False)
    for _ in range(3):
        agent.tick()
        tk.run(45)
    agent.tick()
    assert tk.writes == [] and events(bus) == []
    assert usage(bus).total_s == 135


def test_no_remaining_reports_every_listed_user(env):
    agent, bus, tk, _ = env
    tk.user_list = ["kid-a", "kid-b", "parent"]
    agent.start()
    agent.tick()
    assert tk.writes == []
    for user in tk.user_list:
        assert topics.usage(HOST, user) in bus.retained


def test_unlimit_releases_once_even_after_restart(env, tmp_path):
    agent, bus, tk, clock = env
    agent.start()
    send_remaining(bus, 0)
    agent.tick()
    assert tk.writes == [(USER, 0)]
    send_remaining(bus, 0, limited=False)
    agent.tick()
    agent.tick()
    assert tk.writes == [(USER, 0), (USER, 86400)]
    agent2 = new_agent(agent, bus, tk, clock, tmp_path)
    agent2.start()  # the retained unlimited remaining is delivered again
    agent2.tick()
    assert tk.writes == [(USER, 0), (USER, 86400)]
    send_remaining(bus, 300)
    agent2.tick()
    assert tk.writes[-1][0] == USER and 0 < tk.writes[-1][1] <= 300


def test_user_appearing_later_uses_retained_remaining(env):
    agent, bus, tk, _ = env
    agent.start()
    send_remaining(bus, 600, kid="kid-b")
    agent.tick()
    assert tk.writes == []
    tk.user_list.append("kid-b")
    agent.tick()
    assert tk.writes == [("kid-b", 600)]


def test_invalid_and_host_named_users_skipped(env, caplog):
    agent, bus, tk, _ = env
    tk.user_list = ["Kid", HOST, "kid-a"]
    agent.start()
    agent.tick()
    agent.tick()
    usage_topics = [t for t in bus.retained if t.endswith("/usage")]
    assert usage_topics == [topics.usage(HOST, "kid-a")]
    assert caplog.text.count("user 'Kid'") == 1
    assert caplog.text.count(f"user '{HOST}'") == 1


def test_userlist_failure_keeps_last_users(env):
    agent, bus, tk, _ = env
    agent.start()
    agent.tick()
    tk.fail_users = 1
    send_remaining(bus, 600)
    agent.tick()
    assert tk.writes == [(USER, 600)]


def test_usage_published_cumulative_and_retained(env):
    agent, bus, tk, _ = env
    agent.start()
    agent.tick()
    tk.run(45)
    agent.tick()
    tk.run(45)
    agent.tick()
    assert usage(bus) == Usage(week=WEEK, total_s=90, day="2026-11-16", total_day_s=90, play_s=90, play_day_s=90)
    assert (topics.usage(HOST, KID), usage(bus).to_json(), True) in bus.published


def test_no_remaining_means_no_write(env):
    agent, bus, tk, _ = env
    agent.start()
    agent.tick()
    tk.run(45)
    agent.tick()
    assert tk.writes == []


def test_remaining_applied_as_time_left(env):
    agent, bus, tk, _ = env
    agent.start()
    send_remaining(bus, 5400)
    agent.tick()
    assert tk.writes == [(USER, 5400)]


def test_remaining_capped_at_24h(env):
    agent, bus, tk, _ = env
    tk.left = 3000
    agent.start()
    send_remaining(bus, 200000)
    agent.tick()
    assert tk.writes == [(USER, 86400)]


def test_unchanged_remaining_not_rewritten(env):
    agent, bus, tk, _ = env
    agent.start()
    send_remaining(bus, 5400)
    agent.tick()
    tk.run(45)
    agent.tick()
    assert tk.writes == [(USER, 5400)]


def test_capped_by_midnight_not_rewritten_every_tick(env):
    agent, bus, tk, clock = env
    clock.set(datetime(2026, 11, 16, 23, 0))
    tk.left = 3600  # timekpr shows time until midnight
    agent.start()
    send_remaining(bus, 20000)
    for _ in range(3):
        agent.tick()
        tk.run(45)
    assert tk.writes == []


def test_broker_outage_keeps_enforcing_last_remaining(env):
    agent, bus, tk, _ = env
    agent.start()
    send_remaining(bus, 600)
    agent.tick()
    bus.drop()
    for _ in range(20):
        tk.run(45)
        agent.tick()  # must not raise, must not hand the old 600 s back
    assert tk.consumed <= 600
    assert tk.time_left_s(USER) == 0
    bus.reconnect()
    agent.tick()
    assert usage(bus).total_s == tk.consumed


def test_time_up_event_once(env):
    agent, bus, tk, _ = env
    agent.start()
    send_remaining(bus, 0)
    agent.tick()
    agent.tick()
    assert events(bus).count("time_up") == 1


def test_time_low_event_once_and_rearmed(env):
    agent, bus, tk, _ = env
    agent.start()
    send_remaining(bus, 250)
    agent.tick()
    agent.tick()
    assert events(bus).count("time_low") == 1
    send_remaining(bus, 3600)
    agent.tick()
    send_remaining(bus, 200)
    agent.tick()
    assert events(bus).count("time_low") == 2


def test_lockout_type_applied_on_settings_change(env):
    agent, bus, tk, _ = env
    agent.start()
    send_remaining(bus, 5400)
    bus.publish(topics.settings(KID), Settings(time_up_action="lock").to_json(), retain=True)
    agent.tick()
    agent.tick()
    assert tk.lockouts == [(USER, "lock")]
    bus.publish(topics.settings(KID), Settings(time_up_action="shutdown").to_json(), retain=True)
    agent.tick()
    assert tk.lockouts == [(USER, "lock"), (USER, "shutdown")]


def test_malformed_remaining_and_settings_ignored(env):
    agent, bus, tk, _ = env
    agent.start()
    bus.publish(topics.remaining(KID), "garbage", retain=True)
    bus.publish(topics.settings(KID), '{"time_up_action": "explode"}', retain=True)
    agent.tick()
    assert tk.writes == [] and tk.lockouts == []


def test_restart_continues_counting_without_loss_or_double(env, tmp_path):
    agent, bus, tk, clock = env
    agent.start()
    send_remaining(bus, 5400)
    agent.tick()
    tk.run(100)
    agent.tick()
    before = usage(bus).total_s
    tk.run(50)  # used while the agent is down
    agent2 = new_agent(agent, bus, tk, clock, tmp_path)
    agent2.start()
    agent2.tick()
    assert usage(bus).total_s == before + 50 == tk.consumed


def test_lost_state_file_restored_from_retained_usage(env, tmp_path):
    agent, bus, tk, clock = env
    agent.start()
    agent.tick()
    tk.run(300)
    agent.tick()
    (tmp_path / f"{USER}.json").unlink()
    agent2 = new_agent(agent, bus, tk, clock, tmp_path)
    agent2.start()
    agent2.tick()
    tk.run(45)
    agent2.tick()
    assert usage(bus).total_s == 345


def test_timekpra_failure_does_not_stop_other_kids(env):
    agent, bus, tk, _ = env

    def boom(user):
        raise RuntimeError("timekpra down")

    tk.time_left_s = boom
    agent.start()
    agent.tick()  # logged, not raised


def test_lagging_remaining_overshoot_is_bounded(env):
    agent, bus, tk, _ = env
    budget, interval = 600, 45
    agent.start()
    reported: list[int] = []
    for i in range(30):
        tk.run(interval)
        agent.tick()
        reported.append(usage(bus).total_s)
        if i >= 1:  # tk-budget's answer always reflects the previous cycle
            send_remaining(bus, max(0, budget - reported[i - 1]))
    assert tk.consumed - budget <= 2 * interval


def test_redelivered_retained_remaining_after_reconnect_grants_nothing(env):
    agent, bus, tk, _ = env
    agent.start()
    send_remaining(bus, 600)
    agent.tick()
    bus.drop()
    for _ in range(20):
        tk.run(45)
        agent.tick()
    bus.reconnect()  # broker re-sends the old retained remaining (600)
    for _ in range(5):
        agent.tick()
        tk.run(45)
    assert tk.consumed == 600


def test_restart_without_broker_keeps_enforcing_last_remaining(env, tmp_path):
    agent, bus, tk, clock = env
    agent.start()
    send_remaining(bus, 600)
    agent.tick()
    tk.run(300)
    agent.tick()
    # next day, PC boots without broker; timekpr has reset time left to its own 24 h
    clock.set(datetime(2026, 11, 17, 10, 0))
    tk.left = 86400
    agent2 = new_agent(agent, InMemoryBus(), tk, clock, tmp_path)
    agent2.tick()
    assert tk.writes[-1] == (USER, 300)


def test_lockout_type_not_set_for_unlimited_or_unknown(env):
    agent, bus, tk, _ = env
    agent.start()
    bus.publish(topics.settings(KID), Settings(time_up_action="lock").to_json(), retain=True)
    agent.tick()
    send_remaining(bus, 0, limited=False)
    agent.tick()
    assert tk.lockouts == []
    send_remaining(bus, 600)
    agent.tick()
    assert tk.lockouts == [(USER, "lock")]


def test_logged_out_user_reported_but_not_written(env):
    agent, bus, tk, _ = env
    tk.logged_out.add(USER)
    agent.start()
    send_remaining(bus, 600)
    agent.tick()
    agent.tick()
    assert tk.writes == []
    assert usage(bus) == Usage(week=WEEK, total_s=0, day="2026-11-16", total_day_s=0)


def test_logged_out_keeps_counters_and_enforces_after_login(env):
    agent, bus, tk, _ = env
    agent.start()
    send_remaining(bus, 600)
    agent.tick()
    tk.run(100)
    agent.tick()
    tk.logged_out.add(USER)
    for _ in range(3):
        agent.tick()
    assert usage(bus).total_s == 100
    tk.logged_out.discard(USER)
    tk.left = 86400  # timekpr's own value at login
    agent.tick()
    assert tk.writes[-1] == (USER, 500)
    assert usage(bus).total_s == 100


def test_release_waits_for_login(env):
    agent, bus, tk, _ = env
    agent.start()
    send_remaining(bus, 0)
    agent.tick()
    tk.logged_out.add(USER)
    send_remaining(bus, 0, limited=False)
    agent.tick()
    assert tk.writes == [(USER, 0)]
    tk.logged_out.discard(USER)
    agent.tick()
    assert tk.writes == [(USER, 0), (USER, 86400)]


# --- sessions ---------------------------------------------------------------------------

def test_sessions_published_retained_once_and_on_change(senv):
    agent, bus, tk, clock, fs = senv
    fs.by_user[USER] = [sess("5", locked=True)]
    agent.tick()
    agent.tick()
    assert json.loads(bus.retained[topics.sessions(HOST, KID)]) == {
        "count": 1, "locked": 1, "state": "locked", "kind": "wayland", "connect": ""}
    assert [r for t, _, r in bus.published if t == topics.sessions(HOST, KID)] == [True]
    assert published_sessions(bus) == [(1, 1)]
    fs.by_user[USER] = [sess("5", locked=True), sess("8")]
    agent.tick()
    assert published_sessions(bus) == [(1, 1), (2, 1)]


def test_sessions_published_first_tick_even_if_retained_exists(senv):
    agent, bus, tk, clock, fs = senv
    bus.publish(topics.sessions(HOST, KID), Sessions(0, 0).to_json(), retain=True)
    agent.tick()
    assert published_sessions(bus) == [(0, 0), (0, 0)]


def test_sessions_published_for_logged_out_user(senv):
    agent, bus, tk, clock, fs = senv
    tk.logged_out.add(USER)
    agent.tick()
    agent.tick()
    assert published_sessions(bus) == [(0, 0)]


def test_sessions_error_warns_once_and_publishes_nothing(senv, caplog):
    agent, bus, tk, clock, fs = senv
    fs.error = LoginctlError("boom")
    with caplog.at_level("WARNING"):
        agent.tick()
        agent.tick()
    assert published_sessions(bus) == []
    assert len([r for r in caplog.records if "loginctl" in r.getMessage()]) == 1
    fs.error = None
    agent.tick()
    assert len(published_sessions(bus)) == 1
    fs.error = LoginctlError("again")
    with caplog.at_level("WARNING"):
        agent.tick()
    assert len([r for r in caplog.records if "loginctl" in r.getMessage()]) == 2


def action(bus, op, ts, kid=KID, retain=False):
    bus.publish(topics.action(kid), Action(op=op, ts=ts).to_json(), retain=retain)


def test_end_inactive_terminates_only_inactive(senv):
    agent, bus, tk, clock, fs = senv
    fs.by_user[USER] = [sess("1", locked=True), sess("2"), sess("3", state="online")]
    agent.tick()
    action(bus, "end_inactive", clock.now().isoformat())
    assert fs.ended_sessions == ["1", "3"]
    assert fs.ended_users == []


def test_logout_terminates_user(senv):
    agent, bus, tk, clock, fs = senv
    agent.tick()
    action(bus, "logout", clock.now().isoformat())
    assert fs.ended_users == [USER]


def test_old_action_ignored(senv):
    agent, bus, tk, clock, fs = senv
    agent.tick()
    action(bus, "logout", (clock.now() - timedelta(minutes=3)).isoformat())
    action(bus, "logout", (clock.now() + timedelta(minutes=3)).isoformat())
    assert fs.ended_users == []


def test_action_for_unknown_user_ignored_and_not_parked(senv):
    agent, bus, tk, clock, fs = senv
    agent.tick()
    action(bus, "logout", clock.now().isoformat(), kid="kid-b")
    tk.user_list = [USER, "kid-b"]
    agent.tick()
    assert fs.ended_users == []


def test_malformed_action_ignored(senv):
    agent, bus, tk, clock, fs = senv
    agent.tick()
    bus.publish(topics.action(KID), "garbage")
    bus.publish(topics.action(KID), Action(op="logout", ts="yesterday").to_json())
    assert fs.ended_users == []


def test_retained_action_handled_per_message_and_agent_never_publishes_action(senv):
    agent, bus, tk, clock, fs = senv
    agent.tick()
    action(bus, "logout", clock.now().isoformat(), retain=True)
    assert fs.ended_users == [USER]
    agent.tick()
    assert fs.ended_users == [USER]
    assert [t for t, _, _ in bus.published if t == topics.action(KID)] == [topics.action(KID)]  # the test's own


def test_sessions_count_leaves_out_closing(senv):
    agent, bus, tk, clock, fs = senv
    fs.by_user[USER] = [sess("1"), sess("2", state="closing", locked=True)]
    agent.tick()
    assert published_sessions(bus) == [(1, 0)]
    action(bus, "end_inactive", clock.now().isoformat())
    assert fs.ended_sessions == ["2"]  # closing sessions are still ended


def test_retained_action_from_before_start_ignored(env):
    agent, bus, tk, clock = env
    action(bus, "logout", clock.now().isoformat(), retain=True)
    fs = FakeSessions()
    agent = Agent(agent.cfg, bus, tk, agent._meter_factory, clock, fs)
    agent.start()
    agent.tick()
    bus.drop()
    bus.reconnect()
    agent.tick()
    assert fs.ended_users == []


def test_reconnect_republishes_online_and_sessions(senv):
    agent, bus, tk, clock, fs = senv
    fs.by_user[USER] = [sess("1")]
    agent.tick()
    agent.tick()
    assert published_sessions(bus) == [(1, 0)]
    bus.publish(topics.status(HOST), "offline", retain=True)  # the broker fired the last will
    bus.drop()
    bus.reconnect()
    assert bus.retained[topics.status(HOST)] == "online"
    agent.tick()
    assert published_sessions(bus) == [(1, 0), (1, 0)]


def test_action_triggers_republish_next_tick(senv):
    agent, bus, tk, clock, fs = senv
    fs.by_user[USER] = [sess("1", locked=True)]
    agent.tick()
    action(bus, "end_inactive", clock.now().isoformat())
    agent.tick()
    assert published_sessions(bus) == [(1, 1), (1, 1)]


# --- play time --------------------------------------------------------------------------

def send_play(bus, play_s, play_limited=True, total_s=0, limited=False, week=WEEK):
    bus.publish(topics.remaining(KID), Remaining(week=week, total_s=total_s, limited=limited, play_s=play_s,
                                                 play_limited=play_limited).to_json(), retain=True)


def play_warnings(caplog):
    return [r for r in caplog.records if "PlayTime seems off" in r.getMessage()]


def test_play_usage_reported_only_while_playing(env):
    agent, bus, tk, _ = env
    tk.play_left = 3600  # below the total time left, so the total cap does not move play left
    agent.start()
    agent.tick()
    tk.run(100, playing=True)
    agent.tick()
    tk.run(50, playing=False)
    agent.tick()
    assert usage(bus) == Usage(week=WEEK, total_s=150, day="2026-11-16", total_day_s=150,
                               play_s=100, play_day_s=100)


def test_play_off_in_timekpr_measures_no_play(env):
    agent, bus, tk, _ = env
    tk.play_enabled = False
    agent.start()
    for _ in range(3):
        agent.tick()
        tk.run(45)
    agent.tick()
    assert usage(bus).play_s == 0 and usage(bus).play_day_s == 0
    assert usage(bus).total_s == 135


def test_play_limit_enforced_from_receipt(env):
    agent, bus, tk, _ = env
    agent.start()
    send_play(bus, 1800)
    agent.tick()
    assert tk.play_writes == [(USER, 1800)]
    tk.run(600)
    agent.tick()
    assert tk.play_writes == [(USER, 1800)]  # timekpr counts down itself
    tk.play_left = 17000  # timekpr hands out its own value again (e.g. a PlayTime reset)
    agent.tick()
    assert len(tk.play_writes) == 2 and abs(tk.play_writes[-1][1] - 1200) < 60
    assert usage(bus).play_s == 600
    assert tk.writes == []  # total unlimited from the start: never written


def test_play_snapshot_after_playing_writes_rest(env):
    agent, bus, tk, _ = env
    agent.start()
    agent.tick()
    tk.run(600)
    agent.tick()
    send_play(bus, 1200)  # tk-budget already subtracted the 600 s
    agent.tick()
    assert tk.play_writes == [(USER, 1200)]


def test_play_unlimited_from_start_never_writes(env):
    agent, bus, tk, _ = env
    agent.start()
    send_play(bus, 0, play_limited=False)
    for _ in range(3):
        agent.tick()
        tk.run(45)
    agent.tick()
    assert tk.play_writes == [] and tk.writes == []


def test_play_release_once_even_after_restart(env, tmp_path):
    agent, bus, tk, clock = env
    agent.start()
    send_play(bus, 0)
    agent.tick()
    assert tk.play_writes == [(USER, 0)]
    send_play(bus, 0, play_limited=False)
    agent.tick()
    agent.tick()
    assert tk.play_writes == [(USER, 0), (USER, 86400)]
    agent2 = new_agent(agent, bus, tk, clock, tmp_path)
    agent2.start()
    agent2.tick()
    assert tk.play_writes == [(USER, 0), (USER, 86400)]
    assert tk.writes == []


def test_play_release_ignores_unenforceable_guard(env):
    agent, bus, tk, _ = env
    tk.play_enabled = False
    agent.start()
    send_play(bus, 600)
    agent.tick()
    send_play(bus, 0, play_limited=False)
    agent.tick()
    agent.tick()
    assert tk.play_writes == [(USER, 600), (USER, 86400)]


def test_play_off_in_timekpr_one_write_and_one_warning_per_episode(env, caplog):
    agent, bus, tk, _ = env
    tk.play_enabled = False
    agent.start()
    send_play(bus, 1800)
    with caplog.at_level("WARNING"):
        for _ in range(5):
            agent.tick()
            tk.run(45)
    assert tk.play_writes == [(USER, 1800)]
    assert len(play_warnings(caplog)) == 1
    send_play(bus, 1500)  # a new play value alone does not clear the guard
    with caplog.at_level("WARNING"):
        for _ in range(3):
            agent.tick()
    assert tk.play_writes == [(USER, 1800)]
    assert len(play_warnings(caplog)) == 1


def test_play_off_guard_holds_while_total_caps_play(env, caplog):
    """tk-budget caps play by total, so play_s changes with every usage report. PlayTime off in timekpr."""
    agent, bus, tk, clock = env
    tk.play_enabled = False
    agent.start()
    total = 3600
    with caplog.at_level("WARNING"):
        for _ in range(6):
            send_play(bus, total, total_s=total, limited=True)
            agent.tick()
            tk.run(45, playing=False)
            total -= 45
        assert len(tk.play_writes) == 1
        assert len(play_warnings(caplog)) == 1
        start = clock.now()
        while clock.now() - start < timedelta(minutes=15):
            send_play(bus, total, total_s=total, limited=True)
            agent.tick()
            tk.run(45, playing=False)
            total -= 45
        send_play(bus, total, total_s=total, limited=True)
        agent.tick()
    assert len(tk.play_writes) == 2  # one retry after 15 minutes
    assert len(play_warnings(caplog)) == 1  # one warning per episode


def test_play_off_guard_cleared_by_play_limited_change(env):
    agent, bus, tk, _ = env
    tk.play_enabled = False
    agent.start()
    send_play(bus, 1800)
    agent.tick()
    agent.tick()
    assert tk.play_writes == [(USER, 1800)]
    send_play(bus, 0, play_limited=False)
    agent.tick()  # release
    send_play(bus, 1800)
    agent.tick()
    assert tk.play_writes == [(USER, 1800), (USER, 86400), (USER, 1800)]


def test_play_unenforceable_retried_next_day(env, caplog):
    agent, bus, tk, clock = env
    tk.play_enabled = False
    agent.start()
    send_play(bus, 1800, week="2026-W47")
    with caplog.at_level("WARNING"):
        agent.tick()
        agent.tick()
        clock.set(datetime(2026, 11, 17, 10, 0))
        agent.tick()
        agent.tick()
    assert tk.play_writes == [(USER, 1800), (USER, 1800)]
    assert len(play_warnings(caplog)) == 2


def test_play_capped_by_total_is_not_unenforceable(env, caplog):
    agent, bus, tk, _ = env
    agent.start()
    send_play(bus, 1800, total_s=600, limited=True)
    with caplog.at_level("WARNING"):
        for _ in range(4):
            agent.tick()
            tk.run(45)
    assert tk.writes == [(USER, 600)]
    assert tk.play_writes == [(USER, 1800)]  # timekpr shows min(play, total): no rewrite each tick
    assert play_warnings(caplog) == []


def test_play_change_keeps_total_receipt(env):
    agent, bus, tk, _ = env
    agent.start()
    send_play(bus, 1800, total_s=5400, limited=True)
    agent.tick()
    tk.run(600)
    agent.tick()
    send_play(bus, 3000, total_s=5400, limited=True)  # only the play part changed
    agent.tick()
    assert tk.writes == [(USER, 5400)]  # a reset total receipt would write 5400 again
    assert tk.play_writes[-1] == (USER, 3000)


def test_total_change_keeps_play_receipt(env):
    agent, bus, tk, _ = env
    agent.start()
    send_play(bus, 1800, total_s=5400, limited=True)
    agent.tick()
    tk.run(600)
    agent.tick()
    send_play(bus, 1800, total_s=3000, limited=True)  # only the total part changed
    agent.tick()
    assert tk.writes[-1] == (USER, 3000)
    assert tk.play_writes == [(USER, 1800)]  # play still 1200 left, a reset would write 1800


def test_play_redelivery_keeps_play_receipt(env):
    agent, bus, tk, _ = env
    agent.start()
    send_play(bus, 1800)
    agent.tick()
    tk.run(600)
    agent.tick()
    send_play(bus, 1800)  # retained value re-delivered
    tk.play_left = 17000
    agent.tick()
    assert abs(tk.play_writes[-1][1] - 1200) < 60


def test_total_only_snapshot_keeps_play_untouched(env):
    agent, bus, tk, _ = env
    agent.start()
    send_remaining(bus, 600)
    for _ in range(3):
        agent.tick()
        tk.run(45)
    send_remaining(bus, 0, limited=False)
    agent.tick()
    assert tk.play_writes == []


def test_play_survives_unlimited_total(env):
    agent, bus, tk, _ = env
    agent.start()
    send_play(bus, 900, total_s=0, limited=False)
    agent.tick()
    assert tk.play_writes == [(USER, 900)] and tk.writes == []


def test_old_snapshot_file_without_play_keys_loads(env, tmp_path):
    agent, bus, tk, clock = env
    (tmp_path / f"{USER}.remaining.json").write_text(json.dumps(
        {"week": WEEK, "total_s": 600, "receipt_week": WEEK, "receipt_usage_s": 0, "limited": True,
         "released": True}))
    agent.tick()
    assert tk.writes == [(USER, 600)]
    assert tk.play_writes == []


def test_play_meter_uses_own_state_file(env, tmp_path):
    agent, bus, tk, _ = env
    agent.start()
    agent.tick()
    tk.run(100)
    agent.tick()
    assert (tmp_path / f"{USER}.play.json").exists()
    assert json.loads((tmp_path / f"{USER}.play.json").read_text())["week_total_s"] == 100


def test_play_not_written_while_logged_out(env):
    agent, bus, tk, _ = env
    tk.logged_out.add(USER)
    agent.start()
    send_play(bus, 600)
    agent.tick()
    assert tk.play_writes == []
    tk.logged_out.discard(USER)
    agent.tick()
    assert tk.play_writes == [(USER, 600)]


def test_play_capped_by_total_not_counted_without_activity(env):
    agent, bus, tk, _ = env
    agent.start()  # play allowance 86400 >= total left: play left falls with every active second
    agent.tick()
    for _ in range(3):
        tk.run(100, playing=False)
        agent.tick()
    assert tk.play_left_s(USER) < 86400 - 200  # play left did fall (total cap)
    assert usage(bus).play_s == 0 and usage(bus).play_day_s == 0
    assert usage(bus).total_s == 300


def test_play_capped_by_total_counted_while_activity_runs(env):
    agent, bus, tk, _ = env
    agent.start()
    agent.tick()
    tk.run(100, playing=False)
    agent.tick()
    tk.run(100, playing=True)
    agent.tick()
    assert usage(bus).play_s == 100 and usage(bus).total_s == 200


def test_play_read_is_one_realtime_call_per_tick(env):
    agent, bus, tk, _ = env
    agent.start()
    for _ in range(3):
        agent.tick()
        tk.run(45)
    assert tk.rt_calls == 3


# --- presence ---------------------------------------------------------------------------

def x11_loginctl(fixture="show-session-x11-locked.txt"):
    text = (Path(__file__).parent / "fixtures" / fixture).read_text()
    return Loginctl(lambda argv: "7\n" if argv[0] == "show-user" else text)


def test_x11_locked_session_reported_with_vnc_url(env):
    agent, bus, tk, clock = env
    cfg = dataclasses.replace(agent.cfg, remote_host="pc1")
    agent = Agent(cfg, bus, tk, agent._meter_factory, clock, x11_loginctl())
    agent.start()
    agent.tick()
    assert Sessions.from_json(bus.retained[topics.sessions(HOST, KID)]) == Sessions(
        count=1, locked=1, state="locked", kind="x11", connect="vnc://pc1:5901")


@pytest.mark.parametrize("found,state,kind,connect", [
    ([], "none", "none", ""),
    ([Session("1", "user", "wayland", "active", False, False)], "active", "wayland", "rdp://kid-a@pc1:3389"),
    ([Session("1", "user", "wayland", "online", False, False)], "away", "wayland", "rdp://kid-a@pc1:3389"),
    ([Session("1", "user", "tty", "active", False, True)], "active", "tty", ""),
    ([Session("1", "user", "unspecified", "online", False, True)], "away", "tty", ""),
    ([Session("1", "user", "wayland", "closing", False, False)], "none", "none", ""),
])
def test_presence_fields(env, found, state, kind, connect):
    agent, bus, tk, clock = env
    fs = FakeSessions()
    fs.by_user[USER] = found
    agent = Agent(dataclasses.replace(agent.cfg, remote_host="pc1"), bus, tk, agent._meter_factory, clock, fs)
    agent.start()
    agent.tick()
    r = Sessions.from_json(bus.retained[topics.sessions(HOST, KID)])
    assert (r.state, r.kind, r.connect) == (state, kind, connect)


def test_presence_change_alone_republishes(senv):
    agent, bus, tk, clock, fs = senv
    fs.by_user[USER] = [sess("1")]
    agent.tick()
    fs.by_user[USER] = [sess("1", state="online")]
    agent.tick()
    states = [Sessions.from_json(p).state for t, p, _ in bus.published if t == topics.sessions(HOST, KID)]
    assert states == ["active", "away"]


# --- immediate apply and acks --------------------------------------------------------------

def send_rev(bus, total_s, rev, kid=KID):
    bus.publish(topics.remaining(kid), Remaining(week=WEEK, total_s=total_s, limited=True, rev=rev).to_json(),
                retain=True)


def acks(bus, kid=KID):
    return [(Applied.from_json(p).rev, r) for t, p, r in bus.published if t == topics.applied(HOST, kid)]


def test_new_remaining_wakes_and_is_acked_once(env):
    agent, bus, tk, _ = env
    agent.start()
    agent.tick()
    assert not agent.wake.is_set()
    send_rev(bus, 600, rev=3)
    assert agent.wake.is_set()
    assert acks(bus) == []  # acked after the tick that applied it, not on receipt
    agent.tick()
    agent.tick()
    assert tk.writes == [(USER, 600)]
    assert acks(bus) == [(3, True)]


def test_redelivered_remaining_does_not_wake(env):
    agent, bus, tk, _ = env
    agent.start()
    send_rev(bus, 600, rev=3)
    agent.tick()
    agent.wake.clear()
    bus.drop()
    bus.reconnect()  # retained remaining re-delivered unchanged
    assert not agent.wake.is_set()


def test_same_values_new_rev_wakes_acks_and_keeps_receipt(env):
    agent, bus, tk, _ = env
    agent.start()
    send_rev(bus, 600, rev=3)
    agent.tick()
    tk.run(300)
    agent.tick()
    agent.wake.clear()
    send_rev(bus, 600, rev=4)  # the parent set the same value again
    assert agent.wake.is_set()
    agent.tick()
    assert tk.writes == [(USER, 600)]  # receipt point unchanged: 300 s left, nothing written
    assert acks(bus) == [(3, True), (4, True)]


def test_logged_out_user_not_acked(env):
    agent, bus, tk, _ = env
    tk.logged_out.add(USER)
    agent.start()
    send_rev(bus, 600, rev=3)
    agent.tick()
    assert acks(bus) == []
    tk.logged_out.discard(USER)
    agent.tick()
    assert acks(bus) == [(3, True)]


def test_settings_only_change_wakes_and_is_acked(env):
    agent, bus, tk, _ = env
    agent.start()
    send_rev(bus, 600, rev=3)
    agent.tick()
    agent.wake.clear()
    bus.publish(topics.settings(KID), Settings(time_up_action="lock", rev=4).to_json(), retain=True)
    assert agent.wake.is_set()
    agent.tick()
    assert acks(bus) == [(3, True), (4, True)]


def test_settings_same_action_new_rev_wakes(env):
    agent, bus, tk, _ = env
    agent.start()
    bus.publish(topics.settings(KID), Settings(time_up_action="lock", rev=1).to_json(), retain=True)
    agent.tick()
    agent.wake.clear()
    bus.publish(topics.settings(KID), Settings(time_up_action="lock", rev=2).to_json(), retain=True)
    assert agent.wake.is_set()
    agent.tick()
    assert acks(bus) == [(1, True), (2, True)]


def test_rev_zero_never_acked(env):
    agent, bus, tk, _ = env
    agent.start()
    send_remaining(bus, 600)
    agent.tick()
    assert acks(bus) == []


def test_failed_tick_not_acked(env):
    agent, bus, tk, _ = env
    agent.start()
    send_rev(bus, 600, rev=3)

    def boom(user, seconds):
        raise RuntimeError("timekpra down")

    tk.set_time_left_s = boom
    agent.tick()
    assert acks(bus) == []


def test_remaining_rev_persisted_and_reacked_after_restart(env, tmp_path):
    agent, bus, tk, clock = env
    agent.start()
    send_rev(bus, 600, rev=3)
    agent.tick()
    agent2 = new_agent(agent, InMemoryBus(), tk, clock, tmp_path)  # no broker: rev from the snapshot file
    agent2.start()
    agent2.tick()
    assert acks(agent2._bus) == [(3, True)]


def test_old_snapshot_without_rev_loads_as_zero(env, tmp_path):
    agent, bus, tk, clock = env
    (tmp_path / f"{USER}.remaining.json").write_text(json.dumps(
        {"week": WEEK, "total_s": 600, "receipt_week": WEEK, "receipt_usage_s": 0}))
    agent.start()
    agent.tick()
    assert tk.writes == [(USER, 600)]
    assert acks(bus) == []


def test_reconnect_reacks(env):
    agent, bus, tk, _ = env
    agent.start()
    send_rev(bus, 600, rev=3)
    agent.tick()
    bus.drop()
    bus.reconnect()
    agent.tick()
    assert acks(bus) == [(3, True), (3, True)]


def test_usage_driven_remaining_does_not_wake(env):
    """tk-budget republishes remaining after every usage report with the same rev: waking on that
    would tick again at once, publish usage again, and loop as fast as the broker answers."""
    agent, bus, tk, _ = env
    agent.start()
    send_rev(bus, 600, rev=3)
    agent.tick()
    agent.wake.clear()
    tk.run(45)
    agent.tick()
    send_rev(bus, 555, rev=3)  # tk-budget's answer to the usage report
    assert not agent.wake.is_set()
    agent.tick()
    assert acks(bus) == [(3, True)]
