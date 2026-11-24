from tk_budget.store import Store


def test_settings_roundtrip(tmp_path):
    s = Store(str(tmp_path / "db.sqlite"))
    assert s.get_setting("kid-a", "weekly_total_min") is None
    assert s.get_setting("kid-a", "weekly_total_min", "0") == "0"
    s.set_setting("kid-a", "weekly_total_min", "600")
    s.set_setting("kid-a", "weekly_total_min", "300")
    assert s.get_setting("kid-a", "weekly_total_min") == "300"
    assert s.get_setting("kid-b", "weekly_total_min") is None


def test_adjustments_accumulate_per_week(tmp_path):
    s = Store(str(tmp_path / "db.sqlite"))
    assert s.get_adjustment_s("kid-a", "2026-W47") == 0
    assert s.add_adjustment_s("kid-a", "2026-W47", 900) == 900
    assert s.add_adjustment_s("kid-a", "2026-W47", -300) == 600
    assert s.get_adjustment_s("kid-a", "2026-W47") == 600
    assert s.get_adjustment_s("kid-a", "2026-W48") == 0
    assert s.get_adjustment_s("kid-b", "2026-W47") == 0


def test_daily_record_keeps_max(tmp_path):
    s = Store(str(tmp_path / "db.sqlite"))
    s.record_daily("kid-a", "2026-11-16", "pc1", 100)
    s.record_daily("kid-a", "2026-11-16", "pc1", 50)
    s.record_daily("kid-a", "2026-11-16", "pc2", 70)
    assert s.daily_totals("kid-a", "2026-11-16") == {"pc1": 100, "pc2": 70}
    assert s.daily_totals("kid-a", "2026-11-17") == {}


def test_usage_keeps_max(tmp_path):
    s = Store(str(tmp_path / "db.sqlite"))
    s.save_usage("kid-a", "pc1", "2026-W47", 3600)
    s.save_usage("kid-a", "pc1", "2026-W47", 100)
    assert s.load_usage() == [("kid-a", "pc1", "2026-W47", 3600)]


def test_data_survives_reopen(tmp_path):
    path = str(tmp_path / "db.sqlite")
    s = Store(path)
    s.set_setting("kid-a", "time_up_action", "lock")
    s.add_adjustment_s("kid-a", "2026-W47", 900)
    s.record_daily("kid-a", "2026-11-16", "pc1", 1200)
    s.save_usage("kid-a", "pc1", "2026-W47", 3600)
    s.close()

    s2 = Store(path)
    assert s2.get_setting("kid-a", "time_up_action") == "lock"
    assert s2.get_adjustment_s("kid-a", "2026-W47") == 900
    assert s2.daily_totals("kid-a", "2026-11-16") == {"pc1": 1200}
    assert s2.load_usage() == [("kid-a", "pc1", "2026-W47", 3600)]


def test_add_kid_reports_new_once(tmp_path):
    s = Store(str(tmp_path / "db.sqlite"))
    assert s.add_kid("kid-b") is True
    assert s.add_kid("kid-b") is False
    assert s.add_kid("kid-a") is True
    assert s.kids() == ["kid-a", "kid-b"]
