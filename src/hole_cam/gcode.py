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

    out = ["%", f"O1000 ({_comment(name)[:30]})", "G21 G90 G17 G40 G49 G80", "G54"]
    ops = plan["operations"]
    for k, op in enumerate(ops):
        tool = tools[op["tool_id"]]
        vc = material["vc"]
        if tool["type"] == "spot_drill":
            fpr = material["spot_feed_per_rev"]
        elif tool["type"] == "reamer":
            vc, fpr = material["ream_vc"], _lookup(material["ream_feed_per_rev"], tool["diameter"])
        elif tool["type"] == "tap":
            vc, fpr = material["tap_vc"], tool["pitch"]
        else:
            fpr = _lookup(material["drill_feed_per_rev"], tool["diameter"])
        rpm = min(machine["max_rpm"], round(vc * 1000 / (math.pi * tool["diameter"])))
        if tool["type"] == "tap":
            # A tap must advance exactly one pitch per turn or it strips the thread or snaps: slow the spindle
            # rather than cap the feed.
            rpm = min(rpm, math.floor(machine["max_feed"] / fpr))
            feed = round(rpm * fpr, 3)
        else:
            feed = min(machine["max_feed"], round(rpm * fpr))
        t = tool["number"]
        cycle = op.get("cycle") if op.get("cycle") in ("G83", "G84", "G85") else "G81"
        peck = f" Q{_f(op.get('peck', tool['diameter']))}" if cycle == "G83" else ""

        # Back-to-back operations with the same tool (e.g. G81 then G83) keep it loaded: no tool change, no trip home.
        if k == 0 or ops[k - 1]["tool_id"] != op["tool_id"]:
            # G00 stated, not assumed: LinuxCNC rejects a bare "G43 H Z", and some Fanucs power up in G01.
            out += [f"T{t} M06 ({_comment(tool['description'])})", f"G00 G43 H{t} Z{_f(clear)} S{rpm} M03", "M08"]
        for i, h in enumerate(_nearest_first([holes[i] for i in op["hole_ids"]])):
            top = h["top_z"] - z0
            z = top - _tip_depth(op, tool, h, machine)
            xy, zr = f"X{_f(h['x'])} Y{_f(h['y'])}", f"Z{_f(z)} R{_f(top + r_clear)}"
            if i == 0:
                out.append(f"G00 {xy}")
                if cycle == "G84":
                    out.append(f"M29 S{rpm}")  # Fanuc rigid tapping: the feed axis follows the spindle
                out.append(f"G98 {cycle} {xy} {zr}{peck} F{_f(feed)}")
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
    if op["op"] == "tap":
        # shortcut: the thread depth on the drawing isn't read. A blind hole is tapped to one pitch off the bottom
        # (full threads stop about 4 pitches up, past the tap's lead); a through hole until the 3-pitch lead is out.
        # A 1/2-13 lead is 5.9 mm, past the checker's 5 mm too-deep limit, so that program is blocked, not cut short.
        return hole["depth"] + 3 * tool["pitch"] if hole["through"] else hole["depth"] - tool["pitch"]
    if op["op"] == "ream":
        # shortcut: a reamer's short lead chamfer is ignored; in a blind hole the last few tenths of a mm stay
        # drilled size unless the pre-drill goes deeper.
        return hole["depth"] + (machine["breakthrough"] if hole["through"] else 0)
    # The full diameter must reach the hole depth, so the point goes further; through holes add breakthrough.
    point = (tool["diameter"] / 2) / math.tan(math.radians(tool["point_angle"] / 2))
    return hole["depth"] + point + (machine["breakthrough"] if hole["through"] else 0)


def _lookup(table, diameter):
    for max_d, value in table:
        if diameter <= max_d:
            return value
    raise ValueError(f"no feed in the material table for a {diameter} mm tool")


def _nearest_first(holes):
    """Visit holes nearest-neighbor order, starting from X0 Y0."""
    left, x, y, out = list(holes), 0.0, 0.0, []
    while left:
        nxt = min(left, key=lambda h: (h["x"] - x) ** 2 + (h["y"] - y) ** 2)
        left.remove(nxt)
        out.append(nxt)
        x, y = nxt["x"], nxt["y"]
    return out


def _comment(text):
    """Text that's safe inside one ( ) comment. The name can come from an uploaded file, and a "(" or ")" inside
    a comment ends it early on a Fanuc (LinuxCNC rejects it), so a stray ")" or injected code would follow."""
    return re.sub(r"[^A-Z0-9 _.#/-]", "", text.upper())


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
