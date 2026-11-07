"""Plan drilling operations with Claude, then check the plan with plain code (SPEC.md 3.2 and 3.3).

Claude picks the operations, tools, cycles and order. It never sets speeds or feeds; those come from the
material table in gcode.py. Every plan goes through validate(), and a rejected plan is sent back once with
the errors listed.

CLI: uv run python -m hole_cam.planner PART.step MATERIAL > plan.json
"""

import json
import math
import sys

import anthropic

from hole_cam.gcode import _load, _tip_depth

MODEL = "claude-opus-5-5"
DIAMETER_TOL = 0.05  # mm

SYSTEM = """You are a CNC programmer planning hole-making on a 3-axis vertical mill. One setup, parts are drilled from the top.

You get the holes found in the CAD model, the material, the tool library and the machine limits. Return a plan:
- Spot drill every hole you will drill, before drilling it. For a spot drill operation, set depth (mm below the hole top) so the spot cone is a little smaller than the drill. Use cycle G81 and peck 0.
- Drill each hole with the library drill whose diameter matches the hole. If no drill matches, do not drill that hole; add a warning naming the hole and what it would need (a reamer, a boring bar, helical interpolation).
- Use G83 peck drilling when the hole is deeper than {peck_ratio} x its diameter. Peck about 1 x the drill diameter. Otherwise use G81 with peck 0. For drill operations, set depth to 0 (the depth comes from the model).
- Group holes that share a tool and cycle into one operation to keep tool changes down.
- Only use tool IDs from the library. Give each operation a one-sentence reason a machinist would agree with.
Speeds and feeds are calculated elsewhere; do not include them."""


def plan_schema(tools):
    return {
        "type": "object",
        "properties": {
            "operations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": ["spot_drill", "drill"]},
                        "tool_id": {"type": "string", "enum": [t["id"] for t in tools]},
                        "hole_ids": {"type": "array", "items": {"type": "integer"}},
                        "depth": {"type": "number"},
                        "cycle": {"type": "string", "enum": ["G81", "G83"]},
                        "peck": {"type": "number"},
                        "reason": {"type": "string"},
                    },
                    "required": ["op", "tool_id", "hole_ids", "depth", "cycle", "peck", "reason"],
                    "additionalProperties": False,
                },
            },
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["operations", "warnings"],
        "additionalProperties": False,
    }


def plan_job(found, material, tools, machine, client=None):
    """Ask Claude for a plan; retry once if validation rejects it. Returns {plan, errors, notes}."""
    client = client or anthropic.Anthropic()
    job = {"material": material["name"], "holes": found["holes"], "skipped": found["skipped"],
           "tools": tools, "machine": machine}
    messages = [{"role": "user", "content": "Plan this job:\n" + json.dumps(job, indent=1)}]

    for attempt in range(2):
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM.format(peck_ratio=machine["peck_ratio"]),
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": plan_schema(tools)}},
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


def validate(plan, found, tools, machine):
    """Hard errors block G-code generation; notes are worth showing but are safe."""
    holes = {h["id"]: h for h in found["holes"]}
    lib = {t["id"]: t for t in tools}
    errors, notes = [], []
    spotted, drilled, prev_tool = set(), {}, None

    for n, op in enumerate(plan["operations"], 1):
        tool = lib.get(op["tool_id"])
        where = f"operation {n} ({op['op']} {op['tool_id']})"
        if tool is None:
            errors.append(f"{where}: tool is not in the library")
            continue
        if tool["type"] != op["op"]:
            errors.append(f"{where}: a {tool['type']} can't do a {op['op']} operation")
            continue
        if op["tool_id"] == prev_tool:
            notes.append(f"{where}: same tool as the operation before; merging them saves a tool change")
        prev_tool = op["tool_id"]

        for hid in op["hole_ids"]:
            h = holes.get(hid)
            if h is None:
                errors.append(f"{where}: hole {hid} does not exist")
                continue
            if op["op"] == "spot_drill":
                spot_d = 2 * op["depth"] * math.tan(math.radians(tool["point_angle"] / 2))
                if op["depth"] <= 0 or spot_d > h["diameter"] + 1:
                    errors.append(f"{where}: spot depth {op['depth']} mm gives a {spot_d:.1f} mm spot on a {h['diameter']} mm hole")
                spotted.add(hid)
                continue
            if hid in drilled:
                errors.append(f"{where}: hole {hid} is already drilled in operation {drilled[hid]}")
            drilled[hid] = n
            if hid not in spotted:
                errors.append(f"{where}: hole {hid} is drilled before it is spotted")
            if abs(tool["diameter"] - h["diameter"]) > DIAMETER_TOL:
                errors.append(f"{where}: a {tool['diameter']} mm drill on a {h['diameter']} mm hole {hid}")
            reach = _tip_depth(op, tool, h, machine)
            if reach > tool["flute_length"]:
                errors.append(f"{where}: hole {hid} needs {reach:.1f} mm of reach; flutes are {tool['flute_length']} mm")
            if h["depth"] > machine["peck_ratio"] * h["diameter"] and op["cycle"] != "G83":
                errors.append(f"{where}: hole {hid} is {h['depth'] / h['diameter']:.1f}xD deep and needs G83 peck drilling")
        if op["op"] == "drill" and op["cycle"] == "G83" and op["peck"] <= 0:
            errors.append(f"{where}: G83 needs a peck depth above 0")

    drills = [t for t in tools if t["type"] == "drill"]
    for hid, h in holes.items():
        if hid not in drilled and any(abs(t["diameter"] - h["diameter"]) <= DIAMETER_TOL for t in drills):
            errors.append(f"hole {hid} ({h['diameter']} mm) is not drilled, but the library has a drill for it")
    return {"errors": errors, "notes": notes}


if __name__ == "__main__":
    from hole_cam.holes import find_holes

    step, mat = sys.argv[1:3]
    result = plan_job(find_holes(step), _load("materials.json")[mat], _load("tools.json"), _load("machine.json"))
    json.dump(result["plan"], sys.stdout, indent=2)
    for line in result["errors"] + result["notes"] + result["plan"]["warnings"]:
        print(line, file=sys.stderr)
    sys.exit(1 if result["errors"] else 0)
