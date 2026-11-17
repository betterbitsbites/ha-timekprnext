from datetime import date

from tk_protocol.clock import day_key, week_key


def test_week_key_monday_start():
    assert week_key(date(2026, 11, 15)) == "2026-W46"
    assert week_key(date(2026, 11, 16)) == "2026-W47"


def test_day_key():
    assert day_key(date(2026, 11, 16)) == "2026-11-16"
