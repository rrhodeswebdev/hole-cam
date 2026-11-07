import json
from pathlib import Path

import pytest

from hole_cam.holes import find_holes

PARTS = Path(__file__).parent.parent / "fixtures" / "parts"
NAMES = sorted(p.name.removesuffix(".step") for p in PARTS.glob("*.step"))


def _key(h):
    return (h["x"], h["y"], h["top_z"])


@pytest.mark.parametrize("name", NAMES)
def test_finds_expected_holes(name):
    expected = json.loads((PARTS / f"{name}.expected.json").read_text())
    found = find_holes(PARTS / f"{name}.step")

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
