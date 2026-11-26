from tk_protocol.bus import InMemoryBus


def collect(bus, pattern):
    got = []
    bus.subscribe(pattern, lambda t, p: got.append((t, p)))
    return got


def test_inmemory_bus_delivers_retained_to_late_subscriber():
    bus = InMemoryBus()
    bus.publish("a/b", "1", retain=True)
    assert collect(bus, "a/b") == [("a/b", "1")]


def test_inmemory_bus_plus_wildcard():
    bus = InMemoryBus()
    got = collect(bus, "timekpr/+/kid-a/usage")
    bus.publish("timekpr/pc1/kid-a/usage", "x")
    bus.publish("timekpr/pc2/kid-a/usage", "y")
    bus.publish("timekpr/pc2/kid-b/usage", "z")
    assert got == [("timekpr/pc1/kid-a/usage", "x"), ("timekpr/pc2/kid-a/usage", "y")]


def test_inmemory_bus_nonretained_not_replayed():
    bus = InMemoryBus()
    bus.publish("a/b", "1")
    assert collect(bus, "a/b") == []


def test_inmemory_bus_drop_loses_messages_and_reconnect_replays_retained():
    bus = InMemoryBus()
    got = collect(bus, "a/#")
    bus.drop()
    bus.publish("a/x", "lost")
    bus.publish("a/y", "kept", retain=True)
    assert got == []
    assert bus.published == []
    bus.reconnect()
    bus.publish("a/z", "after", retain=True)
    assert ("a/z", "after") in got
    assert ("a/y", "kept") not in got


def test_retained_empty_payload_clears():
    bus = InMemoryBus()
    bus.publish("a/b", "1", retain=True)
    bus.publish("a/b", "", retain=True)
    assert collect(bus, "a/b") == []


def test_inmemory_bus_on_connect_hooks_run_on_reconnect_only():
    bus = InMemoryBus()
    calls = []
    bus.on_connect(lambda: calls.append("a"))
    bus.on_connect(lambda: calls.append("b"))
    assert calls == []
    bus.drop()
    bus.reconnect()
    assert calls == ["a", "b"]


def test_inmemory_bus_on_connect_hook_runs_after_resubscribe_and_errors_are_contained():
    bus = InMemoryBus()
    bus.publish("a/b", "1", retain=True)
    seen = []
    bus.subscribe("a/b", lambda t, p: seen.append("msg"))

    def bad():
        raise RuntimeError("boom")

    bus.on_connect(bad)
    bus.on_connect(lambda: seen.append("hook"))
    bus.reconnect()
    assert seen == ["msg", "msg", "hook"]


def test_inmemory_bus_non_retained_subscription_skips_retained_replay():
    bus = InMemoryBus()
    bus.publish("a/b", "old", retain=True)
    got = []
    bus.subscribe("a/b", lambda t, p: got.append(p), retained=False)
    assert got == []
    bus.reconnect()
    assert got == []
    bus.publish("a/b", "live", retain=True)  # live messages are delivered, retained flag or not
    bus.publish("a/b", "live2")
    assert got == ["live", "live2"]
