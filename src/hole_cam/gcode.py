"""Turn a machining plan into Fanuc-style G-code (SPEC.md 3.4).

This module makes no machining decisions. The plan chooses the tools and cycles, the material table sets
speeds and feeds, and this code only does the arithmetic and formatting.
Coordinates: G54 X0 Y0 is the part's front-left corner and Z0 is the top of the part.

CLI: uv run python -m hole_cam.gcode PART.step PLAN.json MATERIAL > out.nc
"""

import json
import math
import re
import sys
from pathlib import Path

DATA = Path(__file__).parents[2] / "data"


def generate(found, plan, tools, material, machine, name="PART"):
    holes = {h["id"]: h for h in found["holes"]}
    tools = {t["id"]: t for t in tools}
    z0 = found["part"]["top_z"]
    clear, r_clear = machine["clearance"], machine["r_clearance"]

    # The name can come from an uploaded file; parentheses or newlines would end the comment and inject code.
    safe_name = re.sub(r"[^A-Z0-9 _.-]", "", name.upper())[:30]
    out = ["%", f"O1000 ({safe_name})", "G21 G90 G17 G40 G49 G80", "G54"]
    ops = plan["operations"]
    for k, op in enumerate(ops):
        tool = tools[op["tool_id"]]
        if tool["type"] == "spot_drill":
            fpr = material["spot_feed_per_rev"]
        else:
            fpr = _lookup(material["drill_feed_per_rev"], tool["diameter"])
        rpm = min(machine["max_rpm"], round(material["vc"] * 1000 / (math.pi * tool["diameter"])))
        feed = min(machine["max_feed"], round(rpm * fpr))
        t = tool["number"]
        cycle = "G83" if op.get("cycle") == "G83" else "G81"
        peck = f" Q{_f(op.get('peck', tool['diameter']))}" if cycle == "G83" else ""

        # Back-to-back operations with the same tool (e.g. G81 then G83) keep it loaded: no tool change, no trip home.
        if k == 0 or ops[k - 1]["tool_id"] != op["tool_id"]:
            out += [f"T{t} M06 ({tool['description']})", f"G43 H{t} Z{_f(clear)} S{rpm} M03", "M08"]
        for i, h in enumerate(_nearest_first([holes[i] for i in op["hole_ids"]])):
            top = h["top_z"] - z0
            z = top - _tip_depth(op, tool, h, machine)
            xy, zr = f"X{_f(h['x'])} Y{_f(h['y'])}", f"Z{_f(z)} R{_f(top + r_clear)}"
            if i == 0:
                out += [f"G00 {xy}", f"G98 {cycle} {xy} {zr}{peck} F{_f(feed)}"]
            else:
                out.append(f"{xy} {zr}")
        out.append("G80")
        if k == len(ops) - 1 or ops[k + 1]["tool_id"] != op["tool_id"]:
            out += ["M09", "M05", "G91 G28 Z0.", "G90"]
    out += ["M30", "%"]
    return "\n".join(out) + "\n"


def _tip_depth(op, tool, hole, machine):
    """How far below the hole's top the tool tip goes."""
    if op["op"] == "spot_drill":
        return op["depth"]
    # The full diameter must reach the hole depth, so the point goes further; through holes add breakthrough.
    point = (tool["diameter"] / 2) / math.tan(math.radians(tool["point_angle"] / 2))
    return hole["depth"] + point + (machine["breakthrough"] if hole["through"] else 0)


def _lookup(table, diameter):
    for max_d, value in table:
        if diameter <= max_d:
            return value
    raise ValueError(f"no feed in the material table for a {diameter} mm drill")


def _nearest_first(holes):
    """Visit holes nearest-neighbor order, starting from X0 Y0."""
    left, x, y, out = list(holes), 0.0, 0.0, []
    while left:
        nxt = min(left, key=lambda h: (h["x"] - x) ** 2 + (h["y"] - y) ** 2)
        left.remove(nxt)
        out.append(nxt)
        x, y = nxt["x"], nxt["y"]
    return out


def _f(v):
    """Fanuc number: always a decimal point, no trailing zeros ("15.", "-1.5")."""
    s = f"{v:.3f}".rstrip("0")
    return "0." if s == "-0." else s


def _load(name):
    return json.loads((DATA / name).read_text())


if __name__ == "__main__":
    from hole_cam.holes import find_holes

    step, plan_path, mat = sys.argv[1:4]
    print(generate(find_holes(step), json.loads(Path(plan_path).read_text()), _load("tools.json"),
                   _load("materials.json")[mat], _load("machine.json"), name=Path(step).stem), end="")
