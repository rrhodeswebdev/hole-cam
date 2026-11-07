"""Build the G-code check set (SPEC.md 5): clean programs plus copies with exactly one planted problem.

Run: uv run python fixtures/make_bad_gcode.py
File names are <part>__<expected check>__<n>.nc, or <part>__clean.nc. The part name gives the checker its context.
"""

import json
from pathlib import Path

from hole_cam.gcode import _load, generate
from hole_cam.holes import find_holes

FIX = Path(__file__).parent
OUT = FIX / "gcode"

# (check, old text, new text): replaces the first occurrence in the clean mixed-part program.
MIXED_BUGS = [
    ("rapid-into-part", "G00 X15. Y15.\n", "G00 X15. Y15. Z-2.\n"),                       # rapid sideways inside the part
    ("rapid-into-part", "R2. F119.", "R-1. F119."),                                     # R plane below the top
    ("rapid-into-part", "G43 H1 Z25.", "G43 H1 Z-3."),                                  # clearance height typed wrong
    ("rapid-into-part", "G00 X65. Y45.\n", "G00 X65. Y45.\nG00 Z-5.\n"),                # rapid plunge
    ("spindle-off-cut", "S2387 M03", "S2387"),                                          # forgot M03
    ("spindle-off-cut", "M08\nG00 X35. Y45.", "M08\nM05\nG00 X35. Y45."),               # spindle stopped before drilling
    ("spindle-off-cut", "X85. Y15. Z-1.5 R2.\nG80", "M05\nX85. Y15. Z-1.5 R2.\nG80"),   # stopped mid-cycle
    ("no-tool-length-offset", "G43 H6 Z25. S4775 M03", "Z25. S4775 M03"),              # G43 left out
    ("no-tool-length-offset", "G43 H8", "G43 H6"),                                      # offset for the wrong tool
    ("no-tool-length-offset", "M08\n", "M08\nG49\n"),                                   # offset cancelled
    ("over-machine-limit", "S2387", "S12000"),
    ("over-machine-limit", "F632.", "F12000."),
    ("over-machine-limit", "F573.", "F15000."),
    ("too-deep", "Z-28.043 R2. Q6.8", "Z-35. R2. Q6.8"),
    ("too-deep", "Z-21.502", "Z-31.502"),
    ("unsafe-end", "M30\n", ""),                                                        # no program end
    ("unsafe-end", "G80\nM09", "M09"),                                                  # cycle left on at G28 / tool change
]
# The boss stands 10 mm above the plate, so going between holes at the plate's R plane (G99) hits it.
BOSS_BUGS = [("rapid-into-part", "G98 G81 X10. Y10. Z-11.5", "G99 G81 X10. Y10. Z-11.5")]


def program(part):
    found = find_holes(FIX / "parts" / f"{part}.step")
    plan = json.loads((FIX / "plans" / f"{part}.json").read_text())
    return generate(found, plan, _load("tools.json"), _load("materials.json")["6061"], _load("machine.json"), name=part)


def main():
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob("*.nc"):
        old.unlink()
    for part, bugs in [("mixed", MIXED_BUGS), ("boss", BOSS_BUGS)]:
        clean = program(part)
        (OUT / f"{part}__clean.nc").write_text(clean)
        counts = {}
        for kind, old, new in bugs:
            assert old in clean, f"{part}: {old!r} not in program"
            counts[kind] = counts.get(kind, 0) + 1
            (OUT / f"{part}__{kind}__{counts[kind]}.nc").write_text(clean.replace(old, new, 1))
    print(f"{len(list(OUT.glob('*.nc')))} programs in {OUT}")


if __name__ == "__main__":
    main()
