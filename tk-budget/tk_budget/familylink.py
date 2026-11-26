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


def read_minutes(entity: str, env: Mapping[str, str], fetch: Fetch) -> float | None:
    """Numeric state of `entity` in minutes, or None if unavailable. Never raises."""
    token = env.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    try:
        body = json.loads(fetch(STATES_URL + entity, {"Authorization": f"Bearer {token}"}))
        value = float(body["state"])
    except Exception as e:  # network/HTTP error (OSError), bad JSON, missing or non-numeric state
        log.debug("family link sensor %s not readable: %s", entity, e)
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value
