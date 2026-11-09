from functools import cache
from pathlib import Path

import pytest

from hole_cam.checks import check, context_for
from hole_cam.data import load_data
from hole_cam.holes import find_holes

FIX = Path(__file__).parent.parent / "fixtures"
MACHINE = load_data("machine.json")
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


def test_g43_with_no_h_word_is_flagged_not_a_crash():
    found = check("T1 M06\nG00 G43 Z25. S1000 M03\nM30\n", MACHINE)
    assert [f["check"] for f in found] == ["no-tool-length-offset"]
    assert "no H word" in found[0]["message"]


@pytest.mark.parametrize("cycle", ["G98 G81 X1. Y1. Z-5. F100.", "G98 G81 X1. Y1. R2. F100."])
def test_canned_cycle_missing_z_or_r_is_a_clear_error(cycle):
    with pytest.raises(ValueError, match="line 3: G81 has no Z or no R"):
        check(f"T1 M06\nG00 G43 H1 Z25. S1000 M03\n{cycle}\nG80\nM30\n", MACHINE)


def test_incremental_move_after_going_home_stays_above_the_part():
    """G91 Z-10. from home is 10 mm below home, still far above the part, not a rapid to Z-10."""
    program = "T1 M06\nG00 G43 H1 Z25. S1000 M03\nG91 G28 Z0.\nG00 Z-10.\nG90\nM30\n"
    assert check(program, MACHINE) == []
