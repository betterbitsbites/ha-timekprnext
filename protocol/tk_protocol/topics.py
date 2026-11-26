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


def sessions(host: str, kid: str) -> str:
    return f"{PREFIX}/{host}/{kid}/sessions"


def action(kid: str) -> str:
    return f"{PREFIX}/{kid}/action"


def parse_sessions_topic(topic: str) -> tuple[str, str] | None:
    """Return (host, kid) for a sessions topic, else None."""
    parts = topic.split("/")
    if len(parts) != 4 or parts[0] != PREFIX or parts[3] != "sessions":
        return None
    if not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


def parse_status_topic(topic: str) -> str | None:
    """Return the host of a client status topic; None for `_budget` or malformed."""
    parts = topic.split("/")
    if len(parts) != 3 or parts[0] != PREFIX or parts[2] != "status":
        return None
    if not parts[1] or parts[1] == "_budget":
        return None
    return parts[1]


def parse_usage_topic(topic: str) -> tuple[str, str] | None:
    """Return (host, kid) for a usage topic, else None."""
    parts = topic.split("/")
    if len(parts) != 4 or parts[0] != PREFIX or parts[3] != "usage":
        return None
    if not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]
