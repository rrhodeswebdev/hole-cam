"""Scoring tests for scripts/eval.py. No API calls."""

import copy
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hole_cam.planner import drawing_callouts

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location("planner_eval", ROOT / "scripts" / "eval.py")
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

GOOD = json.loads((ROOT / "fixtures/plans/mixed.json").read_text())
TRUTH = [{"callout": "8X Ø6.35 +0.05 -0", "units": "mm", "diameter": 6.35, "plus": 0.05, "minus": 0, "thread": 0,
          "holes": 8, "needs": "ream"}]


def callout(ids, diameter=6.35, plus=0.05):
    return {"hole_ids": ids, "callout": "", "units": "mm", "diameter": diameter, "plus": plus, "minus": 0, "thread": 0}


def test_one_callout_and_split_callouts_score_the_same():
    one = ev.score_drawing({"drawing": [callout(list(range(1, 9)))]}, TRUTH)
    split = ev.score_drawing({"drawing": [callout([1, 2]), callout([3, 4]), callout([5, 6]), callout([7, 8])]}, TRUTH)
    assert one == split == {"found": 8, "wanted": 8, "extra": 0}


def test_wrong_size_or_tolerance_is_not_found_and_counts_as_extra():
    assert ev.score_drawing({"drawing": [callout([1, 2], diameter=6.37)]}, TRUTH) == {"found": 0, "wanted": 8, "extra": 2}
    assert ev.score_drawing({"drawing": [callout([1, 2], plus=0.06)]}, TRUTH)["found"] == 0


def test_more_holes_than_the_drawing_calls_out_count_as_extra():
    assert ev.score_drawing({"drawing": [callout(list(range(1, 11)))]}, TRUTH) == {"found": 8, "wanted": 8, "extra": 2}


@pytest.mark.parametrize("case", ["ftc07", "ftc08", "ftc10"])
def test_truth_files_agree_with_the_code_on_what_each_hole_needs(case):
    truth = json.loads((ROOT / f"fixtures/eval/{case}.truth.json").read_text())
    for t, c in zip(truth, drawing_callouts({"drawing": truth}, ev.MACHINE)):
        assert ("tap" if c["tap"] else "ream" if c["ream"] else "drill") == t["needs"], t["callout"]


def test_truth_files_cover_every_hole_the_part_has():
    for case, holes in [("ftc07", 16), ("ftc08", 20), ("ftc10", 28)]:
        truth = json.loads((ROOT / f"fixtures/eval/{case}.truth.json").read_text())
        assert sum(t["holes"] for t in truth) == holes, case


def test_ops_ignore_order_spot_depth_and_peck_but_not_tool_or_cycle():
    other = copy.deepcopy(GOOD)
    other["operations"].reverse()
    other["operations"][-1]["depth"] = 2.0  # the spot op, now last
    other["operations"][0]["peck"] = 3.0
    assert ev.ops(other) == ev.ops(GOOD)
    other["operations"][0]["tool_id"] = "DRL-7.0"
    assert ev.ops(other) != ev.ops(GOOD)
    other = copy.deepcopy(GOOD)
    other["operations"][1]["cycle"] = "G83"
    assert ev.ops(other) != ev.ops(GOOD)


class FakeAPI:
    """Stands in for anthropic.Anthropic() under RecordingClient; returns a plan with usage."""

    def __init__(self, plan, model="claude-opus-5-5", iterations=None, tokens=(1000, 500)):
        self.plan, self.model, self.iterations, self.tokens, self.calls = plan, model, iterations, tokens, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append(kw)
        usage = SimpleNamespace(input_tokens=self.tokens[0], output_tokens=self.tokens[1], iterations=self.iterations)
        return SimpleNamespace(stop_reason="end_turn", stop_details=None, model=self.model, usage=usage,
                               content=[SimpleNamespace(type="text", text=json.dumps(self.plan))])


PLAN = {**GOOD, "operations": [{"depth": 0, "peck": 0, "cycle": "G81", **o} for o in GOOD["operations"]],
        "warnings": [], "drawing": []}


@pytest.fixture(scope="module")
def mixed():
    return ev.load_case("mixed")


def test_a_whole_run_is_scored_and_recorded(mixed):
    client = ev.RecordingClient("claude-opus-5-5", "medium", inner=FakeAPI(PLAN))
    rec = ev.run_one("mixed", mixed, client)
    assert rec["valid_first"] and rec["valid"] and rec["safe"] and rec["matches_reference"]
    assert rec["attempts"] == 1 and rec["cost_usd"] == pytest.approx((1000 * 4 + 500 * 20) / 1e6)
    assert not rec["fallback"] and rec["callouts"] is None
    for key in ("errors", "input_tokens", "output_tokens", "seconds", "served_model", "ops", "warnings", "plan"):
        assert key in rec


def test_a_retried_run_records_what_the_first_attempt_got_wrong(mixed):
    bad = copy.deepcopy(PLAN)
    bad["operations"][2]["cycle"] = "G81"  # hole 4 is 4xD deep and needs G83

    class BadThenGood(FakeAPI):
        def create(self, **kw):
            self.plan = bad if not self.calls else PLAN
            return super().create(**kw)

    rec = ev.run_one("mixed", mixed, ev.RecordingClient("claude-opus-5-5", "medium", inner=BadThenGood(PLAN)))
    assert rec["valid"] and not rec["valid_first"] and rec["attempts"] == 2
    assert len(rec["first_errors"]) == 1 and "needs G83" in rec["first_errors"][0]


def test_the_request_carries_the_settings_model_and_effort(mixed):
    api = FakeAPI(PLAN, model="claude-sonnet-5-5")
    ev.run_one("mixed", mixed, ev.RecordingClient("claude-sonnet-5-5", "low", inner=api))
    assert api.calls[0]["model"] == "claude-sonnet-5-5"
    assert api.calls[0]["output_config"]["effort"] == "low"
    assert api.calls[0]["fallbacks"] == "default"


def test_haiku_is_sent_without_the_fallback(mixed):
    api = FakeAPI(PLAN, model="claude-haiku-5-5")
    ev.run_one("mixed", mixed, ev.RecordingClient("claude-haiku-5-5", "medium", inner=api))
    assert "fallbacks" not in api.calls[0]
    assert not any(b.startswith("server-side-fallback") for b in api.calls[0]["betas"])


def test_a_run_answered_by_the_fallback_is_flagged(mixed):
    api = FakeAPI(PLAN, model="claude-opus-5", iterations=[SimpleNamespace(type="message"),
                                                          SimpleNamespace(type="fallback_message")])
    rec = ev.run_one("mixed", mixed, ev.RecordingClient("claude-opus-5-5", "medium", inner=api))
    assert rec["fallback"] and rec["served_model"] == "claude-opus-5"


def test_a_failed_request_is_recorded_not_raised(mixed):
    class Refuses(FakeAPI):
        def create(self, **kw):
            return SimpleNamespace(**{**vars(super().create(**kw)), "stop_reason": "refusal"})

    rec = ev.run_one("mixed", mixed, ev.RecordingClient("claude-opus-5-5", "medium", inner=Refuses(PLAN)))
    assert not rec["valid"] and "planner stopped early" in rec["errors"][0]


def test_the_sweep_stops_at_the_cost_limit(mixed, tmp_path):
    def make(model, effort):  # each run costs $1: 250K input tokens at $4/M
        return ev.RecordingClient(model, effort, inner=FakeAPI(PLAN, tokens=(250_000, 0)))

    out = tmp_path / "r.jsonl"
    records = ev.sweep([("claude-opus-5-5", "medium")], ["mixed"], 5, 2.0, out, make, {"mixed": mixed})
    assert len(records) == 2 and len(out.read_text().splitlines()) == 2
    assert "| opus-5-5/medium | 2 | 100% |" in ev.scorecard(records)
