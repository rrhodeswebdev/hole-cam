"""Eval harness for the Claude planner. Results and design: docs/AI-IN-HOLE-CAM.md.

Runs the real planner on a fixed set of parts, several times per model and effort setting, scores each run with the
app's own validation and safety checks plus hand-written drawing answers, and prints a Markdown scorecard.

    uv run --env-file .env python scripts/eval.py [--settings opus-5-5/medium,sonnet-5-5/medium] [--runs 3]
    uv run python scripts/eval.py --estimate
    uv run python scripts/eval.py --summarize evals/results/<file>.jsonl
"""

import argparse
import collections
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import anthropic

from hole_cam.checks import check, context_for
from hole_cam.data import load_data
from hole_cam.gcode import generate
from hole_cam.holes import find_holes
from hole_cam.planner import drawing_callouts, plan_job

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "fixtures"
RESULTS = ROOT / "evals" / "results"
TOOLS, MATERIALS, MACHINE = load_data("tools.json"), load_data("materials.json"), load_data("machine.json")
MATERIAL = MATERIALS["6061"]

# $ per million tokens (input, output), first-party list prices.
# shortcut: Haiku 5.5 doubles past 100K input tokens and a fallback bills at the fallback model's rates; plans here
# stay under 25K input and fallbacks are flagged, so the requested model's base rate is used.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}
NO_FALLBACK = {"claude-haiku-5-5"}  # Haiku 5.5 has no server-side refusal fallback; sending one would fail the request
BASELINE = "opus-5-5/medium"

# case: (STEP file, drawing PDF or None, hand-checked reference plan or None, Opus cost per plan from the docs)
CASES = {
    "mixed": ("parts/mixed.step", None, "plans/mixed.json", 0.033),
    "boss": ("parts/boss.step", None, "plans/boss.json", 0.03),
    "odd_size": ("parts/odd_size.step", None, None, 0.021),
    "plate_4_through": ("parts/plate_4_through.step", None, None, 0.03),
    "ftc07": ("nist/nist_ftc_07_asme1_rd.stp", "nist/nist_ftc_07_asme1_rd.pdf", None, 0.17),
    "ftc08": ("nist/nist_ftc_08_asme1_rc.stp", "nist/nist_ftc_08_asme1_rc.pdf", None, 0.12),
    "ftc10": ("nist/nist_ftc_10_asme1_rb.stp", "nist/nist_ftc_10_asme1_rb.pdf", None, 0.25),
}


def parse_setting(s):
    """'sonnet-5-5/medium' -> ('claude-sonnet-5-5', 'medium')."""
    model, effort = s.split("/")
    model = model if model.startswith("claude-") else "claude-" + model
    if model not in PRICES:
        raise SystemExit(f"unknown model {model}; known: {', '.join(PRICES)}")
    return model, effort


class RecordingClient:
    """Stands in for anthropic.Anthropic() in plan_job(): runs each request on this setting's model and effort, and
    records usage. Same seam the FakeClient tests use, so the planner itself is unchanged."""

    def __init__(self, model, effort, inner=None):
        self.inner, self.model, self.effort, self.calls = inner or anthropic.Anthropic(), model, effort, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        kw = {**kw, "model": self.model, "output_config": {**kw["output_config"], "effort": self.effort}}
        if self.model in NO_FALLBACK:
            kw.pop("fallbacks", None)
            kw["betas"] = [b for b in kw.get("betas", []) if not b.startswith("server-side-fallback")]
        last = kw["messages"][-1]["content"]  # on a retry, the planner's message listing the rejected plan's errors
        retry_of = [line[2:] for line in last.splitlines() if line.startswith("- ")] if isinstance(last, str) else []
        start = time.monotonic()
        response = self.inner.beta.messages.create(**kw)
        u = response.usage
        price_in, price_out = PRICES[self.model]
        self.calls.append({
            "retry_of": retry_of,
            "seconds": time.monotonic() - start, "served_model": response.model,
            "fallback": any(i.type == "fallback_message" for i in getattr(u, "iterations", None) or []),
            "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
            "cost_usd": (u.input_tokens * price_in + u.output_tokens * price_out) / 1e6,
        })
        return response


def ops(plan):
    """The plan's operations in a comparable form. Order, spot depth and peck size vary between good plans."""
    return sorted([o["op"], o["tool_id"], sorted(o["hole_ids"]), o.get("cycle", "G81")] for o in plan["operations"])


def _same_group(a, b):
    return (abs(a["diameter_mm"] - b["diameter_mm"]) <= 0.01 and abs(a["plus_mm"] - b["plus_mm"]) <= 0.003
            and abs(a["minus_mm"] - b["minus_mm"]) <= 0.003 and abs(a["pitch_mm"] - b["pitch_mm"]) <= 0.001)


def score_drawing(plan, truth):
    """Score callouts by hole group (size, tolerance, thread), not by callout: one 8X callout and four 2X callouts for
    the same holes score the same. Returns {found, wanted, extra} in holes."""
    want = drawing_callouts({"drawing": truth}, MACHINE)
    covered, extra = [0] * len(want), 0
    for c in drawing_callouts(plan, MACHINE):
        i = next((i for i, w in enumerate(want) if _same_group(c, w)), None)
        if i is None:
            extra += len(c["hole_ids"])
        else:
            covered[i] += len(c["hole_ids"])
    found = sum(min(n, w["holes"]) for n, w in zip(covered, want))
    extra += sum(max(0, n - w["holes"]) for n, w in zip(covered, want))
    return {"found": found, "wanted": sum(w["holes"] for w in want), "extra": extra}


def score_run(case, found, result, calls, truth=None, reference=None):
    """One result line. result is plan_job()'s return, or {"exception": text} if it raised."""
    rec = {"case": case, "attempts": len(calls),
           **{k: sum(c[k] for c in calls) for k in ("input_tokens", "output_tokens", "cost_usd", "seconds")},
           "first_errors": calls[1]["retry_of"] if len(calls) > 1 else [],
           "served_model": calls[-1]["served_model"] if calls else None,
           "fallback": any(c["fallback"] for c in calls)}
    if "exception" in result:
        return {**rec, "valid_first": False, "valid": False, "safe": False, "errors": [result["exception"]],
                "ops": None, "matches_reference": None, "warnings": [], "plan": None,
                "callouts": score_drawing({}, truth) if truth is not None else None}  # a failed run found nothing
    plan, errors = result["plan"], result["errors"]
    safe = False
    if not errors:
        gcode = generate(found, plan, TOOLS, MATERIAL, MACHINE, name=case)
        safe = not check(gcode, MACHINE, **context_for(found))
    return {**rec, "valid_first": not errors and len(calls) == 1, "valid": not errors, "safe": safe,
            "errors": errors, "ops": ops(plan),
            "matches_reference": ops(plan) == ops(reference) if reference else None,
            "callouts": score_drawing(plan, truth) if truth is not None else None,
            "warnings": plan.get("warnings", []), "plan": plan}


def load_case(case):
    step, pdf, ref, _ = CASES[case]
    truth = FIX / "eval" / f"{case}.truth.json"
    return {"found": find_holes(FIX / step), "drawing": (FIX / pdf).read_bytes() if pdf else None,
            "reference": json.loads((FIX / ref).read_text()) if ref else None,
            "truth": json.loads(truth.read_text()) if pdf else None}


def run_one(case, loaded, client):
    start = len(client.calls)
    try:
        result = plan_job(loaded["found"], MATERIAL, TOOLS, MACHINE, client=client, drawing=loaded["drawing"])
    except (RuntimeError, anthropic.APIError) as e:
        result = {"exception": f"{type(e).__name__}: {e}"}
    return score_run(case, loaded["found"], result, client.calls[start:], loaded["truth"], loaded["reference"])


def estimate(settings, cases, runs):
    """Dollars, from the documented Opus cost per case scaled by each model's output price."""
    return sum(CASES[c][3] * PRICES[m][1] / PRICES["claude-opus-5-5"][1] for m, _ in settings for c in cases) * runs


def sweep(settings, cases, runs, max_cost, out, make_client=RecordingClient, loaded=None):
    """Run every setting x case x run, appending each result to out (a file path). Stops before the next run once
    max_cost dollars are spent. Returns the records."""
    loaded = loaded or {c: load_case(c) for c in cases}
    records, spent = [], 0.0
    for model, effort in settings:
        client = make_client(model, effort)
        for case in cases:
            for n in range(runs):
                if spent >= max_cost:
                    print(f"stopped: ${spent:.2f} spent, --max-cost is ${max_cost:.2f}", file=sys.stderr)
                    return records
                rec = {"setting": f"{model.removeprefix('claude-')}/{effort}", "run": n + 1,
                       **run_one(case, loaded[case], client)}
                spent += rec["cost_usd"]
                records.append(rec)
                with open(out, "a") as f:
                    f.write(json.dumps(rec) + "\n")
                print(f"{rec['setting']:>20} {case:<16} run {n + 1}: valid={rec['valid']} safe={rec['safe']} "
                      f"${rec['cost_usd']:.3f} {rec['seconds']:.0f}s  (total ${spent:.2f})", file=sys.stderr)
    return records


def _pct(xs):
    return f"{100 * sum(xs) / len(xs):.0f}%" if xs else "–"


def scorecard(records, by_case=False):
    groups = collections.defaultdict(list)
    for r in records:
        groups[(r["setting"], r["case"]) if by_case else (r["setting"],)].append(r)
    rows = ["| " + " | ".join(["Setting"] + (["Case"] if by_case else []) + [
        "Runs", "Valid 1st try", "Valid", "Safe", "Callouts found", "Extra callout holes/run", "Same plan",
        "$/plan", "p50 s", "max s", "Fallback"]) + " |"]
    rows.append("|" + "---|" * (len(rows[0].split("|")) - 2))
    for key, rs in groups.items():
        drawn = [r["callouts"] for r in rs if r["callouts"]]
        found = f"{100 * sum(c['found'] for c in drawn) / sum(c['wanted'] for c in drawn):.0f}%" if drawn else "–"
        extra = f"{sum(c['extra'] for c in drawn) / len(drawn):.1f}" if drawn else "–"
        same = []  # per case: share of runs with that case's most common operations
        for case in {r["case"] for r in rs}:
            plans = [json.dumps(r["ops"]) for r in rs if r["case"] == case and r["ops"]]
            same += [p == collections.Counter(plans).most_common(1)[0][0] for p in plans]
        secs = [r["seconds"] for r in rs]
        rows.append("| " + " | ".join(list(key) + [
            str(len(rs)), _pct([r["valid_first"] for r in rs]), _pct([r["valid"] for r in rs]),
            _pct([r["safe"] for r in rs]), found, extra, _pct(same),
            f"${statistics.mean(r['cost_usd'] for r in rs):.3f}", f"{statistics.median(secs):.0f}",
            f"{max(secs):.0f}", str(sum(r["fallback"] for r in rs))]) + " |")
    return "\n".join(rows)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--settings", default=BASELINE, help="comma-separated model/effort, e.g. opus-5-5/medium")
    p.add_argument("--cases", default=",".join(CASES), help="comma-separated, from: " + ", ".join(CASES))
    p.add_argument("--runs", type=int, default=3)
    p.add_argument("--max-cost", type=float, default=5.0, help="dollars; the sweep stops once this is spent")
    p.add_argument("--by-case", action="store_true", help="one scorecard row per setting and case")
    p.add_argument("--estimate", action="store_true", help="print the estimated cost and exit, no API calls")
    p.add_argument("--summarize", metavar="JSONL", help="reprint the scorecard from saved results, no API calls")
    a = p.parse_args(argv)
    if a.summarize:
        lines = Path(a.summarize).read_text().splitlines()
        print(scorecard([json.loads(line) for line in lines if line.strip()], a.by_case))
        return
    settings = [parse_setting(s) for s in a.settings.split(",")]
    cases = a.cases.split(",")
    if unknown := set(cases) - set(CASES):
        raise SystemExit(f"unknown cases: {', '.join(sorted(unknown))}")
    cost = estimate(settings, cases, a.runs)
    print(f"{len(settings) * len(cases) * a.runs} runs, estimated ${cost:.2f} (max ${a.max_cost:.2f})", file=sys.stderr)
    if a.estimate:
        return
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"{datetime.now():%Y-%m-%dT%H%M%S}.jsonl"
    records = sweep(settings, cases, a.runs, a.max_cost, out)
    print(f"results: {out}", file=sys.stderr)
    print(scorecard(records, a.by_case))


if __name__ == "__main__":
    main()
