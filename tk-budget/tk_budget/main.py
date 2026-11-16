"""Add-on entry point: read options, connect to the broker, run the budget service forever."""

from __future__ import annotations

import json
import logging
import os
import time

from tk_protocol.bus import PahoBus
from tk_protocol.clock import SystemClock

from . import discovery
from .service import BudgetService
from .store import Store

OPTIONS_PATH = os.environ.get("TK_BUDGET_OPTIONS", "/data/options.json")
TICK_S = 30

log = logging.getLogger("tk_budget")


def load_options(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        opts = json.load(f)
    kids = [str(k).strip() for k in opts.get("kids", []) if str(k).strip()]
    if not kids:
        raise SystemExit("tk-budget: option 'kids' is empty")
    return {
        "broker_host": opts.get("broker_host", "core-mosquitto"),
        "broker_port": int(opts.get("broker_port", 1883)),
        "broker_user": opts.get("broker_user") or None,
        "broker_password": opts.get("broker_password") or None,
        "kids": kids,
        "db_path": opts.get("db_path", "/data/tk-budget.db"),
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    opts = load_options(OPTIONS_PATH)
    store = Store(opts["db_path"])
    bus = PahoBus(opts["broker_host"], opts["broker_port"], opts["broker_user"], opts["broker_password"],
                  client_id="tk-budget", will_topic=discovery.STATUS_TOPIC, will_payload="offline")
    service = BudgetService(bus, store, SystemClock(), opts["kids"])
    service.start()
    log.info("tk-budget running for kids: %s", ", ".join(opts["kids"]))
    while True:
        time.sleep(TICK_S)
        try:
            service.tick()
        except Exception:
            log.exception("tick failed")


if __name__ == "__main__":
    main()
