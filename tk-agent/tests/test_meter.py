from datetime import datetime

import pytest

from tk_agent.meter import UsageMeter
from tk_protocol.clock import FixedClock

MON = datetime(2026, 11, 16, 10, 0)


@pytest.fixture
def clock():
    return FixedClock(MON)


@pytest.fixture
def state(tmp_path):
    return str(tmp_path / "kid-a.json")


def test_usage_is_last_written_minus_now(state, clock):
    m = UsageMeter(state, clock)
    m.wrote(3600)
    assert m.observe(3500) == 100
    assert m.week_total_s("2026-W47") == 100
    assert m.day_total_s("2026-11-16") == 100


def test_external_increase_is_not_negative_usage(state, clock):
    m = UsageMeter(state, clock)
    m.wrote(3600)
    assert m.observe(4000) == 0
    assert m.week_total_s("2026-W47") == 0


def test_observe_without_write_measures_from_previous_observation(state, clock):
    m = UsageMeter(state, clock)
    assert m.observe(5000) == 0  # first ever reading: nothing to compare with
    assert m.observe(4900) == 100
    assert m.observe(4850) == 50
    assert m.week_total_s("2026-W47") == 150


def test_restart_keeps_last_written_and_counters(state, clock):
    m = UsageMeter(state, clock)
    m.wrote(3600)
    m.observe(3500)
    m.wrote(3000)
    m2 = UsageMeter(state, clock)
    assert m2.week_total_s("2026-W47") == 100
    assert m2.day_total_s("2026-11-16") == 100
    assert m2.observe(2900) == 100
    assert m2.week_total_s("2026-W47") == 200


def test_new_day_resets_day_counter_but_not_week(state, clock):
    m = UsageMeter(state, clock)
    m.wrote(3600)
    m.observe(3000)  # 600 on Monday
    m.wrote(3000)
    clock.set(datetime(2026, 11, 17, 0, 0, 20))
    assert m.observe(86380) == 0  # timekpr reset at midnight: delta across midnight discarded
    assert m.day_total_s("2026-11-17") == 0
    assert m.day_total_s("2026-11-16") == 0  # only the current day is kept
    m.wrote(2400)
    assert m.observe(2300) == 100
    assert m.day_total_s("2026-11-17") == 100
    assert m.week_total_s("2026-W47") == 700


def test_new_week_resets_week_counter(state, clock):
    clock.set(datetime(2026, 11, 22, 23, 59, 0))  # Sunday
    m = UsageMeter(state, clock)
    m.wrote(600)
    m.observe(500)
    assert m.week_total_s("2026-W47") == 100
    clock.set(datetime(2026, 11, 23, 0, 0, 30))  # Monday
    m.observe(86370)
    m.wrote(1000)
    assert m.observe(900) == 100
    assert m.week_total_s("2026-W48") == 100
    assert m.week_total_s("2026-W47") == 0


def test_counters_roll_over_without_observation(state, clock):
    m = UsageMeter(state, clock)
    m.wrote(3600)
    m.observe(3000)
    clock.set(datetime(2026, 11, 23, 9, 0))
    assert m.week_total_s("2026-W48") == 0
    assert m.day_total_s("2026-11-23") == 0


def test_seed_raises_counters_but_never_lowers(state, clock):
    m = UsageMeter(state, clock)
    m.wrote(3600)
    m.observe(3500)
    m.seed("2026-W47", 5000, "2026-11-16", 1200)
    assert m.week_total_s("2026-W47") == 5000
    assert m.day_total_s("2026-11-16") == 1200
    m.seed("2026-W47", 10, "2026-11-16", 10)
    assert m.week_total_s("2026-W47") == 5000
    m.seed("2026-W46", 99999, "2026-11-15", 99999)  # other week/day: ignored
    assert m.week_total_s("2026-W47") == 5000
    assert UsageMeter(state, clock).week_total_s("2026-W47") == 5000


def test_corrupt_state_starts_fresh(state, clock):
    with open(state, "w") as f:
        f.write("{not json")
    m = UsageMeter(state, clock)
    assert m.week_total_s("2026-W47") == 0
    assert m.observe(100) == 0


def test_state_file_written_atomically(state, clock, tmp_path):
    m = UsageMeter(state, clock)
    m.wrote(100)
    assert [p.name for p in tmp_path.iterdir()] == ["kid-a.json"]


def test_observe_without_count_moves_baseline_only(state, clock):
    m = UsageMeter(state, clock)
    m.wrote(3600)
    assert m.observe(3500, count=False) == 0
    assert m.week_total_s("2026-W47") == 0
    assert m.observe(3450) == 50  # measured from the moved baseline
    m2 = UsageMeter(state, clock)  # persisted
    assert m2.observe(3400) == 50
