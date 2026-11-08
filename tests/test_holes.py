import json
from pathlib import Path

import cadquery as cq
import pytest

from hole_cam.holes import find_holes

PARTS = Path(__file__).parent.parent / "fixtures" / "parts"
NAMES = sorted(p.name.removesuffix(".step") for p in PARTS.glob("*.step"))


def _key(h):
    return (h["x"], h["y"], h["top_z"])


@pytest.mark.parametrize("name", NAMES)
def test_finds_expected_holes(name):
    expected = json.loads((PARTS / f"{name}.expected.json").read_text())
    found = find_holes(cq.importers.importStep(str(PARTS / f"{name}.step")).val())  # as modeled: top side up

    got = sorted(({k: v for k, v in h.items() if k != "id"} for h in found["holes"]), key=_key)
    want = sorted(expected["holes"], key=_key)
    assert len(got) == len(want)
    for g, w in zip(got, want):
        assert g == pytest.approx(w, abs=1e-3)

    def skip_key(s):
        return (s["diameter"], s["reason"])

    assert sorted(map(skip_key, found["skipped"])) == sorted(map(skip_key, expected["skipped"]))


@pytest.mark.parametrize("name", ["mixed", "boss"])
def test_split_faces_give_same_holes(name, tmp_path):
    """Many CAD exporters store each hole as two half-cylinder faces; results must not change."""
    import cadquery as cq
    from OCP.ShapeUpgrade import ShapeUpgrade_ShapeDivideClosed

    shape = cq.importers.importStep(str(PARTS / f"{name}.step")).val()
    div = ShapeUpgrade_ShapeDivideClosed(shape.wrapped)
    div.SetNbSplitPoints(1)
    div.Perform()
    split = cq.Shape.cast(div.Result())
    n_cyl = lambda s: sum(f.geomType() == "CYLINDER" for f in s.Faces())
    assert n_cyl(split) == 2 * n_cyl(shape)  # make sure the split actually happened

    path = tmp_path / "split.step"
    cq.exporters.export(split, str(path))
    assert find_holes(path) == find_holes(PARTS / f"{name}.step")


def test_ids_are_unique_and_start_at_one():
    ids = [h["id"] for h in find_holes(PARTS / "mixed.step")["holes"]]
    assert sorted(ids) == list(range(1, len(ids) + 1))


def _save(shape, tmp_path, name="part.step"):
    path = tmp_path / name
    cq.exporters.export(shape, str(path))
    return path


def test_picks_the_side_that_drills_the_most_holes():
    found = find_holes(PARTS / "side_hole.step")
    # Flipped over, the through hole and the hole from underneath are both drillable in one setup.
    assert found["part"]["setup"] == "-Z"
    assert sorted(h["diameter"] for h in found["holes"]) == [4, 5]
    assert find_holes(PARTS / "mixed.step")["part"]["setup"] == "+Z"


def test_rounded_inside_corners_are_not_holes(tmp_path):
    # A pocket with 3 mm vertical corner radii, and one real 6 mm hole beside it.
    plate = (cq.Workplane().box(80, 50, 10).faces(">Z").workplane()
             .center(-10, 0).rect(30, 20).cutBlind(-4).edges("|Z and (not <X) and (not >X) and (not <Y) and (not >Y)").fillet(3)
             .faces(">Z").workplane().center(35, 0).hole(6))
    found = find_holes(_save(plate.val(), tmp_path))
    assert [h["diameter"] for h in found["holes"]] == [6]


def test_part_is_moved_so_its_front_left_corner_is_x0_y0(tmp_path):
    plate = cq.Workplane().box(60, 40, 10).faces(">Z").workplane().pushPoints([(-20, -10)]).hole(5)  # centered on the origin
    found = find_holes(_save(plate.val(), tmp_path))
    assert (found["holes"][0]["x"], found["holes"][0]["y"]) == pytest.approx((10, 10))


def test_part_modeled_on_its_side_is_turned_to_drill_its_holes(tmp_path):
    plate = cq.Workplane("XZ").box(60, 40, 8).faces("<Y").workplane().pushPoints([(-20, 0), (20, 0)]).hole(5)
    found = find_holes(_save(plate.val(), tmp_path))
    assert found["part"]["setup"] in ("+Y", "-Y") and len(found["holes"]) == 2
    assert all(h["through"] and h["depth"] == pytest.approx(8) for h in found["holes"])


NIST = Path(__file__).parent.parent / "fixtures" / "nist"


def _sizes(found):
    return sorted((round(h["diameter"], 2), round(h["depth"], 2), h["through"]) for h in found["holes"])


def test_nist_ftc_08_finds_exactly_the_holes_on_its_drawing():
    # Drawing (inches): 10x .213, 4x .238 datum holes on the lower flange (modeled at .2375), 2x .250, 2x .228,
    # 2x .242, all through.
    # The scalloped cutout, keyholes and corner radii have curved walls but are not holes.
    found = find_holes(NIST / "nist_ftc_08_asme1_rc.stp")
    assert found["part"]["setup"] == "+Z"
    assert sorted(round(h["diameter"] / 25.4, 3) for h in found["holes"]) == sorted(
        [0.213] * 10 + [0.237] * 4 + [0.25] * 2 + [0.228] * 2 + [0.242] * 2)
    assert all(h["through"] for h in found["holes"])


def test_nist_ftc_07_is_turned_open_side_up_and_finds_its_counterbored_holes():
    # Drawing: 8x .250 holes .30 deep on the flange, and 4x .281 through with a .438 counterbore .30 deep.
    # The model's open side is +Y; holes on the walls need other setups.
    found = find_holes(NIST / "nist_ftc_07_asme1_rd.stp")
    assert found["part"]["setup"] == "+Y"
    assert _sizes(found) == sorted([(6.35, 7.62, False)] * 8 + [(11.11, 7.62, False)] * 4 + [(7.14, 11.43, True)] * 4)
    assert {s["reason"] for s in found["skipped"]} == {"on another side (needs another setup)"}
    assert min(h["x"] for h in found["holes"]) > 0 and min(h["y"] for h in found["holes"]) > 0
