"""HTTP API: upload a STEP part (and optionally its drawing), get back the plan, G-code, safety checks and a 3D
preview. See docs/HOW-IT-WORKS.md.

Run: uv run uvicorn hole_cam.api:app --reload
"""

import json
import tempfile
from pathlib import Path

import anthropic
from fastapi import Depends, FastAPI, Form, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from hole_cam.checks import check, context_for
from hole_cam.data import load_data
from hole_cam.gcode import generate
from hole_cam.holes import load_part, mesh
from hole_cam.planner import drawing_callouts, plan_job, revise_plan

MAX_UPLOAD = 20 * 1024 * 1024  # bytes, for the part and for the drawing
MAX_REQUEST = 500  # characters in a plan change request
WEB = Path(__file__).parents[2] / "web" / "dist"
TOOLS, MATERIALS, MACHINE = load_data("tools.json"), load_data("materials.json"), load_data("machine.json")
TOOL_NUMBERS = {t["id"]: t["number"] for t in TOOLS}
TOOL_INFO = {t["number"]: {k: t.get(k) for k in ("type", "diameter", "point_angle", "flute_length", "pitch")} for t in TOOLS}

app = FastAPI(title="Hole-CAM")


def anthropic_client():
    return anthropic.Anthropic()


@app.get("/materials")
def materials():
    return {k: v["name"] for k, v in MATERIALS.items() if not k.startswith("_")}


@app.post("/jobs")
def create_job(file: UploadFile, material: str = Form(), drawing: UploadFile | None = None,
               client=Depends(anthropic_client)):
    # Sync on purpose: the CAD kernel and the Claude call block, so FastAPI runs this in a worker thread.
    pdf = _read_drawing(drawing)
    found, part_mesh, name = _read_part(file, material)
    job = _job(found, part_mesh)
    if not found["holes"]:
        job["plan_errors"] = ["No holes that can be drilled from the top were found."]
        return job
    result = _call_planner(plan_job, found, MATERIALS[material], TOOLS, MACHINE, client=client, drawing=pdf)
    return _finish(job, found, result, material, name)


@app.post("/jobs/revise")
def revise_job(file: UploadFile, material: str = Form(), plan: str = Form(), request: str = Form(),
               drawing: UploadFile | None = None, client=Depends(anthropic_client)):
    """Change a plan as the machinist asks. The part (and drawing) are uploaded again because they drive the
    G-code and the tolerance rules; the current plan only serves as context for Claude, and the revision is
    validated like any plan."""
    request = request.strip()
    if not request or len(request) > MAX_REQUEST:
        raise HTTPException(422, f"Describe the change in 1 to {MAX_REQUEST} characters.")
    try:
        current = json.loads(plan)
    except ValueError:
        current = None
    if not isinstance(current, dict) or not isinstance(current.get("operations"), list):
        raise HTTPException(422, "The current plan isn't valid. Plan the job again.")
    pdf = _read_drawing(drawing)
    found, part_mesh, name = _read_part(file, material)
    if not found["holes"]:
        raise HTTPException(422, "No holes that can be drilled from the top were found.")
    result = _call_planner(revise_plan, found, MATERIALS[material], TOOLS, MACHINE, current, request, client=client,
                           drawing=pdf)
    return {**_finish(_job(found, part_mesh), found, result, material, name), "summary": result["summary"]}


def _read_part(file, material):
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
            shape, found = load_part(path)
            part_mesh = mesh(shape, found["part"]["top_z"])
        except Exception as e:  # the CAD kernel raises many kinds of errors on a bad file
            raise HTTPException(422, "Could not read that STEP file. Is it a single solid part?") from e
    return found, part_mesh, name


def _read_drawing(file):
    """The drawing's bytes, or None when there isn't one. It goes to Claude only, never to a file path."""
    if file is None or not file.filename:
        return None
    if Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(422, "The drawing must be a .pdf file.")
    data = file.file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, f"Drawing is over {MAX_UPLOAD // 2**20} MB.")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(422, "That drawing isn't a PDF.")
    return data


def _job(found, part_mesh):
    return {**found, "mesh": part_mesh, "plan": None, "plan_errors": [],
            "gcode": None, "findings": [], "toolpath": [], "export_ok": False,
            "tool_numbers": TOOL_NUMBERS, "tool_info": TOOL_INFO,
            "cycle_seconds": None, "drawing": [], "drill_oversize": MACHINE["drill_oversize"]}


def _call_planner(fn, *args, client, drawing):
    try:
        return fn(*args, client=client, drawing=drawing)
    except (RuntimeError, anthropic.APIError) as e:
        raise HTTPException(502, f"The planner failed: {e}") from e
    except TypeError as e:
        # The SDK raises a bare TypeError when it finds no credentials; anything else is a real bug.
        if "authentication" not in str(e):
            raise
        raise HTTPException(503, "Claude API credentials aren't set up on the server. Set ANTHROPIC_API_KEY.") from e


def _finish(job, found, result, material, name):
    job.update(plan=result["plan"], plan_errors=result["errors"], drawing=drawing_callouts(result["plan"], MACHINE))
    if result["errors"]:
        return job
    gcode = generate(found, result["plan"], TOOLS, MATERIALS[material], MACHINE, name=Path(name).stem)
    trace = []
    job.update(gcode=gcode, findings=check(gcode, MACHINE, **context_for(found), trace=trace), toolpath=trace,
               cycle_seconds=round(sum(m["seconds"] for m in trace), 1))
    job["export_ok"] = not job["findings"]
    return job


if WEB.is_dir():  # built UI (cd web && npm run build); mounted last so the API routes win
    app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
