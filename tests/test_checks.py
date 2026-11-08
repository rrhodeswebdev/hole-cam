from functools import cache
from pathlib import Path

import pytest

from hole_cam.checks import check, context_for
from hole_cam.gcode import _load
from hole_cam.holes import find_holes

FIX = Path(__file__).parent.parent / "fixtures"
MACHINE = _load("machine.json")
PROGRAMS = sorted((FIX / "gcode").glob("*.nc"))


@cache
def context(part):
    return context_for(find_holes(FIX / "parts" / f"{part}.step"))


@pytest.mark.parametrize("path", PROGRAMS, ids=lambda p: p.stem)
def test_check_set(path):
    """Each bad program trips exactly its planted check (anything else is a false alarm); clean ones trip nothing."""
    part, expected = path.stem.split("__")[:2]
    found = check(path.read_text(), MACHINE, **context(part))
    want = set() if expected == "clean" else {expected}
    assert {f["check"] for f in found} == want, found


def test_check_set_size():
    assert len(PROGRAMS) == 20  # 18 bad + 2 clean; regenerate with fixtures/make_bad_gcode.py


def test_rapid_into_a_lower_hole_needs_part_context():
    # R-8. over the boss part's plate hole is fine, but only the part geometry can show it.
    nc = (FIX / "gcode/boss__clean.nc").read_text()
    assert check(nc, MACHINE, **context("boss")) == []
    assert {f["check"] for f in check(nc, MACHINE)} == {"rapid-into-part"}


def test_findings_point_at_the_right_line():
    nc = (FIX / "gcode/mixed__spindle-off-cut__1.nc").read_text()
    found = check(nc, MACHINE, **context("mixed"))
    assert len(found) == 4  # M03 missing, so all 4 spot-drilled holes are cut with the spindle stopped
    assert nc.splitlines()[found[0]["line"] - 1].startswith("G98 G81 X15. Y15.")


def test_trace_times_each_move_from_its_feed_or_the_rapid_rate():
    nc = "T1 M06\nG43 H1 Z10. S1000 M03\nG00 X0. Y0.\nG01 Z-5. F600.\nG00 Z10.\nM05\nM30\n"
    trace = []
    check(nc, MACHINE, trace=trace)
    # From home and from an unknown XY there is no distance to time, so those moves are left out.
    assert [(m["kind"], m["line"], m["seconds"]) for m in trace] == [
        ("tool_change", 1, MACHINE["tool_change_seconds"]),
        ("feed", 4, 1.5),                                    # 15 mm at 600 mm/min
        ("rapid", 5, round(15 / MACHINE["rapid_rate"] * 60, 3)),
    ]


def test_trace_expands_g83_into_pecks():
    nc = "T1 M06\nG43 H1 Z25. S1000 M03\nG00 X5. Y5.\nG98 G83 X5. Y5. Z-10. R2. Q5. F100.\nG80\nM05\nM30\n"
    trace = []
    check(nc, MACHINE, trace=trace)
    zs = [(m["kind"], m["from"][2], m["to"][2]) for m in trace if m.get("line") == 4]
    assert zs == [
        ("rapid", 25, 2),                    # down to R
        ("feed", 2, -3), ("rapid", -3, 2),   # peck 1, out to clear chips
        ("rapid", 2, -2), ("feed", -2, -8), ("rapid", -8, 2),  # back to 1 mm above the bottom, peck 2
        ("rapid", 2, -7), ("feed", -7, -10),  # last peck stops at full depth
        ("rapid", -10, 25),                  # G98: back to the starting height
    ]


def test_stacked_holes_use_the_lowest_top_whatever_their_order():
    hole = {"x": 10, "y": 10, "through": False}
    cbore, under = {**hole, "top_z": 30, "diameter": 11, "depth": 7}, {**hole, "top_z": 23, "diameter": 7, "depth": 10}
    for holes in ([cbore, under], [under, cbore]):
        ctx = context_for({"part": {"top_z": 30, "bottom_z": 0}, "holes": holes})
        assert ctx["local_tops"] == {(10, 10): -7}
