"""Thin wrapper around the `timekpra` CLI (the only way this project touches timekpr).

Formats verified in spike/RESULTS.md (timekpr-next 0.5.10): `--settimeleft <user> = <seconds>`,
time left is `ACTUAL_TIME_LEFT_DAY` (seconds) in `--getuserinfort`. timekpra exits 0 even
for values it ignores, so callers read the value back instead of trusting the exit code.
For a user without a session there is no real-time info and writes are ignored.
"""

from __future__ import annotations

import re
import subprocess
from typing import Callable

LOCKOUT_TYPES = ("lock", "shutdown", "terminate", "suspend")
_TIME_LEFT_RE = re.compile(r"^ACTUAL_TIME_LEFT_DAY:\s*(\S+)\s*$", re.MULTILINE)
_USERLIST_HEADER_RE = re.compile(r"^\d+ users? in total:$")

Runner = Callable[[list[str]], str]


class TimekpraError(RuntimeError):
    pass


def subprocess_runner(argv: list[str]) -> str:
    try:
        proc = subprocess.run(["timekpra", *argv], capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise TimekpraError(f"timekpra {argv[0]} failed: {e}") from e
    if proc.returncode != 0:
        raise TimekpraError(f"timekpra {argv[0]} exited {proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


class Timekpra:
    def __init__(self, runner: Runner = subprocess_runner) -> None:
        self._run = runner

    def users(self) -> list[str]:
        """timekpr's users: a header `N users in total:`, then one name per line."""
        lines = [line.strip() for line in self._run(["--userlist"]).splitlines() if line.strip()]
        if not lines or not _USERLIST_HEADER_RE.match(lines[0]):
            raise TimekpraError("unexpected timekpra --userlist output")
        return lines[1:]

    def time_left_s(self, user: str) -> int | None:
        """Seconds left today, or None if the user has no session (timekpra prints only the
        header then, and `--settimeleft` has no effect: verified in Task 9)."""
        out = self._run(["--getuserinfort", _user(user)])
        if [line for line in out.splitlines() if line.strip()] == [f"# Configuration for user {user}:"]:
            return None
        m = _TIME_LEFT_RE.search(out)
        if not m:
            raise TimekpraError(f"ACTUAL_TIME_LEFT_DAY missing in timekpra output for {user}")
        try:
            return int(m.group(1))
        except ValueError:
            raise TimekpraError(f"ACTUAL_TIME_LEFT_DAY not an integer: {m.group(1)!r}") from None

    def set_time_left_s(self, user: str, seconds: int) -> None:
        self._run(["--settimeleft", _user(user), "=", str(max(0, int(seconds)))])

    def set_lockout_type(self, user: str, value: str) -> None:
        if value not in LOCKOUT_TYPES:
            raise ValueError(f"unsupported lockout type {value!r}")
        self._run(["--setlockouttype", _user(user), value])


def _user(user: str) -> str:
    if not user or user.startswith("-") or any(c.isspace() for c in user):
        raise ValueError(f"invalid user name {user!r}")
    return user
