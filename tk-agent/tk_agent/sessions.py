"""Thin wrapper around `loginctl`: list a user's login sessions and end them.

Formats verified in spike/RESULTS.md. `loginctl show-user` exits non-zero for a user who is
not logged in; that is reported as "no sessions", not as an error.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Callable

from .config import Config

Runner = Callable[[list[str]], str]

_SESSION_ID_RE = re.compile(r"^[0-9a-z]+$")
_X11_DISPLAY_RE = re.compile(r"^:(\d+)(?:\.\d+)?$")
GRAPHICAL = ("wayland", "x11")


class LoginctlError(RuntimeError):
    """loginctl exited non-zero, timed out or could not be run."""


class LoginctlUnavailable(LoginctlError):
    """loginctl could not be run at all (missing binary, timeout)."""


def subprocess_runner(argv: list[str]) -> str:
    try:
        proc = subprocess.run(["loginctl", *argv], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise LoginctlUnavailable(f"loginctl {argv[0]} failed: {e}") from e
    if proc.returncode != 0:
        raise LoginctlError(f"loginctl {argv[0]} exited {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


@dataclass(frozen=True)
class Session:
    id: str
    cls: str
    type: str
    state: str
    locked: bool
    remote: bool
    display: str = ""  # X11 display such as ":1"; empty for Wayland and text sessions


def inactive(s: Session) -> bool:
    return s.locked or s.state != "active"


def primary(sessions: list[Session]) -> Session | None:
    """The session that best describes where the kid is: a graphical one that is active and
    unlocked, else a graphical locked one, else a graphical one switched away from (`online`),
    else the first session that is not closing; None if there is none."""
    live = [s for s in sessions if s.state != "closing"]
    graphical = [s for s in live if s.type in GRAPHICAL]
    for pick in (lambda s: s.state == "active" and not s.locked, lambda s: s.locked,
                 lambda s: s.state == "online"):
        for s in graphical:
            if pick(s):
                return s
    return live[0] if live else None


def connect_url(cfg: Config, kid: str, s: Session | None) -> str:
    """Remote-desktop URL for session `s`: RDP for Wayland, VNC by display number for X11, else ""."""
    if s is None or not cfg.remote_host:
        return ""
    if s.type == "wayland":
        return f"rdp://{kid}@{cfg.remote_host}:{cfg.rdp_port}"
    if s.type == "x11":
        m = _X11_DISPLAY_RE.match(s.display)
        if m:
            port = cfg.vnc_base_port + int(m.group(1))
            if port <= 65535:
                return f"vnc://{cfg.remote_host}:{port}"
    return ""


class Loginctl:
    def __init__(self, runner: Runner = subprocess_runner) -> None:
        self._run = runner

    def sessions(self, user: str) -> list[Session]:
        """The user's `Class=user*` sessions (greeter/manager sessions are left out)."""
        user = _user(user)
        try:
            ids = self._run(["show-user", user, "-p", "Sessions", "--value"]).split()
        except LoginctlUnavailable:
            raise
        except LoginctlError:
            return []  # not logged in
        found = []
        for sid in ids:
            try:
                out = self._run(["show-session", _session_id(sid), "-p", "Id", "-p", "Class", "-p", "Type",
                                 "-p", "State", "-p", "Remote", "-p", "LockedHint", "-p", "Display"])
            except LoginctlUnavailable:
                raise
            except LoginctlError:
                continue  # ended meanwhile
            props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
            cls = props.get("Class", "")
            if cls.startswith("user"):
                found.append(Session(id=sid, cls=cls, type=props.get("Type", ""), state=props.get("State", ""),
                                     locked=props.get("LockedHint") == "yes", remote=props.get("Remote") == "yes",
                                     display=props.get("Display", "")))
        return found

    def terminate_session(self, session_id: str) -> None:
        self._run(["terminate-session", _session_id(session_id)])

    def terminate_user(self, user: str) -> None:
        self._run(["terminate-user", _user(user)])


def _user(user: str) -> str:
    if not user or user.startswith("-") or any(c.isspace() for c in user):
        raise ValueError(f"invalid user name {user!r}")
    return user


def _session_id(session_id: str) -> str:
    if not _SESSION_ID_RE.match(session_id):
        raise ValueError(f"invalid session id {session_id!r}")
    return session_id
