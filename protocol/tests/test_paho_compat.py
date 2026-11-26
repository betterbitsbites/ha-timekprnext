"""PahoBus must run on paho-mqtt 1.x (Arch/Manjaro `python-paho-mqtt` 1.6) and 2.x (tk-budget)."""

import sys
import types

import pytest

from tk_protocol.bus import PahoBus


class _Client:
    instances: list["_Client"] = []

    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        self.subscribed: list[str] = []
        _Client.instances.append(self)

    def username_pw_set(self, u, p): pass
    def will_set(self, *a, **k): pass
    def reconnect_delay_set(self, **k): pass
    def connect_async(self, *a, **k): pass
    def loop_start(self): pass
    def subscribe(self, pattern, qos=0): self.subscribed.append(pattern)


class _Msg:
    def __init__(self, topic, payload, retain):
        self.topic, self.payload, self.retain = topic, payload.encode(), retain


def _connect(client, v2, ok=True):
    if v2:
        client.on_connect(client, None, {}, _Reason(not ok), None)
    else:
        client.on_connect(client, None, {}, 0 if ok else 5)


class _Reason:
    def __init__(self, failure): self.is_failure = failure


def _install(monkeypatch, v2: bool):
    mod = types.ModuleType("paho.mqtt.client")
    mod.Client = _Client
    if v2:
        mod.CallbackAPIVersion = types.SimpleNamespace(VERSION2="v2")
    paho, pm = types.ModuleType("paho"), types.ModuleType("paho.mqtt")
    paho.mqtt, pm.client = pm, mod
    monkeypatch.setitem(sys.modules, "paho", paho)
    monkeypatch.setitem(sys.modules, "paho.mqtt", pm)
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", mod)
    _Client.instances.clear()


@pytest.mark.parametrize("v2", [False, True])
def test_pahobus_connects_and_resubscribes_on_paho_1_and_2(monkeypatch, v2):
    _install(monkeypatch, v2)
    bus = PahoBus("h", 1883, "u", "p", client_id="c")
    client = _Client.instances[0]
    assert client.kwargs["client_id"] == "c"
    assert client.args == (("v2",) if v2 else ())
    bus.subscribe("a/#", lambda t, p: None)
    client.subscribed.clear()
    if v2:
        client.on_connect(client, None, {}, _Reason(False), None)
    else:
        client.on_connect(client, None, {}, 0)
    assert client.subscribed == ["a/#"]


@pytest.mark.parametrize("v2", [False, True])
def test_pahobus_failed_connect_does_not_subscribe(monkeypatch, v2):
    _install(monkeypatch, v2)
    bus = PahoBus("h", 1883, None, None, client_id="c")
    client = _Client.instances[0]
    bus.subscribe("a/#", lambda t, p: None)
    client.subscribed.clear()
    if v2:
        client.on_connect(client, None, {}, _Reason(True), None)
    else:
        client.on_connect(client, None, {}, 5)  # 5 = not authorised
    assert client.subscribed == []


@pytest.mark.parametrize("v2", [False, True])
def test_pahobus_on_connect_hooks_after_resubscribe_on_every_successful_connect(monkeypatch, v2):
    _install(monkeypatch, v2)
    bus = PahoBus("h", 1883, None, None, client_id="c")
    client = _Client.instances[0]
    bus.subscribe("a/#", lambda t, p: None)
    seen = []

    def bad():
        raise RuntimeError("boom")

    bus.on_connect(bad)  # must not raise into paho
    bus.on_connect(lambda: seen.append(list(client.subscribed)))
    client.subscribed.clear()
    _connect(client, v2, ok=False)
    assert seen == []
    _connect(client, v2)
    _connect(client, v2)
    assert seen == [["a/#"], ["a/#", "a/#"]]


@pytest.mark.parametrize("v2", [False, True])
def test_pahobus_non_retained_subscription_skips_retained_messages(monkeypatch, v2):
    _install(monkeypatch, v2)
    bus = PahoBus("h", 1883, None, None, client_id="c")
    client = _Client.instances[0]
    all_, live = [], []
    bus.subscribe("a/#", lambda t, p: all_.append(p))
    bus.subscribe("a/#", lambda t, p: live.append(p), retained=False)
    client.on_message(client, None, _Msg("a/x", "old", True))
    client.on_message(client, None, _Msg("a/x", "new", False))
    assert all_ == ["old", "new"]
    assert live == ["new"]
