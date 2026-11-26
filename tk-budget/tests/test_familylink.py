import json

import pytest

from tk_budget.familylink import read_minutes

ENV = {"SUPERVISOR_TOKEN": "tok"}


def fetcher(body):
    calls = []

    def fetch(url, headers):
        calls.append((url, headers))
        if isinstance(body, Exception):
            raise body
        return body if isinstance(body, str) else json.dumps(body)

    fetch.calls = calls
    return fetch


def test_numeric_state():
    f = fetcher({"state": "42"})
    assert read_minutes("sensor.x", ENV, f) == 42.0
    assert f.calls == [("http://supervisor/core/api/states/sensor.x", {"Authorization": "Bearer tok"})]


@pytest.mark.parametrize("body", [{"state": "unavailable"}, {"state": "unknown"}, {"state": "abc"},
                                  OSError("down"), "not json", {"nostate": 1}, [1]])
def test_bad_values_give_none(body):
    assert read_minutes("sensor.x", ENV, fetcher(body)) is None


def test_missing_token():
    f = fetcher({"state": "1"})
    assert read_minutes("sensor.x", {}, f) is None
    assert f.calls == []
