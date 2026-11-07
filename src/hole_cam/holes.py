"""Find drillable holes in a STEP part (SPEC.md 3.1).

A hole is an inner cylindrical surface. It's drillable in v1 if its axis is vertical and it opens upward.
Each hole is reported in mm as {id, x, y, diameter, depth, through, top_z}. Depth runs from the opening
to the end of the full-diameter section, so a drill-point cone is not counted.
"""

import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Surface

TOL = 1e-3  # mm


def find_holes(step_path):
    shape = cq.importers.importStep(str(step_path)).val()
    groups = {}  # (x, y, r) -> list of [zmin, zmax, faces]; CAD tools often split one hole into several faces
    skipped = []

    for face in shape.Faces():
        if face.geomType() != "CYLINDER":
            continue
        cyl = BRepAdaptor_Surface(face.wrapped).Cylinder()
        loc, axis, r = cyl.Axis().Location(), cyl.Axis().Direction(), cyl.Radius()

        p = face.Edges()[0].startPoint()
        radial = cq.Vector(p.x - loc.X(), p.y - loc.Y(), p.z - loc.Z())
        if face.normalAt(p).dot(radial) > 0:
            continue  # outer surface (a boss or a rounded edge), not a hole

        if abs(abs(axis.Z()) - 1) > TOL:
            skipped.append({"diameter": round(2 * r, 3), "reason": "not vertical"})
            continue

        bb = face.BoundingBox()
        key = (round(loc.X(), 3), round(loc.Y(), 3), round(r, 4))
        groups.setdefault(key, []).append([bb.zmin, bb.zmax, [face]])

    holes = []
    for (x, y, r), spans in groups.items():
        for zmin, zmax, faces in _merge(spans):
            mid = (zmin + zmax) / 2
            if _closed(shape, faces, zmax, mid):
                skipped.append({"diameter": round(2 * r, 3), "reason": "opens on the bottom"})
                continue
            holes.append({
                "x": x, "y": y, "diameter": round(2 * r, 3),
                "depth": round(zmax - zmin, 3),
                "through": not _closed(shape, faces, zmin, mid),
                "top_z": round(zmax, 3),
            })

    holes.sort(key=lambda h: (h["y"], h["x"], -h["top_z"]))
    for i, h in enumerate(holes, 1):
        h["id"] = i
    bb = shape.BoundingBox()
    return {"part": {"top_z": round(bb.zmax, 3), "bottom_z": round(bb.zmin, 3)}, "holes": holes, "skipped": skipped}


def _merge(spans):
    """Join faces of the same hole whose Z ranges touch or overlap."""
    spans.sort(key=lambda s: s[0])
    out = [spans[0]]
    for zmin, zmax, faces in spans[1:]:
        last = out[-1]
        if zmin <= last[1] + TOL:
            last[1] = max(last[1], zmax)
            last[2] += faces
        else:
            out.append([zmin, zmax, faces])
    return out


def _closed(shape, faces, z, mid):
    """Is the hole closed at height z? Yes if the face there is a drill-point cone or a floor/ceiling facing into the hole."""
    for face in faces:
        for edge in face.Edges():
            if edge.geomType() != "CIRCLE" or abs(edge.Center().z - z) > TOL:
                continue
            for other in edge.ancestors(shape, "Face"):
                if other.isSame(face) or any(other.isSame(f) for f in faces):
                    continue
                if other.geomType() == "CONE":
                    return True
                if other.geomType() == "PLANE" and other.normalAt(edge.Center()).z * (mid - z) > 0:
                    return True
    return False
