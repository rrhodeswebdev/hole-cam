import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_planner import GOOD, FakeClient

from hole_cam.api import MAX_UPLOAD, anthropic_client, app
from hole_cam.gcode import _load, generate

FIX = Path(__file__).parent.parent / "fixtures"
MIXED = (FIX / "parts/mixed.step").read_bytes()


@pytest.fixture
def api():
    def use(*plans, **kw):
        fake = FakeClient(*plans, **kw)
        app.dependency_overrides[anthropic_client] = lambda: fake
        return TestClient(app)
    yield use
    app.dependency_overrides.clear()


def post(client, data=MIXED, name="mixed.step", material="6061"):
    return client.post("/jobs", files={"file": (name, data)}, data={"material": material})


def test_good_job_returns_program_checks_and_preview(api):
    r = post(api(GOOD))
    assert r.status_code == 200, r.text
    job = r.json()
    assert job["gcode"] == (FIX / "programs/mixed_6061.nc").read_text()
    assert job["findings"] == [] and job["export_ok"] is True
    assert len(job["holes"]) == 4 and job["mesh"]["triangles"]
    assert max(v[2] for v in job["mesh"]["vertices"]) == 0  # mesh is in work coordinates, Z0 on top
    assert sum(m["kind"] == "feed" for m in job["toolpath"]) == 8  # 4 spots + 4 drilled holes


def test_rejected_plan_gives_errors_and_no_program(api):
    bad = copy.deepcopy(GOOD)
    bad["operations"][2]["cycle"] = "G81"
    job = post(api(bad, bad)).json()
    assert job["plan_errors"] and job["gcode"] is None and job["export_ok"] is False


def test_planner_refusal_is_a_502(api):
    assert post(api(GOOD, stop_reason="refusal")).status_code == 502


def test_missing_credentials_is_a_clear_503():
    def no_auth(**kw):
        raise TypeError("Could not resolve authentication method. Expected one of api_key, ...")
    app.dependency_overrides[anthropic_client] = lambda: SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=no_auth)))
    try:
        r = post(TestClient(app))
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]


@pytest.mark.parametrize("kw, status", [
    ({"material": "unobtanium"}, 422),
    ({"material": "_note"}, 422),
    ({"name": "mixed.stl"}, 422),
    ({"data": b"not a step file"}, 422),
    ({"data": b"0" * (MAX_UPLOAD + 1)}, 413),
])
def test_bad_uploads_are_rejected(api, kw, status):
    assert post(api(), **kw).status_code == status


def test_part_name_cannot_break_out_of_the_gcode_comment():
    found = {"part": {"top_z": 0}, "holes": []}
    nc = generate(found, {"operations": []}, [], {}, _load("machine.json"), name="evil)\nM30\n(x")
    assert nc.splitlines()[1] == "O1000 (EVILM30X)"
