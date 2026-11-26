from pathlib import Path

import pytest

from tk_agent.config import Config
from tk_agent.sessions import Loginctl, LoginctlError, Session, connect_url, inactive, primary

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
        Session(id="5", cls="user", type="wayland", state="active", locked=True, remote=False, display="")]
    assert ["show-user", "kid-a", "-p", "Sessions", "--value"] in run.calls
    assert ["show-session", "5", "-p", "Id", "-p", "Class", "-p", "Type", "-p", "State", "-p", "Remote",
            "-p", "LockedHint", "-p", "Display"] in run.calls


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


def fixture_sessions(*names: str) -> list[Session]:
    """Sessions parsed by Loginctl from the given show-session fixtures, in order."""
    by_id = {fixture(n).split("\n", 1)[0].split("=", 1)[1]: n for n in names}

    def run(argv):
        if argv[0] == "show-user":
            return " ".join(by_id) + "\n"
        return fixture(by_id[argv[1]])
    return Loginctl(run).sessions("kid-a")


def test_x11_fixture_parsed_with_display():
    assert fixture_sessions("show-session-x11-locked.txt") == [
        Session(id="7", cls="user", type="x11", state="active", locked=True, remote=False, display=":1")]


def s(id, type="wayland", state="active", locked=False, display=""):
    return Session(id=id, cls="user", type=type, state=state, locked=locked, remote=False, display=display)


def test_primary_wayland_active_wins():
    active = s("2")
    assert primary([s("1", type="tty"), s("3", locked=True), active, s("4", state="online")]) == active


def test_primary_x11_locked():
    [x11] = fixture_sessions("show-session-x11-locked.txt")
    assert primary(fixture_sessions("show-session-online.txt", "show-session-x11-locked.txt")) == x11
    assert primary([s("1", type="tty"), x11]) == x11


def test_primary_switched_away():
    [online] = fixture_sessions("show-session-online.txt")
    assert primary([s("1", type="tty"), online]) == online


def test_primary_remote_ssh_only():
    ssh = s("9", type="tty")
    assert primary([s("8", type="tty", state="closing"), ssh]) == ssh


def test_primary_no_session():
    assert primary([]) is None
    assert primary([s("8", state="closing")]) is None


CFG = Config(broker_host="b", broker_port=1883, username=None, password=None, host_id="pc1",
             remote_host="pc1", rdp_port=3390, vnc_base_port=5900)


def test_connect_url_wayland_is_rdp_with_kid():
    assert connect_url(CFG, "kid-a", s("1")) == "rdp://kid-a@pc1:3390"


@pytest.mark.parametrize("display,url", [(":1", "vnc://pc1:5901"), (":0", "vnc://pc1:5900"),
                                         (":12.0", "vnc://pc1:5912"), ("", ""), ("host:1", ""), (":x", "")])
def test_connect_url_x11_is_vnc_by_display(display, url):
    assert connect_url(CFG, "kid-a", s("1", type="x11", display=display)) == url


def test_connect_url_empty_otherwise():
    assert connect_url(CFG, "kid-a", s("1", type="tty")) == ""
    assert connect_url(CFG, "kid-a", s("1", type="unspecified")) == ""
    assert connect_url(CFG, "kid-a", None) == ""
