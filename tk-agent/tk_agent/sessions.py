"""Thin wrapper around `loginctl`: list a user's login sessions and end them.

Formats verified in spike/RESULTS.md. `loginctl show-user` exits non-zero for a user who is
not logged in; that is reported as "no sessions", not as an error.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Callable

Runner = Callable[[list[str]], str]

_SESSION_ID_RE = re.compile(r"^[0-9a-z]+$")


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


def inactive(s: Session) -> bool:
    return s.locked or s.state != "active"


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
                                 "-p", "State", "-p", "Remote", "-p", "LockedHint"])
            except LoginctlUnavailable:
                raise
            except LoginctlError:
                continue  # ended meanwhile
            props = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
            cls = props.get("Class", "")
            if cls.startswith("user"):
                found.append(Session(id=sid, cls=cls, type=props.get("Type", ""), state=props.get("State", ""),
                                     locked=props.get("LockedHint") == "yes", remote=props.get("Remote") == "yes"))
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
