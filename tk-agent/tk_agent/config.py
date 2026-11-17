"""Agent configuration, INI file `/etc/tk-agent.conf` (mode 600, root:root)."""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass, field

_ID_RE = re.compile(r"^[a-z0-9_-]+$")


@dataclass(frozen=True)
class Config:
    broker_host: str
    broker_port: int
    username: str | None
    password: str | None
    host_id: str
    interval_s: int = 45
    kids: dict[str, str] = field(default_factory=dict)  # timekpr user -> kid id
    state_dir: str = "/var/lib/tk-agent"


def load(path: str) -> Config:
    cp = configparser.ConfigParser()
    cp.optionxform = str  # keep user names as written
    if not cp.read(path, encoding="utf-8"):
        raise ValueError(f"cannot read {path}")
    try:
        b, a = cp["broker"], cp["agent"]
        kids = dict(cp["kids"])
    except KeyError as e:
        raise ValueError(f"missing section {e}") from None

    host_id = a.get("host_id", "").strip()
    _check_id("host_id", host_id)
    if not kids:
        raise ValueError("[kids] is empty")
    for user, kid in kids.items():
        _check_id(f"kid id for {user}", kid)
        if kid == host_id:
            raise ValueError(f"host_id {host_id!r} equals a kid id; they share the topic namespace")
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
        kids=kids,
        state_dir=a.get("state_dir", "/var/lib/tk-agent"),
    )


def _check_id(what: str, value: str) -> None:
    if not _ID_RE.match(value):
        raise ValueError(f"{what} {value!r} must match [a-z0-9_-]+")
