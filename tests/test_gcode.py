import json
from pathlib import Path

import pytest

from hole_cam.gcode import _f, _load, _lookup, generate
from hole_cam.holes import find_holes

FIX = Path(__file__).parent.parent / "fixtures"
TOOLS, MATERIALS, MACHINE = _load("tools.json"), _load("materials.json"), _load("machine.json")


def test_mixed_part_matches_hand_checked_program():
    # Speeds, feeds and depths in the golden file were checked by hand against the formulas in gcode.py.
    plan = json.loads((FIX / "plans/mixed.json").read_text())
    nc = generate(find_holes(FIX / "parts/mixed.step"), plan, TOOLS, MATERIALS["6061"], MACHINE, name="mixed")
    assert nc == (FIX / "programs/mixed_6061.nc").read_text()


def test_hole_on_a_lower_face_is_offset_from_part_top():
    # Boss part: G54 Z0 is the boss top (Z20 in the model), so the plate hole starts at Z-10.
    found = find_holes(FIX / "parts/boss.step")
    plate = next(h for h in found["holes"] if h["top_z"] == 10)
    plan = {"operations": [{"op": "spot_drill", "tool_id": "SPOT-90-10", "hole_ids": [plate["id"]], "depth": 1}]}
    nc = generate(found, plan, TOOLS, MATERIALS["6061"], MACHINE)
    assert "Z-11. R-8." in nc


def test_spindle_speed_is_capped_and_feed_follows():
    found = {"part": {"top_z": 10}, "holes": [{"id": 1, "x": 5, "y": 5, "diameter": 3, "depth": 4, "through": False, "top_z": 10}]}
    plan = {"operations": [{"op": "drill", "tool_id": "DRL-3.0", "hole_ids": [1], "cycle": "G81"}]}
    nc = generate(found, plan, TOOLS, MATERIALS["6061"], {**MACHINE, "max_rpm": 6000})
    assert "S6000 " in nc and "F360." in nc  # 6000 rpm x 0.06 mm/rev


def test_number_format():
    assert [_f(15), _f(-1.5), _f(-0.0001), _f(2.0436)] == ["15.", "-1.5", "0.", "2.044"]


def test_drill_bigger_than_feed_table_is_an_error():
    with pytest.raises(ValueError):
        _lookup(MATERIALS["6061"]["drill_feed_per_rev"], 20)
