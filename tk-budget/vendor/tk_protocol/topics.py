"""MQTT topic names. All topics live under the `timekpr/` prefix."""

PREFIX = "timekpr"


def usage(host: str, kid: str) -> str:
    return f"{PREFIX}/{host}/{kid}/usage"


def remaining(kid: str) -> str:
    return f"{PREFIX}/{kid}/remaining"


def settings(kid: str) -> str:
    return f"{PREFIX}/{kid}/settings"


def status(host: str) -> str:
    return f"{PREFIX}/{host}/status"


def event(host: str, kid: str) -> str:
    return f"{PREFIX}/{host}/{kid}/event"


def cmd(kid: str) -> str:
    return f"{PREFIX}/{kid}/cmd"


def parse_usage_topic(topic: str) -> tuple[str, str] | None:
    """Return (host, kid) for a usage topic, else None."""
    parts = topic.split("/")
    if len(parts) != 4 or parts[0] != PREFIX or parts[3] != "usage":
        return None
    if not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]
