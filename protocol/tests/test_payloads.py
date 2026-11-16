import pytest

from tk_protocol.payloads import Cmd, Event, Remaining, Settings, Usage


def test_usage_roundtrip():
    u = Usage(week="2026-W47", total_s=3600, day="2026-11-16", total_day_s=1200)
    assert Usage.from_json(u.to_json()) == u


@pytest.mark.parametrize("bad", ["{}", "nope", "[]", '{"week": "2026-W47"}',
                                 '{"week": "2026-W47", "total_s": "x", "day": "2026-11-16", "total_day_s": 1}',
                                 '{"week": "2026-W47", "total_s": -1, "day": "2026-11-16", "total_day_s": 1}',
                                 '{"week": "2026-W47", "total_s": true, "day": "2026-11-16", "total_day_s": 1}'])
def test_usage_from_json_rejects_garbage(bad):
    with pytest.raises(ValueError):
        Usage.from_json(bad)


def test_other_payloads_roundtrip():
    for p in (Remaining(week="2026-W47", total_s=0),
              Settings(time_up_action="lock"),
              Event(type="time_up", ts="2026-11-16T10:00:00"),
              Cmd(op="adjust_total", minutes=-15)):
        assert type(p).from_json(p.to_json()) == p


def test_literal_fields_validated():
    with pytest.raises(ValueError):
        Event.from_json('{"type": "boom", "ts": "x"}')
    with pytest.raises(ValueError):
        Cmd.from_json('{"op": "rm", "minutes": 1}')
