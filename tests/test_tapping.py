"""Tapping: Claude decides which holes get threads (from the drawing or the machinist); code checks the tap, its
drill and the cycle, and sets the feed from the pitch."""

import copy

import pytest
from test_planner import GOOD, MACHINE, MATERIALS, MIXED, TOOLS

from hole_cam.checks import check, context_for
from hole_cam.gcode import generate
from hole_cam.planner import drawing_callouts, validate


def tap(tool, ids, cycle="G84"):
    return {"op": "tap", "tool_id": tool, "hole_ids": ids, "depth": 0, "cycle": cycle, "peck": 0, "reason": ""}


M6 = {"hole_ids": [3, 4], "callout": "2X M6x1.0-6H", "units": "mm", "diameter": 6, "plus": 0, "minus": 0, "thread": 1}
M8 = {"hole_ids": [1, 2], "callout": "2X M8x1.25-6H THRU", "units": "mm", "diameter": 8, "plus": 0, "minus": 0, "thread": 1.25}
# The mixed plate's 5 mm and 6.8 mm holes are the M6 and M8 tap drill sizes.
TAPPED = {**copy.deepcopy(GOOD), "warnings": [], "drawing": [M6, M8]}
TAPPED["operations"] += [tap("TAP-M6X1", [3, 4]), tap("TAP-M8X1.25", [1, 2])]


def errors(plan, found=MIXED):
    return validate(plan, found, TOOLS, MACHINE)["errors"]


def test_inch_thread_callout_becomes_a_pitch_in_mm():
    (c,) = drawing_callouts({"drawing": [{"hole_ids": [1], "callout": "1/4-20 UNC-2B", "units": "in", "diameter": 0.25,
                                         "plus": 0, "minus": 0, "thread": 20}]}, MACHINE)
    assert (c["diameter_mm"], c["pitch_mm"], c["tap"], c["ream"]) == (6.35, 1.27, True, False)


def test_drill_then_tap_is_valid():
    assert errors(TAPPED) == []


def with_ops(*ops, drawing=(M6, M8)):
    return {"operations": [copy.deepcopy(o) for o in GOOD["operations"]] + list(ops), "warnings": [], "drawing": list(drawing)}


@pytest.mark.parametrize("plan, expect", [
    (with_ops(tap("TAP-M6X1", [3, 4]), tap("TAP-M8X1.25", [1, 2], cycle="G81")), "tapping uses G84"),
    (with_ops(tap("TAP-M6X1", [3, 4])), "threaded '2X M8x1.25-6H THRU' on the drawing: drill it with DRL-6.8 and tap it with TAP-M8X1.25"),
    # The drawing says M8 fine pitch (1.0); the library tap is 1.25.
    (with_ops(tap("TAP-M6X1", [3, 4]), tap("TAP-M8X1.25", [1, 2]), drawing=[M6, {**M8, "thread": 1.0}]), "doesn't make that"),
    (with_ops(tap("TAP-M8X1.25", [3, 4]), tap("TAP-M8X1.25", [1, 2])), "doesn't fit a 5.0 mm hole"),
])
def test_validation_catches(plan, expect):
    found = errors(plan)
    assert any(expect in e for e in found), found


def test_tapping_before_drilling_is_caught():
    plan = with_ops(drawing=[M6])
    plan["operations"].insert(1, tap("TAP-M6X1", [3, 4]))
    assert any("tapped before it is drilled" in e for e in errors(plan))


def test_machinist_can_ask_for_a_thread_with_no_drawing():
    assert errors(with_ops(tap("TAP-M6X1", [3]), drawing=[])) == []


# A plate with one hole modeled at thread size (6 mm for M6), as some CAD exports do.
AT_SIZE = {"part": {"top_z": 0, "bottom_z": -10},
           "holes": [{"id": 1, "x": 10, "y": 10, "diameter": 6.0, "depth": 10, "through": True, "top_z": 0}], "skipped": []}
SPOT = {"op": "spot_drill", "tool_id": "SPOT-90-10", "hole_ids": [1], "depth": 2.4, "cycle": "G81", "peck": 0, "reason": ""}


def drill(tool):
    return {"op": "drill", "tool_id": tool, "hole_ids": [1], "depth": 0, "cycle": "G81", "peck": 0, "reason": ""}


def test_hole_modeled_at_thread_size_is_drilled_at_tap_drill_size():
    m6 = {**M6, "callout": "M6x1.0-6H", "hole_ids": [1]}
    good = {"operations": [SPOT, drill("DRL-5.0"), tap("TAP-M6X1", [1])], "warnings": [], "drawing": [m6]}
    assert errors(good, AT_SIZE) == []
    drilled_to_size = {**good, "operations": [SPOT, drill("DRL-6.0"), tap("TAP-M6X1", [1])]}
    assert any("drilled 6 mm; this tap needs DRL-5.0" in e for e in errors(drilled_to_size, AT_SIZE))
    # With no tap for the thread, the hole is left out rather than drilled to 6 mm, and Claude warns.
    m6_fine = {**m6, "callout": "M6x0.75-6H", "thread": 0.75}
    assert errors({"operations": [], "warnings": [], "drawing": [m6_fine]}, AT_SIZE) == []


def test_thread_modeled_at_its_minor_diameter():
    # FTC-10's M3 holes are 2.46 mm in the model: the M3 minor diameter, not the 2.5 mm tap drill.
    found = {**AT_SIZE, "holes": [{**AT_SIZE["holes"][0], "diameter": 2.46, "through": False, "depth": 12}]}
    m3 = {"hole_ids": [1], "callout": "M3x0.5-6g", "units": "mm", "diameter": 3, "plus": 0, "minus": 0, "thread": 0.5}
    spot = {**SPOT, "depth": 1.0}
    pecked = {**drill("DRL-2.5"), "cycle": "G83", "peck": 2.5}  # 12 mm deep is 4.9 x D
    plan = {"operations": [spot, pecked, tap("TAP-M3X0.5", [1])], "warnings": [], "drawing": [m3]}
    assert errors(plan, found) == []
    assert any("drill it with DRL-2.5 and tap it with TAP-M3X0.5" in e for e in errors({**plan, "operations": [spot, pecked]}, found))


def test_tapping_program_feeds_one_pitch_per_turn():
    nc = generate(MIXED, TAPPED, TOOLS, MATERIALS["6061"], MACHINE)
    # 15 m/min on an 8 mm tap is 597 rpm; 597 x 1.25 = 746.25 mm/min. Through hole: 25 mm + 3 pitches of lead.
    assert "M29 S597\nG98 G84 X15. Y15. Z-28.75 R2. F746.25" in nc
    # Blind M6: one pitch off the 20 mm bottom.
    assert "X65. Y45. Z-19. R2." in nc
    assert check(nc, MACHINE, **context_for(MIXED)) == []


def test_tap_feed_is_never_capped_out_of_step_with_the_spindle():
    slow = {**MACHINE, "max_feed": 500}
    nc = generate(MIXED, TAPPED, TOOLS, MATERIALS["6061"], slow)
    assert "M29 S400\nG98 G84 X15. Y15. Z-28.75 R2. F500." in nc  # rpm drops to 400 so 400 x 1.25 fits


def test_trace_reverses_out_of_a_g84_hole():
    nc = "T1 M06\nG00 G43 H1 Z25. S600 M03\nG00 X5. Y5.\nM29 S600\nG98 G84 X5. Y5. Z-10. R2. F600.\nG80\nM05\nM30\n"
    trace = []
    assert check(nc, MACHINE, trace=trace) == []
    zs = [(m["kind"], m["from"][2], m["to"][2]) for m in trace if m.get("line") == 5]
    assert zs == [("rapid", 25, 2), ("feed", 2, -10), ("feed", -10, 2), ("rapid", 2, 25)]
