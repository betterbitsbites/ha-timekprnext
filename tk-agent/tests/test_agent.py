import json
from datetime import datetime, timedelta

import pytest

from tk_agent.agent import Agent
from tk_agent.config import Config
from tk_agent.meter import UsageMeter
from tk_protocol import topics
from tk_protocol.bus import InMemoryBus
from tk_protocol.clock import FixedClock
from tk_protocol.payloads import Event, Remaining, Settings, Usage

USER = KID = "kid-a"  # the timekpr user name is the kid id
HOST = "pc1"
WEEK = "2026-W47"
MON_10 = datetime(2026, 11, 16, 10, 0)


class FakeTimekpra:
    """Simulates timekpr: time left ticks down while the user is active and is capped at midnight."""

    def __init__(self, clock: FixedClock, left: int = 86400) -> None:
        self.clock = clock
        self.left = left
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

    def run(self, seconds: int) -> None:
        """User active for `seconds`; the clock advances too."""
        used = min(seconds, self.time_left_s(USER))
        self.left -= used
        self.consumed += used
        self.clock.set(self.clock.now() + timedelta(seconds=seconds))


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
    assert usage(bus) == Usage(week=WEEK, total_s=90, day="2026-11-16", total_day_s=90)
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
