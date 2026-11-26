from pathlib import Path

import pytest

from tk_agent.sessions import Loginctl, LoginctlError, Session, inactive

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


class FixtureRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        if argv[0] == "show-user":
            return fixture("show-user-sessions.txt")
        if argv[0] == "show-session":
            return fixture("show-session-manager.txt" if argv[1] == "6" else "show-session-user-locked.txt")
        return ""


def test_sessions_keeps_only_user_class():
    run = FixtureRunner()
    assert Loginctl(run).sessions("kid-a") == [
        Session(id="5", cls="user", type="wayland", state="active", locked=True, remote=False)]
    assert ["show-user", "kid-a", "-p", "Sessions", "--value"] in run.calls
    assert ["show-session", "5", "-p", "Id", "-p", "Class", "-p", "Type", "-p", "State", "-p", "Remote",
            "-p", "LockedHint"] in run.calls


def test_not_logged_in_is_empty():
    def run(argv):
        raise LoginctlError("exited 1")
    assert Loginctl(run).sessions("kid-a") == []


def test_empty_sessions_value():
    assert Loginctl(lambda argv: "\n").sessions("kid-a") == []


def test_vanished_session_skipped():
    def run(argv):
        if argv[0] == "show-user":
            return "5 7\n"
        if argv[1] == "5":
            raise LoginctlError("gone")
        return fixture("show-session-user-locked.txt").replace("Id=5", "Id=7")
    assert [s.id for s in Loginctl(run).sessions("kid-a")] == ["7"]


def test_terminate_commands():
    calls = []
    lc = Loginctl(lambda argv: calls.append(argv) or "")
    lc.terminate_session("5")
    lc.terminate_user("kid-a")
    assert calls == [["terminate-session", "5"], ["terminate-user", "kid-a"]]


def test_validation():
    lc = Loginctl(lambda argv: "")
    with pytest.raises(ValueError):
        lc.terminate_user("-x")
    with pytest.raises(ValueError):
        lc.terminate_session("5; rm")
    with pytest.raises(ValueError):
        lc.sessions("a b")


def test_inactive():
    base = dict(id="1", cls="user", type="x11", remote=False)
    assert not inactive(Session(state="active", locked=False, **base))
    assert inactive(Session(state="active", locked=True, **base))
    assert inactive(Session(state="online", locked=False, **base))
