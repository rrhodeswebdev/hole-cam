# NIST test parts

Real CAD parts from NIST's MBE PMI test models: FTC-07 and FTC-08 are regression tests for hole finding
(`tests/test_holes.py`), and the three drawings are for trying the drawing upload. FTC-10's drawing has M3 threads,
so it exercises tapping. NIST says the test cases, CAD models and STEP files can be used without restriction.

- Source: [NIST: Download Free CAD Models, STEP Files, and Test Results](https://www.nist.gov/el/systems-integration-division-73400/mbe-pmi-validation-and-conformance-testing/download-free-cad), `NIST-PMI-STEP-Files.zip`, folder `AP203 geometry only`.
- The expected holes in the tests come from the parts' drawings in the same zip (`PDF/`), not from this app's output.

| File | Part | Units on the drawing |
|---|---|---|
| `nist_ftc_07_asme1_rd.stp` | FTC-07, an open box with counterbored holes on an inside boss | inches |
| `nist_ftc_08_asme1_rc.stp` | FTC-08, a flanged tray with a scalloped cutout and keyhole slots | inches |
| `nist_ftc_10_asme1_rb.stp` | FTC-10, a ribbed housing with a pocket, counterbores and M3 threads | millimetres |
| `nist_ftc_07_asme1_rd.pdf` | FTC-07's 4-page drawing, from the zip's `PDF/` folder | inches |
| `nist_ftc_08_asme1_rc.pdf` | FTC-08's drawing | inches |
| `nist_ftc_10_asme1_rb.pdf` | FTC-10's 5-page drawing | millimetres |
