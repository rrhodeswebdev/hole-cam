"""Drawing callouts and reaming: Claude reads the tolerances, plain code decides and checks what they need."""

import base64
import json

import pytest
from fastapi.testclient import TestClient
from test_api import MIXED as MIXED_STEP
from test_planner import GOOD, MACHINE, MATERIALS, MIXED, TOOLS, FakeClient

from hole_cam.api import anthropic_client, app
from hole_cam.checks import check
from hole_cam.gcode import generate
from hole_cam.planner import drawing_callouts, offered_tools, plan_job, validate

# Two 1/4" blind holes like FTC-07's flange holes, and one 9/32" hole the drawing tolerances loosely.
FOUND = {"part": {"top_z": 10, "bottom_z": -20}, "holes": [
    {"id": 1, "x": 10, "y": 10, "diameter": 6.35, "depth": 7.62, "through": False, "top_z": 10},
    {"id": 2, "x": 40, "y": 10, "diameter": 6.35, "depth": 7.62, "through": False, "top_z": 10},
    {"id": 3, "x": 70, "y": 10, "diameter": 7.1437, "depth": 11.43, "through": True, "top_z": 10},
]}
TIGHT = {"hole_ids": [1, 2], "callout": "2X Ø.250 +.003 -.000", "units": "in", "diameter": 0.25, "plus": 0.003, "minus": 0}
LOOSE = {"hole_ids": [3], "callout": "Ø.281 +.006 -.001", "units": "in", "diameter": 0.28125, "plus": 0.006, "minus": -0.001}


def spot(ids):
    return {"op": "spot_drill", "tool_id": "SPOT-90-10", "hole_ids": ids, "depth": 2.5, "cycle": "G81", "peck": 0, "reason": ""}


def drill(tool, ids):
    return {"op": "drill", "tool_id": tool, "hole_ids": ids, "depth": 0, "cycle": "G81", "peck": 0, "reason": ""}


def ream(ids, cycle="G85"):
    return {"op": "ream", "tool_id": "RMR-1/4IN", "hole_ids": ids, "depth": 0, "cycle": cycle, "peck": 0, "reason": ""}


REAMED = {"operations": [spot([1, 2, 3]), drill("DRL-6.0", [1, 2]), drill("DRL-9/32IN", [3]), ream([1, 2])],
          "warnings": [], "drawing": [TIGHT, LOOSE]}


def errors(plan):
    return validate(plan, FOUND, TOOLS, MACHINE)["errors"]


def test_callouts_are_converted_to_mm_and_code_decides_what_to_ream():
    tight, loose = drawing_callouts(REAMED, MACHINE)
    assert (tight["diameter_mm"], tight["plus_mm"], tight["ream"]) == (6.35, 0.0762, True)   # +.003" is under 0.1 mm
    assert (loose["diameter_mm"], loose["plus_mm"], loose["ream"]) == (7.1437, 0.1524, False)  # a drill holds +.006"


def test_drill_under_then_ream_is_valid():
    assert errors(REAMED) == []


def with_ops(*ops, drawing=(TIGHT, LOOSE)):
    return {"operations": list(ops), "warnings": [], "drawing": list(drawing)}


@pytest.mark.parametrize("plan, expect", [
    # Drilled to size: a drill can't hold +.003".
    (with_ops(spot([1, 2, 3]), drill("DRL-1/4IN", [1, 2]), drill("DRL-9/32IN", [3])), "tighter than a drill holds"),
    # Drilled under but never reamed.
    (with_ops(spot([1, 2, 3]), drill("DRL-6.0", [1, 2]), drill("DRL-9/32IN", [3])), "ream it with RMR-1/4IN"),
    (with_ops(spot([1, 2, 3]), ream([1, 2]), drill("DRL-6.0", [1, 2]), drill("DRL-9/32IN", [3])), "reamed before it is drilled"),
    # 5.8 mm leaves 0.55 mm, too much for the reamer.
    (with_ops(spot([1, 2, 3]), drill("DRL-5.8", [1, 2]), drill("DRL-9/32IN", [3]), ream([1, 2])), "leaving 0.55 mm"),
    (with_ops(spot([1, 2, 3]), drill("DRL-6.0", [1, 2]), drill("DRL-9/32IN", [3]), ream([1, 2], cycle="G81")), "reaming uses G85"),
    # Callout put on the wrong hole: the sizes disagree.
    (with_ops(*REAMED["operations"], drawing=[{**TIGHT, "hole_ids": [1, 3]}, {**LOOSE, "hole_ids": []}]), "wrong hole, or the drawing"),
    (with_ops(*REAMED["operations"], drawing=[{**TIGHT, "callout": "1X Ø.250 +.003 -.000"}, LOOSE]), "is for 1 holes but lists 2"),
    (with_ops(*REAMED["operations"], drawing=[TIGHT, {**LOOSE, "hole_ids": [3, 9]}]), "hole 9, which does not exist"),
])
def test_validation_catches(plan, expect):
    found = errors(plan)
    assert any(expect in e for e in found), found


def test_loose_tolerance_is_drilled_to_size():
    plan = with_ops(spot([1, 2, 3]), drill("DRL-1/4IN", [1, 2]), drill("DRL-9/32IN", [3]), drawing=[{**TIGHT, "plus": 0.005}, LOOSE])
    assert errors(plan) == []


def test_tight_hole_the_library_cant_ream_may_be_left_out():
    tight_9_32 = {**LOOSE, "plus": 0.002}  # no 9/32" reamer in the library
    plan = with_ops(spot([1, 2]), drill("DRL-6.0", [1, 2]), ream([1, 2]), drawing=[TIGHT, tight_9_32])
    assert errors(plan) == []


def test_reaming_program_uses_g85_at_reaming_speed():
    nc = generate(FOUND, REAMED, TOOLS, MATERIALS["6061"], MACHINE)
    # 40 m/min on a 6.35 mm reamer is 2005 rpm; 0.2 mm/rev is 401 mm/min. It reams to full depth, no point.
    assert "T43 M06 (REAMER 1/4 6.350MM)\nG00 G43 H43 Z25. S2005 M03" in nc
    assert "G98 G85 X10. Y10. Z-7.62 R2. F401." in nc
    assert check(nc, MACHINE, local_tops={(h["x"], h["y"]): 0 for h in FOUND["holes"]}, bottom_z=-30) == []


def test_trace_feeds_back_out_of_a_g85_hole():
    nc = "T1 M06\nG43 H1 Z25. S1000 M03\nG00 X5. Y5.\nG98 G85 X5. Y5. Z-10. R2. F100.\nG80\nM05\nM30\n"
    trace = []
    check(nc, MACHINE, trace=trace)
    zs = [(m["kind"], m["from"][2], m["to"][2]) for m in trace if m.get("line") == 4]
    assert zs == [("rapid", 25, 2), ("feed", 2, -10), ("feed", -10, 2), ("rapid", 2, 25)]


def test_claude_is_offered_the_reamer_and_its_pre_drill():
    ids = [t["id"] for t in offered_tools(FOUND, TOOLS)]
    assert {"RMR-1/4IN", "DRL-6.0", "DRL-1/4IN"} <= set(ids) and "RMR-6H7" not in ids


def test_the_drawing_goes_to_claude_as_a_pdf_before_the_job():
    client = FakeClient({**GOOD, "drawing": []})
    plan_job(MIXED, MATERIALS["6061"], TOOLS, MACHINE, client=client, drawing=b"%PDF-1.4 fake")
    pdf, text = client.calls[0]["messages"][0]["content"]
    assert pdf["type"] == "document" and pdf["source"]["media_type"] == "application/pdf"
    assert base64.b64decode(pdf["source"]["data"]) == b"%PDF-1.4 fake"
    assert text["text"].startswith("Plan this job:")
    assert "drill_oversize" not in client.calls[0]["system"] and "0.1 mm needs reaming" in client.calls[0]["system"]


@pytest.fixture
def api():
    fake = FakeClient({**GOOD, "drawing": []})
    app.dependency_overrides[anthropic_client] = lambda: fake
    yield TestClient(app), fake
    app.dependency_overrides.clear()


def test_api_sends_the_drawing_and_returns_its_callouts(api):
    client, fake = api
    r = client.post("/jobs", files={"file": ("mixed.step", MIXED_STEP), "drawing": ("d.pdf", b"%PDF-1.7 x")},
                    data={"material": "6061"})
    assert r.status_code == 200, r.text
    assert r.json()["drawing"] == [] and r.json()["export_ok"] is True
    assert r.json()["drill_oversize"] == 0.1  # the drawing card's note reads it, not a hard-coded number
    assert fake.calls[0]["messages"][0]["content"][0]["type"] == "document"


@pytest.mark.parametrize("name, data, status", [("d.png", b"%PDF-1.7", 422), ("d.pdf", b"not a pdf", 422)])
def test_api_rejects_a_drawing_that_isnt_a_pdf(api, name, data, status):
    client, fake = api
    r = client.post("/jobs", files={"file": ("mixed.step", MIXED_STEP), "drawing": (name, data)}, data={"material": "6061"})
    assert r.status_code == status and fake.calls == []


def test_revision_sends_the_drawing_again():
    fake = FakeClient({**GOOD, "drawing": [], "summary": "No change."})
    app.dependency_overrides[anthropic_client] = lambda: fake
    try:
        r = TestClient(app).post("/jobs/revise", files={"file": ("mixed.step", MIXED_STEP), "drawing": ("d.pdf", b"%PDF-1.7 x")},
                                 data={"material": "6061", "plan": json.dumps(GOOD), "request": "Keep it"})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200, r.text
    assert fake.calls[0]["messages"][0]["content"][0]["type"] == "document"
