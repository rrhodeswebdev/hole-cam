# Hole-CAM

Upload a STEP part and get back a checked drilling program. Claude plans the job, plain code writes the G-code, and safety checks block the download until the program is safe to send to a machine.

It's a weekend-sized version of the "engineering model → machining strategy → G-code" problem: holes only, one setup, a 3-axis mill.

![Hole-CAM showing a plate with four holes, the drilling plan and the 3D toolpath](docs/screenshot.png)
<sub>The plan in this screenshot is the hand-written test plan, not Claude's.</sub>
<!-- TODO: replace with a screenshot from a live Claude run -->

## The user and the problem

The user is a CNC programmer turning a customer's CAD model into a program the machine can run. Hole-making is a large share of that work, and most of it is repetitive: find every hole, pick the drill, decide whether to peck, set speeds and feeds, order the holes, write the cycles. It's also where small slips are expensive. A rapid move at the wrong height, a missing tool length offset or a stopped spindle can break a tool, scrap the part or crash the spindle.

So the goal isn't just "generate G-code fast". It's to generate it fast **and** show the programmer why each choice was made, while refusing to hand over a program that fails basic safety checks.

## What it does

```
STEP file ──▶ find holes (CadQuery) ──▶ plan (Claude) ──▶ validate plan ──▶ write G-code ──▶ safety checks ──▶ preview + download
                                              ▲                │ rejected
                                              └── one retry ◀──┘
```

1. **Find holes:** reads the STEP geometry and finds inner cylindrical faces, merging the split halves many CAD exporters produce. It tells through holes from blind holes by what closes the bottom (the part's underside, a flat floor or a drill point). Holes it can't drill in one top-side setup are reported as skipped, with a reason.
2. **Plan:** Claude gets the holes, the material, the tool library and the machine limits. It returns a plan in a fixed JSON format: spot drill, then drill, G81 or G83 peck drilling, holes grouped by tool, and a one-line reason for each operation.
3. **Validate:** plain code checks the plan. It rejects the wrong drill size, holes drilled before they're spotted, holes drilled twice or missed, drills too short for the depth, and deep holes without pecking. A rejected plan goes back to Claude once with the errors listed.
4. **Write G-code:** plain code. Speeds and feeds come from a material table, never from the model.
5. **Safety checks:** a G-code reader tracks the machine's state line by line and runs six checks:
   - rapid moves into the part
   - cutting with the spindle off
   - a missing or wrong tool length offset
   - speed or feed above the machine's limits
   - drilling past the bottom of the part
   - an unsafe end of program or tool change

   The checks work on any Fanuc-style drilling program, not just this tool's.
6. **Preview:** the part and toolpath in 3D. The toolpath is drawn by reading back the generated G-code, so it shows what the machine will do, not what was intended.

## Where the AI is, and where it isn't

Claude does the part that's judgment: choosing operations and order, matching tools to holes, deciding when to peck, and explaining its choices in a machinist's terms.

Anything that can break a tool if it's wrong is plain, tested code: geometry, feeds and speeds, G-code syntax and the safety checks. The model's output is treated as a proposal. Its format is enforced by a schema, its tool IDs are limited to the real library, and every plan is validated before any G-code exists.

## How I know it works

- **62 automated tests:** `uv run pytest`.
- **Hole finding** is tested on five generated parts with known answers: a plate, mixed through and blind holes with a drill-point bottom, an odd size with no matching drill, a side hole and a hole drilled up from the bottom, and a boss whose outer wall must not count as a hole. Two of the parts are also re-run with every hole split into half-faces, as real CAD exports often do.
- **G-code:** the reference program's speeds, feeds, depths and hole order were checked by hand against the formulas, and a test fails if the output ever changes.
- **Safety checks:** a set of 20 programs, 18 with one planted problem each and 2 clean. Result: **18/18 caught, 0 false alarms.** Each bad program must trip exactly its planted check.
- **The validator caught a mistake in my own plan:** I'd written plain drilling for 25 mm deep, 6.8 mm holes. That's 3.7× the diameter, past the 3× peck rule the tool is meant to enforce.
- **The web page** was driven in a real browser (light mode, dark mode and phone width) with the planner faked.

**Not verified yet:**
- [ ] A live Claude run end to end <!-- TODO -->
- [ ] A STEP file exported from real CAD software
- [ ] The programs proven out on a machine or in a simulator (NCViewer / CAMotics)
- [ ] Speeds and feeds checked against a drill maker's chart. Today they're conservative starting values.

## What I'd measure in a plant

- Programming time per part for hole-making, before and after.
- How often programmers edit the generated program before running it, and what they change. That's the best feedback for the planner prompt and the validator.
- Problems found at prove-out (first run on the machine) for generated programs versus hand-written ones.
- How often the checks block a program, and whether each block was a real problem.

## Limits and next steps

- **v1 scope:** round holes along Z, opening on top, one setup, metric, Fanuc-style. Tapping, reaming, pockets, chamfers and multiple setups are out of scope.
- **Safety checks:** the rapid check looks at each move's start and end points, not the full path, and only knows the surface height at hole centers. "Too deep" is measured against the bottom of the part, not each blind hole's depth.
- **Next:** tapping (G84), a post-processor per machine, a rules-only planner to compare against Claude's plans, and a record of operator feedback on each job.

## Running it

Needs Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 20+, and an Anthropic API key.

```bash
uv sync
(cd web && npm install && npm run build)
export ANTHROPIC_API_KEY=...
uv run uvicorn hole_cam.api:app       # open http://127.0.0.1:8000
```

For development, run the API with `--reload` and `npm run dev` in `web/`.

Command-line versions of each step:

```bash
uv run python -m hole_cam.planner fixtures/parts/mixed.step 6061 > plan.json
uv run python -m hole_cam.gcode   fixtures/parts/mixed.step plan.json 6061 > mixed.nc
uv run python -m hole_cam.checks  mixed.nc fixtures/parts/mixed.step
```

Regenerate the test fixtures with `uv run python fixtures/make_parts.py` and `uv run python fixtures/make_bad_gcode.py`.

On Intel Macs, a few CadQuery dependencies are pinned to their last releases with Intel builds (see `pyproject.toml`).

## Project layout

| Path | What |
|---|---|
| `src/hole_cam/holes.py` | Find holes in a STEP file |
| `src/hole_cam/planner.py` | Claude plan and validation |
| `src/hole_cam/gcode.py` | Plan to G-code |
| `src/hole_cam/checks.py` | G-code safety checks and toolpath trace |
| `src/hole_cam/api.py` | HTTP API and static web page |
| `web/` | React and three.js front end |
| `data/` | Tool library, materials, machine limits |
| `fixtures/` | Test parts, plans, reference program, bad G-code set |
| `SPEC.md` | The original weekend spec |

Built with Claude Code as a pair programmer. Every step has its own tests, and the numbers above come from running them.
