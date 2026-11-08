"""Build the SPEC.md section 5 test parts as STEP files, each with the hole list a correct detector must return.

Run: uv run python fixtures/make_parts.py
Units are mm. Each part's front-left-bottom corner is at the origin, and holes are drilled downward (-Z).
Each hole's depth is measured from its own top_z down to the end of the full-diameter section, so a
drill-point cone is not included.
"""

import json
import math
from pathlib import Path

import cadquery as cq

OUT = Path(__file__).parent / "parts"
POINT_ANGLE = 118  # standard twist-drill point
V = cq.Vector


def block(l, w, t):
    return cq.Workplane("XY").box(l, w, t, centered=False).val()


def blind(x, y, d, top_z, depth, point=False):
    """A cylinder of the given depth going down from top_z, optionally finished with a drill-point cone."""
    r = d / 2
    s = cq.Solid.makeCylinder(r, depth + 1, V(x, y, top_z - depth))  # +1 pokes above the face to avoid coplanar cuts
    if point:
        h = r / math.tan(math.radians(POINT_ANGLE / 2))
        s = s.fuse(cq.Solid.makeCone(0, r, h, V(x, y, top_z - depth - h)))
    return s


def through(x, y, d, top_z):
    return cq.Solid.makeCylinder(d / 2, top_z + 2, V(x, y, -1))


def cut(body, *tools):
    for t in tools:
        before = body.Volume()
        body = body.cut(t)
        assert body.Volume() < before, "cut removed nothing; tool misses the part"
    return body


def h(x, y, d, depth, through_, top_z):
    return {"x": x, "y": y, "diameter": d, "depth": depth, "through": through_, "top_z": top_z}


def plate_4_through():
    pts = [(15, 15), (85, 15), (15, 45), (85, 45)]
    body = cut(block(100, 60, 10), *(through(x, y, 6.8, 10) for x, y in pts))
    return body, [h(x, y, 6.8, 10, True, 10) for x, y in pts], []


def mixed():
    body = cut(
        block(100, 60, 25),
        through(15, 15, 6.8, 25),
        through(85, 15, 6.8, 25),
        blind(35, 45, 5, 25, 5),  # shallow, flat bottom
        blind(65, 45, 5, 25, 20, point=True),  # 4xD deep: needs pecking
    )
    holes = [
        h(15, 15, 6.8, 25, True, 25),
        h(85, 15, 6.8, 25, True, 25),
        h(35, 45, 5, 5, False, 25),
        h(65, 45, 5, 20, False, 25),
    ]
    return body, holes, []


def odd_size():
    body = cut(block(60, 40, 10), through(20, 20, 7.1, 10), through(40, 20, 6.8, 10))
    return body, [h(20, 20, 7.1, 10, True, 10), h(40, 20, 6.8, 10, True, 10)], []


def side_hole():
    # Neither extra hole can be reached from the top in one setup:
    # a Ø6 x 15 deep hole drilled in from the +X face, and a Ø4 x 6 deep hole drilled up from the bottom.
    side = cq.Solid.makeCylinder(3, 16, V(61, 20, 15), V(-1, 0, 0))
    bottom = cq.Solid.makeCylinder(2, 7, V(40, 20, -1))
    body = cut(block(60, 40, 30), through(20, 20, 5, 30), side, bottom)
    skipped = [{"diameter": 6, "reason": "on another side (needs another setup)"}, {"diameter": 4, "reason": "opens on the bottom"}]
    return body, [h(20, 20, 5, 30, True, 30)], skipped


def boss():
    # The boss's outer cylinder must not count as a hole; the coaxial hole drilled into the boss must.
    body = block(80, 50, 10).fuse(cq.Solid.makeCylinder(10, 10, V(40, 25, 10)))
    body = cut(body, through(10, 10, 5, 10), blind(40, 25, 5, 20, 8))
    return body, [h(10, 10, 5, 10, True, 10), h(40, 25, 5, 8, False, 20)], []


PARTS = [plate_4_through, mixed, odd_size, side_hole, boss]


def main():
    OUT.mkdir(exist_ok=True)
    for make in PARTS:
        name = make.__name__
        body, holes, skipped = make()
        assert body.isValid(), f"{name}: invalid solid"
        step = OUT / f"{name}.step"
        cq.exporters.export(body, str(step))

        # Self-check: the STEP file reads back as the same valid solid.
        back = cq.importers.importStep(str(step)).val()
        assert back.isValid() and math.isclose(back.Volume(), body.Volume(), rel_tol=1e-6), f"{name}: STEP round-trip changed the part"

        expected = {"part": name, "units": "mm", "holes": holes, "skipped": skipped}
        (OUT / f"{name}.expected.json").write_text(json.dumps(expected, indent=2) + "\n")
        print(f"{name}: {len(holes)} holes, {len(skipped)} skipped, volume {body.Volume():.1f} mm³")


if __name__ == "__main__":
    main()
