# How AI is used in Hole-CAM

Hole-CAM makes **one call to Claude per plan**, plus at most one retry. That call turns a list of holes into a drilling plan: which operations, which tools, which drilling cycle, in what order, and why. A machinist can then ask for changes in plain English, and each change is one more call ([Revising a plan](#revising-a-plan)). Everything else in the app is ordinary code. Claude's plan is treated as a proposal: its format is fixed by a schema, plain code validates it, and the final program is checked for crashes before anyone can download it.

All the code described here is in `src/hole_cam/planner.py`.

---

## Where the AI sits

```mermaid
sequenceDiagram
    participant API as api.py
    participant P as planner.py
    participant C as Claude (Opus 5.5)
    participant V as validate()

    API->>P: plan_job(holes, material, tools, machine)
    P->>C: system prompt + job JSON, with the plan's JSON schema
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

---

## What Claude is asked to decide

| Claude decides | Plain code decides |
|---|---|
| Which operations to run (spot drill, drill) and in what order | Finding the holes and measuring them |
| Which drill goes with each hole | Spindle speeds and feed rates |
| Plain drilling (G81) or peck drilling (G83), and the peck depth | Every coordinate and number in the G-code |
| How deep to spot each hole | Whether the plan is acceptable |
| What to warn the programmer about | Whether the finished program is safe to run |
| A one-sentence reason for every operation | |

The split follows one rule: **anything that can break a tool or crash the machine if it's wrong is computed, not generated.** Claude handles choices that need judgment and explanation. Those choices are then checked by code that doesn't need judgment.

---

## What Claude receives

### The system prompt

This is sent unchanged with every request. The `{peck_ratio}` is filled in from `data/machine.json`, which currently sets it to 3.

```text
You are a CNC programmer planning hole-making on a 3-axis vertical mill. One setup, parts are drilled from the top.

You get the holes found in the CAD model, the material, the tool library and the machine limits. Return a plan:
- Spot drill every hole you will drill, before drilling it. For a spot drill operation, set depth (mm below the hole top) so the spot cone is a little smaller than the drill. Use cycle G81 and peck 0.
- Drill each hole with the library drill whose diameter matches the hole. If no drill matches, do not drill that hole; add a warning naming the hole and what it would need (a reamer, a boring bar, helical interpolation).
- Use G83 peck drilling when the hole is deeper than {peck_ratio} x its diameter. Peck about 1 x the drill diameter. Otherwise use G81 with peck 0. For drill operations, set depth to 0 (the depth comes from the model).
- Group holes that share a tool and cycle into one operation to keep tool changes down.
- Only use tool IDs from the library. Give each operation a one-sentence reason a machinist would agree with.
Speeds and feeds are calculated elsewhere; do not include them.
```

### The job

The user message is `Plan this job:` followed by JSON with five parts:

| Field | Contents |
|---|---|
| `material` | The material's name, for example "6061-T6 aluminum". Speeds and feeds are deliberately left out. |
| `holes` | Each hole's ID, X, Y, diameter, depth, through or blind, and top height |
| `skipped` | Holes the app found but can't drill, with the reason, so Claude can mention them |
| `tools` | The tools that matter for this part: spot drills, each drill that fits a hole, and for a hole no drill fits, the nearest drill on either side. ID, type, diameter, point angle, flute length. |
| `machine` | The machine's limits and settings, including the peck ratio |

Hole heights are given in the program's coordinates, where Z0 is the top of the part. That matches the machine settings Claude also sees. An earlier version sent raw CAD coordinates instead, and Claude correctly warned that "every depth will be off by 25 mm". The G-code was actually fine, but the input contradicted itself (see [What the live runs taught us](#what-the-live-runs-taught-us)).

Sending only the relevant tools keeps the prompt short as the library grows. The library has 36 drills, but the demo plate's prompt uses 3 tools and 1,893 input tokens. The schema's list of allowed tool IDs uses the same short list. Validation still checks against the whole library, so trimming the prompt can't let a bad tool choice through.

---

## What Claude returns

The reply must match a JSON schema (`plan_schema()`), using the API's **structured outputs** feature. The API guarantees the reply is valid JSON in exactly this shape, so there's no parsing guesswork.

| Field | Type | Constraint |
|---|---|---|
| `operations[].op` | text | must be `spot_drill` or `drill` |
| `operations[].tool_id` | text | **must be one of the tool IDs in the library** |
| `operations[].hole_ids` | list of integers | |
| `operations[].depth` | number | spot depth in mm, or 0 for drills |
| `operations[].cycle` | text | must be `G81` or `G83` |
| `operations[].peck` | number | peck depth in mm, or 0 |
| `operations[].reason` | text | one sentence |
| `warnings` | list of text | anything the programmer should know |

Every field is required, and no extra fields are allowed. The tool list in the schema is built from the live tool library on each request, so Claude can't name a drill the shop doesn't have.

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

Warnings Claude has produced in live runs:

- *"Hole 1 (7.1 mm through, 10 mm deep at X20 Y20) was not drilled or spotted because the library has no 7.1 mm drill. It needs a 7.1 mm reamer after a pilot drill, a boring bar, or helical interpolation with an end mill. Confirm the intended size, since 7.1 mm is a non-standard diameter."* This is from the odd-size test part. It did what the prompt asked, and added a useful question about the size.
- *"Holes 1 and 2 (6.8 mm) and holes 3 and 4 (5.0 mm) match M8 and M6 tap drill sizes; if these holes are meant to be threaded, tapping is not included in this plan and needs to be added."* Nothing in the prompt mentions threads.
- *"Holes 1 and 2 are through holes; confirm that the 1.0 mm breakthrough plus the drill point clear any fixture or parallels underneath."*

Warnings are the least predictable part of the output: the same part can get different warnings, or none, on different runs. The operations have been very similar across runs. Small details vary, such as a spot depth of 2.25 or 2.2 mm, or which of two operations with the same tool comes first.

---

## Request settings

| Setting | Value | Why |
|---|---|---|
| Model | `claude-opus-5-5` | The current default Claude model, chosen for quality. |
| Effort | `medium` | Enough reasoning for a small, well-defined planning task without long waits. |
| Output format | JSON schema (structured outputs) | A fixed, machine-checkable reply format |
| Max tokens | 16,000 | Plenty of room. Real plans use under 1,200 output tokens. |
| Refusal fallback | `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`) | If a safety classifier declines the request, the API retries it on a fallback model instead of failing |
| Tools, streaming, conversation history | None | One self-contained request and reply. A retry adds the rejected plan and the errors to the same conversation. |
| Network retries | SDK default (2) | The Anthropic SDK automatically retries rate limits and server errors |

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
| A drill that doesn't exist | The schema: tool IDs are limited to the library |
| A refusal or a reply cut off at the token limit | The stop reason check: raises an error, so no partial plan is used |
| Wrong drill size for a hole | Validation, within 0.02 mm |
| A hole drilled before it's spotted, drilled twice, or forgotten | Validation |
| No peck drilling on a deep hole, or a peck depth of 0 | Validation |
| A drill too short for the hole | Validation, against the tool's flute length |
| A spot cone much wider than the hole | Validation |
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

## Measured behavior

Live runs on 6061 aluminum, measured on 2026-11-07:

| Part | Time | Input tokens | Output tokens | Approx. cost | Attempts |
|---|---|---|---|---|---|
| Mixed plate (4 holes) | 11.3 s | 2,710 | 1,171 | $0.034 | 1, valid |
| Mixed plate, after the coordinate fix | 8.0 s | 2,710 | 847 | $0.028 | 1, valid |
| Odd-size plate (2 holes) | 5.9 s | 2,560 | 544 | $0.021 | 1, valid |
| NIST FTC-07 (16 holes, counterbores) | 38.6 s | 6,814 | 3,796 | $0.103 | 1, valid |
| NIST FTC-08 (20 holes) | 14.9 s | 6,715 | 1,572 | $0.058 | 1, valid |
| NIST FTC-09 (29 holes) | 21.7 s | 7,390 | 2,235 | $0.074 | 1, valid |
| NIST FTC-10 (28 holes) | 55.7 s | 19,597 | 5,776 | $0.194 | 2, valid on the retry |

Costs are estimated from the token counts at Opus 5.5 list prices ($4 per million input tokens, $20 per million output). Output tokens include the model's reasoning. FTC-10 is the only live plan so far that failed validation; the retry with the errors listed fixed it.

The NIST runs came after the tool library grew from 12 to 36 drills, which is why input is about 7,000 tokens: at the time, the whole library went into every prompt. Since then Claude gets only the relevant tools:

| Part | Time | Input tokens | Output tokens | Approx. cost | Attempts |
|---|---|---|---|---|---|
| Mixed plate, relevant tools only | 12.7 s | 1,893 | 1,258 | $0.033 | 1, valid |
| NIST FTC-07, relevant tools only | 33.1 s | 3,303 | 3,320 | $0.080 | 1, valid |

What Claude did on those real parts is in [REAL-PARTS.md](REAL-PARTS.md).

---

## Revising a plan

`revise_plan()` reuses the same prompt, schema, validation and retry as the first plan, with three differences:
- **The message** contains the job, the current plan and the machinist's request.
- **The schema** adds a required `summary` field: one or two sentences to the machinist saying what changed, or why it couldn't.
- **The system prompt** gets an extra paragraph separating the rules the code enforces from the defaults the machinist may override:

```text
Revising a plan: you also get the current plan and a change the machinist asked for. Make that change and keep the rest of the plan as it is.
These rules are checked by code after you answer, so keep them even if asked not to: every drilled hole is spotted first (a hole at the floor of a wider hole already drilled counts as spotted by that drill's point); each drill matches its hole's diameter; tools come from the library; a hole deeper than 3.0 x its diameter uses G83 with a peck above 0; the drill's flutes reach the full depth; every hole the library has a drill for is drilled; a hole that starts at the floor of a wider hole deeper than the R clearance (under a counterbore) is spotted and drilled after the wider one.
Everything else above is a default the machinist may override: peck size, pecking shallow holes too, spot depth, the order of operations and holes, and how operations are grouped. Do what they ask there.
If the request conflicts with a checked rule, keep the rule, get as close to the request as the rules allow, and say so in summary.
```

The revised plan is never trusted more than the first one. It goes through the validator, the G-code writer and the safety checks. The holes are measured again from the uploaded STEP file, not taken from the browser.

Live results on the mixed plate (2026-11-07):

| Request | Time | What Claude did | Cycle time |
|---|---|---|---|
| "Do the deep holes first" | 8.7 s | Moved the deep 5 mm hole ahead of the shallow one, keeping the same drill loaded so no tool change was added | 0:35 → 0:35 |
| "Skip spot drilling to save time" | 6.8 s | Declined: every drilled hole must be spotted first. Suggested merging the two spot operations instead, and noted the chamfer that would leave | unchanged |
| "Peck every hole, 2 mm at a time" | 6.2 s | Switched every drill operation to G83 with 2 mm pecks, and merged the two 5 mm operations now that they match | 0:35 → 0:40 |

Token counts weren't recorded for these runs. The prompt is the first plan's prompt plus the current plan, so cost should be close to the first plan's, about $0.03 per request.

---

## What the live runs taught us

Running against the real model found five problems the fake-client tests couldn't:

1. **Contradictory input caused a false warning.** Hole heights were sent in CAD coordinates while the machine settings said Z0 is the top of the part. Claude flagged it. The fix was to send hole heights in program coordinates. A test now checks this, and the warning is gone.
2. **Claude's plans exposed a G-code inefficiency.** Claude split the spot drill into two operations with different depths, and the 5 mm drill into G81 and G83 operations. Both splits are correct, but the generator did a full tool change and trip home between operations using the same tool. The generator now keeps the tool loaded.

3. **Claude treated defaults as rules.** In the first version of the revision prompt, "peck every hole, 2 mm at a time" was refused: Claude read the planning guidance ("peck about 1× the diameter", "use G83 past 3×D") as hard rules. The validator doesn't enforce either. The prompt now lists exactly what the code checks and names the rest as overridable defaults, and the same request is applied.

4. **Claude's correct plan exposed a gap in the checks.** On NIST FTC-07, Claude cut each counterbore before the hole beneath it, because the smaller hole's R plane is otherwise inside solid metal. The order was right, but nothing in the code required it: a program in the wrong order passed all six safety checks. The validator now enforces it ([REAL-PARTS.md](REAL-PARTS.md#a-gap-in-the-safety-net)).

5. **The validator was stricter than good practice.** On another FTC-07 run, Claude didn't spot drill the holes under the counterbores, and validation rejected the plan. Claude was right: the counterbore drill's point leaves a centered cone that guides the next drill like a spot. The validator now accepts it, and still rejects drilling that hole before the counterbore.

All five are good examples of why a live model run belongs in testing, not just mocks.

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

## Testing the AI parts without the API

The automated tests never call the real API. A `FakeClient` in `tests/test_planner.py` stands in for the Anthropic client and returns canned plans. The tests check:

- every validation rule, with one deliberately broken plan per rule
- that a rejected plan is retried once, with the errors in the retry message
- giving up after one retry, and returning the errors
- that a refusal raises an error instead of returning a plan
- that hole heights reach the prompt in program coordinates
- that a revision sends the current plan and the request, requires a summary, and is retried and rejected like any plan when it breaks a rule
- the full API flow (`tests/test_api.py`): a good plan produces G-code, a bad plan produces errors, and refusals and missing credentials give the right error codes.

Live behavior is checked separately, by running real parts as above.

---

## What could come next

- **Compare against a rules-only planner.** For holes alone, fixed rules can produce most plans. Comparing the two on the same parts would show exactly where the model adds value.
- **Try cheaper settings.** Validation gives an objective pass/fail, so running the test parts with Sonnet 5.5 or a lower effort setting, and counting rejections, is a cheap experiment.
- **Log usage per job:** tokens, time, attempts and validation errors, so cost and failure rates are visible over time.
- **Learn from edits.** In a plant, the changes programmers make to generated plans are the best evaluation data for the prompt and the validator.

---

## AI in building the app

Hole-CAM was built over a weekend with Claude Code as a pair programmer, one stage at a time, each with tests before moving on. The same rule applied to the AI writing the code as to the AI in the app: its output was checked. Tests and live runs caught real mistakes along the way, including:
- a wrong peck-drilling call in a hand-written plan
- a G-code tool-change inefficiency
- the coordinate mismatch above
- a few overclaims in the README that didn't match what had actually been tested.
