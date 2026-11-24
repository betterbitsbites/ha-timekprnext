"""Agent configuration, INI file `/etc/tk-agent.conf` (mode 600, root:root).

Nothing per kid or per PC is configured: the kids are timekpr's users, and the host id
(which keeps the PCs' cumulative usage reports apart) is derived from the host name.
"""

from __future__ import annotations

import configparser
import logging
import re
import socket
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[a-z0-9_-]+$")


@dataclass(frozen=True)
class Config:
    broker_host: str
    broker_port: int
    username: str | None
    password: str | None
    host_id: str
    interval_s: int = 45
    state_dir: str = "/var/lib/tk-agent"


def host_key(hostname: str) -> str:
    """Host id from the host name: first label, lower case, other characters as `-`."""
    key = re.sub(r"[^a-z0-9_-]", "-", hostname.split(".")[0].lower())
    return key or "pc"


def load(path: str, hostname: Callable[[], str] = socket.gethostname) -> Config:
    cp = configparser.ConfigParser()
    cp.optionxform = str
    if not cp.read(path, encoding="utf-8"):
        raise ValueError(f"cannot read {path}")
    if "broker" not in cp:
        raise ValueError("missing section 'broker'")
    if "kids" in cp:
        log.warning("%s: section [kids] is ignored; every timekpr user is a kid", path)
    b = cp["broker"]
    a = cp["agent"] if "agent" in cp else cp[cp.default_section]

    host_id = a.get("host_id", "").strip() or host_key(hostname())
    if not _ID_RE.match(host_id):
        raise ValueError(f"host_id {host_id!r} must match [a-z0-9_-]+")
    interval = a.getint("interval_s", 45)
    if not 30 <= interval <= 60:
        raise ValueError(f"interval_s must be 30..60, got {interval}")

    return Config(
        broker_host=b.get("host", "localhost"),
        broker_port=b.getint("port", 1883),
        username=b.get("username") or None,
        password=b.get("password") or None,
        host_id=host_id,
        interval_s=interval,
        state_dir=a.get("state_dir", "/var/lib/tk-agent"),
    )
