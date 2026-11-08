import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hole_cam.gcode import _load
from hole_cam.holes import find_holes
from hole_cam.planner import offered_tools, plan_job, revise_plan, validate

FIX = Path(__file__).parent.parent / "fixtures"
TOOLS, MATERIALS, MACHINE = _load("tools.json"), _load("materials.json"), _load("machine.json")
MIXED = find_holes(FIX / "parts/mixed.step")  # holes 1,2: Ø6.8 through; 3: Ø5 x 5; 4: Ø5 x 20
GOOD = json.loads((FIX / "plans/mixed.json").read_text())


def op(plan, n):
    return plan["operations"][n]


def broken(change):
    plan = copy.deepcopy(GOOD)
    change(plan)
    return validate(plan, MIXED, TOOLS, MACHINE)["errors"]


def test_hand_plan_is_valid():
    assert validate(GOOD, MIXED, TOOLS, MACHINE) == {"errors": []}


@pytest.mark.parametrize("change, expect", [
    (lambda p: op(p, 3).update(tool_id="DRL-6.0"), "6.0 mm drill on a 6.8 mm hole"),
    (lambda p: op(p, 2).update(cycle="G81"), "needs G83"),
    (lambda p: op(p, 2).update(peck=0), "peck depth above 0"),
    (lambda p: op(p, 0).update(hole_ids=[1, 2, 3]), "hole 4 is drilled before it is spotted"),
    (lambda p: op(p, 0).update(depth=6), "spot depth"),
    (lambda p: op(p, 3).update(hole_ids=[1]), "hole 2 (6.8 mm) is not drilled"),
    (lambda p: op(p, 3).update(hole_ids=[1, 2, 9]), "hole 9 does not exist"),
    (lambda p: op(p, 3).update(tool_id="DRL-99"), "not in the library"),
    (lambda p: op(p, 3).update(tool_id="SPOT-90-10"), "can't do a drill operation"),
    (lambda p: p["operations"].append(copy.deepcopy(op(p, 3))), "already drilled"),
])
def test_validation_catches(change, expect):
    errors = broken(change)
    assert any(expect in e for e in errors), errors


def test_short_flutes_are_caught():
    stubby = [{**t, "flute_length": 20} if t["id"] == "DRL-6.8" else t for t in TOOLS]
    errors = validate(GOOD, MIXED, stubby, MACHINE)["errors"]
    assert any("28.0 mm of reach" in e for e in errors), errors


def test_hole_with_no_matching_drill_may_be_left_out():
    found = find_holes(FIX / "parts/odd_size.step")  # hole 1: Ø7.1 (no drill), hole 2: Ø6.8
    plan = {"operations": [
        {"op": "spot_drill", "tool_id": "SPOT-90-10", "hole_ids": [2], "depth": 1.5, "cycle": "G81", "peck": 0, "reason": ""},
        {"op": "drill", "tool_id": "DRL-6.8", "hole_ids": [2], "depth": 0, "cycle": "G81", "peck": 0, "reason": ""},
    ], "warnings": ["Hole 1 is 7.1 mm; no drill matches"]}
    assert validate(plan, found, TOOLS, MACHINE)["errors"] == []


class FakeClient:
    """Stands in for anthropic.Anthropic(); returns canned plans in order."""

    def __init__(self, *plans, stop_reason="end_turn"):
        self.plans, self.stop_reason, self.calls = list(plans), stop_reason, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})
        text = json.dumps(self.plans.pop(0))
        return SimpleNamespace(stop_reason=self.stop_reason, stop_details=None,
                               content=[SimpleNamespace(type="text", text=text)])


def test_rejected_plan_is_retried_once_with_the_errors():
    bad = copy.deepcopy(GOOD)
    op(bad, 2)["cycle"] = "G81"
    client = FakeClient(bad, GOOD)
    result = plan_job(MIXED, MATERIALS["6061"], TOOLS, MACHINE, client=client)
    assert result["plan"] == GOOD and result["errors"] == []
    assert len(client.calls) == 2
    assert "needs G83" in client.calls[1]["messages"][-1]["content"]
    assert client.calls[0]["model"] == "claude-opus-5-5"


def test_prompt_gives_hole_heights_in_program_coordinates():
    client = FakeClient(GOOD)
    plan_job(MIXED, MATERIALS["6061"], TOOLS, MACHINE, client=client)
    job = json.loads(client.calls[0]["messages"][0]["content"].split("\n", 1)[1])
    assert {h["top_z"] for h in job["holes"]} == {0}  # model Z25 is the part top, so program Z0
    assert MIXED["holes"][0]["top_z"] == 25  # the caller's data is not modified


def test_gives_up_after_one_retry_and_returns_the_errors():
    bad = copy.deepcopy(GOOD)
    op(bad, 2)["cycle"] = "G81"
    result = plan_job(MIXED, MATERIALS["6061"], TOOLS, MACHINE, client=FakeClient(bad, bad))
    assert any("needs G83" in e for e in result["errors"])


def test_refusal_is_an_error_not_a_plan():
    with pytest.raises(RuntimeError, match="refusal"):
        plan_job(MIXED, MATERIALS["6061"], TOOLS, MACHINE, client=FakeClient(GOOD, stop_reason="refusal"))


def test_revision_sends_the_current_plan_and_request_and_returns_the_summary():
    revised = copy.deepcopy(GOOD)
    revised["operations"][2:4] = revised["operations"][3:1:-1]  # deep 6.8 mm holes before the 5 mm one
    client = FakeClient({**revised, "summary": "Moved the 6.8 mm holes first."})
    result = revise_plan(MIXED, MATERIALS["6061"], TOOLS, MACHINE, GOOD, "Do the deep holes first", client=client)
    assert result == {"plan": revised, "errors": [], "summary": "Moved the 6.8 mm holes first."}
    prompt = client.calls[0]["messages"][0]["content"]
    assert "Current plan:\n" + json.dumps(GOOD, indent=1) in prompt and prompt.endswith("Do the deep holes first")
    assert "summary" in client.calls[0]["output_config"]["format"]["schema"]["required"]
    assert "Revising a plan" in client.calls[0]["system"]


def test_revision_that_breaks_a_rule_is_retried_then_rejected():
    unspotted = copy.deepcopy(GOOD)
    del unspotted["operations"][0]  # "skip spot drilling"
    bad = {**unspotted, "summary": "Removed spot drilling."}
    client = FakeClient(bad, copy.deepcopy(bad))
    result = revise_plan(MIXED, MATERIALS["6061"], TOOLS, MACHINE, GOOD, "Skip spot drilling", client=client)
    assert len(client.calls) == 2 and any("before it is spotted" in e for e in result["errors"])
    assert result["summary"] == "Removed spot drilling." and "summary" not in result["plan"]


def _counterbored():
    # A 7/16" counterbore 7.62 mm deep over a 9/32" through hole, as on NIST FTC-07.
    return {"part": {"top_z": 0, "bottom_z": -20}, "skipped": [], "holes": [
        {"id": 1, "x": 10, "y": 10, "diameter": 11.1125, "depth": 7.62, "through": False, "top_z": 0},
        {"id": 2, "x": 10, "y": 10, "diameter": 7.1438, "depth": 12.38, "through": True, "top_z": -7.62},
    ]}


def _ops(*ops):
    return {"operations": [{"op": o, "tool_id": t, "hole_ids": [h], "depth": d, "cycle": "G81", "peck": 0, "reason": ""}
                           for o, t, h, d in ops], "warnings": []}


def test_hole_under_a_counterbore_must_wait_for_the_counterbore():
    cbore_first = _ops(("spot_drill", "SPOT-90-10", 1, 4.5), ("drill", "DRL-7/16IN", 1, 0),
                       ("spot_drill", "SPOT-90-10", 2, 3), ("drill", "DRL-9/32IN", 2, 0))
    assert validate(cbore_first, _counterbored(), TOOLS, MACHINE)["errors"] == []
    small_first = _ops(("spot_drill", "SPOT-90-10", 2, 3), ("drill", "DRL-9/32IN", 2, 0),
                       ("spot_drill", "SPOT-90-10", 1, 4.5), ("drill", "DRL-7/16IN", 1, 0))
    errors = validate(small_first, _counterbored(), TOOLS, MACHINE)["errors"]
    assert len(errors) == 1 and "hole 2 starts 7.62 mm down, at the floor of hole 1" in errors[0]
    # A counterbore no deeper than the R clearance leaves the R plane at or above the surface, so order is free.
    shallow = _counterbored()
    shallow["holes"][0]["depth"], shallow["holes"][1]["top_z"] = MACHINE["r_clearance"], -MACHINE["r_clearance"]
    assert validate(small_first, shallow, TOOLS, MACHINE)["errors"] == []


def test_hole_under_a_counterbore_with_no_drill_may_be_left_out():
    found = _counterbored()
    found["holes"][0]["diameter"] = 11.5  # no drill this size, so hole 1 needs an end mill and hole 2 can't be reached
    assert validate(_ops(), found, TOOLS, MACHINE)["errors"] == []


def test_claude_sees_only_the_drills_that_matter():
    client = FakeClient(GOOD)
    plan_job(MIXED, MATERIALS["6061"], TOOLS, MACHINE, client=client)
    job = json.loads(client.calls[0]["messages"][0]["content"].split("\n", 1)[1])
    # The threads a 5 mm or 6.8 mm hole could be modeled as, in case the drawing or the machinist asks for one:
    # a 5 mm hole is M5 at thread size, or M6 or 1/4-20 near tap drill size; 6.8 mm is M8 or 5/16-18. Each tap
    # comes with its tap drill.
    offered = ["SPOT-90-10", "DRL-4.2", "DRL-5.0", "DRL-6.8", "DRL-#7", "DRL-F",
               "TAP-M5X0.8", "TAP-M6X1", "TAP-M8X1.25", "TAP-1/4-20", "TAP-5/16-18"]
    assert [t["id"] for t in job["tools"]] == offered
    enum = client.calls[0]["output_config"]["format"]["schema"]["properties"]["operations"]["items"]["properties"]["tool_id"]["enum"]
    assert enum == offered


def test_a_hole_no_drill_fits_gets_the_nearest_drill_each_side():
    found = find_holes(FIX / "parts/odd_size.step")  # hole 1: Ø7.1 (no drill), hole 2: Ø6.8
    ids = [t["id"] for t in offered_tools(found, TOOLS)]
    # I = 6.909 mm, 9/32 = 7.144 mm; the 6.8 mm hole could also be an M8 or 5/16-18 thread (F is that tap drill).
    assert ids == ["SPOT-90-10", "DRL-6.8", "DRL-F", "DRL-I", "DRL-9/32IN", "TAP-M8X1.25", "TAP-5/16-18"]


def test_hole_under_a_drilled_counterbore_needs_no_spot():
    step_drilled = _ops(("spot_drill", "SPOT-90-10", 1, 4.5), ("drill", "DRL-7/16IN", 1, 0), ("drill", "DRL-9/32IN", 2, 0))
    assert validate(step_drilled, _counterbored(), TOOLS, MACHINE)["errors"] == []
    unspotted_first = _ops(("drill", "DRL-9/32IN", 2, 0), ("spot_drill", "SPOT-90-10", 1, 4.5), ("drill", "DRL-7/16IN", 1, 0))
    errors = validate(unspotted_first, _counterbored(), TOOLS, MACHINE)["errors"]
    assert any("hole 2 is drilled before it is spotted" in e for e in errors)
