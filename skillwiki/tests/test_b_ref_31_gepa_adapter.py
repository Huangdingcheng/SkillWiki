from __future__ import annotations

from skillwiki.layers.skill_management.gepa_adapter import GepaCandidateRecord, candidate_admission


def test_text_candidate_is_blocked_without_executable_package() -> None:
    status, reason = candidate_admission({"skill_text": "Parse dialogue into dialogue.json."})
    assert status == "blocked_candidate_not_executable"
    assert "implementation" in reason
    assert "manifest" in reason


def test_complete_candidate_is_admissible() -> None:
    status, reason = candidate_admission(
        {
            "implementation": "package/skill.py",
            "entrypoint": "package.skill:run",
            "manifest": {"files": ["package/skill.py"]},
            "modes": ["default"],
            "license_scope": "MIT",
        }
    )
    assert (status, reason) == ("admissible", "")


def test_candidate_record_captures_hash_and_lineage() -> None:
    record = GepaCandidateRecord(
        candidate_text="Parse dialogue into dialogue.json.",
        candidate_component="skill_text",
        input_failure_trace=[{"id": "val-dialogue-1", "failed": True}],
        budget={"max_metric_calls": 4, "retries": 0},
        lineage={"parent": "skill-1"},
        task_contract={"task": "dialogue-parser"},
        environment_identity={"python": "3.12"},
    )
    payload = record.to_dict()
    assert payload["candidate_hash"].startswith("sha256:")
    assert payload["lineage"] == {"parent": "skill-1"}
    assert payload["input_failure_trace"][0]["failed"] is True
