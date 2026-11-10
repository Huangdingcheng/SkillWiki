from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RESULT = ROOT / "artifacts/20260929-skill-provenance-b-ref-21/result.json"


def test_b_ref_21_real_run_keeps_six_states_separate() -> None:
    assert RESULT.is_file(), "run research/20260929/B-REF-21-official-integration.py first"
    payload = json.loads(RESULT.read_text(encoding="utf-8"))
    assert payload["component"]["commit"] == "bcddbd2cd8902ae14e2dcaa328d59fd453be5d16"
    assert payload["component"]["version"] == "7.0.0"
    assert payload["component"]["license"] == "MIT"
    assert payload["state"] == {
        "checked": True,
        "downloaded": True,
        "integrated": True,
        "successfully_run": True,
        "fairly_compared": False,
        "paper_eligible": False,
    }
    assert all(case["expectation_met"] for case in payload["cases"])
    assert payload["package_route"]["run"]["exit_code"] == 0

