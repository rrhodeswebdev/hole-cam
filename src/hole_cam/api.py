"""HTTP API: upload a STEP part, get back the plan, G-code, safety checks and a 3D preview (SPEC.md 7).

Run: uv run uvicorn hole_cam.api:app --reload
"""

import tempfile
from pathlib import Path

import anthropic
import cadquery as cq
from fastapi import Depends, FastAPI, Form, HTTPException, UploadFile

from hole_cam.checks import check, context_for
from hole_cam.gcode import _load, generate
from hole_cam.holes import find_holes
from hole_cam.planner import plan_job

MAX_UPLOAD = 20 * 1024 * 1024  # bytes
TOOLS, MATERIALS, MACHINE = _load("tools.json"), _load("materials.json"), _load("machine.json")

app = FastAPI(title="Hole-CAM")


def anthropic_client():
    return anthropic.Anthropic()


@app.get("/materials")
def materials():
    return {k: v["name"] for k, v in MATERIALS.items() if not k.startswith("_")}


@app.post("/jobs")
def create_job(file: UploadFile, material: str = Form(), client=Depends(anthropic_client)):
    # Sync on purpose: the CAD kernel and the Claude call block, so FastAPI runs this in a worker thread.
    if material.startswith("_") or material not in MATERIALS:
        raise HTTPException(422, f"Unknown material {material!r}. Choose one of: {', '.join(materials())}.")
    name = Path(file.filename or "part").name
    if Path(name).suffix.lower() not in (".step", ".stp"):
        raise HTTPException(422, "Upload a .step or .stp file.")
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, f"File is over {MAX_UPLOAD // 2**20} MB.")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "upload.step"  # never use the client's file name as a path
        path.write_bytes(data)
        try:
            found = find_holes(path)
            mesh = _mesh(path, found["part"]["top_z"])
        except Exception:
            raise HTTPException(422, "Could not read that STEP file. Is it a single solid part?")

    job = {**found, "mesh": mesh, "plan": None, "plan_errors": [], "plan_notes": [],
           "gcode": None, "findings": [], "toolpath": [], "export_ok": False}
    if not found["holes"]:
        job["plan_errors"] = ["No holes that can be drilled from the top were found."]
        return job

    try:
        result = plan_job(found, MATERIALS[material], TOOLS, MACHINE, client=client)
    except (RuntimeError, anthropic.APIError) as e:
        raise HTTPException(502, f"The planner failed: {e}")
    except TypeError as e:
        # The SDK raises a bare TypeError when it finds no credentials; anything else is a real bug.
        if "authentication" not in str(e):
            raise
        raise HTTPException(503, "Claude API credentials aren't set up on the server. Set ANTHROPIC_API_KEY.")
    job.update(plan=result["plan"], plan_errors=result["errors"], plan_notes=result["notes"])
    if result["errors"]:
        return job

    gcode = generate(found, result["plan"], TOOLS, MATERIALS[material], MACHINE, name=Path(name).stem)
    trace = []
    job.update(gcode=gcode, findings=check(gcode, MACHINE, **context_for(found), trace=trace), toolpath=trace)
    job["export_ok"] = not job["findings"]
    return job


def _mesh(path, top_z):
    """Triangle mesh of the part in work coordinates (Z0 = top of the part), for three.js."""
    verts, tris = cq.importers.importStep(str(path)).val().tessellate(0.1, 0.2)
    return {"vertices": [[round(v.x, 3), round(v.y, 3), round(v.z - top_z, 3)] for v in verts],
            "triangles": [list(t) for t in tris]}
