"""JSON payloads exchanged over MQTT. `from_json` raises ValueError on anything malformed."""

from __future__ import annotations

import dataclasses
import json
import typing
from dataclasses import dataclass
from typing import Any, Literal, Self

EventType = Literal["time_low", "time_up", "login", "logout"]
CmdOp = Literal["adjust_total"]


class _Payload:
    def to_json(self) -> str:
        return json.dumps(dataclasses.asdict(self), sort_keys=True)  # type: ignore[call-overload]

    @classmethod
    def from_json(cls, s: str) -> Self:
        try:
            data = json.loads(s)
        except (TypeError, json.JSONDecodeError) as e:
            raise ValueError(f"invalid JSON: {e}") from None
        if not isinstance(data, dict):
            raise ValueError("payload must be a JSON object")
        hints = typing.get_type_hints(cls)
        kwargs: dict[str, Any] = {}
        for f in dataclasses.fields(cls):  # type: ignore[arg-type]
            if f.name not in data:
                if f.default is not dataclasses.MISSING:
                    continue  # optional field, added later: keep the dataclass default
                raise ValueError(f"missing field {f.name!r}")
            kwargs[f.name] = _check(f.name, hints[f.name], data[f.name])
        return cls(**kwargs)


def _check(name: str, tp: Any, value: Any) -> Any:
    if typing.get_origin(tp) is Literal:
        if value not in typing.get_args(tp):
            raise ValueError(f"{name}: {value!r} not one of {typing.get_args(tp)}")
        return value
    if tp is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{name}: expected bool")
        return value
    if tp is int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{name}: expected int")
        return value
    if tp is str:
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name}: expected non-empty string")
        return value
    raise TypeError(f"unsupported field type {tp!r}")


def _non_negative(**fields: int) -> None:
    for name, v in fields.items():
        if v < 0:
            raise ValueError(f"{name} must be >= 0")


@dataclass(frozen=True)
class Usage(_Payload):
    """Cumulative usage of one kid on one host: this ISO week and this day."""

    week: str
    total_s: int
    day: str
    total_day_s: int

    def __post_init__(self) -> None:
        _non_negative(total_s=self.total_s, total_day_s=self.total_day_s)


@dataclass(frozen=True)
class Remaining(_Payload):
    """Shared remaining time of one kid. `limited` false: no limit, `total_s` is 0 and ignored.
    Payloads from before the field existed parse as limited (fail closed)."""

    week: str
    total_s: int
    limited: bool = True

    def __post_init__(self) -> None:
        _non_negative(total_s=self.total_s)


@dataclass(frozen=True)
class Settings(_Payload):
    time_up_action: str


@dataclass(frozen=True)
class Event(_Payload):
    type: EventType
    ts: str


@dataclass(frozen=True)
class Cmd(_Payload):
    op: CmdOp
    minutes: int
