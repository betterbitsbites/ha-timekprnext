"""Home Assistant MQTT discovery entities published by tk-budget, one device per kid."""

from __future__ import annotations

import re

from tk_protocol import topics
from tk_protocol.payloads import Cmd

# timekpr's own lockout-type names (accepted in spike/RESULTS.md (e)); `terminate` logs the user out.
TIME_UP_ACTIONS = ["shutdown", "lock", "terminate", "suspend"]
DEFAULT_TIME_UP_ACTION = "lock"  # chosen by the human partner
MAX_WEEKLY_MIN = 7 * 24 * 60
STATUS_TOPIC = f"{topics.PREFIX}/_budget/status"
LIMIT_KEY = "limit_active"  # "ON"/"OFF"; off (default) = no limit
DAILY_KEY = "daily_limit_active"  # "ON"/"OFF"; off (default) = no daily limit
COMBINED_KEY = "combined_limit_active"  # "ON"/"OFF"; PC + phone daily limit (Family Link kids only)
MAX_DAILY_MIN = 24 * 60
_KID_RE = re.compile(r"^[a-z0-9-][a-z0-9_-]*$")  # leading "_" is reserved (`_budget`)


def valid_kid(kid: str) -> bool:
    return bool(_KID_RE.match(kid))


def state_topic(kid: str) -> str:
    """Retained JSON with everything the kid's HA entities display."""
    return f"{topics.PREFIX}/{kid}/state"


def set_topic(kid: str, key: str) -> str:
    """Command topic HA writes to for a setting (`weekly_total_min`, `time_up_action`, `limit_active`)."""
    return f"{topics.PREFIX}/{kid}/set/{key}"


def _base(kid: str, component: str, obj: str, name: str) -> dict:
    return {
        "name": name,
        "unique_id": f"tk_{kid}_{obj}",
        # HA >= 2025.10 ignores object_id; this sets the entity id when HA first sees the entity
        "default_entity_id": f"{component}.tk_{kid.replace('-', '_')}_{obj}",
        "availability_topic": STATUS_TOPIC,
        "device": {"identifiers": [f"tk_budget_{kid}"], "name": f"Screen time {kid}",
                   "manufacturer": "ha-timekprnext", "model": "tk-budget"},
    }


def _topic(component: str, kid: str, obj: str) -> str:
    return f"homeassistant/{component}/tk_{kid}/{obj}/config"


COMBINED_OBJECTS = (("number", "daily_combined_limit"), ("switch", COMBINED_KEY),
                    ("sensor", "remaining_combined"), ("sensor", "combined_left_pct"),
                    ("sensor", "phone_today"))


def combined_topics(kid: str) -> list[str]:
    return [_topic(c, kid, o) for c, o in COMBINED_OBJECTS]


def combined_entities(kid: str) -> list[tuple[str, dict]]:
    """Entities for the combined PC + phone daily limit; only for kids with a Family Link sensor."""
    st = state_topic(kid)
    out: list[tuple[str, dict]] = []

    p = _base(kid, "number", "daily_combined_limit", "Daily combined limit")
    p.update(state_topic=st, command_topic=set_topic(kid, "combined_total_min"),
             value_template="{{ value_json.combined_total_min }}", min=0, max=MAX_DAILY_MIN,
             step=5, mode="box", unit_of_measurement="min", icon="mdi:cellphone-clock")
    out.append((_topic("number", kid, "daily_combined_limit"), p))

    p = _base(kid, "switch", COMBINED_KEY, "Combined limit active")
    p.update(state_topic=st, command_topic=set_topic(kid, COMBINED_KEY), payload_on="ON", payload_off="OFF",
             value_template="{{ 'ON' if value_json.combined_limited else 'OFF' }}", icon="mdi:timer-lock-outline")
    out.append((_topic("switch", kid, COMBINED_KEY), p))

    p = _base(kid, "sensor", "remaining_combined", "Remaining combined")
    p.update(state_topic=st, unit_of_measurement="min", icon="mdi:timer-sand",
             value_template="{{ ((value_json.limits.combined.remaining_s / 60) | round(0) | int)"
                            " if value_json.limits.combined.on else None }}")
    out.append((_topic("sensor", kid, "remaining_combined"), p))

    p = _base(kid, "sensor", "combined_left_pct", "Combined left")
    p.update(state_topic=st, unit_of_measurement="%", icon="mdi:percent",
             value_template="{{ value_json.limits.combined.pct if value_json.limits.combined.on else None }}",
             json_attributes_topic=st,
             json_attributes_template=(
                 "{% set l = value_json.limits.combined %}"
                 "{{ ({'remaining_min': (l.remaining_s / 60) | round(0) | int,"
                 " 'limit_min': (l.allowance_s / 60) | round(0) | int} if l.on"
                 " else {'remaining_min': none, 'limit_min': none}) | tojson }}"))
    out.append((_topic("sensor", kid, "combined_left_pct"), p))

    p = _base(kid, "sensor", "phone_today", "Phone time today")
    p.update(state_topic=st, unit_of_measurement="min", icon="mdi:cellphone",
             value_template="{{ (value_json.phone_today_s / 60) | round(0) | int }}")
    out.append((_topic("sensor", kid, "phone_today"), p))

    return out


def entities(kid: str) -> list[tuple[str, dict]]:
    st = state_topic(kid)
    out: list[tuple[str, dict]] = []

    p = _base(kid, "sensor", "remaining_total", "Remaining total")
    p.update(state_topic=st, unit_of_measurement="min", icon="mdi:timer-sand",
             value_template="{{ ((value_json.remaining_s / 60) | round(0) | int) if value_json.limited else None }}")
    out.append((_topic("sensor", kid, "remaining_total"), p))

    p = _base(kid, "sensor", "total_today", "Total time today")
    p.update(state_topic=st, unit_of_measurement="min", icon="mdi:monitor-dashboard",
             state_class="total_increasing",
             value_template="{{ (value_json.today_s / 60) | round(0) | int }}")
    out.append((_topic("sensor", kid, "total_today"), p))

    p = _base(kid, "number", "weekly_total_budget", "Weekly total budget")
    p.update(state_topic=st, command_topic=set_topic(kid, "weekly_total_min"),
             value_template="{{ value_json.weekly_total_min }}", min=0, max=MAX_WEEKLY_MIN,
             step=15, mode="box", unit_of_measurement="min", icon="mdi:calendar-clock")
    out.append((_topic("number", kid, "weekly_total_budget"), p))

    for obj, name, minutes in (("adjust_plus_15", "+15 min", 15), ("adjust_minus_15", "−15 min", -15)):
        p = _base(kid, "button", obj, name)
        p.update(command_topic=topics.cmd(kid),
                 payload_press=Cmd(op="adjust_total", minutes=minutes).to_json(),
                 icon="mdi:plus-circle" if minutes > 0 else "mdi:minus-circle")
        out.append((_topic("button", kid, obj), p))

    p = _base(kid, "select", "time_up_action", "Time-up action")
    p.update(state_topic=st, command_topic=set_topic(kid, "time_up_action"),
             value_template="{{ value_json.time_up_action }}", options=list(TIME_UP_ACTIONS),
             icon="mdi:power")
    out.append((_topic("select", kid, "time_up_action"), p))

    p = _base(kid, "switch", LIMIT_KEY, "Weekly limit active")
    p.update(state_topic=st, command_topic=set_topic(kid, LIMIT_KEY), payload_on="ON", payload_off="OFF",
             value_template="{{ 'ON' if value_json.week_limited else 'OFF' }}", icon="mdi:timer-lock")
    out.append((_topic("switch", kid, LIMIT_KEY), p))

    p = _base(kid, "number", "daily_total_limit", "Daily total limit")
    p.update(state_topic=st, command_topic=set_topic(kid, "daily_total_min"),
             value_template="{{ value_json.daily_total_min }}", min=0, max=MAX_DAILY_MIN,
             step=5, mode="box", unit_of_measurement="min", icon="mdi:calendar-today")
    out.append((_topic("number", kid, "daily_total_limit"), p))

    p = _base(kid, "switch", DAILY_KEY, "Daily limit active")
    p.update(state_topic=st, command_topic=set_topic(kid, DAILY_KEY), payload_on="ON", payload_off="OFF",
             value_template="{{ 'ON' if value_json.daily_limited else 'OFF' }}", icon="mdi:timer-lock-outline")
    out.append((_topic("switch", kid, DAILY_KEY), p))

    for obj, name, key, icon in (("remaining_week", "Remaining this week", "week", "mdi:timer-sand"),
                                 ("remaining_today", "Remaining today", "day", "mdi:timer-sand")):
        p = _base(kid, "sensor", obj, name)
        p.update(state_topic=st, unit_of_measurement="min", icon=icon,
                 value_template=f"{{{{ ((value_json.limits.{key}.remaining_s / 60) | round(0) | int)"
                                f" if value_json.limits.{key}.on else None }}}}")
        out.append((_topic("sensor", kid, obj), p))

    for obj, name, key in (("week_left_pct", "Week left", "week"), ("today_left_pct", "Today left", "day")):
        p = _base(kid, "sensor", obj, name)
        p.update(state_topic=st, unit_of_measurement="%", icon="mdi:percent",
                 value_template=f"{{{{ value_json.limits.{key}.pct if value_json.limits.{key}.on else None }}}}",
                 json_attributes_topic=st,
                 json_attributes_template=(
                     f"{{% set l = value_json.limits.{key} %}}"
                     "{{ ({'remaining_min': (l.remaining_s / 60) | round(0) | int,"
                     " 'limit_min': (l.allowance_s / 60) | round(0) | int} if l.on"
                     " else {'remaining_min': none, 'limit_min': none}) | tojson }}"))
        out.append((_topic("sensor", kid, obj), p))

    p = _base(kid, "sensor", "sessions", "Sessions")
    p.update(state_topic=st, icon="mdi:account-multiple", value_template="{{ value_json.sessions }}",
             json_attributes_topic=st,
             json_attributes_template=("{{ {'locked': value_json.sessions_locked,"
                                       " 'by_host': value_json.sessions_by_host} | tojson }}"))
    out.append((_topic("sensor", kid, "sessions"), p))

    for obj, name, op, icon in (("end_inactive_sessions", "End inactive sessions", "end_inactive",
                                 "mdi:account-lock"),
                                ("logout", "Log out everywhere", "logout", "mdi:logout")):
        p = _base(kid, "button", obj, name)
        p.update(command_topic=topics.cmd(kid), payload_press=Cmd(op=op).to_json(), icon=icon)
        out.append((_topic("button", kid, obj), p))

    return out
