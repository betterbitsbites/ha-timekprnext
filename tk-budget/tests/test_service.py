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
    store.add_kid(KID)
    store.set_setting(KID, "weekly_total_min", "600")
    store.set_setting(KID, "limit_active", "ON")
    clock = FixedClock(MONDAY)
    svc = BudgetService(bus, store, clock)
    svc.start()
    return bus, store, clock, svc


def remaining(bus, kid=KID):
    return Remaining.from_json(bus.retained[topics.remaining(kid)])


def report(bus, host, total_s, week="2026-W47", day="2026-11-16", day_s=None, kid=KID):
    u = Usage(week=week, total_s=total_s, day=day, total_day_s=total_s if day_s is None else day_s)
    bus.publish(topics.usage(host, kid), u.to_json(), retain=True)


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
    BudgetService(bus2, store, clock).start()
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
    assert Settings.from_json(bus.retained[topics.settings(KID)]).time_up_action == "lock"
    bus.publish(discovery.set_topic(KID, "time_up_action"), "shutdown")
    assert Settings.from_json(bus.retained[topics.settings(KID)]) == Settings(time_up_action="shutdown")
    bus.publish(discovery.set_topic(KID, "time_up_action"), "explode")
    assert Settings.from_json(bus.retained[topics.settings(KID)]) == Settings(time_up_action="shutdown")


def test_limit_on_without_budget_is_zero(tmp_path):
    bus = InMemoryBus()
    store = Store(str(tmp_path / "db.sqlite"))
    BudgetService(bus, store, FixedClock(MONDAY)).start()
    report(bus, "pc1", 60, kid="kid-b")
    bus.publish(discovery.set_topic("kid-b", "limit_active"), "ON")
    assert remaining(bus, "kid-b") == Remaining(week="2026-W47", total_s=0, limited=True)


def test_kid_learned_from_usage_and_persisted(env):
    bus, store, clock, _ = env
    report(bus, "pc1", 60, kid="kid-b")
    assert "homeassistant/switch/tk_kid-b/limit_active/config" in bus.retained
    assert remaining(bus, "kid-b") == Remaining(week="2026-W47", total_s=0, limited=False)
    assert store.kids() == ["kid-a", "kid-b"]
    bus2 = InMemoryBus()
    BudgetService(bus2, store, clock).start()
    assert "homeassistant/switch/tk_kid-b/limit_active/config" in bus2.retained
    assert remaining(bus2, "kid-b").limited is False


@pytest.mark.parametrize("bad", ["_budget", "Kid", "a.b"])
def test_invalid_kid_ignored(env, bad):
    bus, store, *_ = env
    report(bus, "pc1", 60, kid=bad)
    assert store.kids() == ["kid-a"]
    assert not any(t.startswith(f"homeassistant/switch/tk_{bad}/") for t in bus.retained)
    assert topics.remaining(bad) not in bus.retained


def test_cmd_and_set_for_unknown_kid_do_not_create_it(env):
    bus, store, *_ = env
    bus.publish(topics.cmd("kid-z"), Cmd(op="adjust_total", minutes=15).to_json())
    bus.publish(discovery.set_topic("kid-z", "limit_active"), "ON")
    assert store.kids() == ["kid-a"]
    assert topics.remaining("kid-z") not in bus.retained


def test_limit_off_means_unlimited(env):
    bus, store, *_ = env
    bus.publish(discovery.set_topic(KID, "limit_active"), "OFF")
    assert remaining(bus) == Remaining(week="2026-W47", total_s=0, limited=False)
    report(bus, "pc1", 600, day_s=600)
    state = json.loads(bus.retained[discovery.state_topic(KID)])
    assert state["today_s"] == 600 and state["limited"] is False
    bus.publish(discovery.set_topic(KID, "limit_active"), "ON")
    assert remaining(bus) == Remaining(week="2026-W47", total_s=36000 - 600, limited=True)


def test_limit_active_rejects_other_payloads(env):
    bus, store, *_ = env
    bus.publish(discovery.set_topic(KID, "limit_active"), "maybe")
    assert store.get_setting(KID, "limit_active") == "ON"
    assert remaining(bus).limited is True


def test_discovery_published_retained_on_start(env):
    bus, *_ = env
    for topic, payload in discovery.entities(KID):
        assert json.loads(bus.retained[topic]) == payload


def test_discovery_has_all_entities():
    ents = discovery.entities(KID)
    by_id = {p["unique_id"]: (t, p) for t, p in ents}
    assert len(ents) == 7
    expected = {
        "tk_kid-a_remaining_total": "sensor",
        "tk_kid-a_total_today": "sensor",
        "tk_kid-a_weekly_total_budget": "number",
        "tk_kid-a_adjust_plus_15": "button",
        "tk_kid-a_adjust_minus_15": "button",
        "tk_kid-a_time_up_action": "select",
        "tk_kid-a_limit_active": "switch",
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
    sw = by_id["tk_kid-a_limit_active"][1]
    assert sw["command_topic"] == discovery.set_topic(KID, "limit_active")
    assert (sw["payload_on"], sw["payload_off"]) == ("ON", "OFF")


def test_upgrade_kid_with_budget_stays_limited(tmp_path):
    # 0.2.0 stored budgets for configured kids but had no kids table and no limit switch
    store = Store(str(tmp_path / "db.sqlite"))
    store.set_setting("kid-b", "weekly_total_min", "120")
    bus = InMemoryBus()
    BudgetService(bus, store, FixedClock(MONDAY)).start()
    report(bus, "pc1", 600, kid="kid-b")
    assert remaining(bus, "kid-b") == Remaining(week="2026-W47", total_s=7200 - 600, limited=True)
    assert store.get_setting("kid-b", "limit_active") == "ON"


def test_discovery_sets_default_entity_id():
    # HA >= 2025.10 ignores object_id; default_entity_id fixes the entity id on first discovery
    for topic, p in discovery.entities(KID):
        component, obj = topic.split("/")[1], topic.split("/")[3]
        assert p["default_entity_id"] == f"{component}.tk_kid_a_{obj}"
        assert "object_id" not in p
