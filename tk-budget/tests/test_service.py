import json
from datetime import datetime

import pytest

from tk_budget import discovery
from tk_budget.service import BudgetService
from tk_budget.store import Store
from tk_protocol import topics
from tk_protocol.bus import InMemoryBus
from tk_protocol.clock import FixedClock
from tk_protocol.payloads import Cmd, Remaining, Settings, Usage

KID = "kid-a"
MONDAY = datetime(2026, 11, 16, 10, 0)


@pytest.fixture
def env(tmp_path):
    bus = InMemoryBus()
    store = Store(str(tmp_path / "db.sqlite"))
    store.set_setting(KID, "weekly_total_min", "600")
    clock = FixedClock(MONDAY)
    svc = BudgetService(bus, store, clock, [KID])
    svc.start()
    return bus, store, clock, svc


def remaining(bus, kid=KID):
    return Remaining.from_json(bus.retained[topics.remaining(kid)])


def report(bus, host, total_s, week="2026-W47", day="2026-11-16", day_s=None):
    u = Usage(week=week, total_s=total_s, day=day, total_day_s=total_s if day_s is None else day_s)
    bus.publish(topics.usage(host, KID), u.to_json(), retain=True)


def test_initial_remaining_published_on_start(env):
    bus, *_ = env
    assert remaining(bus) == Remaining(week="2026-W47", total_s=36000)


def test_remaining_published_after_usage(env):
    bus, *_ = env
    report(bus, "pc1", 3600)
    assert remaining(bus) == Remaining(week="2026-W47", total_s=600 * 60 - 3600)


def test_two_hosts_share_budget(env):
    bus, *_ = env
    report(bus, "pc1", 3600)
    report(bus, "pc2", 1800)
    assert remaining(bus).total_s == 36000 - 5400


def test_resent_usage_not_counted_twice(env):
    bus, *_ = env
    report(bus, "pc1", 3600)
    report(bus, "pc1", 3600)
    report(bus, "pc1", 1000)
    assert remaining(bus).total_s == 36000 - 3600


def test_adjust_cmd_changes_remaining_and_resets_next_week(env):
    bus, store, clock, svc = env
    bus.publish(topics.cmd(KID), Cmd(op="adjust_total", minutes=15).to_json())
    assert remaining(bus).total_s == 36000 + 900
    clock.set(datetime(2026, 11, 23, 0, 0, 30))
    svc.tick()
    assert remaining(bus) == Remaining(week="2026-W48", total_s=36000)


def test_negative_adjust_clamps_at_zero(env):
    bus, *_ = env
    bus.publish(topics.cmd(KID), Cmd(op="adjust_total", minutes=-100000).to_json())
    assert remaining(bus).total_s == 0


def test_malformed_and_unknown_kid_ignored(env):
    bus, store, *_ = env
    before = remaining(bus)
    bus.publish(topics.usage("pc1", KID), "garbage", retain=True)
    bus.publish(topics.usage("pc1", KID), '{"week": "2026-W47"}', retain=True)
    bus.publish(topics.usage("pc1", "x"),
                Usage(week="2026-W47", total_s=5, day="2026-11-16", total_day_s=5).to_json(), retain=True)
    bus.publish(topics.cmd(KID), "garbage")
    bus.publish(topics.cmd("x"), Cmd(op="adjust_total", minutes=15).to_json())
    assert remaining(bus) == before
    assert topics.remaining("x") not in bus.retained
    assert store.load_usage() == []


def test_stale_week_usage_ignored(env):
    bus, store, *_ = env
    report(bus, "pc1", 7200, week="2026-W46", day="2026-11-15")
    assert remaining(bus).total_s == 36000
    assert store.load_usage() == []


def test_daily_stats_recorded(env):
    bus, store, *_ = env
    report(bus, "pc1", 5000, day_s=1200)
    assert store.daily_totals(KID, "2026-11-16") == {"pc1": 1200}


def test_state_shows_today_and_budget(env):
    bus, *_ = env
    report(bus, "pc1", 5000, day_s=1200)
    report(bus, "pc2", 600, day_s=600)
    state = json.loads(bus.retained[discovery.state_topic(KID)])
    assert state["today_s"] == 1800
    assert state["weekly_total_min"] == 600
    assert state["remaining_s"] == 36000 - 5600


def test_restart_restores_state(env, tmp_path):
    bus, store, clock, _ = env
    bus.publish(topics.cmd(KID), Cmd(op="adjust_total", minutes=15).to_json())
    report(bus, "pc1", 3600)
    expected = remaining(bus)
    bus2 = InMemoryBus()
    BudgetService(bus2, store, clock, [KID]).start()
    assert remaining(bus2) == expected


def test_budget_number_command_sets_budget(env):
    bus, store, *_ = env
    bus.publish(discovery.set_topic(KID, "weekly_total_min"), "120.0")
    assert store.get_setting(KID, "weekly_total_min") == "120"
    assert remaining(bus).total_s == 7200
    bus.publish(discovery.set_topic(KID, "weekly_total_min"), "nope")
    assert remaining(bus).total_s == 7200


def test_time_up_action_select_publishes_settings(env):
    bus, store, *_ = env
    assert Settings.from_json(bus.retained[topics.settings(KID)]).time_up_action == "terminate"
    bus.publish(discovery.set_topic(KID, "time_up_action"), "lock")
    assert Settings.from_json(bus.retained[topics.settings(KID)]) == Settings(time_up_action="lock")
    bus.publish(discovery.set_topic(KID, "time_up_action"), "explode")
    assert Settings.from_json(bus.retained[topics.settings(KID)]) == Settings(time_up_action="lock")


def test_unset_budget_fails_closed(tmp_path):
    bus = InMemoryBus()
    BudgetService(bus, Store(str(tmp_path / "db.sqlite")), FixedClock(MONDAY), [KID]).start()
    assert remaining(bus).total_s == 0


def test_discovery_published_retained_on_start(env):
    bus, *_ = env
    for topic, payload in discovery.entities(KID):
        assert json.loads(bus.retained[topic]) == payload


def test_discovery_has_all_entities():
    ents = discovery.entities(KID)
    by_id = {p["unique_id"]: (t, p) for t, p in ents}
    assert len(ents) == 6
    expected = {
        "tk_kid-a_remaining_total": "sensor",
        "tk_kid-a_total_today": "sensor",
        "tk_kid-a_weekly_total_budget": "number",
        "tk_kid-a_adjust_plus_15": "button",
        "tk_kid-a_adjust_minus_15": "button",
        "tk_kid-a_time_up_action": "select",
    }
    for uid, component in expected.items():
        topic, payload = by_id[uid]
        assert topic.startswith(f"homeassistant/{component}/") and topic.endswith("/config")
    plus = by_id["tk_kid-a_adjust_plus_15"][1]
    assert plus["command_topic"] == topics.cmd(KID)
    assert Cmd.from_json(plus["payload_press"]) == Cmd(op="adjust_total", minutes=15)
    minus = by_id["tk_kid-a_adjust_minus_15"][1]
    assert Cmd.from_json(minus["payload_press"]) == Cmd(op="adjust_total", minutes=-15)
    assert by_id["tk_kid-a_time_up_action"][1]["options"] == ["shutdown", "lock", "terminate", "suspend"]
