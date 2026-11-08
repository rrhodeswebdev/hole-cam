"""Find drillable holes in a STEP part (SPEC.md 3.1).

A hole is an inner cylindrical surface that goes all the way around; a partial one is a rounded inside
corner or the end of a slot, not a hole. The part is turned so the side with the most drillable holes faces
up (+Z), and moved so its front-left corner is at X0 Y0. A hole is drillable in that setup if its axis is
vertical and it opens upward. Each hole is reported in mm as {id, x, y, diameter, depth, through, top_z}.
Depth runs from the opening to the end of the full-diameter section, so a drill-point cone is not counted.
"""

import math

import cadquery as cq
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
from OCP.TopExp import TopExp
from OCP.TopLoc import TopLoc_Location
from OCP.TopTools import TopTools_IndexedMapOfShape

TOL = 1e-3  # mm
# Rotation (axis, degrees) that turns each side of the model to face up.
SIDES = {"+Z": None, "-Z": ((1, 0, 0), 180), "+Y": ((1, 0, 0), 90), "-Y": ((1, 0, 0), -90),
         "+X": ((0, 1, 0), -90), "-X": ((0, 1, 0), 90)}


def load_part(step_path):
    """Return (shape, found): the part turned to the side that drills the most holes and moved into work
    coordinates, and the holes found in that setup. found["part"]["setup"] names the side, e.g. "-Y"."""
    shape = cq.importers.importStep(str(step_path)).val()
    # Only the two sides facing along the axis with the most holes are worth a full search.
    axis = max("ZYX", key=lambda a: sum(abs(abs(c["axis"]["XYZ".index(a)]) - 1) <= TOL for c in _holes_any_axis(shape)))
    tries = [(_turn(shape, side), side) for side in (f"+{axis}", f"-{axis}")]
    tries = [(turned, side, _find(turned)) for turned, side in tries]
    turned, side, (holes, skipped) = max(tries, key=lambda t: len(t[2][0]))  # ties keep the + side
    found = _found(turned, holes, skipped)
    found["part"]["setup"] = side
    return turned, found


def find_holes(part):
    """part: a STEP file path (turned to its best setup), or a shape to search as it is."""
    if isinstance(part, cq.Shape):
        return _found(part, *_find(part))
    return load_part(part)[1]


def mesh(shape, top_z):
    """Triangle mesh of the part in work coordinates (Z0 = top of the part), for three.js. The same as
    shape.tessellate(0.1, 0.2), which iterates each face's triangles from Python at ~5 ms a face."""
    shape.mesh(0.1, 0.2)
    vertices, triangles = [], []
    for face in _faces(shape):
        loc = TopLoc_Location()
        poly = BRep_Tool.Triangulation_s(face.wrapped, loc)
        if poly is None:
            continue
        trsf, base = loc.Transformation(), len(vertices) - 1  # OCP numbers nodes from 1
        flip = face.wrapped.Orientation() == TopAbs_REVERSED
        for i in range(1, poly.NbNodes() + 1):
            p = poly.Node(i).Transformed(trsf)
            vertices.append([round(p.X(), 3), round(p.Y(), 3), round(p.Z() - top_z, 3)])
        for i in range(1, poly.NbTriangles() + 1):
            t = poly.Triangle(i)
            a, b, c = (t.Value(k) + base for k in ((1, 3, 2) if flip else (1, 2, 3)))
            triangles.append([a, b, c])
    return {"vertices": vertices, "triangles": triangles}


def _found(shape, holes, skipped):
    bb = shape.BoundingBox()
    return {"part": {"top_z": round(bb.zmax, 3), "bottom_z": round(bb.zmin, 3)}, "holes": holes, "skipped": skipped}


def _turn(shape, side):
    if SIDES[side]:
        axis, degrees = SIDES[side]
        shape = shape.rotate(cq.Vector(0, 0, 0), cq.Vector(*axis), degrees)
    bb = shape.BoundingBox()
    return shape.translate(cq.Vector(-bb.xmin, -bb.ymin, 0))


def _find(shape):
    groups = {}  # (x, y, r) -> list of [zmin, zmax, faces]; CAD tools often split one hole into several faces
    skipped = []
    for c in _holes_any_axis(shape):
        if abs(abs(c["axis"][2]) - 1) > TOL:
            skipped.append({"diameter": round(2 * c["r"], 3), "reason": "on another side (needs another setup)"})
            continue
        for face in c["faces"]:
            bb = face.BoundingBox()
            groups.setdefault((c["x"], c["y"], c["r"]), []).append([bb.zmin, bb.zmax, [face]])

    neighbors = {}  # edge -> faces that share it; built once (cq's edge.ancestors() rebuilds it for every edge)
    for face in _faces(shape):
        for edge in _edges(face):
            neighbors.setdefault(edge, []).append(face)

    holes = []
    for (x, y, r), spans in groups.items():
        for zmin, zmax, faces in _merge(spans):
            if not _full_circle(faces):
                continue  # e.g. a corner radius that lines up with a hole above or below it
            mid = (zmin + zmax) / 2
            if _closed(neighbors, faces, zmax, mid):
                skipped.append({"diameter": round(2 * r, 3), "reason": "opens on the bottom"})
                continue
            holes.append({
                "x": x, "y": y, "diameter": round(2 * r, 3),
                "depth": round(zmax - zmin, 3),
                "through": not _closed(neighbors, faces, zmin, mid),
                "top_z": round(zmax, 3),
            })

    holes.sort(key=lambda h: (h["y"], h["x"], -h["top_z"]))
    for i, h in enumerate(holes, 1):
        h["id"] = i
    return holes, skipped


def _holes_any_axis(shape):
    """Inner cylinders grouped by axis line and radius, keeping only groups that go all the way around."""
    groups = {}
    for face in _faces(shape):
        if face.geomType() != "CYLINDER":
            continue
        cyl = BRepAdaptor_Surface(face.wrapped).Cylinder()
        loc, d, r = cyl.Axis().Location(), cyl.Axis().Direction(), cyl.Radius()
        p = _edges(face)[0].startPoint()
        if face.normalAt(p).dot(cq.Vector(p.x - loc.X(), p.y - loc.Y(), p.z - loc.Z())) > 0:
            continue  # outer surface (a boss or a rounded edge), not a hole
        axis = cq.Vector(d.X(), d.Y(), d.Z())
        if axis.x < -TOL or (abs(axis.x) <= TOL and (axis.y < -TOL or (abs(axis.y) <= TOL and axis.z < 0))):
            axis = -axis  # one direction per axis line
        origin = cq.Vector(loc.X(), loc.Y(), loc.Z())
        foot = origin - axis * origin.dot(axis)  # where the axis line passes closest to the origin
        key = tuple(round(v, 3) for v in (*axis.toTuple(), *foot.toTuple(), r))
        groups.setdefault(key, []).append(face)
    return [{"axis": k[:3], "x": k[3], "y": k[4], "r": k[6], "faces": faces}
            for k, faces in groups.items() if _full_circle(faces)]


def _full_circle(faces):
    """Do the faces cover all 360° around their axis? The U parameter of a cylinder is the angle."""
    spans = []
    for face in faces:
        u0, u1, _, _ = face._uvBounds()
        a = u0 % (2 * math.pi)
        b = a + (u1 - u0)
        spans += [(a, min(b, 2 * math.pi))] + ([(0, b - 2 * math.pi)] if b > 2 * math.pi else [])
    covered, reach = 0.0, 0.0
    for a, b in sorted(spans):
        a = max(a, reach)
        if b > a:
            covered += b - a
            reach = b
    return covered > 2 * math.pi - 1e-3


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


def _closed(neighbors, faces, z, mid):
    """Is the hole closed at height z? Yes if the face there is a drill-point cone or a floor/ceiling facing into the hole."""
    for face in faces:
        for edge in _edges(face):
            if edge.geomType() != "CIRCLE" or abs(edge.Center().z - z) > TOL:
                continue
            for other in neighbors[edge]:
                if other in faces:
                    continue
                if other.geomType() == "CONE":
                    return True
                if other.geomType() == "PLANE" and other.normalAt(edge.Center()).z * (mid - z) > 0:
                    return True
    return False


# shape.Faces() and face.Edges() iterate an OCP map from Python, which costs ~5 ms a call; reading it by index doesn't.
def _faces(shape):
    found = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape.wrapped, TopAbs_FACE, found)
    return [cq.Face(found.FindKey(i)) for i in range(1, found.Extent() + 1)]


def _edges(face):
    found = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(face.wrapped, TopAbs_EDGE, found)
    edges = [cq.Edge(found.FindKey(i)) for i in range(1, found.Extent() + 1)]
    return [e for e in edges if not BRep_Tool.Degenerated_s(e.wrapped)]
