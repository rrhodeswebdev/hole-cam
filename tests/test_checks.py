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
