from datetime import date

from tk_budget.engine import Limit, UsageLedger, effective, limit, remaining_total_s, week_key


def test_week_key_monday_start():
    assert week_key(date(2026, 11, 15)) == "2026-W46"
    assert week_key(date(2026, 11, 16)) == "2026-W47"


def test_week_key_iso_year_boundary():
    assert week_key(date(2027, 1, 1)) == "2026-W53"


def test_remaining_basic():
    assert remaining_total_s(600, 900, 3600) == 600 * 60 + 900 - 3600


def test_remaining_clamped_at_zero():
    assert remaining_total_s(10, 0, 3600) == 0


def test_negative_adjustment_larger_than_budget():
    assert remaining_total_s(10, -100000, 0) == 0


def test_ledger_sums_hosts():
    led = UsageLedger()
    led.update("pc1", "kid-a", "2026-W47", 3600, "2026-11-16", 3600)
    led.update("pc2", "kid-a", "2026-W47", 1800, "2026-11-16", 1800)
    assert led.used_s("kid-a", "2026-W47") == 5400


def test_ledger_resend_idempotent():
    led = UsageLedger()
    assert led.update("pc1", "kid-a", "2026-W47", 3600, "2026-11-16", 3600) is True
    assert led.update("pc1", "kid-a", "2026-W47", 3600, "2026-11-16", 3600) is False
    assert led.used_s("kid-a", "2026-W47") == 3600


def test_ledger_ignores_lower_value():
    led = UsageLedger()
    led.update("pc1", "kid-a", "2026-W47", 3600, "2026-11-16", 3600)
    assert led.update("pc1", "kid-a", "2026-W47", 1000, "2026-11-16", 1000) is False
    assert led.used_s("kid-a", "2026-W47") == 3600


def test_ledger_other_week_not_counted():
    led = UsageLedger()
    led.update("pc1", "kid-a", "2026-W46", 3600, "2026-11-15", 3600)
    assert led.used_s("kid-a", "2026-W47") == 0


def test_ledger_day_used_per_host():
    led = UsageLedger()
    led.update("pc1", "kid-a", "2026-W47", 3600, "2026-11-16", 600)
    led.update("pc2", "kid-a", "2026-W47", 1800, "2026-11-16", 300)
    led.update("pc1", "kid-a", "2026-W47", 3700, "2026-11-17", 100)
    assert led.day_used_s("kid-a", "2026-11-16") == {"pc1": 600, "pc2": 300}
    assert led.day_used_s("kid-a", "2026-11-17") == {"pc1": 100}
    assert led.day_used_s("kid-b", "2026-11-16") == {}


def test_ledger_restore_week_total_only():
    led = UsageLedger()
    led.restore("pc1", "kid-a", "2026-W47", 3600)
    assert led.used_s("kid-a", "2026-W47") == 3600
    assert led.update("pc1", "kid-a", "2026-W47", 3000, "2026-11-16", 10) is True  # day is new
    assert led.used_s("kid-a", "2026-W47") == 3600


def test_limit_with_adjustment():
    lim = limit("day", True, 120, 900, 3000)
    assert lim == Limit("day", True, 8100, 5100)
    assert lim.pct == 63


def test_limit_clamped_at_zero():
    lim = limit("week", True, 10, -100000, 0)
    assert lim.allowance_s == 0
    assert lim.remaining_s == 0
    assert lim.pct == 0


def test_effective_minimum_of_active():
    assert effective([limit("week", True, 600, 0, 3600), limit("day", True, 60, 0, 600)]) == (True, 3000)


def test_effective_ignores_inactive():
    assert effective([limit("week", False, 600, 0, 0), limit("day", True, 60, 0, 0)]) == (True, 3600)


def test_effective_none_active():
    assert effective([limit("week", False, 600, 0, 0)]) == (False, 0)
