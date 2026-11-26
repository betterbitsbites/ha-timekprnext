import json
import logging

import pytest

from tk_budget.familylink import PhonePoller, read_minutes

ENV = {"SUPERVISOR_TOKEN": "tok"}


def fetcher(body):
    def fetch(url, headers):
        return json.dumps(body)
    return fetch


@pytest.mark.parametrize("unit,state,expected", [
    ("min", "30", 30.0), (None, "30", 30.0), ("s", "600", 10.0), ("h", "1.5", 90.0), ("d", "1", 1440.0),
    ("ms", "5", None), ("weird", "5", None),
])
def test_unit_conversion(unit, state, expected):
    attrs = {} if unit is None else {"unit_of_measurement": unit}
    assert read_minutes("sensor.x", ENV, fetcher({"state": state, "attributes": attrs})) == expected


def test_poller_returns_readable_values():
    assert PhonePoller({"kid-a": "sensor.x"}, ENV, fetcher({"state": "7"})).poll() == {"kid-a": 7.0}


def test_poller_warns_once_on_unreadable_then_info_on_recovery(caplog):
    caplog.set_level(logging.INFO)
    box = {"body": {"state": "unavailable"}}

    def fetch(url, headers):
        return json.dumps(box["body"])

    p = PhonePoller({"kid-a": "sensor.x"}, ENV, fetch)
    assert p.poll() == {}
    assert p.poll() == {}
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1
    assert "kid-a" in warns[0].getMessage() and "sensor.x" in warns[0].getMessage()
    box["body"] = {"state": "3"}
    assert p.poll() == {"kid-a": 3.0}
    assert p.poll() == {"kid-a": 3.0}
    assert len([r for r in caplog.records if r.levelno == logging.INFO]) == 1
    box["body"] = {"state": "bad"}
    p.poll()
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2


def test_poller_warns_once_for_missing_token(caplog):
    p = PhonePoller({"kid-a": "sensor.x"}, {}, fetcher({"state": "1"}))
    p.poll()
    p.poll()
    warns = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warns) == 1 and "SUPERVISOR_TOKEN" in warns[0].getMessage()
