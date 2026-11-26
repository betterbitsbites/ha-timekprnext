"""systemd entry point: `python -m tk_agent.main [config]` (default /etc/tk-agent.conf)."""

from __future__ import annotations

import logging
import os
import stat
import sys

from tk_protocol import topics
from tk_protocol.bus import PahoBus
from tk_protocol.clock import SystemClock

from .agent import Agent
from .config import load
from .meter import UsageMeter
from .sessions import Loginctl
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
    # meter names: `<user>` (total) and `<user>.play` (PlayTime), each in `<state_dir>/<name>.json`
    agent = Agent(cfg, bus, Timekpra(),
                  lambda name: UsageMeter(os.path.join(cfg.state_dir, f"{name}.json"), clock), clock, Loginctl())
    agent.start()
    log.info("tk-agent %s running for all timekpr users", cfg.host_id)
    while True:
        try:
            agent.tick()
        except Exception:
            log.exception("tick failed")
        # a changed remaining/settings wakes the loop at once; the tick still runs on this thread
        agent.wake.wait(cfg.interval_s)
        agent.wake.clear()


if __name__ == "__main__":
    main()
