#!/usr/bin/env python3
"""
==============================================================================
lynx_test_sample.py  -  rehearsal / calibration run on a TEST sample
==============================================================================
Same hardware and same code as lynx_measure.py (it imports it), but with
test defaults:
    - label starts with TEST_ so test data never mixes with real data
    - short preset (300 s)
    - prints an alpha-energy CALIBRATION HINT using the Am-241 line (5485.56 keV)

Use it to:
    1. prove that both Lynx boxes, sync and file output work, BEFORE the real sample
    2. calibrate the alpha detector with the Am-241 source
    3. see whether the dt peak (real coincidences) appears

Both files must stay in the same folder.

Fake run (no hardware):
    python lynx_test_sample.py
REAL test, sync cable connected, supervisor has bias on:
    python lynx_test_sample.py --real --sync
Longer or named:
    python lynx_test_sample.py --real --sync --preset 600 --label Am241_alpha
Without the source, with a test sample in the beam, just rename:
    python lynx_test_sample.py --real --sync --label W_tile_test

Everything else (--probe, --hours, --from-csv, ...) works exactly as in
lynx_measure.py.
==============================================================================
"""

import lynx_measure as lm

TEST_PROFILE = {
    "label": "sample",           # becomes TEST_sample
    "preset": 300.0,             # seconds
    "note": "TEST run - not final data",
    "cal_kev": 5485.56,          # Am-241 main alpha line
    "test": True,
}

if __name__ == "__main__":
    raise SystemExit(lm.main(profile=TEST_PROFILE))
