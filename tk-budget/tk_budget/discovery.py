"""Home Assistant MQTT discovery entities published by tk-budget, one device per kid."""

from __future__ import annotations

from tk_protocol import topics
from tk_protocol.payloads import Cmd

# timekpr's own lockout-type names (accepted in spike/RESULTS.md (e)); `terminate` logs the user out.
TIME_UP_ACTIONS = ["shutdown", "lock", "terminate", "suspend"]
DEFAULT_TIME_UP_ACTION = "lock"  # chosen by the human partner
MAX_WEEKLY_MIN = 7 * 24 * 60
STATUS_TOPIC = f"{topics.PREFIX}/_budget/status"


def state_topic(kid: str) -> str:
    """Retained JSON with everything the kid's HA entities display."""
    return f"{topics.PREFIX}/{kid}/state"


def set_topic(kid: str, key: str) -> str:
    """Command topic HA writes to for a setting (`weekly_total_min`, `time_up_action`)."""
    return f"{topics.PREFIX}/{kid}/set/{key}"


def _base(kid: str, obj: str, name: str) -> dict:
    return {
        "name": name,
        "unique_id": f"tk_{kid}_{obj}",
        "object_id": f"tk_{kid}_{obj}",
        "availability_topic": STATUS_TOPIC,
        "device": {"identifiers": [f"tk_budget_{kid}"], "name": f"Screen time {kid}",
                   "manufacturer": "ha-timekprnext", "model": "tk-budget"},
    }


def _topic(component: str, kid: str, obj: str) -> str:
    return f"homeassistant/{component}/tk_{kid}/{obj}/config"


def entities(kid: str) -> list[tuple[str, dict]]:
    st = state_topic(kid)
    out: list[tuple[str, dict]] = []

    p = _base(kid, "remaining_total", "Remaining total")
    p.update(state_topic=st, unit_of_measurement="min", icon="mdi:timer-sand",
             value_template="{{ (value_json.remaining_s / 60) | round(0) | int }}")
    out.append((_topic("sensor", kid, "remaining_total"), p))

    p = _base(kid, "total_today", "Total time today")
    p.update(state_topic=st, unit_of_measurement="min", icon="mdi:monitor-dashboard",
             state_class="total_increasing",
             value_template="{{ (value_json.today_s / 60) | round(0) | int }}")
    out.append((_topic("sensor", kid, "total_today"), p))

    p = _base(kid, "weekly_total_budget", "Weekly total budget")
    p.update(state_topic=st, command_topic=set_topic(kid, "weekly_total_min"),
             value_template="{{ value_json.weekly_total_min }}", min=0, max=MAX_WEEKLY_MIN,
             step=15, mode="box", unit_of_measurement="min", icon="mdi:calendar-clock")
    out.append((_topic("number", kid, "weekly_total_budget"), p))

    for obj, name, minutes in (("adjust_plus_15", "+15 min", 15), ("adjust_minus_15", "−15 min", -15)):
        p = _base(kid, obj, name)
        p.update(command_topic=topics.cmd(kid),
                 payload_press=Cmd(op="adjust_total", minutes=minutes).to_json(),
                 icon="mdi:plus-circle" if minutes > 0 else "mdi:minus-circle")
        out.append((_topic("button", kid, obj), p))

    p = _base(kid, "time_up_action", "Time-up action")
    p.update(state_topic=st, command_topic=set_topic(kid, "time_up_action"),
             value_template="{{ value_json.time_up_action }}", options=list(TIME_UP_ACTIONS),
             icon="mdi:power")
    out.append((_topic("select", kid, "time_up_action"), p))

    return out
