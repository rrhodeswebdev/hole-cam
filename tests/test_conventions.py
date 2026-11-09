"""Repo conventions no linter rule covers."""

import ast
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "hole_cam"


def private_imports(source):
    """`from hole_cam.x import _name` imports in a module's source, as "module._name"."""
    return [f"{node.module}.{alias.name}" for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("hole_cam")
            for alias in node.names if alias.name.startswith("_")]


def test_package_modules_use_only_each_others_public_names():
    # ruff's PLC2701 covers scripts/ and fixtures/ but skips imports inside the package.
    found = {p.name: private_imports(p.read_text()) for p in SRC.glob("*.py")}
    assert {name: names for name, names in found.items() if names} == {}


def test_private_imports_are_found():
    assert private_imports("from hole_cam.gcode import _tip_depth, generate\nimport json\n") == ["hole_cam.gcode._tip_depth"]
