"""Message bus abstraction: an in-memory bus for tests and a paho-mqtt 2.x implementation."""

from __future__ import annotations

import logging
import threading
from typing import Callable, Protocol

Handler = Callable[[str, str], None]

log = logging.getLogger(__name__)


class Bus(Protocol):
    def publish(self, topic: str, payload: str, retain: bool = False) -> None: ...

    def subscribe(self, pattern: str, handler: Handler) -> None: ...


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
    nothing is delivered. `reconnect()` resubscribes, which replays retained messages.
    `published` records every (topic, payload, retain) that reached the broker.
    """

    def __init__(self) -> None:
        self._subs: list[tuple[str, Handler]] = []
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
        for pattern, handler in list(self._subs):
            if topic_matches(pattern, topic):
                handler(topic, payload)

    def subscribe(self, pattern: str, handler: Handler) -> None:
        self._subs.append((pattern, handler))
        if self.connected:
            self._replay(pattern, handler)

    def drop(self) -> None:
        self.connected = False

    def reconnect(self) -> None:
        self.connected = True
        for pattern, handler in list(self._subs):
            self._replay(pattern, handler)

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

        self._subs: list[tuple[str, Handler]] = []
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

    def subscribe(self, pattern: str, handler: Handler) -> None:
        with self._lock:
            self._subs.append((pattern, handler))
        self._client.subscribe(pattern, qos=1)

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
            patterns = {p for p, _ in self._subs}
        for p in patterns:
            client.subscribe(p, qos=1)

    def _on_message(self, client, userdata, msg) -> None:  # noqa: ANN001
        payload = msg.payload.decode("utf-8", errors="replace")
        with self._lock:
            subs = list(self._subs)
        for pattern, handler in subs:
            if topic_matches(pattern, msg.topic):
                try:
                    handler(msg.topic, payload)
                except Exception:  # a bad message must never kill the network thread
                    log.exception("handler failed for %s", msg.topic)
