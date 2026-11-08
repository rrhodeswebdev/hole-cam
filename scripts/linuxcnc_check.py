"""Prove a program out on LinuxCNC's G-code interpreter: an independent reading of the program, not ours.

Runs the generated program through `rs274`, the interpreter a LinuxCNC machine runs, in Docker. Then checks:
  1. it reads the whole program with no errors,
  2. its moves match the toolpath our checker traced, move for move (so the preview and the checks read it right),
  3. what it cuts matches the plan: each planned hole is cut to its diameter and depth, nothing is cut anywhere else,
     and each tap goes deep enough without hitting the bottom, feeding exactly one pitch per spindle turn.
Two Fanuc-vs-LinuxCNC differences are allowed for: M29 (Fanuc's rigid tapping switch) is dropped, and the way out
of a G84/G85 hole under G98 counts as one move (Fanuc feeds to R then rapids up; LinuxCNC feeds all the way up).

Run: uv run python scripts/linuxcnc_check.py PART.step PLAN.json MATERIAL   (needs Docker)
"""

import json
import math
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from hole_cam.checks import check, context_for
from hole_cam.gcode import _load, generate
from hole_cam.holes import find_holes

HERE = Path(__file__).parent
IMAGE = "hole-cam-linuxcnc"
HOME_Z = 1000.0  # G28 Z home for the run; set high so it can't look like a move near the part
MOVE = re.compile(r"(STRAIGHT_TRAVERSE|STRAIGHT_FEED)\(([-\d.]+), ([-\d.]+), ([-\d.]+)")
TOOL = re.compile(r"CHANGE_TOOL\((\d+)\)")
SPEED = re.compile(r"SET_SPINDLE_SPEED\(\d+, ([\d.]+)\)")
FEED = re.compile(r"SET_FEED_RATE\(([\d.]+)\)")
# rs274 echoes the offending line after an error message; normal output lines all start with a sequence number.
OUTPUT = re.compile(r"^\s*\d+ N\.\.\.\.\. |^executing$")


def run_rs274(program, tools):
    subprocess.run(["docker", "build", "-q", "-t", IMAGE, "-f", HERE / "linuxcnc.Dockerfile", HERE],
                   check=True, capture_output=True)
    with tempfile.TemporaryDirectory() as d:
        Path(d, "prog.ngc").write_text(re.sub(r"(?m)^M29 S\d+\n", "", program))
        Path(d, "tool.tbl").write_text("".join(f"T{t['number']} P{t['number']} Z0 D{t['diameter']}\n" for t in tools))
        Path(d, "sim.var").write_text(f"5161\t0\n5162\t0\n5163\t{HOME_Z}\n5220\t1\n")
        r = subprocess.run(["docker", "run", "--rm", "-v", f"{d}:/w", "-w", "/w", IMAGE,
                            "rs274", "-t", "tool.tbl", "-v", "sim.var", "-g", "prog.ngc", "out.canon"],
                           capture_output=True, text=True)
        canon = Path(d, "out.canon").read_text() if Path(d, "out.canon").exists() else ""
    errors = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip() and not OUTPUT.match(ln)]
    return canon, errors


def their_moves(canon):
    """[(kind, tool, (x, y, z))] for every move that goes somewhere. Like our trace, it leaves out moves that start
    from an unknown position: before the program first sets X and Y (rs274 assumes 0, 0), and on the way out of G28 home."""
    moves, tool, at, xy_known = [], None, (0.0, 0.0, 0.0), False
    for ln in canon.splitlines():
        if m := TOOL.search(ln):
            tool = int(m[1])
        elif m := MOVE.search(ln):
            to = tuple(round(float(v), 3) for v in m.groups()[1:])
            if to != at and xy_known and at[2] < HOME_Z and to[2] < HOME_Z:
                moves.append(("rapid" if m[1] == "STRAIGHT_TRAVERSE" else "feed", tool, to))
            xy_known = xy_known or to[:2] != at[:2]
            at = to
    return moves


def our_moves(trace):
    return [(m["kind"], m["tool"], tuple(round(v, 3) for v in m["to"])) for m in trace if m["kind"] != "tool_change"]


def one_way_out(moves):
    """Merge a feed up followed by a rapid further up on the same spot into one move out of the hole."""
    out = []
    for mv in moves:
        if (out and mv[0] == "rapid" and out[-1][0] == "feed" and out[-1][2][:2] == mv[2][:2] and len(out) > 1
                and out[-2][2][:2] == mv[2][:2] and out[-2][2][2] < out[-1][2][2] < mv[2][2]):
            out[-1] = ("feed", mv[1], mv[2])
        else:
            out.append(mv)
    return out


def tap_sync(canon, tools):
    """Tap feeds that aren't exactly spindle speed x pitch, as LinuxCNC set them."""
    by_number, tool, speed, feed, bad = {t["number"]: t for t in tools}, None, 0.0, 0.0, []
    for ln in canon.splitlines():
        if m := TOOL.search(ln):
            tool = by_number.get(int(m[1]))
        elif m := SPEED.search(ln):
            speed = float(m[1])
        elif m := FEED.search(ln):
            feed = float(m[1])
        elif "STRAIGHT_FEED" in ln and tool and tool["type"] == "tap" and abs(feed - speed * tool["pitch"]) > 0.01:
            bad.append(f"{tool['id']} feeds {feed:g} mm/min at {speed:g} rpm; one pitch per turn is {speed * tool['pitch']:g}")
    return sorted(set(bad))


def cuts(moves, tools):
    """{(x, y): [(tool, full-diameter depth reached)]}: every feed straight down, with the tool's point allowed for."""
    by_number = {t["number"]: t for t in tools}
    out, at = {}, None
    for kind, n, to in moves:
        if kind == "feed" and at and at[:2] == to[:2] and to[2] < at[2]:
            t = by_number[n]
            point = 0 if t["type"] in ("spot_drill", "reamer", "tap") else (t["diameter"] / 2) / math.tan(math.radians(t["point_angle"] / 2))
            out.setdefault(to[:2], []).append((t, to[2] + point))
        at = to
    return out


def compare_cuts(found, plan, cut, tools):
    z0, holes, problems = found["part"]["top_z"], {h["id"]: h for h in found["holes"]}, []
    lib = {t["id"]: t for t in tools}
    taps = {i: lib[op["tool_id"]] for op in plan["operations"] if op["op"] == "tap" for i in op["hole_ids"]}
    planned = {}
    for op in plan["operations"]:
        for i in op["hole_ids"]:
            h = holes[i]
            planned.setdefault((round(h["x"], 3), round(h["y"], 3)), set()).add(i)
    for xy in cut.keys() - planned.keys():
        problems.append(f"cuts at X{xy[0]:g} Y{xy[1]:g}, where the plan has no hole")
    # A tapped hole is drilled at its tap's drill size, whatever size the model shows.
    size = lambda i: lib[taps[i]["tap_drill"]]["diameter"] if i in taps else holes[i]["diameter"]
    rows = []
    for xy, ids in sorted(planned.items()):
        made = [(t, z) for t, z in cut.get(xy, []) if t["type"] not in ("spot_drill", "tap")]
        biggest = max((t["diameter"] for t, _ in made), default=0)
        for i in sorted(ids):
            h = holes[i]
            bottom = h["top_z"] - z0 - h["depth"]
            sized = [z for t, z in made if abs(t["diameter"] - size(i)) <= 0.02]
            reached = min(sized, default=None)
            # rs274 prints 3 decimals, hence the 0.01 mm.
            ok = reached is not None and reached <= bottom + 0.01 and (not h["through"] or reached < bottom)
            rows.append((i, h["diameter"], biggest, bottom, reached, ok))
            if not ok:
                problems.append(f"hole {i} (Ø{h['diameter']:g}) is not cut to size and depth")
            if i in taps:
                tap = taps[i]
                tips = [z for t, z in cut.get(xy, []) if t["id"] == tap["id"]]
                # Through: the tap's 3-pitch lead must clear the bottom. Blind: within a pitch of the bottom, not on it.
                need = (bottom - 3 * tap["pitch"] + 0.01) if h["through"] else (bottom + tap["pitch"] + 0.01)
                if not tips or min(tips) > need or (not h["through"] and min(tips) < bottom - 0.01):
                    problems.append(f"hole {i}: {tap['id']} reaches Z{min(tips, default=float('nan')):g}; it should reach "
                                    f"Z{need:g}{'' if h['through'] else f' without passing the bottom at Z{bottom:g}'}")
        if biggest > max(size(i) for i in ids) + 0.02:  # taps excluded: they cut a thread, not a bore
            problems.append(f"Ø{biggest:g} tool at X{xy[0]:g} Y{xy[1]:g} is bigger than the hole")
    return rows, problems


def main(step, plan_path, material):
    found, plan, tools = find_holes(step), json.loads(Path(plan_path).read_text()), _load("tools.json")
    plan = plan.get("plan", plan)  # a saved live run keeps the plan under "plan"
    program = generate(found, plan, tools, _load("materials.json")[material], _load("machine.json"), name=Path(step).stem)
    # LinuxCNC's G83 comes back down to 0.010" (0.254 mm) above the last peck, fixed; on a Fanuc it's a parameter.
    machine = {**_load("machine.json"), "peck_clearance": 0.254}
    trace = []
    findings = check(program, machine, **context_for(found), trace=trace)
    canon, errors = run_rs274(program, tools)
    theirs, ours = one_way_out(their_moves(canon)), one_way_out(our_moves(trace))

    print(f"{Path(step).name}: {len(program.splitlines())} lines, our checks: {len(findings)} problem(s)")
    print(f"1. LinuxCNC read it: {'yes' if not errors else 'NO'}")
    for e in errors:
        print("   ", e)
    same = sum(a == b for a, b in zip(theirs, ours))
    print(f"2. moves: LinuxCNC {len(theirs)}, ours {len(ours)}, identical {same}")
    for k, (a, b) in enumerate(zip(theirs, ours)):
        if a != b:
            print(f"   first difference, move {k + 1}: LinuxCNC {a}  ours {b}")
            break
    rows, problems = compare_cuts(found, plan, cuts(theirs, tools), tools)
    problems += tap_sync(canon, tools)
    print(f"3. holes cut by LinuxCNC's moves: {sum(r[-1] for r in rows)} of {len(rows)} planned holes to size and depth")
    for i, d, biggest, bottom, reached, ok in rows:
        print(f"   hole {i:>2}  Ø{d:<7g} largest tool Ø{biggest:<7g} bottom Z{bottom:<9g} "
              f"full size reaches Z{'-' if reached is None else f'{reached:g}'}  {'ok' if ok else 'PROBLEM'}")
    for p in problems:
        print("   PROBLEM:", p)
    passed = not errors and not findings and theirs == ours and not problems
    print("PASS" if passed else "FAIL")
    return passed


if __name__ == "__main__":
    sys.exit(0 if main(*sys.argv[1:4]) else 1)
