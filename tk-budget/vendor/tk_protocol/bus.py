"""Message bus abstraction: an in-memory bus for tests and a paho-mqtt 2.x implementation."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Protocol

Handler = Callable[[str, str], None]
Hook = Callable[[], None]

log = logging.getLogger(__name__)


class Bus(Protocol):
    def publish(self, topic: str, payload: str, retain: bool = False) -> None: ...

    def subscribe(self, pattern: str, handler: Handler, retained: bool = True) -> None:
        """retained=False: the handler never sees retained deliveries (replays), only live messages."""
        ...

    def on_connect(self, fn: Hook) -> None:
        """Register `fn` to run after every successful (re)connect, once subscriptions are renewed."""
        ...


def _run_hooks(hooks: list[Hook]) -> None:
    for fn in hooks:
        try:
            fn()
        except Exception:  # a hook must never break the connection handling
            log.exception("on_connect hook failed")


def topic_matches(pattern: str, topic: str) -> bool:
    """MQTT topic filter matching with `+` and trailing `#`."""
    p = pattern.split("/")
    t = topic.split("/")
    for i, part in enumerate(p):
        if part == "#":
            return True
        if i >= len(t):
            return False
        if part != "+" and part != t[i]:
            return False
    return len(p) == len(t)


class InMemoryBus:
    """Synchronous broker stand-in.

    `drop()` simulates a broker outage for this client: publishes are lost and
    nothing is delivered. `reconnect()` resubscribes, which replays retained messages
    (except to subscriptions made with retained=False), then runs the on_connect hooks.
    `published` records every (topic, payload, retain) that reached the broker.
    """

    def __init__(self) -> None:
        self._subs: list[tuple[str, Handler, bool]] = []
        self._hooks: list[Hook] = []
        self.retained: dict[str, str] = {}
        self.published: list[tuple[str, str, bool]] = []
        self.connected = True

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        if not self.connected:
            return
        self.published.append((topic, payload, retain))
        if retain:
            if payload == "":
                self.retained.pop(topic, None)
            else:
                self.retained[topic] = payload
        for pattern, handler, _ in list(self._subs):
            if topic_matches(pattern, topic):
                handler(topic, payload)

    def subscribe(self, pattern: str, handler: Handler, retained: bool = True) -> None:
        self._subs.append((pattern, handler, retained))
        if self.connected and retained:
            self._replay(pattern, handler)

    def on_connect(self, fn: Hook) -> None:
        self._hooks.append(fn)

    def drop(self) -> None:
        self.connected = False

    def reconnect(self) -> None:
        self.connected = True
        for pattern, handler, retained in list(self._subs):
            if retained:
                self._replay(pattern, handler)
        _run_hooks(list(self._hooks))

    def _replay(self, pattern: str, handler: Handler) -> None:
        for topic, payload in list(self.retained.items()):
            if topic_matches(pattern, topic):
                handler(topic, payload)


class PahoBus:
    """paho-mqtt client with a retained last will. paho is imported lazily.

    Works with paho 2.x (tk-budget's venv) and 1.x (Arch/Manjaro `python-paho-mqtt` 1.6)."""

    def __init__(self, host: str, port: int, username: str | None, password: str | None,
                 client_id: str, will_topic: str | None = None, will_payload: str | None = None) -> None:
        import paho.mqtt.client as mqtt

        self._subs: list[tuple[str, Handler, bool]] = []
        self._hooks: list[Hook] = []
        self._lock = threading.Lock()
        if hasattr(mqtt, "CallbackAPIVersion"):  # paho >= 2.0
            self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
            self._client.on_connect = self._on_connect_v2
        else:  # paho 1.x
            self._client = mqtt.Client(client_id=client_id)
            self._client.on_connect = self._on_connect_v1
        if username:
            self._client.username_pw_set(username, password)
        if will_topic is not None:
            self._client.will_set(will_topic, will_payload or "", qos=1, retain=True)
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=60)
        self._client.connect_async(host, port, keepalive=60)
        self._client.loop_start()

    def publish(self, topic: str, payload: str, retain: bool = False) -> None:
        self._client.publish(topic, payload, qos=1, retain=retain)

    def subscribe(self, pattern: str, handler: Handler, retained: bool = True) -> None:
        with self._lock:
            self._subs.append((pattern, handler, retained))
        self._client.subscribe(pattern, qos=1)

    def on_connect(self, fn: Hook) -> None:
        with self._lock:
            self._hooks.append(fn)

    def stop(self) -> None:
        self._client.loop_stop()
        self._client.disconnect()

    def _on_connect_v2(self, client, userdata, flags, reason_code, properties) -> None:  # noqa: ANN001
        self._connected(client, not reason_code.is_failure, reason_code)

    def _on_connect_v1(self, client, userdata, flags, rc) -> None:  # noqa: ANN001
        self._connected(client, rc == 0, f"rc={rc}")

    def _connected(self, client, ok: bool, reason: object) -> None:  # noqa: ANN001
        if not ok:
            log.warning("MQTT connect failed: %s", reason)
            return
        log.info("MQTT connected")
        with self._lock:
            patterns = list(dict.fromkeys(p for p, _, _ in self._subs))
            hooks = list(self._hooks)
        for p in patterns:
            client.subscribe(p, qos=1)
        _run_hooks(hooks)

    def _on_message(self, client, userdata, msg) -> None:  # noqa: ANN001
        payload = msg.payload.decode("utf-8", errors="replace")
        with self._lock:
            subs = list(self._subs)
        replayed = bool(getattr(msg, "retain", False))  # True only for retained deliveries on subscribe
        for pattern, handler, retained in subs:
            if replayed and not retained:
                continue
            if topic_matches(pattern, msg.topic):
                try:
                    handler(msg.topic, payload)
                except Exception:  # a bad message must never kill the network thread
                    log.exception("handler failed for %s", msg.topic)
