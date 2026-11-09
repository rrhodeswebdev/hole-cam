"""The real API and built web page with Claude replaced, for driving the page in a browser (web/e2e/ui_check.mjs).

Every plan is the hand-checked plan for the mixed plate (fixtures/plans/mixed.json). A plan change pecks every G83
hole 2 mm at a time, so the page has a real revision to show.

Run: uv run uvicorn --app-dir scripts fake_planner_api:app --port 8765   (build the page first: cd web && npm run build)
"""

import copy
import json
from pathlib import Path
from types import SimpleNamespace

from hole_cam.api import anthropic_client, app

PLAN = json.loads((Path(__file__).parents[1] / "fixtures" / "plans" / "mixed.json").read_text())
SUMMARY = "Pecked the deep holes 2 mm at a time."


class FakeClient:
    """Answers client.beta.messages.create() the way the planner reads it (see FakeClient in tests/test_planner.py)."""

    def __init__(self):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kw):
        plan = copy.deepcopy(PLAN)
        content = kw["messages"][-1]["content"]
        text = content if isinstance(content, str) else content[-1]["text"]  # a drawing comes first, then the text
        if "The machinist asks:" in text:
            for op in plan["operations"]:
                if op.get("cycle") == "G83":
                    op["peck"] = 2
            plan["summary"] = SUMMARY
        return SimpleNamespace(stop_reason="end_turn", stop_details=None,
                               content=[SimpleNamespace(type="text", text=json.dumps(plan))])


app.dependency_overrides[anthropic_client] = FakeClient
