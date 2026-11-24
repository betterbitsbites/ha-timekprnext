import json

import pytest

from tk_budget.main import SUPERVISOR_MQTT_URL, load_options, resolve_broker

SERVICE = {"result": "ok", "data": {"host": "core-mosquitto", "port": 1883, "ssl": False,
                                    "protocol": "3.1.1", "username": "addons", "password": "secret"}}


class FakeFetch:
    def __init__(self, response=SERVICE):
        self.response = response
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        return json.dumps(self.response)


def opts(**over):
    base = {"db_path": "/data/x.db"}
    base.update(over)
    return base


def test_options_override_wins():
    fetch = FakeFetch()
    b = resolve_broker(opts(broker_host="mq.lan", broker_port=1884, broker_user="u", broker_password="p"),
                       {"SUPERVISOR_TOKEN": "t"}, fetch)
    assert b == {"host": "mq.lan", "port": 1884, "username": "u", "password": "p"}
    assert fetch.calls == []


def test_supervisor_service_used_when_no_override():
    fetch = FakeFetch()
    b = resolve_broker(opts(), {"SUPERVISOR_TOKEN": "tok"}, fetch)
    assert b == {"host": "core-mosquitto", "port": 1883, "username": "addons", "password": "secret"}
    assert fetch.calls == [(SUPERVISOR_MQTT_URL, {"Authorization": "Bearer tok"})]


def test_no_token_and_no_override_exits():
    with pytest.raises(SystemExit, match="MQTT service not available"):
        resolve_broker(opts(), {}, FakeFetch())


def test_ssl_service_rejected():
    svc = {"result": "ok", "data": dict(SERVICE["data"], ssl=True)}
    with pytest.raises(SystemExit, match="TLS"):
        resolve_broker(opts(), {"SUPERVISOR_TOKEN": "t"}, FakeFetch(svc))


def test_service_error_exits():
    with pytest.raises(SystemExit, match="MQTT service not available"):
        resolve_broker(opts(), {"SUPERVISOR_TOKEN": "t"}, FakeFetch({"result": "error", "message": "no"}))


def test_empty_option_strings_mean_unset(tmp_path):
    p = tmp_path / "options.json"
    p.write_text(json.dumps(opts(broker_host="", broker_user="", broker_password="")))
    o = load_options(str(p))
    assert o["broker_host"] is None and o["broker_user"] is None and o["broker_password"] is None
    fetch = FakeFetch()
    assert resolve_broker(o, {"SUPERVISOR_TOKEN": "t"}, fetch)["host"] == "core-mosquitto"


def test_options_without_kids_and_leftover_kids_ignored(tmp_path):
    p = tmp_path / "options.json"
    p.write_text(json.dumps({"db_path": "/data/x.db"}))
    assert "kids" not in load_options(str(p))
    p.write_text(json.dumps({"db_path": "/data/x.db", "kids": ["kid-a"]}))
    assert "kids" not in load_options(str(p))
