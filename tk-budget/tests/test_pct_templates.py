import json

import pytest

from tk_budget import discovery


def _attr_templates():
    out = {}
    for ents in (discovery.entities("kid-a"), discovery.combined_entities("kid-a")):
        for _topic, p in ents:
            if p.get("unit_of_measurement") == "%":
                out[p["unique_id"]] = p["json_attributes_template"]
    return out


def test_pct_attribute_templates_check_limit_on():
    tpls = _attr_templates()
    assert len(tpls) == 5
    for t in tpls.values():
        assert ".on" in t


def test_pct_attribute_templates_render():
    jinja2 = pytest.importorskip("jinja2")
    env = jinja2.Environment()
    for uid, tpl in _attr_templates().items():
        name = {"tk_kid-a_week_left_pct": "week", "tk_kid-a_today_left_pct": "day",
                "tk_kid-a_combined_left_pct": "combined", "tk_kid-a_play_week_left_pct": "play_week",
                "tk_kid-a_play_today_left_pct": "play_day"}[uid]

        def render(on):
            lim = {"on": on, "remaining_s": 600, "allowance_s": 3600, "pct": 17}
            return json.loads(env.from_string(tpl).render(value_json={"limits": {name: lim}}))

        assert render(True) == {"remaining_min": 10, "limit_min": 60}
        assert render(False) == {"remaining_min": None, "limit_min": None}
