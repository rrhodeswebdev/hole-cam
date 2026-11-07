"""Safety checks on a G-code program (SPEC.md 3.5).

The checker reads the program one line at a time and keeps track of the machine's state: motion mode, canned
cycle, spindle, loaded tool, tool length offset and position. Each problem is reported with its line number.
It works on any Fanuc-style drilling program, not only the ones this tool writes.

Assumes G54 Z0 is the highest point of the part, so any move at Z >= 0 is clear of it. Below Z0, a rapid
is only safe straight down into a known hole whose top is below the move (see `local_tops`).

CLI: uv run python -m hole_cam.checks PROGRAM.nc [PART.step]
"""

import math
import re
import sys

WORD = re.compile(r"([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")
CYCLES = {73, 81, 82, 83, 84, 85, 86, 89}
CHECKS = {
    "rapid-into-part": "Rapid move below the top of the part: the tool can crash into the part",
    "spindle-off-cut": "Cutting with the spindle off: the tool will break",
    "no-tool-length-offset": "Tool length offset missing or wrong: the machine doesn't know how long the tool is",
    "over-machine-limit": "Speed or feed above what the machine allows",
    "too-deep": "Drills past the bottom of the part into the fixture or table",
    "unsafe-end": "Program or tool change leaves the machine in an unsafe state",
}


def context_for(found):
    """Checker context from find_holes() output, in work coordinates (Z0 = top of the part)."""
    z0 = found["part"]["top_z"]
    return {
        "local_tops": {(h["x"], h["y"]): h["top_z"] - z0 for h in found["holes"]},
        "bottom_z": found["part"]["bottom_z"] - z0,
    }


def check(program, machine, local_tops=None, bottom_z=None, max_breakthrough=5.0):
    """Return findings as [{line, check, message}]. bottom_z enables the too-deep check."""
    local_tops = {(round(x, 3), round(y, 3)): z for (x, y), z in (local_tops or {}).items()}
    findings = []

    def flag(n, kind, detail):
        if not any(f["line"] == n and f["check"] == kind for f in findings):
            findings.append({"line": n, "check": kind, "message": f"{CHECKS[kind]}. {detail}"})

    def top_at(x, y):
        if x is None or y is None:
            return 0.0
        return local_tops.get((round(x, 3), round(y, 3)), 0.0)

    x = y = None
    z = math.inf  # the machine starts at home, above everything
    absolute, motion, cycle, retract_to_initial = True, 0, None, True
    spindle, tool, pending_tool, offset, offset_missing_flagged = False, None, None, None, False
    cycle_z = cycle_r = initial_z = None
    ended = False

    def needs_offset(n):
        nonlocal offset_missing_flagged
        if tool is not None and offset is None and not offset_missing_flagged:
            flag(n, "no-tool-length-offset", f"T{tool} moves in Z with no G43 H{tool} after the tool change.")
            offset_missing_flagged = True

    def deep(n, target):
        if bottom_z is not None and target < bottom_z - max_breakthrough:
            flag(n, "too-deep", f"Z{target:g} is {bottom_z - target:.1f} mm below the bottom of the part (limit {max_breakthrough:g}).")

    for n, raw in enumerate(program.splitlines(), 1):
        line = re.sub(r"\(.*?\)|;.*", "", raw).upper().strip()
        if not line or line == "%" or line.startswith("O"):
            continue
        words = WORD.findall(line)
        g = [float(v) for k, v in words if k == "G"]
        m = [float(v) for k, v in words if k == "M"]
        val = {k: float(v) for k, v in words if k not in "GM"}

        if 90 in g: absolute = True
        if 91 in g: absolute = False
        if 98 in g: retract_to_initial = True
        if 99 in g: retract_to_initial = False
        if 80 in g: cycle = None
        if 49 in g: offset = None
        if 43 in g:
            offset = val.get("H")
            if tool is not None and offset != tool:
                flag(n, "no-tool-length-offset", f"G43 H{offset:g} is the wrong offset for T{tool}.")
        if "T" in val: pending_tool = int(val["T"])
        if "S" in val and val["S"] > machine["max_rpm"]:
            flag(n, "over-machine-limit", f"S{val['S']:g} rpm; the machine maxes out at {machine['max_rpm']}.")
        if "F" in val and val["F"] > machine["max_feed"]:
            flag(n, "over-machine-limit", f"F{val['F']:g} mm/min; the machine maxes out at {machine['max_feed']}.")

        if 6 in m:
            if cycle is not None:
                flag(n, "unsafe-end", "Tool change while a drilling cycle is still active; add G80 first.")
            tool, spindle, offset, offset_missing_flagged = pending_tool, False, None, False
        if 3 in m or 4 in m: spindle = True
        if 30 in m or 2 in m:
            ended = True
            if cycle is not None:
                flag(n, "unsafe-end", "Program ends with a drilling cycle still active; add G80 first.")

        if 28 in g:
            if cycle is not None:
                flag(n, "unsafe-end", "Going home with a drilling cycle still active; add G80 first.")
            z = math.inf
            if 5 in m: spindle = False
            continue

        for code in g:
            if code in (0, 1, 2, 3):
                motion, cycle = int(code), None
            elif code in CYCLES:
                cycle, initial_z = int(code), z

        def target(axis, cur):
            if axis not in val:
                return cur
            return val[axis] if absolute or cur is None or cur == math.inf else cur + val[axis]

        if cycle is not None and ({"X", "Y"} & val.keys() or any(c in CYCLES for c in g)):
            cycle_z, cycle_r = val.get("Z", cycle_z), val.get("R", cycle_r)
            nx, ny = target("X", x), target("Y", y)
            if not spindle:
                flag(n, "spindle-off-cut", f"G{cycle} drills X{nx:g} Y{ny:g} with the spindle stopped; add M03.")
            needs_offset(n)
            if (nx, ny) != (x, y) and z < 0:
                flag(n, "rapid-into-part", f"Moves to the next hole at Z{z:g}, below the top of the part.")
            if cycle_r < top_at(nx, ny):
                flag(n, "rapid-into-part", f"R{cycle_r:g} is below the surface at X{nx:g} Y{ny:g} (Z{top_at(nx, ny):g}); the rapid to R hits the part.")
            deep(n, cycle_z)
            x, y = nx, ny
            z = initial_z if retract_to_initial else cycle_r
        elif {"X", "Y", "Z"} & val.keys():
            nx, ny, nz = target("X", x), target("Y", y), target("Z", z)
            if nz != z:
                needs_offset(n)
            if motion == 0:
                moves_xy = (nx, ny) != (x, y)
                if moves_xy and min(z, nz) < 0:
                    flag(n, "rapid-into-part", f"Rapid sideways at Z{min(z, nz):g}, below the top of the part.")
                elif not moves_xy and nz < top_at(nx, ny):
                    flag(n, "rapid-into-part", f"Rapid down to Z{nz:g}; the part surface here is Z{top_at(nx, ny):g}.")
            else:
                if not spindle:
                    flag(n, "spindle-off-cut", f"G0{motion} feed move with the spindle stopped; add M03.")
                deep(n, nz)
            x, y, z = nx, ny, nz

        if 5 in m: spindle = False

    if not ended:
        flag(len(program.splitlines()), "unsafe-end", "No M30 at the end of the program.")
    return findings


if __name__ == "__main__":
    import json
    from pathlib import Path

    from hole_cam.gcode import _load

    ctx = {}
    if len(sys.argv) > 2:
        from hole_cam.holes import find_holes
        ctx = context_for(find_holes(sys.argv[2]))
    found = check(Path(sys.argv[1]).read_text(), _load("machine.json"), **ctx)
    for f in found:
        print(f"line {f['line']}: [{f['check']}] {f['message']}")
    print("OK: no problems found" if not found else f"{len(found)} problem(s)")
    sys.exit(1 if found else 0)
