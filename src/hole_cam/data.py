"""The shop's reference data in data/: the tool library, the material table and the machine's limits."""

import json
from pathlib import Path

DATA = Path(__file__).parents[2] / "data"


def load_data(name):
    """One data file, e.g. load_data("tools.json")."""
    return json.loads((DATA / name).read_text())
