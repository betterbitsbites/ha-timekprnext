"""Read a child's daily screen time (minutes) from the HA "Google Family Link" sensor.

The sensor is read through the Supervisor's Core API proxy (`homeassistant_api: true`).
"""

from __future__ import annotations

import json
import logging
import math
from typing import Callable, Mapping

log = logging.getLogger(__name__)

Fetch = Callable[[str, dict], str]
STATES_URL = "http://supervisor/core/api/states/"
# HA lets users change a duration sensor's display unit; the REST state follows it.
UNIT_TO_MIN = {"min": 1.0, "s": 1 / 60, "h": 60.0, "d": 1440.0}


def _read(entity: str, env: Mapping[str, str], fetch: Fetch) -> tuple[float | None, str]:
    """(minutes, "") on success, (None, reason) otherwise. Never raises."""
    token = env.get("SUPERVISOR_TOKEN")
    if not token:
        return None, "SUPERVISOR_TOKEN is not set"
    try:
        body = json.loads(fetch(STATES_URL + entity, {"Authorization": f"Bearer {token}"}))
        value = float(body["state"])
        attrs = body.get("attributes")
        unit = attrs.get("unit_of_measurement") if isinstance(attrs, dict) else None
        if unit is not None and unit not in UNIT_TO_MIN:
            return None, f"unsupported unit {unit!r}"
        value *= UNIT_TO_MIN.get(unit, 1.0)
    except Exception as e:  # network/HTTP error (OSError), bad JSON, missing or non-numeric state
        return None, str(e) or type(e).__name__
    if not math.isfinite(value) or value < 0:
        return None, f"invalid value {value}"
    return value, ""


def read_minutes(entity: str, env: Mapping[str, str], fetch: Fetch) -> float | None:
    """Numeric state of `entity` in minutes, or None if unavailable. Never raises."""
    return _read(entity, env, fetch)[0]


class PhonePoller:
    """Polls the mapped sensors; logs a warning once when a kid's sensor becomes unreadable
    and an info line once when it recovers."""

    def __init__(self, entities: Mapping[str, str], env: Mapping[str, str], fetch: Fetch) -> None:
        self._entities = dict(entities)
        self._env = env
        self._fetch = fetch
        self._ok: dict[str, bool] = {}

    def poll(self) -> dict[str, float]:
        """Minutes per kid for the sensors that could be read right now."""
        out: dict[str, float] = {}
        for kid, entity in self._entities.items():
            value, reason = _read(entity, self._env, self._fetch)
            was_ok = self._ok.get(kid)
            if value is None:
                if was_ok is not False:
                    log.warning("Family Link sensor %s for %s is not readable (%s); "
                                "phone time counts as 0 until it is", entity, kid, reason)
                self._ok[kid] = False
                continue
            if was_ok is False:
                log.info("Family Link sensor %s for %s is readable again", entity, kid)
            self._ok[kid] = True
            out[kid] = value
        return out
