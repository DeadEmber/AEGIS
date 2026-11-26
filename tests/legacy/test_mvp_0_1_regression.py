"""Characterisation test: the original MVP 0.1 behaviour, kept verbatim in legacy/mvp_0_1.

Runs the original unittest suite in a subprocess (its module is also named `aegis`, which would
clash with the new package). The new engine intentionally does NOT reproduce the 110.41 EUR demo
subtotal (see docs/AUDIT_MVP_0_1.md section 6).
"""
import subprocess
import sys
from pathlib import Path

LEGACY = Path(__file__).resolve().parents[2] / "legacy" / "mvp_0_1"


def test_legacy_suite_passes():
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "-v", "test_aegis"],
        cwd=LEGACY, capture_output=True, text=True, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Ran 2 tests" in proc.stderr
