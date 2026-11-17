from pathlib import Path

import pytest

from tk_agent.timekpra import Timekpra, TimekpraError

FIXTURES = Path(__file__).parent / "fixtures"


class FakeRunner:
    def __init__(self, output: str = "") -> None:
        self.output = output
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        return self.output


def test_parse_time_left_from_fixture():
    run = FakeRunner((FIXTURES / "getuserinfort.txt").read_text())
    assert Timekpra(run).time_left_s("tkprobe") == 5130
    assert run.calls == [["--getuserinfort", "tkprobe"]]


def test_parse_time_left_from_full_info_fixture():
    run = FakeRunner((FIXTURES / "getuserinfo.txt").read_text())
    assert Timekpra(run).time_left_s("tkprobe") == 5400


def test_time_left_missing_field_raises():
    with pytest.raises(TimekpraError):
        Timekpra(FakeRunner("# Configuration for user x:\nTIME_LEFT_DAY: 5\n")).time_left_s("x")
    with pytest.raises(TimekpraError):
        Timekpra(FakeRunner("ACTUAL_TIME_LEFT_DAY: soon\n")).time_left_s("x")


def test_set_time_left_builds_expected_command():
    run = FakeRunner()
    Timekpra(run).set_time_left_s("tkprobe", 3600)
    Timekpra(run).set_time_left_s("tkprobe", -5)
    assert run.calls == [["--settimeleft", "tkprobe", "=", "3600"], ["--settimeleft", "tkprobe", "=", "0"]]


def test_set_lockout_type():
    run = FakeRunner()
    Timekpra(run).set_lockout_type("tkprobe", "lock")
    assert run.calls == [["--setlockouttype", "tkprobe", "lock"]]
    with pytest.raises(ValueError):
        Timekpra(run).set_lockout_type("tkprobe", "explode")


def test_option_like_username_rejected():
    with pytest.raises(ValueError):
        Timekpra(FakeRunner()).time_left_s("--help")
