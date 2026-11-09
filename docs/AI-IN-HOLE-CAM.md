# How AI is used in Hole-CAM

## In one minute

- **One call to Claude per plan**, plus at most one retry. Claude turns the list of holes (and, if uploaded, the part's PDF drawing) into a drilling plan: which operations, which tools, which cycle, in what order, and a reason for each. A machinist can then ask for changes in plain English; each change is one more call.
- **Claude makes judgment calls; code computes anything that can break a tool.** Speeds, feeds, coordinates, unit conversions and tolerance rules are never generated. The two numbers Claude does pick, spot depth and peck size, are bounds-checked.
- **Every reply is treated as a proposal.** A JSON schema fixes its shape, about 30 validation rules check the machining choices, a rejected plan gets one retry with the exact errors, and the final G-code passes six safety checks before it can be downloaded.
- **Measured, not assumed.** An eval plans 7 parts 3 times on each of 3 models. Every plan was valid after at most one retry and every program passed the safety checks, but a quarter to a third of first attempts were rejected, which is what the retry is for. The results file is in the repo, and one command rebuilds the scorecard ([How well it works](#how-well-it-works)).
- **What a plan costs:** about $0.02–0.06 and 5–17 s for a small part on Opus 5.5, and $0.12–0.27 and 31–81 s for a NIST part with a 4–5 page drawing. Sonnet 5.5 scored the same as Opus on these parts at 41% of the cost, though it warned less.

---

## Where the AI sits

```mermaid
sequenceDiagram
    participant API as api.py
    participant P as planner.py
    participant C as Claude (Opus 5.5)
    participant V as validate()

    API->>P: plan_job(found holes, material, tools, machine, drawing)
    P->>C: system prompt + drawing PDF + job JSON, with the plan's JSON schema
    C-->>P: plan (JSON matching the schema)
    P->>V: check the plan
    alt plan is valid
        V-->>P: no errors
        P-->>API: plan
    else plan is rejected
        V-->>P: list of errors
        P->>C: "The plan was rejected: ...", return a corrected plan
        C-->>P: corrected plan
        P->>V: check again
        V-->>P: errors, or none
        P-->>API: plan and any remaining errors
    end
    Note over API: errors mean no G-code, otherwise the program is written and safety-checked
```

Where the code is:

| File | AI-related parts |
|---|---|
| `src/hole_cam/planner.py` | The prompt, `plan_schema()`, `offered_tools()`, `plan_job()`, `revise_plan()`, `drawing_callouts()` (converts Claude's callouts to mm and decides ream or tap), `validate()` |
| `src/hole_cam/api.py` | The `/jobs` and `/jobs/revise` endpoints, and turning planner failures into HTTP errors |
| `src/hole_cam/gcode.py` | Speeds and feeds from the material table, including a tap's feed of rpm × pitch |
| `src/hole_cam/checks.py` | The six safety checks on the finished program |
| `web/src/ChangePlan.tsx` | The plain-English revision box and the before/after view |
| `scripts/eval.py`, `fixtures/eval/` | The eval harness and the hand-written drawing answers it scores against |

---

## What Claude decides, and what code decides

| Claude decides | Plain code decides |
|---|---|
| Which operations to run (spot drill, drill, ream, tap) and in what order | Finding the holes and measuring them |
| What the drawing's hole callouts say, and which holes they point at | Converting them to mm, and whether a hole needs reaming or tapping |
| Which holes to tap, when the drawing or the machinist asks for threads | Whether the tap matches the thread, and its feed (rpm × pitch) |
| Which drill goes with each hole | Spindle speeds and feed rates |
| Plain drilling (G81) or peck drilling (G83), and the peck depth | Every coordinate, and the drilling depths |
| How deep to spot each hole (bounds-checked) | Whether the plan is acceptable |
| What to warn the programmer about | Whether the finished program is safe to run |
| A one-sentence reason for every operation | |

The split follows one rule: **anything that can break a tool or crash the machine if it's wrong is computed, not generated.** Claude handles the choices that need judgment and explanation, and code that doesn't need judgment checks them.

That rule is also why the scope is holes only. Hole-making is a large, repetitive share of CNC programming, and every choice in it can be checked by code: a drill size, a cycle, an order, a depth. That makes it a place where a model's plan can be trusted *because* it's verified, which is the part of the job worth showing in a weekend.

---

## What Claude gets and returns

### The system prompt

Sent unchanged with every request. `{peck_ratio}` and `{drill_oversize}` are filled in from `data/machine.json`, which sets them to 3 and 0.1.

```text
You are a CNC programmer planning hole-making on a 3-axis vertical mill. One setup, parts are drilled from the top.

You get the holes found in the CAD model, the material, the tool library and the machine limits, and sometimes the part's engineering drawing as a PDF. Return a plan:
- Spot drill every hole you will drill, before drilling it. For a spot drill operation, set depth (mm below the hole top) so the spot cone is a little smaller than the drill. Use cycle G81 and peck 0.
- Drill each hole with the library drill whose diameter matches the hole. If no drill matches, do not drill that hole; add a warning naming the hole and what it would need (a reamer, a boring bar, helical interpolation).
- Use G83 peck drilling when the hole is deeper than {peck_ratio} x its diameter. Peck about 1 x the drill diameter. Otherwise use G81 with peck 0. For drill operations, set depth to 0 (the depth comes from the model).
- Group holes that share a tool and cycle into one operation to keep tool changes down.
- Tap a hole only when the drawing calls it out as a thread or the machinist asks for one. Use the tap whose diameter and pitch match the thread, drill the hole first with that tap's tap_drill, then tap it: op tap, cycle G84, depth 0, peck 0. If the library has no matching tap, don't drill the hole bigger than the tap drill; leave it out and warn.
- Only use tool IDs from the library. Give each operation a one-sentence reason a machinist would agree with.
Speeds and feeds are calculated elsewhere; do not include them.

The drawing, when there is one:
- Read the hole callouts. For each callout that points at holes in this job, add an entry to drawing: the hole ids it points at, the callout as written (e.g. "4X Ø.250 +.003 -.001"), its units ("in" or "mm"), the nominal diameter, the upper and lower deviations as signed numbers in those units (+.003 -.001 is 0.003 and -0.001; ±.010 is 0.010 and -0.010; for a fit like H7, its deviations), and thread 0. For a thread callout (e.g. "M6x1.0-6H", "1/4-20 UNC-2B"), diameter is the thread's nominal size, plus and minus are 0, and thread is the pitch in mm for a metric thread or the threads per inch for an inch one. A threaded hole is usually modeled at its tap drill size, smaller than the thread. Match callouts to holes by size, count and where the leader points in each view. Leave out callouts for holes not in the job, such as holes on other faces. With no drawing, drawing is empty.
- A hole whose upper deviation is under {drill_oversize} mm needs reaming, because a drill can cut that much oversize. Spot it, drill it with a library drill 0.1 to 0.4 mm smaller than the reamer, then ream it with the reamer that matches its diameter: op ream, cycle G85, depth 0, peck 0. If the library has no matching reamer or pre-drill, leave the hole out and warn.
```

### The job

The user message is `Plan this job:` followed by JSON with five parts:

| Field | Contents |
|---|---|
| `material` | The material's name, for example "6061-T6 aluminum". Speeds and feeds are deliberately left out. |
| `holes` | Each hole's ID, X, Y, diameter, depth, through or blind, and top height |
| `skipped` | Holes the app found but can't drill, with the reason, so Claude can mention them |
| `tools` | The tools that matter for this part (below), each with its full library entry: ID, tool number, type, diameter, point angle, flute length, description, and for a tap its pitch and tap drill |
| `machine` | The machine's limits and settings from `data/machine.json`, including the peck ratio |

**Only the relevant tools are sent** (`offered_tools()`): the spot drill; each drill, reamer or tap that fits a hole; the pre-drills for those reamers and the tap drills for those taps; and, for a hole no drill fits, the nearest drill on either side so Claude can say what's close. The library has 58 tools; the demo plate's prompt gets 11. The schema's list of allowed tool IDs is the same short list. Validation still checks against the whole library, so trimming the prompt can't let a bad tool through. Sending the whole library would make the prompt about twice as long.

**Hole heights are in program coordinates**, where Z0 is the top of the part, matching the machine settings Claude also sees. An earlier version sent raw CAD coordinates, and Claude correctly warned that "every depth will be off by 25 mm" ([lesson 1](#what-real-parts-taught-us)).

### The drawing

When a drawing is uploaded, the PDF goes in the same user message, as a document block ahead of the job text. The API gives Claude each page both as an image and as any text it contains. That matters for drawings like NIST's, where the dimensions are vector lines with no text layer: Claude reads them from the page image.

**Claude only transcribes.** It gives each callout's numbers in the drawing's own units, and for a thread its pitch (M3x0.5 → 0.5) or threads per inch (1/4-20 → 20). Code converts them to mm and applies the reaming and tapping rules. A model converting .003" to mm in its head is one more place for a slip, and the rule is a shop rule that belongs in code, where it can be tested and changed.

### The reply

The reply must match a JSON schema (`plan_schema()`), using the API's **structured outputs**. The API guarantees valid JSON in exactly this shape, so there's no parsing guesswork.

| Field | Type | Constraint |
|---|---|---|
| `operations[].op` | text | `spot_drill`, `drill`, `ream` or `tap` |
| `operations[].tool_id` | text | **one of the offered tool IDs** |
| `operations[].hole_ids` | list of integers | |
| `operations[].depth` | number | spot depth in mm, or 0 for the others |
| `operations[].cycle` | text | `G81`, `G83`, `G84` or `G85` |
| `operations[].peck` | number | peck depth in mm, or 0 |
| `operations[].reason` | text | one sentence |
| `warnings` | list of text | anything the programmer should know |
| `drawing[]` | list | one entry per hole callout: `hole_ids`, `callout` as written, `units` (`in` or `mm`), `diameter`, `plus`, `minus`, and `thread` (0 for a plain hole, otherwise the pitch in mm or the threads per inch). Empty with no drawing. |

Every field is required and no extra fields are allowed.

### A real response

From a live run on the mixed test plate (5 operations; two shown):

```json
{
  "op": "spot_drill", "tool_id": "SPOT-90-10", "hole_ids": [1, 2], "depth": 3.0, "cycle": "G81", "peck": 0,
  "reason": "A 3.0 mm deep 90° spot leaves a 6.0 mm cone, just under the 6.8 mm drill, so the drill starts on location without chipping the edge."
}
{
  "op": "drill", "tool_id": "DRL-6.8", "hole_ids": [1, 2], "depth": 0, "cycle": "G83", "peck": 6.8,
  "reason": "The 25 mm through holes are about 3.7 x diameter, so peck drilling at 1 x D clears the aluminium chips before they pack the flutes."
}
```

Warnings Claude has produced:

- *"Hole 1 (7.1 mm through, 10 mm deep at X20 Y20) was not drilled or spotted because the library has no 7.1 mm drill. It needs a 7.1 mm reamer after a pilot drill, a boring bar, or helical interpolation with an end mill. Confirm the intended size, since 7.1 mm is a non-standard diameter."* It did what the prompt asked, and added a useful question about the size.
- *"Holes 1 and 2 (6.8 mm) and holes 3 and 4 (5.0 mm) match M8 and M6 tap drill sizes; if these holes are meant to be threaded, tapping is not included in this plan and needs to be added."* This came before the prompt mentioned threads at all.
- *"Holes 1 and 2 are through holes; confirm that the 1.0 mm breakthrough plus the drill point clear any fixture or parallels underneath."*

Warnings are the least predictable part of the output: the same part can get different warnings, or none, on different runs. The operations vary much less ([Plans are stable](#how-well-it-works)).

### Request settings

| Setting | Value | Why |
|---|---|---|
| Model | `claude-opus-5-5` | The current default Claude model, chosen for quality. The eval suggests Sonnet 5.5 may do as well for less. |
| Effort | `medium` | Enough reasoning for a small, well-defined planning task without long waits |
| Output format | JSON schema (structured outputs) | A fixed, machine-checkable reply |
| Max tokens | 16,000 | Room for reasoning: in the eval, no Opus reply passed about 8,500 output tokens |
| Refusal fallback | `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`) | If a safety classifier declines the request, the API retries it on a fallback model instead of failing |
| Tools, streaming, history | None | One self-contained request and reply. A retry adds the rejected plan and the errors to the same conversation. |
| Network retries | SDK default (2) | The Anthropic SDK retries rate limits and server errors |

---

## How the output is kept honest

Each layer catches a different kind of mistake:

```mermaid
flowchart LR
    reply["Claude's reply"] --> schema["Schema, enforced by the API<br/>fixed shape, real tools only"]
    schema --> stopcheck{"Stop reason<br/>= end_turn?"}
    stopcheck -->|"refused or cut off"| err1["Error, no plan"]
    stopcheck --> validate{"validate()"}
    validate -->|"bad machining choice"| retry["One retry with<br/>the exact errors"]
    retry --> validate
    validate -->|"still wrong"| err2["Errors shown,<br/>no G-code"]
    validate --> gcode["G-code written<br/>by plain code"]
    gcode --> checks{"Six safety checks"}
    checks -->|"unsafe program"| blocked["Download blocked"]
    checks --> ok["Download allowed"]
```

| Possible mistake | What catches it |
|---|---|
| Malformed reply, missing fields, made-up fields | The schema: the API won't return them |
| A tool that doesn't exist | The schema: tool IDs are limited to the offered tools; validation checks the whole library |
| A refusal or a reply cut off at the token limit | The stop-reason check: raises an error, so no partial plan is used |
| Wrong drill size for a hole | Validation, within 0.02 mm |
| A hole drilled before it's spotted, drilled twice, or forgotten | Validation |
| No peck drilling on a deep hole, or a peck depth of 0 | Validation |
| A drill too short for the hole | Validation, against the tool's flute length |
| A spot depth of 0, or a spot cone much wider than the hole | Validation |
| A hole under a counterbore drilled before the counterbore | Validation: its R plane (the height where the drill switches from rapid to feed) would be inside solid metal |
| A drawing callout put on the wrong hole | Validation: its size must match the model's hole, a `4X` callout can't list five holes, and a hole can't have two callouts |
| A tight-tolerance hole drilled to size, reamed without a proper pre-drill, reamed twice or with the wrong reamer | Validation, using the tolerance converted by code |
| A tapped hole drilled with the wrong drill, tapped before it's drilled, tapped twice, or with a tap that doesn't match the drawing's thread | Validation, against the tap's own tap drill and the thread converted by code |
| A drawing thread left untapped when the library has the tap | Validation |
| A tap fed out of step with the spindle | Can't happen: the feed is computed as rpm × pitch |
| A plausible plan that still produces an unsafe program, including bugs in the G-code writer | The six safety checks on the final G-code |
| Wrong speeds or feeds | Can't happen: Claude never provides them |

When validation rejects a plan, the retry message lists the exact errors, for example:

```text
The plan was rejected:
- operation 3 (drill DRL-5.0): hole 4 is 4.0xD deep and needs G83 peck drilling
Return a corrected plan.
```

Only one retry is allowed. A model that fails twice on the same job is more likely misunderstanding the input than making a slip, so the errors go to a person instead of looping.

---

## Revising a plan

The machinist can type a change ("do the deep holes first", "peck every hole 2 mm at a time") and get a new, checked program. `revise_plan()` reuses the same prompt, schema, validation and retry as the first plan, with three differences:

- **The message** contains the drawing (if any), the job, the current plan and the machinist's request.
- **The schema** adds a required `summary`: one or two sentences to the machinist saying what changed, or why it couldn't.
- **The system prompt** gets an extra paragraph separating the rules the code enforces from the defaults the machinist may override:

```text
Revising a plan: you also get the current plan and a change the machinist asked for. Make that change and keep the rest of the plan as it is.
These rules are checked by code after you answer, so keep them even if asked not to: every drilled hole is spotted first (a hole at the floor of a wider hole already drilled counts as spotted by that drill's point); each drill matches its hole's diameter; tools come from the library; a hole deeper than {peck_ratio} x its diameter uses G83 with a peck above 0; the drill's flutes reach the full depth; every hole the library has a drill for is drilled; a tapped hole is drilled with its tap's tap_drill, then tapped with G84 by a tap matching the drawing's thread; a hole the drawing threads is tapped when the library has the tap; a hole the drawing tolerances tighter than {drill_oversize} mm is drilled 0.1 to 0.4 mm under and then reamed with G85; the drawing entries match the holes' diameters; a hole that starts at the floor of a wider hole deeper than the R clearance (under a counterbore) is spotted and drilled after the wider one.
Everything else above is a default the machinist may override: peck size, pecking shallow holes too, spot depth, the order of operations and holes, and how operations are grouped. Do what they ask there.
If the request conflicts with a checked rule, keep the rule, get as close to the request as the rules allow, and say so in summary.
summary: one or two sentences to the machinist saying what you changed, or why you couldn't.
```

The revised plan is never trusted more than the first one. It goes through the validator, the G-code writer and the safety checks, and the holes are measured again from the uploaded STEP file rather than taken from the browser.

The eval doesn't cover revisions yet, so these are single live runs on the mixed plate, not repeated. Each started from that session's plan, so the starting cycle times differ:

| Request | Time | What Claude did | Cycle time |
|---|---|---|---|
| "Do the deep holes first" | 8.7 s | Moved the deep 5 mm hole ahead of the shallow one, keeping the same drill loaded so no tool change was added | 0:35 → 0:35 |
| "Skip spot drilling to save time" | 6.8 s | Declined: every drilled hole must be spotted first. Suggested merging the two spot operations instead, and noted the chamfer that would leave | unchanged |
| "Peck every hole, 2 mm at a time" | 6.2 s | Switched every drill operation to G83 with 2 mm pecks, and merged the two 5 mm operations now that they match | 0:35 → 0:40 |
| "Tap the 5 mm holes M6 and the 6.8 mm holes M8." | 10.5 s | Added an M6 and an M8 tap operation, noted the existing drills are already the tap drills, and warned that blind hole 3 is only 5 mm deep, so it gets just 2–3 full threads | 0:32 → 0:55 |

Tokens were recorded only for the tap request: 4,377 in, 1,097 out, about $0.04. A revision's prompt is the first plan's prompt plus the current plan, so the others should cost about the same.

---

## How well it works

Single live runs can't tell a reliable planner from a lucky one, so `scripts/eval.py` plans a fixed set of parts several times per model and scores every run:

- **Cases:** 7 parts. Four of the five generated test parts (all but the side-hole part), and NIST FTC-07, FTC-08 and FTC-10 with their 4–5 page drawings.
- **Scoring:** the app's own code. Was the first attempt valid, was the plan valid after the retry, did the G-code pass the six safety checks, and is the plan the same from run to run.
- **Drawing reading:** callouts are compared with answers written by hand from the drawings (`fixtures/eval/*.truth.json`), by hole group (size, tolerance, thread), so one `4X` callout and two `2X` callouts for the same holes score the same. A run gets credit for each called-out hole it found, and is charged for each hole it called out that the answer doesn't have.
- **Cost and time:** from the token counts at list prices, and wall-clock time per plan, retries included.
- **Guard rails:** `--estimate` prints the cost without calling the API, and `--max-cost` (default $5) stops a sweep once it's spent. Haiku 5.5 has no server-side fallback, so the eval leaves `fallbacks` off for it.

First sweep, 2026-11-08: 3 runs per part on each model, medium effort, 63 plans, $3.29 in total.

| Model | Runs | Valid on the 1st try | Valid after the retry | Passed the 6 checks | Called-out holes found | Holes wrongly called out, per run | Same plan each run | $ per plan | Median time | Slowest |
|---|---|---|---|---|---|---|---|---|---|---|
| Opus 5.5 | 21 | 76% | 100% | 100% | 100% | 0.0 | 90% | $0.108 | 11 s | 81 s |
| Sonnet 5.5 | 21 | 67% | 100% | 100% | 100% | 0.0 | 100% | $0.044 | 6 s | 44 s |
| Haiku 5.5 | 21 | 67% | 100% | 100% | 89% | 0.3 | 90% | $0.004 | 11 s | 67 s |

The raw results are committed. Rebuild the table, or one row per part, without calling the API:

```bash
uv run python scripts/eval.py --summarize evals/results/2026-11-08T1641.jsonl --by-case
```

What it showed:

1. **The retry earns its place.** Every plan was valid after at most one retry, and every program passed the six checks. But a quarter to a third of first attempts were rejected. FTC-07 and FTC-08 had each passed first time in a hand run; over three runs each, Opus did that once on each. The results file doesn't say what the rejected attempts got wrong; the harness now records it (`first_errors`) for the next sweep.
2. **Passing every check doesn't mean the drawing was read right.** Opus and Sonnet found every called-out hole on all three drawings in every run, and called out nothing extra. Haiku missed some. On FTC-10 it left out the Ø3.5 G6 fits in two runs and read them as having no tolerance in the third, and skipped some Ø6, Ø3.5 ±0.2 and counterbore callouts. On FTC-07 it missed one of the Ø.250 +.003 holes that must be reamed, and reamed it anyway only because it assumed so in a warning. All of those plans passed validation and the safety checks, as they should: validation can only check the callouts it's given, and a missed fit makes a hole look like plain drilling. A missed fit is a hole drilled when it should be reamed, a scrapped part that every check passes. Only the comparison with the hand-written answers catches it.
3. **Sonnet 5.5 scored the same as Opus at 41% of the cost and about half the time.** FTC-10 with its drawing took 34 s on Sonnet against 81 s on Opus. The scores don't cover warnings, though: on FTC-07 and FTC-10, Opus warned that a drill leaves a pointed floor where the counterbore needs a flat one in 6 of 6 runs, Sonnet in 1 of 6. Haiku costs a twenty-fifth as much as Opus but isn't faster, and it misreads drawings.
4. **Plans are stable.** Where an Opus plan differed between runs, it only grouped the spot drills differently.
5. **The drawing is most of the cost.** A plan that passed first time used about 3,000–4,000 input tokens for a part alone, and 12,000–16,000 for a NIST part with its 4–5 page drawing. A retry sends it all again.

What it means: Sonnet 5.5 is the likely default, but 3 runs per part can only show large differences, and the warnings it drops would need scoring first. So a 5-run sweep of Opus and Sonnet, with expected warnings in the answers, comes before switching. The model wasn't changed in the same change as the harness, so this sweep stays the baseline.

---

## What real parts taught us

Running against the real model, on generated parts and NIST test parts, found six things the fake-client tests couldn't. The parts themselves, and what the hole finder got wrong on them, are in [REAL-PARTS.md](REAL-PARTS.md).

1. **Contradictory input caused a false warning.** Hole heights were sent in CAD coordinates while the machine settings said Z0 is the top of the part. Claude flagged it. The fix was to send hole heights in program coordinates; a test now checks this.
2. **Claude's plans exposed a G-code inefficiency.** Claude split the spot drill into two operations with different depths, and the 5 mm drill into G81 and G83 operations. Both splits are correct, but the generator did a full tool change and trip home between operations using the same tool. The generator now keeps the tool loaded.
3. **Claude treated defaults as rules.** In the first version of the revision prompt, "peck every hole, 2 mm at a time" was refused: Claude read the planning guidance ("peck about 1× the diameter", "use G83 past 3×D") as hard rules. The validator doesn't enforce either. The prompt now lists exactly what the code checks and names the rest as overridable defaults, and the same request is applied.
4. **Claude's correct plan exposed a gap in the checks.** On NIST FTC-07, Claude cut each counterbore before the hole beneath it, because the smaller hole's R plane is otherwise inside solid metal. The order was right, but nothing in the code required it: a program in the wrong order passed all six safety checks. The validator now enforces it ([A gap in the safety net](REAL-PARTS.md#a-gap-in-the-safety-net)).
5. **The validator was stricter than good practice.** On another FTC-07 run, Claude didn't spot drill the holes under the counterbores, and validation rejected the plan. Claude was right: the counterbore drill's point leaves a centered cone that guides the next drill like a spot. The validator now accepts it, and still rejects drilling that hole before the counterbore.
6. **A right outcome can come with a wrong reason.** On FTC-10 Claude left out three Ø3.5 ±0.2 holes, saying the #29 drill (3.454 mm) can't hold the tolerance. It can: 3.454 mm is inside 3.3–3.7 mm. What actually rules it out is the code's rule that a drill must match the model's hole within 0.02 mm. The reason text isn't validated, so it's worth reading with that in mind.

---

## Failure handling

| What happens | What the user sees |
|---|---|
| Plan rejected twice | The plan, the validation errors, and no G-code |
| Claude declines the request, or the reply is cut off | "The planner failed: …" (HTTP 502) |
| API or network error that survives the SDK's retries | "The planner failed: …" (HTTP 502) |
| No API key configured | "Claude API credentials aren't set up on the server" (HTTP 503) |
| No drillable holes in the part | No API call at all; a message says nothing was found |

---

## Testing without the API

The automated tests never call the real API. A `FakeClient` in `tests/test_planner.py` stands in for the Anthropic client and returns canned plans. The tests check:

- every error the validator can return, each triggered by a deliberately broken plan, including the drawing and reaming errors (`tests/test_drawing.py`) and the tapping errors (`tests/test_tapping.py`)
- that a drawing reaches Claude as a PDF document block ahead of the job, on plans and revisions
- that Claude is offered only the relevant tools, and the schema allows only those
- that a rejected plan is retried once with the errors, and that the errors are returned after a second failure
- that a refusal raises an error instead of returning a plan
- that hole heights reach the prompt in program coordinates
- that a revision sends the current plan and the request, requires a summary, and is retried and rejected like any plan when it breaks a rule
- the full API flow (`tests/test_api.py`): a good plan produces G-code, a bad plan produces errors, and refusals and missing credentials give the right error codes
- the eval's scoring (`tests/test_eval.py`)

---

## What's next

- **Switch to Sonnet 5.5** if a 5-run sweep confirms the first one, and try low effort on both.
- **Cache the system prompt and the drawing**, and measure the saving with the eval: each plan change and retry sends the whole drawing again.
- **Compare against a rules-only planner.** For holes alone, fixed rules can produce most plans. Running both through the eval would show exactly where the model adds value.
- **Add revisions to the eval:** requests with an expected outcome, applied or declined.
- **Log usage per job:** tokens, time, attempts and validation errors, so cost and failure rates are visible over time.
- **Learn from edits.** In a plant, the changes programmers make to generated plans are the best evaluation data for the prompt and the validator.

---

## How I used AI to build it

Hole-CAM was built over a weekend with Claude Code as a pair programmer, one stage at a time, each with tests before moving on. The same rule applied to the AI writing the code as to the AI in the app: its output was checked. Tests and live runs caught real mistakes along the way, including a wrong peck-drilling call in a hand-written plan, a G-code tool-change inefficiency, the coordinate mismatch above, and a few overclaims in the README that didn't match what had actually been tested.
