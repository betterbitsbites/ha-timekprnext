import json
from datetime import datetime

import pytest

from tk_budget import discovery
from tk_budget.service import BudgetService
from tk_budget.store import Store
from tk_protocol import topics
from tk_protocol.bus import InMemoryBus
from tk_protocol.clock import FixedClock
from tk_protocol.payloads import Action, Cmd, Remaining, Sessions, Settings, Usage

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
    assert len(ents) == 26
    expected = {
        "tk_kid-a_daily_total_limit": "number",
        "tk_kid-a_daily_limit_active": "switch",
        "tk_kid-a_remaining_week": "sensor",
        "tk_kid-a_remaining_today": "sensor",
        "tk_kid-a_week_left_pct": "sensor",
        "tk_kid-a_today_left_pct": "sensor",
        "tk_kid-a_remaining_total": "sensor",
        "tk_kid-a_total_today": "sensor",
        "tk_kid-a_weekly_total_budget": "number",
        "tk_kid-a_adjust_plus_15": "button",
        "tk_kid-a_adjust_minus_15": "button",
        "tk_kid-a_time_up_action": "select",
        "tk_kid-a_limit_active": "switch",
        "tk_kid-a_sessions": "sensor",
        "tk_kid-a_end_inactive_sessions": "button",
        "tk_kid-a_logout": "button",
        "tk_kid-a_play_limit_active": "switch",
        "tk_kid-a_weekly_play_budget": "number",
        "tk_kid-a_daily_play_active": "switch",
        "tk_kid-a_daily_play_limit": "number",
        "tk_kid-a_play_today": "sensor",
        "tk_kid-a_remaining_play": "sensor",
        "tk_kid-a_play_week_left_pct": "sensor",
        "tk_kid-a_play_today_left_pct": "sensor",
        "tk_kid-a_play_plus_15": "button",
        "tk_kid-a_play_minus_15": "button",
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
    assert sw["name"] == "Weekly limit active" and "week_limited" in sw["value_template"]
    num = by_id["tk_kid-a_daily_total_limit"][1]
    assert num["command_topic"] == discovery.set_topic(KID, "daily_total_min")
    assert (num["step"], num["max"]) == (5, 1440)
    for uid in ("tk_kid-a_week_left_pct", "tk_kid-a_today_left_pct"):
        p = by_id[uid][1]
        assert p["unit_of_measurement"] == "%"
        assert p["json_attributes_topic"] == discovery.state_topic(KID)
        assert "remaining_min" in p["json_attributes_template"] and "limit_min" in p["json_attributes_template"]


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


def state(bus, kid=KID):
    return json.loads(bus.retained[discovery.state_topic(kid)])


def set_daily(bus, minutes, on="ON"):
    if minutes is not None:
        bus.publish(discovery.set_topic(KID, "daily_total_min"), str(minutes))
    bus.publish(discovery.set_topic(KID, "daily_limit_active"), on)


def test_daily_limit_binds(env):
    bus, *_ = env
    set_daily(bus, 60)
    report(bus, "pc1", 1800, day_s=1800)
    assert remaining(bus).total_s == 1800
    st = state(bus)
    assert st["limits"]["day"]["pct"] == 50
    assert st["limits"]["week"]["remaining_s"] == 36000 - 1800


def test_daily_only_kid_is_limited(env):
    bus, *_ = env
    bus.publish(discovery.set_topic(KID, "limit_active"), "OFF")
    set_daily(bus, 30)
    assert remaining(bus) == Remaining(week="2026-W47", total_s=1800, limited=True)
    st = state(bus)
    assert st["week_limited"] is False and st["limited"] is True and st["daily_limited"] is True


def test_daily_on_without_value_is_zero(env):
    bus, *_ = env
    set_daily(bus, None)
    assert remaining(bus).total_s == 0
    assert remaining(bus).limited is True


def test_adjust_adds_to_week_and_today(env):
    bus, store, clock, svc = env
    set_daily(bus, 60)
    bus.publish(topics.cmd(KID), Cmd(op="adjust_total", minutes=15).to_json())
    assert remaining(bus).total_s == 4500
    clock.set(datetime(2026, 11, 17, 0, 0, 30))
    svc.tick()
    st = state(bus)
    assert st["limits"]["day"]["allowance_s"] == 3600
    assert st["limits"]["week"]["allowance_s"] == 36000 + 900


def test_day_rollover_by_tick(env):
    bus, store, clock, svc = env
    set_daily(bus, 60)
    report(bus, "pc1", 3600, day_s=3600)
    assert remaining(bus).total_s == 0
    clock.set(datetime(2026, 11, 17, 0, 0, 30))
    svc.tick()
    assert remaining(bus).total_s == 3600


def test_today_excludes_familylink_host(env):
    bus, store, *_ = env
    store.record_daily(KID, "2026-11-16", "_familylink", 1200)
    report(bus, "pc1", 600, day_s=600)
    assert state(bus)["today_s"] == 600


def test_limit_switch_reflects_weekly_only(env):
    bus, *_ = env
    bus.publish(discovery.set_topic(KID, "limit_active"), "OFF")
    set_daily(bus, 30)
    assert state(bus)["week_limited"] is False
    sw = dict((p["unique_id"], p) for _, p in discovery.entities(KID))["tk_kid-a_limit_active"]
    assert "week_limited" in sw["value_template"]


def test_daily_settings_validation(env):
    bus, store, *_ = env
    bus.publish(discovery.set_topic(KID, "daily_total_min"), "nope")
    assert store.get_setting(KID, "daily_total_min") is None
    bus.publish(discovery.set_topic(KID, "daily_total_min"), "99999")
    assert store.get_setting(KID, "daily_total_min") == "1440"
    bus.publish(discovery.set_topic(KID, "daily_total_min"), "-5")
    assert store.get_setting(KID, "daily_total_min") == "0"
    bus.publish(discovery.set_topic(KID, "daily_limit_active"), "maybe")
    assert store.get_setting(KID, "daily_limit_active") is None


# --- combined PC + phone limit ---------------------------------------------------

@pytest.fixture
def penv(tmp_path):
    bus = InMemoryBus()
    store = Store(str(tmp_path / "db.sqlite"))
    store.add_kid(KID)
    store.add_kid("kid-b")
    store.set_setting(KID, "weekly_total_min", "600")
    store.set_setting(KID, "limit_active", "ON")
    clock = FixedClock(MONDAY)
    svc = BudgetService(bus, store, clock, phone_kids=[KID])
    svc.start()
    return bus, store, clock, svc


def set_combined(bus, minutes, on="ON"):
    bus.publish(discovery.set_topic(KID, "combined_total_min"), str(minutes))
    bus.publish(discovery.set_topic(KID, "combined_limit_active"), on)


def test_combined_limit_counts_phone_and_pc(penv):
    bus, store, clock, svc = penv
    set_combined(bus, 60)
    report(bus, "pc1", 1200, day_s=1200)
    svc.set_phone_minutes(KID, 20)
    st = state(bus)
    assert st["limits"]["combined"]["remaining_s"] == 3600 - 2400
    assert st["combined_limited"] is True and st["combined_total_min"] == 60
    assert st["phone_today_s"] == 1200
    assert st["today_s"] == 1200
    assert st["limits"]["week"]["remaining_s"] == 36000 - 1200
    assert st["limits"]["day"]["on"] is False
    assert remaining(bus).total_s == 3600 - 2400


def test_phone_ignored_when_combined_off(penv):
    bus, store, clock, svc = penv
    report(bus, "pc1", 1200, day_s=1200)
    svc.set_phone_minutes(KID, 20)
    assert remaining(bus).total_s == 36000 - 1200
    assert state(bus)["combined_limited"] is False


def test_phone_value_never_lowers(penv):
    bus, store, clock, svc = penv
    svc.set_phone_minutes(KID, 40)
    svc.set_phone_minutes(KID, 30)
    assert state(bus)["phone_today_s"] == 2400


def test_midnight_guard(penv):
    bus, store, clock, svc = penv
    store.record_daily(KID, "2026-11-15", "_familylink", 90 * 60)
    clock.set(datetime(2026, 11, 16, 0, 5))
    svc.set_phone_minutes(KID, 90)
    assert "_familylink" not in store.daily_totals(KID, "2026-11-16")
    svc.set_phone_minutes(KID, 3)
    assert store.daily_totals(KID, "2026-11-16")["_familylink"] == 180
    clock.set(datetime(2026, 11, 17, 0, 20))
    store.record_daily(KID, "2026-11-16", "_familylink", 90 * 60)
    svc.set_phone_minutes(KID, 95)
    assert store.daily_totals(KID, "2026-11-17")["_familylink"] == 95 * 60


def test_phone_ignored_for_other_or_unknown_kids(penv):
    bus, store, clock, svc = penv
    svc.set_phone_minutes("kid-b", 10)
    svc.set_phone_minutes("nobody", 10)
    assert store.daily_totals("kid-b", "2026-11-16") == {}
    assert store.daily_totals("nobody", "2026-11-16") == {}


def test_combined_entities_only_for_phone_kids(penv):
    bus, *_ = penv
    for obj in ("daily_combined_limit", "combined_limit_active", "remaining_combined",
                "combined_left_pct", "phone_today"):
        comp = {"daily_combined_limit": "number", "combined_limit_active": "switch"}.get(obj, "sensor")
        assert (f"homeassistant/{comp}/tk_kid-b/{obj}/config", "", True) in bus.published
        assert f"homeassistant/{comp}/tk_kid-b/{obj}/config" not in bus.retained
        assert bus.retained[f"homeassistant/{comp}/tk_kid-a/{obj}/config"] != ""


# --- sessions and actions -----------------------------------------------------

def report_sessions(bus, host, count, locked, kid=KID):
    bus.publish(topics.sessions(host, kid), Sessions(count, locked).to_json(), retain=True)


def test_sessions_summed_over_hosts(env):
    bus, *_ = env
    report(bus, "pc1", 60)
    report_sessions(bus, "pc1", 1, 0)
    report_sessions(bus, "pc2", 2, 1)
    st = state(bus)
    assert (st["sessions"], st["sessions_locked"]) == (3, 1)
    assert st["sessions_by_host"] == {"pc1": 1, "pc2": 2}


def test_offline_host_counts_zero_until_it_reports_again(env):
    bus, *_ = env
    report(bus, "pc1", 60)
    report_sessions(bus, "pc1", 1, 0)
    report_sessions(bus, "pc2", 2, 1)
    bus.publish("timekpr/pc2/status", "offline", retain=True)
    st = state(bus)
    assert (st["sessions"], st["sessions_locked"]) == (1, 0)
    assert st["sessions_by_host"] == {"pc1": 1}
    bus.publish("timekpr/pc2/status", "online", retain=True)
    assert state(bus)["sessions"] == 3  # its stored report counts again
    report_sessions(bus, "pc2", 1, 1)
    assert state(bus)["sessions"] == 2


def test_stale_sessions_after_offline_status_count_zero(env):
    """Retained replay order is random: the offline status may arrive before the stale sessions."""
    bus, *_ = env
    report(bus, "pc1", 60)
    report_sessions(bus, "pc1", 1, 0)
    bus.publish("timekpr/pc2/status", "offline", retain=True)
    report_sessions(bus, "pc2", 2, 1)
    st = state(bus)
    assert (st["sessions"], st["sessions_locked"]) == (1, 0)
    assert st["sessions_by_host"] == {"pc1": 1}
    bus.publish("timekpr/pc2/status", "online", retain=True)
    st = state(bus)
    assert (st["sessions"], st["sessions_locked"]) == (3, 1)
    assert st["sessions_by_host"] == {"pc1": 1, "pc2": 2}


def test_reconnect_republishes_budget_online(env):
    bus, *_ = env
    bus.publish(discovery.STATUS_TOPIC, "offline", retain=True)  # the broker fired the last will
    bus.drop()
    bus.reconnect()
    assert bus.retained[discovery.STATUS_TOPIC] == "online"


@pytest.mark.parametrize("cmd", [Cmd(op="logout"), Cmd(op="end_inactive"), Cmd(op="adjust_total", minutes=15)])
def test_retained_cmd_from_before_start_ignored(tmp_path, cmd):
    bus = InMemoryBus()
    store = Store(str(tmp_path / "db.sqlite"))
    store.add_kid(KID)
    store.set_setting(KID, "weekly_total_min", "600")
    store.set_setting(KID, "limit_active", "ON")
    bus.publish(topics.cmd(KID), cmd.to_json(), retain=True)  # e.g. a retained publish by hand
    BudgetService(bus, store, FixedClock(MONDAY)).start()
    bus.drop()
    bus.reconnect()
    assert not [t for t, *_ in bus.published if t == topics.action(KID)]
    assert remaining(bus).total_s == 36000


def test_sessions_for_unknown_kid_ignored(env):
    bus, store, *_ = env
    report_sessions(bus, "pc1", 1, 0, kid="nobody")
    assert store.kids() == [KID]
    assert topics.remaining("nobody") not in bus.retained


def test_malformed_sessions_ignored(env):
    bus, *_ = env
    bus.publish(topics.sessions("pc1", KID), "garbage", retain=True)
    assert state(bus)["sessions"] == 0


@pytest.mark.parametrize("op", ["logout", "end_inactive"])
def test_action_cmd_publishes_non_retained_action(env, op):
    bus, *_ = env
    before = len(bus.published)
    bus.publish(topics.cmd(KID), Cmd(op=op).to_json())
    acts = [(t, p, r) for t, p, r in bus.published[before:] if t == topics.action(KID)]
    assert len(acts) == 1
    t, p, r = acts[0]
    assert r is False and topics.action(KID) not in bus.retained
    a = Action.from_json(p)
    assert a.op == op and a.ts == "2026-11-16T10:00:00"


def test_action_cmd_for_unknown_kid_does_nothing(env):
    bus, *_ = env
    bus.publish(topics.cmd("nobody"), Cmd(op="logout").to_json())
    assert not [t for t, *_ in bus.published if t == topics.action("nobody")]


def test_session_entities():
    by_id = {p["unique_id"]: p for _, p in discovery.entities(KID)}
    s = by_id["tk_kid-a_sessions"]
    assert s["value_template"] == "{{ value_json.sessions }}" and s["icon"] == "mdi:account-multiple"
    assert s["json_attributes_topic"] == discovery.state_topic(KID)
    assert "locked" in s["json_attributes_template"] and "by_host" in s["json_attributes_template"]
    assert "tojson" in s["json_attributes_template"]
    e = by_id["tk_kid-a_end_inactive_sessions"]
    assert e["name"] == "End inactive sessions" and e["icon"] == "mdi:account-lock"
    assert e["command_topic"] == topics.cmd(KID)
    assert Cmd.from_json(e["payload_press"]) == Cmd(op="end_inactive")
    lo = by_id["tk_kid-a_logout"]
    assert lo["name"] == "Log out everywhere" and lo["icon"] == "mdi:logout"
    assert Cmd.from_json(lo["payload_press"]) == Cmd(op="logout")


# --- play limits ----------------------------------------------------------------

def play_report(bus, host, play_s, play_day_s=None, total_s=0, kid=KID):
    u = Usage(week="2026-W47", total_s=total_s, day="2026-11-16", total_day_s=total_s,
              play_s=play_s, play_day_s=play_s if play_day_s is None else play_day_s)
    bus.publish(topics.usage(host, kid), u.to_json(), retain=True)


def set_play(bus, weekly=None, daily=None, week_on="ON", day_on="ON"):
    if weekly is not None:
        bus.publish(discovery.set_topic(KID, "weekly_play_min"), str(weekly))
        bus.publish(discovery.set_topic(KID, "play_limit_active"), week_on)
    if daily is not None:
        bus.publish(discovery.set_topic(KID, "daily_play_min"), str(daily))
        bus.publish(discovery.set_topic(KID, "daily_play_active"), day_on)


def test_weekly_play_limit(env):
    bus, *_ = env
    set_play(bus, weekly=120)
    play_report(bus, "pc1", 1800)
    r = remaining(bus)
    assert (r.play_s, r.play_limited) == (5400, True)
    st = state(bus)
    assert st["play_limited"] is True and st["play_week_limited"] is True
    assert st["weekly_play_min"] == 120 and st["play_today_s"] == 1800
    assert st["play_remaining_s"] == 5400
    assert st["limits"]["play_week"]["remaining_s"] == 5400
    assert st["limits"]["play_day"]["on"] is False


def test_play_sums_over_hosts(env):
    bus, *_ = env
    set_play(bus, weekly=120)
    play_report(bus, "pc1", 1800)
    play_report(bus, "pc2", 600)
    assert remaining(bus).play_s == 7200 - 2400
    assert state(bus)["play_today_s"] == 2400


def test_daily_play_binds(env):
    bus, *_ = env
    set_play(bus, weekly=120, daily=30)
    play_report(bus, "pc1", 600)
    assert remaining(bus).play_s == 1200
    assert state(bus)["limits"]["play_day"]["remaining_s"] == 1200


def test_play_capped_by_total(env):
    bus, *_ = env
    set_play(bus, weekly=120)
    bus.publish(discovery.set_topic(KID, "weekly_total_min"), "15")
    report(bus, "pc1", 0)
    r = remaining(bus)
    assert r.total_s == 900 and r.play_s == 900 and r.play_limited


def test_play_off_means_unlimited_and_total_unchanged(env):
    bus, *_ = env
    set_play(bus, weekly=120)
    set_play(bus, weekly=120, week_on="OFF")
    play_report(bus, "pc1", 1800, total_s=600)
    r = remaining(bus)
    assert (r.play_limited, r.play_s) == (False, 0)
    assert r.total_s == 36000 - 600 and r.limited
    assert state(bus)["play_remaining_s"] == 0


def test_play_limit_does_not_change_total(env):
    bus, *_ = env
    before = remaining(bus).total_s
    set_play(bus, weekly=1, daily=1)
    assert remaining(bus).total_s == before


def test_adjust_play_changes_both_play_limits(env):
    bus, store, *_ = env
    set_play(bus, weekly=120, daily=30)
    bus.publish(topics.cmd(KID), Cmd(op="adjust_play", minutes=15).to_json())
    st = state(bus)
    assert st["limits"]["play_week"]["allowance_s"] == 7200 + 900
    assert st["limits"]["play_day"]["allowance_s"] == 1800 + 900
    assert store.get_adjustment_s(KID, "2026-W47") == 0
    assert remaining(bus).total_s == 36000


def test_adjust_total_does_not_touch_play(env):
    bus, *_ = env
    set_play(bus, weekly=120)
    bus.publish(topics.cmd(KID), Cmd(op="adjust_total", minutes=15).to_json())
    assert state(bus)["limits"]["play_week"]["allowance_s"] == 7200


def test_play_ledger_restored_after_restart(env, tmp_path):
    bus, store, clock, svc = env
    set_play(bus, weekly=120)
    play_report(bus, "pc1", 1800)
    bus2 = InMemoryBus()
    svc2 = BudgetService(bus2, store, clock)
    svc2.start()
    r = Remaining.from_json(bus2.retained[topics.remaining(KID)])
    assert r.play_s == 5400 and r.play_limited


def test_play_settings_validation(env):
    bus, store, *_ = env
    bus.publish(discovery.set_topic(KID, "weekly_play_min"), "99999")
    assert store.get_setting(KID, "weekly_play_min") == "10080"
    bus.publish(discovery.set_topic(KID, "daily_play_min"), "-5")
    assert store.get_setting(KID, "daily_play_min") == "0"
    bus.publish(discovery.set_topic(KID, "daily_play_min"), "abc")
    assert store.get_setting(KID, "daily_play_min") == "0"
    bus.publish(discovery.set_topic(KID, "play_limit_active"), "maybe")
    assert store.get_setting(KID, "play_limit_active") is None


def test_play_entities():
    by_id = {p["unique_id"]: p for _, p in discovery.entities(KID)}
    sw = by_id["tk_kid-a_play_limit_active"]
    assert sw["name"] == "Weekly play limit active" and "play_week_limited" in sw["value_template"]
    assert sw["command_topic"] == discovery.set_topic(KID, "play_limit_active")
    assert by_id["tk_kid-a_daily_play_active"]["command_topic"] == discovery.set_topic(KID, "daily_play_active")
    w = by_id["tk_kid-a_weekly_play_budget"]
    assert (w["step"], w["max"]) == (15, 10080)
    assert w["command_topic"] == discovery.set_topic(KID, "weekly_play_min")
    d = by_id["tk_kid-a_daily_play_limit"]
    assert (d["step"], d["max"]) == (5, 1440)
    assert d["command_topic"] == discovery.set_topic(KID, "daily_play_min")
    assert by_id["tk_kid-a_play_today"]["unit_of_measurement"] == "min"
    assert "play_limited" in by_id["tk_kid-a_remaining_play"]["value_template"]
    for uid, key in (("tk_kid-a_play_week_left_pct", "play_week"), ("tk_kid-a_play_today_left_pct", "play_day")):
        p = by_id[uid]
        assert f"limits.{key}" in p["value_template"]
        assert p["json_attributes_topic"] == discovery.state_topic(KID)
        assert "none" in p["json_attributes_template"]
    assert Cmd.from_json(by_id["tk_kid-a_play_plus_15"]["payload_press"]) == Cmd(op="adjust_play", minutes=15)
    assert Cmd.from_json(by_id["tk_kid-a_play_minus_15"]["payload_press"]) == Cmd(op="adjust_play", minutes=-15)
