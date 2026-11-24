"""systemd entry point: `python -m tk_agent.main [config]` (default /etc/tk-agent.conf)."""

from __future__ import annotations

import logging
import os
import stat
import sys
import time

from tk_protocol import topics
from tk_protocol.bus import PahoBus
from tk_protocol.clock import SystemClock

from .agent import Agent
from .config import load
from .meter import UsageMeter
from .timekpra import Timekpra

log = logging.getLogger("tk_agent")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    path = sys.argv[1] if len(sys.argv) > 1 else "/etc/tk-agent.conf"
    mode = stat.S_IMODE(os.stat(path).st_mode)
    if mode & 0o077:
        log.warning("%s has mode %o; it holds MQTT credentials, use chmod 600", path, mode)
    cfg = load(path)
    os.makedirs(cfg.state_dir, mode=0o700, exist_ok=True)

    clock = SystemClock()
    bus = PahoBus(cfg.broker_host, cfg.broker_port, cfg.username, cfg.password,
                  client_id=f"tk-agent-{cfg.host_id}",
                  will_topic=topics.status(cfg.host_id), will_payload="offline")
    agent = Agent(cfg, bus, Timekpra(),
                  lambda user: UsageMeter(os.path.join(cfg.state_dir, f"{user}.json"), clock), clock)
    agent.start()
    log.info("tk-agent %s running for all timekpr users", cfg.host_id)
    while True:
        try:
            agent.tick()
        except Exception:
            log.exception("tick failed")
        time.sleep(cfg.interval_s)


if __name__ == "__main__":
    main()
