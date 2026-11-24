"""Add-on entry point: read options, find the broker, run the budget service forever.

The broker comes from the Supervisor's MQTT service (`services: mqtt:need` in config.yaml),
so with the Mosquitto add-on nothing has to be configured. Setting `broker_host` in the
add-on options overrides that and uses the `broker_*` options instead.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.request
from typing import Callable, Mapping

from tk_protocol.bus import PahoBus
from tk_protocol.clock import SystemClock

from . import discovery
from .service import BudgetService
from .store import Store

OPTIONS_PATH = os.environ.get("TK_BUDGET_OPTIONS", "/data/options.json")
SUPERVISOR_MQTT_URL = "http://supervisor/services/mqtt"
TICK_S = 30

Fetch = Callable[[str, dict], str]

log = logging.getLogger("tk_budget")


def _blank_to_none(v: object) -> str | None:
    s = "" if v is None else str(v).strip()
    return s or None


def load_options(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        opts = json.load(f)
    return {
        "broker_host": _blank_to_none(opts.get("broker_host")),
        "broker_port": int(opts.get("broker_port") or 1883),
        "broker_user": _blank_to_none(opts.get("broker_user")),
        "broker_password": _blank_to_none(opts.get("broker_password")),
        "db_path": opts.get("db_path") or "/data/tk-budget.db",
    }


def _urllib_fetch(url: str, headers: dict) -> str:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode("utf-8")


def resolve_broker(opts: Mapping, env: Mapping[str, str], fetch: Fetch = _urllib_fetch) -> dict:
    """Broker connection: add-on options if `broker_host` is set, else the Supervisor MQTT service."""
    if opts.get("broker_host"):
        return {"host": opts["broker_host"], "port": int(opts.get("broker_port") or 1883),
                "username": opts.get("broker_user"), "password": opts.get("broker_password")}
    unavailable = "tk-budget: MQTT service not available; install the Mosquitto add-on or set broker_host"
    token = env.get("SUPERVISOR_TOKEN")
    if not token:
        raise SystemExit(unavailable)
    try:
        body = json.loads(fetch(SUPERVISOR_MQTT_URL, {"Authorization": f"Bearer {token}"}))
        data = body["data"] if body.get("result") == "ok" else None
        if not data:
            raise ValueError(body.get("message", "no data"))
    except Exception as e:  # network error, bad JSON, error result
        raise SystemExit(f"{unavailable} ({e})") from None
    if data.get("ssl"):
        raise SystemExit("tk-budget: the MQTT service requires TLS, which is not supported yet; set broker_host")
    return {"host": data["host"], "port": int(data["port"]),
            "username": data.get("username"), "password": data.get("password")}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    opts = load_options(OPTIONS_PATH)
    broker = resolve_broker(opts, os.environ)
    log.info("using MQTT broker %s:%s as %s", broker["host"], broker["port"], broker["username"] or "(anonymous)")
    store = Store(opts["db_path"])
    bus = PahoBus(broker["host"], broker["port"], broker["username"], broker["password"],
                  client_id="tk-budget", will_topic=discovery.STATUS_TOPIC, will_payload="offline")
    service = BudgetService(bus, store, SystemClock())
    service.start()
    log.info("tk-budget running; known kids: %s", ", ".join(store.kids()) or "none yet")
    while True:
        time.sleep(TICK_S)
        try:
            service.tick()
        except Exception:
            log.exception("tick failed")


if __name__ == "__main__":
    main()
