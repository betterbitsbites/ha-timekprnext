import pytest

from tk_protocol.payloads import Action, Applied, Cmd, Event, Remaining, Sessions, Settings, Usage


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


def test_remaining_limited_roundtrip():
    r = Remaining(week="2026-W47", total_s=0, limited=False)
    assert Remaining.from_json(r.to_json()) == r


def test_remaining_without_limited_is_limited():
    assert Remaining.from_json('{"week":"2026-W47","total_s":5}').limited is True


def test_remaining_limited_must_be_bool():
    with pytest.raises(ValueError):
        Remaining.from_json('{"week":"2026-W47","total_s":5,"limited":1}')


def test_old_usage_parses_with_play_zero():
    u = Usage.from_json('{"week":"2026-W47","total_s":1,"day":"2026-11-16","total_day_s":1}')
    assert u.play_s == 0 and u.play_day_s == 0
    assert Usage.from_json(Usage("w", 1, "d", 1, play_s=5, play_day_s=3).to_json()).play_s == 5


def test_usage_play_negative_rejected():
    with pytest.raises(ValueError):
        Usage("w", 1, "d", 1, play_day_s=-1)


def test_old_remaining_has_no_play():
    r = Remaining.from_json('{"week":"2026-W48","total_s":5}')
    assert r.play_limited is False and r.play_s == 0 and r.limited is True


def test_remaining_play_roundtrip_and_negative():
    r = Remaining(week="w", total_s=1, play_s=7, play_limited=True)
    assert Remaining.from_json(r.to_json()) == r
    with pytest.raises(ValueError):
        Remaining("w", 1, play_s=-1)


def test_cmd_new_ops_and_default_minutes():
    assert Cmd.from_json('{"op":"logout"}').minutes == 0
    for op in ("adjust_play", "end_inactive", "logout"):
        assert Cmd.from_json(Cmd(op=op, minutes=1).to_json()).op == op


def test_sessions_validation():
    s = Sessions(count=2, locked=1)
    assert Sessions.from_json(s.to_json()) == s
    with pytest.raises(ValueError):
        Sessions(count=1, locked=2)
    with pytest.raises(ValueError):
        Sessions(count=-1, locked=0)
    with pytest.raises(ValueError):
        Sessions(count=1, locked=-1)


def test_sessions_old_payload_defaults():
    s = Sessions.from_json('{"count":1,"locked":0}')
    assert (s.state, s.kind, s.connect) == ("none", "none", "")


def test_sessions_presence_roundtrip_with_empty_connect():
    s = Sessions(count=1, locked=0, state="active", kind="wayland", connect="")
    assert Sessions.from_json(s.to_json()) == s
    s2 = Sessions(count=1, locked=1, state="locked", kind="tty", connect="ssh://x")
    assert Sessions.from_json(s2.to_json()) == s2


def test_sessions_bad_kind_and_state():
    with pytest.raises(ValueError):
        Sessions.from_json('{"count":1,"locked":0,"kind":"mir"}')
    with pytest.raises(ValueError):
        Sessions.from_json('{"count":1,"locked":0,"state":"gone"}')


def test_required_str_still_non_empty():
    with pytest.raises(ValueError):
        Event.from_json('{"type":"login","ts":""}')


def test_rev_fields():
    assert Remaining.from_json('{"week":"2026-W47","total_s":1}').rev == 0
    assert Settings.from_json('{"time_up_action":"lock"}').rev == 0
    assert Settings.from_json(Settings(time_up_action="lock", rev=3).to_json()).rev == 3
    with pytest.raises(ValueError):
        Settings.from_json('{"time_up_action":"lock","rev":""}')
    with pytest.raises(ValueError):
        Settings(time_up_action="lock", rev=-1)
    with pytest.raises(ValueError):
        Remaining(week="2026-W47", total_s=1, rev=-1)


def test_applied():
    a = Applied(rev=4)
    assert Applied.from_json(a.to_json()) == a
    with pytest.raises(ValueError):
        Applied(rev=-1)
    with pytest.raises(ValueError):
        Applied.from_json('{}')


def test_action_roundtrip_and_rejects_bad_op():
    a = Action(op="logout", ts="2026-11-26T10:00:00")
    assert Action.from_json(a.to_json()) == a
    with pytest.raises(ValueError):
        Action.from_json('{"op":"shutdown","ts":"x"}')
