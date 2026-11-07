import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hole_cam.gcode import _load
from hole_cam.holes import find_holes
from hole_cam.planner import plan_job, validate

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


def test_hand_plan_is_valid_with_one_note():
    result = validate(GOOD, MIXED, TOOLS, MACHINE)
    assert result["errors"] == []
    assert len(result["notes"]) == 1 and "same tool" in result["notes"][0]


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


def test_gives_up_after_one_retry_and_returns_the_errors():
    bad = copy.deepcopy(GOOD)
    op(bad, 2)["cycle"] = "G81"
    result = plan_job(MIXED, MATERIALS["6061"], TOOLS, MACHINE, client=FakeClient(bad, bad))
    assert any("needs G83" in e for e in result["errors"])


def test_refusal_is_an_error_not_a_plan():
    with pytest.raises(RuntimeError, match="refusal"):
        plan_job(MIXED, MATERIALS["6061"], TOOLS, MACHINE, client=FakeClient(GOOD, stop_reason="refusal"))
