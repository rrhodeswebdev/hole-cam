"""Plan drilling operations with Claude, then check the plan with plain code (SPEC.md 3.2 and 3.3).

Claude picks the operations, tools, cycles and order. It never sets speeds or feeds; those come from the
material table in gcode.py. Every plan goes through validate(), and a rejected plan is sent back once with
the errors listed.

CLI: uv run python -m hole_cam.planner PART.step MATERIAL [DRAWING.pdf] > plan.json
"""

import base64
import json
import math
import re
import sys

import anthropic

from hole_cam.gcode import _load, _tip_depth

MODEL = "claude-opus-5-5"
DIAMETER_TOL = 0.02  # mm: same nominal size, allowing for inch-to-mm rounding; a 9/32" drill is not a 7.1 mm one
# shortcut: one reaming allowance (mm on diameter) for every size; fine up to about 13 mm, bigger reamers want more.
REAM_STOCK = (0.1, 0.4)

SYSTEM = """You are a CNC programmer planning hole-making on a 3-axis vertical mill. One setup, parts are drilled from the top.

You get the holes found in the CAD model, the material, the tool library and the machine limits, and sometimes the part's engineering drawing as a PDF. Return a plan:
- Spot drill every hole you will drill, before drilling it. For a spot drill operation, set depth (mm below the hole top) so the spot cone is a little smaller than the drill. Use cycle G81 and peck 0.
- Drill each hole with the library drill whose diameter matches the hole. If no drill matches, do not drill that hole; add a warning naming the hole and what it would need (a reamer, a boring bar, helical interpolation).
- Use G83 peck drilling when the hole is deeper than {peck_ratio} x its diameter. Peck about 1 x the drill diameter. Otherwise use G81 with peck 0. For drill operations, set depth to 0 (the depth comes from the model).
- Group holes that share a tool and cycle into one operation to keep tool changes down.
- Only use tool IDs from the library. Give each operation a one-sentence reason a machinist would agree with.
Speeds and feeds are calculated elsewhere; do not include them.

The drawing, when there is one:
- Read the hole callouts. For each callout that points at holes in this job, add an entry to drawing: the hole ids it points at, the callout as written (e.g. "4X Ø.250 +.003 -.001"), its units ("in" or "mm"), the nominal diameter, and the upper and lower deviations as signed numbers in those units (+.003 -.001 is 0.003 and -0.001; ±.010 is 0.010 and -0.010; for a fit like H7, its deviations). Match callouts to holes by size, count and where the leader points in each view. Leave out callouts for holes not in the job, such as holes on other faces. With no drawing, drawing is empty.
- A hole whose upper deviation is under {drill_oversize} mm needs reaming, because a drill can cut that much oversize. Spot it, drill it with a library drill 0.1 to 0.4 mm smaller than the reamer, then ream it with the reamer that matches its diameter: op ream, cycle G85, depth 0, peck 0. If the library has no matching reamer or pre-drill, leave the hole out and warn."""

REVISE = """

Revising a plan: you also get the current plan and a change the machinist asked for. Make that change and keep the rest of the plan as it is.
These rules are checked by code after you answer, so keep them even if asked not to: every drilled hole is spotted first (a hole at the floor of a wider hole already drilled counts as spotted by that drill's point); each drill matches its hole's diameter; tools come from the library; a hole deeper than {peck_ratio} x its diameter uses G83 with a peck above 0; the drill's flutes reach the full depth; every hole the library has a drill for is drilled; a hole the drawing tolerances tighter than {drill_oversize} mm is drilled 0.1 to 0.4 mm under and then reamed with G85; the drawing entries match the holes' diameters; a hole that starts at the floor of a wider hole deeper than the R clearance (under a counterbore) is spotted and drilled after the wider one.
Everything else above is a default the machinist may override: peck size, pecking shallow holes too, spot depth, the order of operations and holes, and how operations are grouped. Do what they ask there.
If the request conflicts with a checked rule, keep the rule, get as close to the request as the rules allow, and say so in summary.
summary: one or two sentences to the machinist saying what you changed, or why you couldn't."""


def plan_schema(tools):
    return {
        "type": "object",
        "properties": {
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": ["spot_drill", "drill", "ream"]},
                        "tool_id": {"type": "string", "enum": [t["id"] for t in tools]},
                        "hole_ids": {"type": "array", "items": {"type": "integer"}},
                        "depth": {"type": "number"},
                        "cycle": {"type": "string", "enum": ["G81", "G83", "G85"]},
                        "peck": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["op", "tool_id", "hole_ids", "depth", "cycle", "peck", "reason"],
                    "additionalProperties": False,
                },
            },
            "warnings": {"type": "array", "items": {"type": "string"}},
            "drawing": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "hole_ids": {"type": "array", "items": {"type": "integer"}},
                        "callout": {"type": "string"},
                        "units": {"type": "string", "enum": ["in", "mm"]},
                        "diameter": {"type": "number"},
                        "plus": {"type": "number"},
                        "minus": {"type": "number"},
                    },
                    "required": ["hole_ids", "callout", "units", "diameter", "plus", "minus"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["operations", "warnings", "drawing"],
        "additionalProperties": False,
    }


def _job(found, material, tools, machine):
    # Give hole heights in program coordinates (G54 Z0 = part top) so they match the machine note;
    # model coordinates made Claude warn that every depth would be off.
    z0 = found["part"]["top_z"]
    holes = [{**h, "top_z": round(h["top_z"] - z0, 3)} for h in found["holes"]]
    return json.dumps({"material": material["name"], "holes": holes, "skipped": found["skipped"],
                       "tools": tools, "machine": machine}, indent=1)


def offered_tools(found, tools):
    """The tools worth showing Claude: spot drills, each drill or reamer that fits a hole, the pre-drills for
    those reamers, and for a hole no drill fits, the nearest drill on either side so Claude can say what's close.
    Validation still uses the whole library; this only keeps the prompt short (44 tools would triple its size)."""
    drills = sorted((t for t in tools if t["type"] == "drill"), key=lambda t: t["diameter"])
    keep = {t["id"] for t in tools if t["type"] == "spot_drill"}
    for h in found["holes"]:
        for r in _fits(tools, "reamer", h["diameter"]):
            keep.update([r["id"]] + [t["id"] for t in _pre_drills(tools, r)])
        fits = _fits(tools, "drill", h["diameter"])
        if fits:
            keep.update(t["id"] for t in fits)
            continue
        below = [t for t in drills if t["diameter"] < h["diameter"]]
        above = [t for t in drills if t["diameter"] > h["diameter"]]
        keep.update(t["id"] for t in below[-1:] + above[:1])
    return [t for t in tools if t["id"] in keep]


def _fits(tools, kind, diameter):
    return [t for t in tools if t["type"] == kind and abs(t["diameter"] - diameter) <= DIAMETER_TOL]


def _pre_drills(tools, reamer):
    return [t for t in tools if t["type"] == "drill" and _stock_ok(t["diameter"], reamer["diameter"])]


def _stock_ok(drill_d, reamer_d):
    return REAM_STOCK[0] - 1e-6 <= reamer_d - drill_d <= REAM_STOCK[1] + 1e-6


def plan_job(found, material, tools, machine, client=None, drawing=None):
    """Ask Claude for a plan; retry once if validation rejects it. drawing: the PDF's bytes, if there is one.
    Returns {plan, errors}."""
    offered = offered_tools(found, tools)
    content = "Plan this job:\n" + _job(found, material, offered, machine)
    return _ask(client, SYSTEM, _with_drawing(content, drawing), plan_schema(offered), found, tools, machine)


def revise_plan(found, material, tools, machine, plan, request, client=None, drawing=None):
    """Ask Claude to change `plan` as the machinist requests. Returns {plan, errors, summary}."""
    offered = offered_tools(found, tools)
    schema = plan_schema(offered)
    schema["properties"]["summary"] = {"type": "string"}
    schema["required"].append("summary")
    content = (f"The job:\n{_job(found, material, offered, machine)}\n\nCurrent plan:\n{json.dumps(plan, indent=1)}"
               f"\n\nThe machinist asks:\n{request}")
    result = _ask(client, SYSTEM + REVISE, _with_drawing(content, drawing), schema, found, tools, machine)
    return {**result, "summary": result["plan"].pop("summary")}


def _with_drawing(text, drawing):
    if not drawing:
        return text
    pdf = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                          "data": base64.b64encode(drawing).decode()}}
    return [pdf, {"type": "text", "text": text}]


def _ask(client, system, content, schema, found, tools, machine):
    client = client or anthropic.Anthropic()
    messages = [{"role": "user", "content": content}]
    for attempt in range(2):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system.format(peck_ratio=machine["peck_ratio"], drill_oversize=machine["drill_oversize"]),
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            messages=messages,
        )
        if response.stop_reason != "end_turn":
            raise RuntimeError(f"planner stopped early: {response.stop_reason} {response.stop_details or ''}")
        plan = json.loads(next(b.text for b in response.content if b.type == "text"))
        result = validate(plan, found, tools, machine)
        if not result["errors"] or attempt == 1:
            return {"plan": plan, **result}
        messages += [
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": "The plan was rejected:\n- " + "\n- ".join(result["errors"]) + "\nReturn a corrected plan."},
        ]


def drawing_callouts(plan, machine):
    """The plan's drawing callouts, sized in mm, with whether each needs reaming. Claude reads the drawing;
    this code decides what the tolerance needs."""
    out = []
    for c in plan.get("drawing") or []:
        k = 25.4 if c["units"] == "in" else 1.0
        out.append({**c, "diameter_mm": round(c["diameter"] * k, 4), "plus_mm": round(c["plus"] * k, 4),
                    "minus_mm": round(c["minus"] * k, 4), "ream": c["plus"] * k < machine["drill_oversize"]})
    return out


def _ream_path(tools, hole):
    """(reamer, pre-drill) for a hole, or None if the library can't ream it."""
    for r in _fits(tools, "reamer", hole["diameter"]):
        pre = _pre_drills(tools, r)
        if pre:
            return r, max(pre, key=lambda t: t["diameter"])
    return None


def validate(plan, found, tools, machine):
    """Return {"errors": [...]}; any error blocks G-code generation."""
    holes = {h["id"]: h for h in found["holes"]}
    lib = {t["id"]: t for t in tools}
    errors = []
    spotted, drilled, reamed = {}, {}, {}  # hole id -> operation number
    drill_size = {}  # hole id -> diameter of the drill that made it
    # Holes that start at the floor of a wider hole on the same center (e.g. under a counterbore): hole id -> wider id.
    above = {b["id"]: a["id"] for a in holes.values() for b in holes.values()
             if abs(a["x"] - b["x"]) < 1e-3 and abs(a["y"] - b["y"]) < 1e-3 and a["diameter"] > b["diameter"]
             and abs(b["top_z"] - (a["top_z"] - a["depth"])) < 0.01}

    callout = {}  # hole id -> its drawing callout
    for c in drawing_callouts(plan, machine):
        count = re.match(r"\s*(\d+)\s*X", c["callout"], re.I)
        if count and len(c["hole_ids"]) > int(count[1]):
            errors.append(f"drawing: {c['callout']!r} is for {count[1]} holes but lists {len(c['hole_ids'])}")
        for hid in c["hole_ids"]:
            h = holes.get(hid)
            if h is None:
                errors.append(f"drawing: {c['callout']!r} points at hole {hid}, which does not exist")
                continue
            if hid in callout:
                errors.append(f"drawing: hole {hid} has two callouts, {callout[hid]['callout']!r} and {c['callout']!r}")
            if abs(c["diameter_mm"] - h["diameter"]) > DIAMETER_TOL:
                errors.append(f"drawing: {c['callout']!r} is {c['diameter_mm']:g} mm but hole {hid} is {h['diameter']:g} mm "
                              "in the model: wrong hole, or the drawing and model disagree")
            callout[hid] = c
    needs_ream = {hid for hid, c in callout.items() if c["ream"]}
    to_ream = {hid for op in plan["operations"] if op["op"] == "ream" for hid in op["hole_ids"]}

    for n, op in enumerate(plan["operations"], 1):
        tool = lib.get(op["tool_id"])
        where = f"operation {n} ({op['op']} {op['tool_id']})"
        if tool is None:
            errors.append(f"{where}: tool is not in the library")
            continue
        if tool["type"] != {"ream": "reamer"}.get(op["op"], op["op"]):
            errors.append(f"{where}: a {tool['type']} can't do a {op['op']} operation")
            continue
        for hid in op["hole_ids"]:
            h = holes.get(hid)
            if h is None:
                errors.append(f"{where}: hole {hid} does not exist")
                continue
            if op["op"] == "spot_drill":
                spot_d = 2 * op["depth"] * math.tan(math.radians(tool["point_angle"] / 2))
                if op["depth"] <= 0 or spot_d > h["diameter"] + 1:
                    errors.append(f"{where}: spot depth {op['depth']} mm gives a {spot_d:.1f} mm spot on a {h['diameter']} mm hole")
                spotted.setdefault(hid, n)
                continue
            reach = _tip_depth(op, tool, h, machine)
            if reach > tool["flute_length"]:
                errors.append(f"{where}: hole {hid} needs {reach:.1f} mm of reach; flutes are {tool['flute_length']} mm")
            if op["op"] == "ream":
                if hid in reamed:
                    errors.append(f"{where}: hole {hid} is already reamed in operation {reamed[hid]}")
                reamed[hid] = n
                if abs(tool["diameter"] - h["diameter"]) > DIAMETER_TOL:
                    errors.append(f"{where}: a {tool['diameter']} mm reamer on a {h['diameter']} mm hole {hid}")
                elif hid not in drill_size:
                    errors.append(f"{where}: hole {hid} is reamed before it is drilled")
                elif not _stock_ok(drill_size[hid], tool["diameter"]):
                    errors.append(f"{where}: hole {hid} was drilled {drill_size[hid]:g} mm, leaving {tool['diameter'] - drill_size[hid]:.2f} mm "
                                  f"for the reamer; it should remove {REAM_STOCK[0]:g} to {REAM_STOCK[1]:g} mm")
                continue
            if hid in drilled:
                errors.append(f"{where}: hole {hid} is already drilled in operation {drilled[hid]}")
            drilled[hid], drill_size[hid] = n, tool["diameter"]
            # The point of the wider drill above leaves a centered cone, which guides this drill like a spot would.
            if hid not in spotted and not (hid in above and above[hid] in drilled):
                errors.append(f"{where}: hole {hid} is drilled before it is spotted")
            undersize = hid in to_ream and _stock_ok(tool["diameter"], h["diameter"])
            if abs(tool["diameter"] - h["diameter"]) > DIAMETER_TOL and not undersize:
                errors.append(f"{where}: a {tool['diameter']} mm drill on a {h['diameter']} mm hole {hid}")
            elif hid in needs_ream and not undersize:
                errors.append(f"{where}: hole {hid} is toleranced {callout[hid]['callout']!r}, tighter than a drill holds; "
                              "drill it 0.1 to 0.4 mm under and ream it")
            if h["depth"] > machine["peck_ratio"] * h["diameter"] and op["cycle"] != "G83":
                errors.append(f"{where}: hole {hid} is {h['depth'] / h['diameter']:.1f}xD deep and needs G83 peck drilling")
        if op["op"] == "drill" and op["cycle"] == "G83" and op["peck"] <= 0:
            errors.append(f"{where}: G83 needs a peck depth above 0")
        if (op["op"] == "ream") != (op.get("cycle") == "G85"):
            errors.append(f"{where}: reaming uses G85 (feed in and back out), and only reaming does")

    # A hole under a wider one has its R plane below the surface when the wider one is deeper than the R clearance:
    # until the wider hole is cut, that rapid ends in solid metal. The G-code checker only knows one surface
    # height per hole center, so it can't see this; check it here.
    for b, a in above.items():
        first = min((spotted.get(b, math.inf), drilled.get(b, math.inf)))
        if (holes[a]["depth"] > machine["r_clearance"] + 1e-3 and first < math.inf
                and drilled.get(a, math.inf) > first):
            errors.append(f"hole {b} starts {holes[a]['depth']:g} mm down, at the floor of hole {a}: drill hole {a} "
                          f"before hole {b} is spotted or drilled, or the rapid to its R plane ends inside the part")

    has_drill = lambda h: bool(_fits(tools, "drill", h["diameter"]))
    for hid, h in holes.items():
        path = _ream_path(tools, h)
        if hid in needs_ream and hid not in reamed and path:
            errors.append(f"hole {hid} is toleranced {callout[hid]['callout']!r}, tighter than a drill holds: "
                          f"drill it with {path[1]['id']} and ream it with {path[0]['id']}")
        if hid in above and not has_drill(holes[above[hid]]):
            continue  # can't be reached until the wider hole above it is made some other way
        if hid in needs_ream and not path:
            continue  # the library can't hold the tolerance; Claude warns about it
        if hid not in drilled and has_drill(h):
            errors.append(f"hole {hid} ({h['diameter']} mm) is not drilled, but the library has a drill for it")
    return {"errors": errors}


if __name__ == "__main__":
    from hole_cam.holes import find_holes

    step, mat, *pdf = sys.argv[1:4]
    result = plan_job(find_holes(step), _load("materials.json")[mat], _load("tools.json"), _load("machine.json"),
                      drawing=open(pdf[0], "rb").read() if pdf else None)
    json.dump(result["plan"], sys.stdout, indent=2)
    for line in result["errors"] + result["plan"]["warnings"]:
        print(line, file=sys.stderr)
    sys.exit(1 if result["errors"] else 0)
