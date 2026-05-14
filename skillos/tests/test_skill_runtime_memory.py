from __future__ import annotations

from skillos.layers.skill_runtime.state_tracker import StateTracker


def test_runtime_memory_records_step_evidence():
    tracker = StateTracker("task-1")

    tracker.memory.goal = "finish task"
    tracker.memory.remember_step_start(
        "step-1",
        "skill-a",
        "Skill A",
        {"value": 1},
    )
    tracker.memory.remember_step_success(
        "step-1",
        "skill-a",
        {"ok": True},
    )
    tracker.memory.remember_failure(
        "step-2",
        "skill-b",
        "boom",
        "runtime_error",
    )

    summary = tracker.memory.to_summary()

    assert summary["task_id"] == "task-1"
    assert summary["goal"] == "finish task"
    assert summary["selected_skills"] == ["skill-a"]
    assert summary["step_count"] == 1
    assert summary["failure_count"] == 1
    assert summary["failed_skill_ids"] == ["skill-b"]
    assert tracker.memory.step_outputs["step-1"]["output"] == {"ok": True}


def test_runtime_memory_summary_includes_verification_and_reflection():
    tracker = StateTracker("task-1")
    tracker.memory.verification_summary = {
        "passed": False,
        "failure_type": "missing_skill",
        "recovery_route": "retrieve_alternative_skill",
    }
    tracker.memory.reflection_summary = {
        "root_cause": "missing runtime skill",
        "failed_skill_ids": ["skill-a"],
    }

    summary = tracker.memory.to_summary()

    assert summary["verification"]["failure_type"] == "missing_skill"
    assert summary["reflection"]["failed_skill_ids"] == ["skill-a"]
