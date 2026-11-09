# Hole-CAM on real parts

Until this test, every part Hole-CAM had seen was one I generated, built the way the code expects. Real parts are the first thing a customer brings. So I ran the app on 11 real CAD parts from NIST, checked the results against each part's engineering drawing, and fixed what broke.

![FTC-07, an open box from NIST, turned open side up, with the spot drill working on a counterbored hole on a boss inside the box](real-part.png)

---

## The parts

NIST publishes test parts for its model-based manufacturing project: STEP files exported from several CAD systems, each with a fully dimensioned drawing. NIST says they can be used without restriction. I used the 11 "geometry only" files from [NIST's download page](https://www.nist.gov/el/systems-integration-division-73400/mbe-pmi-validation-and-conformance-testing/download-free-cad).

They aren't friendly test cases:
- Most are **inch** parts, with letter and number drill sizes (for example .238" is a "B" drill, .213" is a "#3").
- Several are **modeled on their side or upside down**, so the face to drill isn't the top in the CAD file.
- They have **pockets with rounded corners, slots, keyholes and counterbores**: shapes with curved walls that aren't simple drilled holes.

---

## What broke

The first run turned up three bugs and two gaps. All five are now fixed and tested.

| Problem | What happened | Fix |
|---|---|---|
| **Rounded corners counted as holes** | FTC-08 has 20 holes on its drawing, but Hole-CAM found 70. The inside corners of pockets, the ends of slots and the lobes of keyholes are all curved walls, and the code accepted any inward-facing curved wall as a hole. | A hole must go all the way around (360°). FTC-08 now finds exactly the 20 holes on the drawing. |
| **Parts modeled on their side** | FTC-09 is a 3 mm plate standing on edge in its CAD file. All 29 of its holes were "not vertical", so none were found. | The part is turned so the side with the most drillable holes faces up. FTC-09 now finds 29 holes from its −Y side. |
| **Program zero in the wrong place** | The G-code assumes X0 Y0 is the part's front-left corner, but hole positions came straight from the CAD file's own origin. All 11 NIST parts extend into negative X, and most are modeled around their center. CTC-01 runs from X−590 to X+580 mm, so its program would have put holes up to 590 mm from where they belong. | The part is moved so its front-left corner is at X0 Y0, and the 3D view uses the same turned and moved part. |
| **No inch drills** | The tool library was metric only, so most holes on US parts had no drill. It was also missing 2.5 mm, the tap drill for M3, the most common metric thread. | Added a standard US set: fractional drills from 1/8" to 1/2" by 32nds, and the number and letter tap drills for common threads. I didn't add sizes just to match these parts. |
| **Drill size matching too loose** | With inch drills in the library, a 9/32" drill (7.144 mm) counted as a match for a 7.1 mm hole, because the match tolerance was 0.05 mm. | Tightened to 0.02 mm: enough for inch-to-mm rounding (every NIST hole matches its nominal drill within 0.01 mm), but no longer accepting a different size. |

Each fix has a test that fails on the old code: a pocket with rounded corners, a part centered on its origin, and a plate modeled on its side. FTC-07 and FTC-08 are now regression tests too, with expected holes taken from their drawings, not from the app's output (`fixtures/nist/`).

### A gap in the safety net

The live run on FTC-07 found something more serious. Its 9/32" through holes start at the floor of 7/16" counterbores, 7.62 mm below the surface. If the small holes are spotted or drilled before the counterbores are cut, the rapid move down to their R plane ends 5.6 mm inside solid metal.

Claude ordered it correctly on its own. But I generated the wrong order on purpose, and the program passed all six safety checks. The checker knows only one surface height per hole center, so it can't tell that the counterbore above hasn't been cut yet.

The validator now enforces the order, since the plan is where order is known. It applies only when the counterbore is deeper than the R-plane clearance. FTC-10's counterbores are 2 mm deep, so its R plane sits at the surface and any order is safe.

---

## Hole finding on all 11 parts

| Part | Setup side | Holes found | Drillable with the library | No drill in the library for |
|---|---|---|---|---|
| CTC-01 | +Z | 8 | 0 | Ø25, Ø35 mm (bores) |
| CTC-02 | +Z | 38 | 0 | Ø10.1, Ø12, Ø22 mm |
| CTC-03 | +Z | 10 | 4 | Ø5/8", Ø1.5", Ø2" |
| CTC-04 | −Z | 44 | 0 | Ø6.648, Ø12.1, Ø14 mm |
| CTC-05 | +Z | 24 | 4 | Ø3/4", Ø1.5" |
| FTC-06 | −Z | 12 | 4 | Ø.400", Ø9/16", Ø13/16" |
| FTC-07 | +Y | 16 | 16 | — |
| FTC-08 | +Z | 20 | 14 | Ø.238", Ø.242" |
| FTC-09 | −Y | 29 | 22 | Ø.221", Ø.234", Ø3/4" |
| FTC-10 | +Y | 28 | 16 | Ø2.46 (M3 threads, see below), Ø3.5 mm |
| FTC-11 | +Z | 1 | 0 | Ø32 mm |

FTC-08's Ø.228" holes gained a drill when the 5.8 mm reaming pre-drill was added; the live runs below were made before that.

I checked four parts against their drawings: CTC-01, FTC-07, FTC-08 and FTC-10. All four matched exactly. The rest weren't checked hole by hole.

Holes with no matching drill aren't errors. Claude names them in the plan's warnings with what they need: a reamer, a boring bar, or helical milling.

---

## Claude on real parts

I ran the full pipeline live on the four parts the library can mostly drill: plan, validate, G-code, safety checks and cycle time. All four programs passed all six safety checks.

| Part | Time | Tokens in / out | Cost | Holes drilled | Cycle time |
|---|---|---|---|---|---|
| FTC-07 | 39 s | 6,814 / 3,796 | $0.10 | 16 of 16 | 1:58 |
| FTC-08 | 15 s | 6,715 / 1,572 | $0.06 | 12 of 20 | 0:57 |
| FTC-09 | 22 s | 7,390 / 2,235 | $0.07 | 22 of 29 | 1:34 |
| FTC-10 | 56 s (2 attempts) | 19,597 / 5,776 | $0.19 | 16 of 28 | 1:17 |

What Claude got right without being told:
- **FTC-07:** recognized the counterbores from the coaxial holes. It cut them before the holes beneath, and warned that a drill leaves a cone-shaped floor where a screw head needs a flat seat.
- **FTC-08:** the 8 holes no library drill fits are Ø.228", .238" and .242", the sizes of #1, B and C drills, which the library doesn't have. Claude named a reamer, a boring bar or the right drill for each. (The library has since gained a 5.8 mm drill, which fits the Ø.228" holes. An earlier version of this page said these were the holes the drawing tolerances tightly. That was wrong: the drawing gives these holes +.005/−.001", which a drill can hold, and every other hole the same except the Ø.250 pair at +.006/−.001.)
- **FTC-09:** the plate is 3 mm thick, so it made the spot holes shallower to keep them from breaking through.
- **FTC-10:** it read the 2.46 mm holes as M3 threads modeled at their inner diameter, and named the 2.5 mm tap drill they'd need.

FTC-10 was the first live plan rejected by validation. The corrected plan passed on the retry. I didn't record which rule the first attempt broke.

The runs above sent Claude the whole 36-drill library, so input grew from about 2,700 tokens to 6,700–7,700 per plan. Claude now gets only the spot drills, the drills that fit a hole, and the nearest drill on each side of any hole no drill fits. Validation still checks against the whole library. FTC-07 then took 3,303 input tokens instead of 6,814, at $0.08, passing on the first attempt. The demo plate dropped to 1,893.

### Claude was right, the validator was wrong

One FTC-07 rerun was rejected: Claude hadn't spot drilled the 9/32" holes under the counterbores. That's correct machining. The 7/16" drill's point leaves a centered cone at the floor of each counterbore, and the cone guides the next drill just as a spot hole would. The validator now counts a hole at the floor of a wider drilled hole as spotted. It still rejects the plan when that hole is drilled before the wider one.

---

## Reading the drawings

A STEP file has the shape but not the tolerances. Hole-CAM now takes the drawing PDF as well: Claude reads the hole callouts, and code decides whether each hole can be drilled, must be reamed, or is a thread to tap (see [HOW-IT-WORKS.md](HOW-IT-WORKS.md#reading-the-drawing)). The NIST drawings are a hard test, because their dimensions are drawn as lines with no text to search: they have to be read off the page.

I read the drawings myself first, so there was an answer to check against:

| Part | Hole callouts in the drilling setup | Needs |
|---|---|---|
| FTC-07 | 8 × Ø.250 +.003 −.000 (or −.001), in four callouts | **Ream**: +.003" is 0.076 mm, under what a drill holds |
| | 4 × Ø.281 +.006 −.001 | Drill |
| | 4 × ⌴Ø.438 ±.010 counterbores | Drill (flat floor needs another tool) |
| FTC-08 | 20 holes, all +.005 −.001 (or +.006 −.001) | Drill: +.005" is 0.127 mm, which a drill holds |
| FTC-10 (mm) | 6 × M3x0.5-6g, in three `2X` callouts | **Tap** M3, drilled 2.5 mm |
| | 3 × Ø3.5 G6, one callout each | **Ream** (G6 is +0.012/+0.004), but the library has no 3.5 mm reamer |
| | 8 × Ø2.5 ±0.15, 4 × Ø6 ±0.15, 3 × Ø3.5 ±0.2, 4 × ⌴Ø10 ±0.25 counterbores | Drill |

**Live, on FTC-07** (one run, $0.17, 63 s): Claude returned all six callouts for the holes in this setup, with the right sizes and tolerances, including the counterbore symbol, and left out the four callouts for holes on the sides. Code marked the 8 Ø.250 holes for reaming. Claude's plan spots them, drills them 6.0 mm and reams them with the 1/4" reamer on `G85`. It passed validation on the first attempt, and the program passed all six safety checks.

**Live, on FTC-08** (2026-11-08, $0.12, 37 s): Claude returned nine callouts covering all 20 holes, every one +.005 −.001 except the Ø.250 pair at +.006, so code marked none for reaming. It drilled 14 holes, using the new 5.8 mm drill for the Ø.228" pair, and checked that 5.8 mm lands inside that tolerance. The other 6 holes it left out with the drill each would need. It passed validation first time and all six safety checks.

**Live, on FTC-10** (2026-11-08, $0.25, 95 s): the five-page drawing has its threads on page 4 only, as `2X M3x0.5-6g`. Claude read all 12 callouts for this setup and none for the side features, which matches the list above. It drilled the six M3 holes 2.5 mm, peck drilling because they're 12 mm deep, then tapped them on `G84`. Unprompted, it pointed out that "6g" is a class for external threads, and tapped them as standard internal M3. Code marked the three Ø3.5 G6 holes for reaming, and with no 3.5 mm reamer they were left out with a warning, as designed. First attempt valid; all six checks passed.

One thing it got wrong was a reason, not a result. It left out the Ø3.5 ±0.2 holes saying a #29 drill (3.454 mm) can't hold the tolerance, but 3.454 mm is inside 3.3–3.7 mm. The real reason is the code's rule that a drill must be within 0.02 mm of the model's hole.

What isn't verified: which of identical callouts went to which holes, such as the four Ø.250 callouts on FTC-07 or the three Ø3.5 G6 callouts on FTC-10. Each set has the same tolerance, so the plan is the same either way, but on a part where they differed this would matter.

---

## Proving the programs out on LinuxCNC

The safety checks read programs with this project's own G-code reader, so a misunderstanding shared by the writer and the reader would slip through. As a second opinion, every program below was run through LinuxCNC's own interpreter (`scripts/linuxcnc_check.py`, see [HOW-IT-WORKS.md](HOW-IT-WORKS.md#a-second-opinion-linuxcncs-interpreter)). That checks the program reads without error, its moves match ours exactly, and the holes it cuts match the plan.

| Program | Lines | Moves (LinuxCNC = ours) | Planned holes cut to size and depth | Result |
|---|---|---|---|---|
| Mixed plate, hand-checked plan | 43 | 67 | 4 of 4 | Pass |
| Mixed plate, live "tap the 5 mm holes M6 and the 6.8 mm holes M8" | 67 | 83 | 4 of 4, all tapped | Pass |
| Boss part | 28 | 15 | 2 of 2 | Pass |
| FTC-07 live, with drawing (reaming) | 102 | 159 | 16 of 16 | Pass |
| FTC-08 live, with drawing | 74 | 111 | 14 of 14 | Pass |
| FTC-10 live, with drawing (M3 tapping) | 108 | 289 | 22 of 22 | Pass |
| FTC-07, 08, 09, 10, the earlier live plans | 59–97 | 95–175 | 66 of 66 | Pass |

The first run failed every program, on a real portability bug: `G43 H1 Z25.` with no motion code. A Fanuc runs it as a rapid because `G00` is still in effect; LinuxCNC rejects it, and a control that starts up in feed mode would feed there instead. The generator now writes `G00 G43 H1 Z25.`. It also found tool comments with parentheses inside parentheses, `(DRILL 7/16 (11.112MM))`, which end the comment early on a Fanuc. Both are fixed, and the reference program was updated.

---

## What's still not handled

- **Counterbores** come out as two holes, one per diameter, and are drilled with a pointed drill. Claude warns about it, but a flat-bottom tool isn't modeled.
- **Tool reach.** On FTC-07 the holes are on a boss 117 mm down inside the box. Nothing checks that the tool and holder are long enough, or clear the walls. Claude raised this on FTC-08; the code doesn't check it.
- **Setups are limited to the six sides of the model's box.** Holes on angled faces are always skipped, and only one setup is planned.
- **Big parts are slow to read.** Hole finding takes up to 16 seconds on CTC-02, because the setup search tries both faces along the main axis.
- **A drill inside the drawing's tolerance but not the model's size** isn't allowed. On FTC-10, a #29 drill would make the Ø3.5 ±0.2 holes, but the validator wants a drill within 0.02 mm of the model, so they're left out. With a drawing, the rule could be "the drilled size lands inside the tolerance".
- **Threads without a drawing** are only tapped when the machinist asks. A 5 mm hole could be a plain hole or an M6 thread, and the model can't say which.
- **Precision holes without a drawing.** The tolerance isn't in the STEP geometry, so without the drawing a hole that needs reaming gets drilled if a drill matches. With the drawing, only holes the library has a reamer and pre-drill for can be reamed; others are left out with a warning.
