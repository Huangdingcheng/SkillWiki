"""Phase 1 tests for D-task self-management agent hardening."""

from __future__ import annotations

from datetime import UTC, datetime
import json

import pytest

from skillos.api.routes import evolution as evolution_routes
from skillos.api.schemas import EvolutionCycleResponse, HealthReportResponse, SystemHealthResponse
from skillos.layers.feedback_evolution import (
    EvolutionReport,
    HealthStatus,
    SkillHealthReport,
    SkillRepair,
    SystemHealthReport,
)
from skillos.layers.skill_management import (
    MaintenanceAction,
    SkillAuditorAgent,
    SkillBuilderAgent,
    SkillMaintainerAgent,
)
from skillos.models.skill_model import (
    MetaSkillCategory,
    Skill,
    SkillImplementation,
    SkillInterface,
    SkillType,
)


class FakeResponse:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeLLM:
    def __init__(self, content: str | None = None, *, should_raise: bool = False) -> None:
        self.content = content or "{}"
        self.should_raise = should_raise

    def chat(self, messages):  # noqa: ANN001
        if self.should_raise:
            raise RuntimeError("llm unavailable")
        return FakeResponse(self.content)


def test_builder_normalizes_invalid_llm_fields() -> None:
    llm = FakeLLM(
        """
        {
          "name": "123 Bad Name!!",
          "description": "",
          "skill_type": "invalid_type",
          "tags": [" Web UI ", "web-ui"],
          "input_schema": {"type": "array", "required": "username"},
          "output_schema": {},
          "prompt_template": "",
          "confidence": 2.5,
          "build_notes": ""
        }
        """
    )

    draft = SkillBuilderAgent(llm).build_from_task("click the submit button")

    assert draft.skill.name == "skill_123_bad_name"
    assert draft.skill.skill_type == SkillType.ATOMIC
    assert draft.confidence == 1.0
    assert draft.skill.interface.input_schema["type"] == "object"
    assert draft.skill.interface.input_schema["properties"] == {}
    assert draft.skill.interface.output_schema["type"] == "object"
    assert draft.skill.implementation is not None
    assert draft.skill.implementation.prompt_template
    assert "task" in draft.skill.tags


def test_builder_fallback_is_readable_and_structured() -> None:
    draft = SkillBuilderAgent(FakeLLM(should_raise=True)).build_from_task("download a report")

    assert draft.skill.name == "skill_from_task"
    assert "reusable workflow" in draft.skill.description
    assert draft.skill.interface.input_schema == {"type": "object", "properties": {}}
    assert draft.skill.interface.output_schema == {"type": "object", "properties": {}}
    assert draft.skill.implementation is not None
    assert draft.skill.implementation.prompt_template
    assert draft.confidence == 0.1


def test_builder_aligns_prompt_variables_with_input_schema() -> None:
    llm = FakeLLM(
        """
        {
          "name": "Search Docs",
          "description": "Search a document source for a user query.",
          "skill_type": "functional",
          "tags": ["search"],
          "input_schema": {
            "type": "object",
            "properties": {
              "source": {"type": "string"}
            },
            "required": ["source", "missing_field"]
          },
          "output_schema": {
            "type": "object",
            "properties": {
              "answer": {"type": "string"}
            }
          },
          "prompt_template": "Search {query} inside {{source}}.",
          "confidence": 0.8,
          "build_notes": "Reusable search workflow"
        }
        """
    )

    draft = SkillBuilderAgent(llm).build_from_task("search docs")
    input_schema = draft.skill.interface.input_schema

    assert draft.skill.name == "search_docs"
    assert input_schema["properties"]["source"]["type"] == "string"
    assert input_schema["properties"]["query"]["type"] == "string"
    assert "missing_field" not in input_schema["required"]


def test_builder_sets_meta_category_for_strategic_skill() -> None:
    llm = FakeLLM(
        """
        {
          "name": "plan repair",
          "description": "Plan a multi-step repair from a failure report.",
          "skill_type": "strategic",
          "tags": ["repair"],
          "input_schema": {
            "type": "object",
            "properties": {
              "failure_report": {"type": "string"}
            }
          },
          "output_schema": {
            "type": "object",
            "properties": {
              "repair_plan": {"type": "string"}
            }
          },
          "prompt_template": "Plan repair steps for {failure_report}.",
          "confidence": 0.9,
          "build_notes": "Strategic repair planning"
        }
        """
    )

    draft = SkillBuilderAgent(llm).build_from_task("plan repair")

    assert draft.skill.skill_type == SkillType.STRATEGIC
    assert draft.skill.meta_category == MetaSkillCategory.GENERATION


def test_auditor_fails_when_required_field_missing_from_properties() -> None:
    skill = Skill(
        name="login_user",
        description="Log in with credentials.",
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"username": {"type": "string"}},
                "required": ["username", "password"],
            },
            output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
        ),
        implementation=SkillImplementation(prompt_template="Login {username}."),
    )

    result = SkillAuditorAgent(FakeLLM(should_raise=True)).audit(skill)

    assert not result.passed
    assert not result.schema_ok
    assert any("password" in issue for issue in result.issues)
    assert result.audit_score < 1.0


def test_auditor_fails_when_prompt_variable_missing_from_schema() -> None:
    skill = Skill(
        name="login_user",
        description="Log in with credentials.",
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"username": {"type": "string"}},
                "required": ["username"],
            },
            output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
        ),
        implementation=SkillImplementation(prompt_template="Login {username} with {{otp_code}}."),
    )

    result = SkillAuditorAgent(FakeLLM(should_raise=True)).audit(skill)

    assert not result.passed
    assert not result.schema_ok
    assert any("otp_code" in issue for issue in result.issues)


def test_auditor_fails_dangerous_code_and_weights_score() -> None:
    skill = Skill(
        name="run_command",
        description="Run a shell command for an automation task.",
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
            output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
        ),
        implementation=SkillImplementation(code="import subprocess\nsubprocess.run(command)"),
    )

    result = SkillAuditorAgent(FakeLLM(should_raise=True)).audit(skill)

    assert not result.passed
    assert not result.safety_ok
    assert any("subprocess" in issue for issue in result.issues)
    assert result.audit_score <= 0.5


def test_auditor_passes_valid_prompt_skill_with_stable_score() -> None:
    skill = Skill(
        name="extract_title",
        description="Extract a readable title from a source document.",
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"document_text": {"type": "string"}},
                "required": ["document_text"],
            },
            output_schema={"type": "object", "properties": {"title": {"type": "string"}}},
        ),
        implementation=SkillImplementation(prompt_template="Extract one title from {document_text}."),
    )

    result = SkillAuditorAgent(FakeLLM(should_raise=True)).audit(skill)

    assert result.passed
    assert result.schema_ok
    assert result.safety_ok
    assert result.postcondition_ok
    assert result.audit_score >= 0.8


def _maintainer_source_skill(name: str = "process_report") -> Skill:
    return Skill(
        name=name,
        description="Process a report and produce a normalized result.",
        skill_type=SkillType.FUNCTIONAL,
        tags=["report", "workflow"],
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"report_text": {"type": "string"}},
                "required": ["report_text"],
            },
            output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
        ),
        implementation=SkillImplementation(prompt_template="Process {report_text}."),
    )


def test_maintainer_repair_fails_when_response_has_no_implementation() -> None:
    llm = FakeLLM(
        """
        {
          "repaired_prompt_template": "",
          "repaired_code": null,
          "repair_notes": "No usable fix",
          "confidence": 0.4
        }
        """
    )

    result = SkillMaintainerAgent(llm).repair(_maintainer_source_skill(), failure_info="bad output")

    assert result.action == MaintenanceAction.REPAIR
    assert not result.success
    assert "repaired_prompt_template or repaired_code" in result.reason
    assert result.details["confidence"] == 0.4


def test_maintainer_split_normalizes_children_and_skips_empty_items() -> None:
    llm = FakeLLM(
        """
        {
          "sub_skills": [
            {"name": "1. Extract!", "description": "", "prompt_template": ""},
            {},
            {"name": "Summarize Report", "description": "Summarize the report.", "prompt_template": "Summarize {report_text}."}
          ],
          "split_notes": "Split into extraction and summary steps",
          "confidence": 0.85
        }
        """
    )

    parent = _maintainer_source_skill()
    result = SkillMaintainerAgent(llm).split(parent, reason="too broad")

    assert result.action == MaintenanceAction.SPLIT
    assert result.success
    assert len(result.new_skills) == 2
    assert result.new_skills[0].name == "skill_1_extract"
    assert result.new_skills[0].skill_type == SkillType.ATOMIC
    assert result.new_skills[0].implementation is not None
    assert result.new_skills[0].implementation.prompt_template
    assert result.new_skills[0].provenance is not None
    assert result.new_skills[0].provenance.parent_skill_ids == [parent.skill_id]
    assert result.details["sub_skill_count"] == 2
    assert result.details["confidence"] == 0.85


def test_maintainer_merge_success_creates_new_skill_with_details() -> None:
    skill_a = _maintainer_source_skill("extract_report_facts")
    skill_b = _maintainer_source_skill("summarize_report_facts")
    llm = FakeLLM(
        """
        {
          "merged_name": "process_report_facts",
          "merged_description": "Extract and summarize reusable facts from a report.",
          "merged_type": "functional",
          "merged_tags": ["report", "facts"],
          "merged_interface": {
            "input_schema": {
              "type": "object",
              "properties": {"report_text": {"type": "string"}}
            },
            "output_schema": {
              "type": "object",
              "properties": {"facts": {"type": "array"}}
            },
            "preconditions": ["report_text is available"],
            "postconditions": ["facts are summarized"],
            "side_effects": []
          },
          "merged_implementation": {
            "language": "python",
            "prompt_template": "Extract and summarize facts from {report_text}."
          },
          "merge_rationale": "The two Skills overlap on report fact processing.",
          "confidence": 0.9
        }
        """
    )

    result = SkillMaintainerAgent(llm).merge(skill_a, skill_b, reason="overlap")

    assert result.action == MaintenanceAction.MERGE
    assert result.success
    assert result.updated_skill is not None
    assert result.updated_skill.name == "process_report_facts"
    assert result.updated_skill.provenance is not None
    assert result.updated_skill.provenance.parent_skill_ids == [skill_a.skill_id, skill_b.skill_id]
    assert result.details["source_skill_ids"] == [skill_a.skill_id, skill_b.skill_id]
    assert result.details["confidence"] == 0.9
    assert "overlap" in result.details["merge_rationale"]


def test_maintainer_merge_fails_on_invalid_or_empty_response() -> None:
    skill_a = _maintainer_source_skill("extract_report_facts")
    skill_b = _maintainer_source_skill("summarize_report_facts")

    invalid = SkillMaintainerAgent(FakeLLM("not json")).merge(skill_a, skill_b)
    assert not invalid.success
    assert "valid JSON" in invalid.reason

    empty_impl = SkillMaintainerAgent(FakeLLM('{"merged_name": "bad_merge"}')).merge(skill_a, skill_b)
    assert not empty_impl.success
    assert "usable merged implementation" in empty_impl.reason


def test_maintainer_deprecate_records_reason_and_replacement() -> None:
    skill = _maintainer_source_skill()

    result = SkillMaintainerAgent(FakeLLM()).deprecate(
        skill,
        reason="replaced by broader skill",
        replacement_skill_id="replacement-1",
    )

    assert result.action == MaintenanceAction.DEPRECATE
    assert result.success
    assert result.reason == "replaced by broader skill"
    assert result.details["reason"] == "replaced by broader skill"
    assert result.details["replacement_skill_id"] == "replacement-1"


@pytest.mark.asyncio
async def test_repair_returns_clear_failure_when_llm_fails() -> None:
    skill = Skill(
        name="unstable_skill",
        description="A degraded skill.",
        implementation=SkillImplementation(prompt_template="Do the task."),
    )
    health = SkillHealthReport(
        skill_id=skill.skill_id,
        skill_name=skill.name,
        status=HealthStatus.CRITICAL,
        success_rate=0.1,
        usage_count=12,
        avg_latency_ms=100.0,
        issues=["low success rate"],
    )

    result = await SkillRepair(FakeLLM(should_raise=True)).repair(skill, health)

    assert not result.success
    assert result.error.startswith("LLM repair call failed")
    assert result.root_cause == "repair_llm_unavailable"


def test_evolution_api_response_fields_remain_stable() -> None:
    health_fields = set(HealthReportResponse.model_fields)
    system_fields = set(SystemHealthResponse.model_fields)
    cycle_fields = set(EvolutionCycleResponse.model_fields)

    assert health_fields == {
        "skill_id",
        "skill_name",
        "status",
        "success_rate",
        "usage_count",
        "avg_latency_ms",
        "issues",
        "recommendations",
    }
    assert system_fields == {
        "total_skills",
        "healthy_count",
        "degraded_count",
        "critical_count",
        "stale_count",
        "health_ratio",
        "skill_reports",
    }
    assert cycle_fields == {
        "cycle_id",
        "started_at",
        "completed_at",
        "tasks_total",
        "tasks_completed",
        "tasks_failed",
        "repaired",
        "deprecated",
        "merged",
        "split",
        "errors",
    }

    EvolutionCycleResponse(
        cycle_id="cycle",
        started_at=datetime.now(UTC),
        completed_at=None,
        tasks_total=0,
        tasks_completed=0,
        tasks_failed=0,
        repaired=[],
        deprecated=[],
        merged=[],
        split=[],
        errors=[],
    )


def test_health_payload_is_json_serializable_and_stable() -> None:
    report = SkillHealthReport(
        skill_id="skill-1",
        skill_name="unstable_skill",
        status=HealthStatus.DEGRADED,
        success_rate=0.62,
        usage_count=12,
        avg_latency_ms=250.0,
        issues=["low success rate"],
    )

    payload = evolution_routes._health_payload(report)

    assert evolution_routes._health_event_name(report.status) == "health_degraded"
    assert payload["skill_id"] == "skill-1"
    assert payload["skill_name"] == "unstable_skill"
    assert payload["status"] == "degraded"
    assert payload["success_rate"] == 0.62
    assert payload["issues"] == ["low success rate"]
    assert payload["timestamp"].endswith("Z")
    json.dumps(payload)


def test_system_health_payload_summarizes_critical_skills() -> None:
    critical = SkillHealthReport(
        skill_id="critical-1",
        skill_name="critical_skill",
        status=HealthStatus.CRITICAL,
        success_rate=0.2,
        usage_count=10,
        avg_latency_ms=100.0,
        issues=["very low success rate"],
    )
    degraded = SkillHealthReport(
        skill_id="degraded-1",
        skill_name="degraded_skill",
        status=HealthStatus.DEGRADED,
        success_rate=0.65,
        usage_count=10,
        avg_latency_ms=100.0,
        issues=["low success rate"],
    )
    report = SystemHealthReport(
        total_skills=2,
        degraded_count=1,
        critical_count=1,
        skill_reports=[critical, degraded],
    )

    payload = evolution_routes._system_health_payload(report, HealthStatus.CRITICAL)

    assert payload["skill_id"] == "system"
    assert payload["status"] == "critical"
    assert payload["degraded_count"] == 1
    assert payload["critical_count"] == 1
    assert payload["affected_skills"] == [
        {
            "skill_id": "critical-1",
            "skill_name": "critical_skill",
            "success_rate": 0.2,
            "issues": ["very low success rate"],
        }
    ]
    json.dumps(payload)


def test_evolution_cycle_payload_uses_summary_counts() -> None:
    report = EvolutionReport(
        cycle_id="cycle-1",
        tasks_total=4,
        tasks_completed=3,
        tasks_failed=1,
        repaired=["repair-1"],
        deprecated=["old-1"],
        merged=[(["a", "b"], "merged-1")],
        split=[("large-1", ["small-1", "small-2"])],
        errors=["merge failed"],
    )

    payload = evolution_routes._cycle_payload(report)

    assert payload["cycle_id"] == "cycle-1"
    assert payload["tasks_total"] == 4
    assert payload["tasks_completed"] == 3
    assert payload["tasks_failed"] == 1
    assert payload["repaired"] == 1
    assert payload["deprecated"] == 1
    assert payload["merged"] == 1
    assert payload["split"] == 1
    assert payload["errors"] == ["merge failed"]
    assert payload["timestamp"].endswith("Z")
    json.dumps(payload)


@pytest.mark.asyncio
async def test_run_evolution_cycle_broadcasts_done_event(monkeypatch) -> None:
    events = []

    async def capture(event, payload):  # noqa: ANN001
        events.append((event, payload))

    class FakeEvolution:
        async def run_evolution_cycle(self) -> EvolutionReport:
            return EvolutionReport(
                cycle_id="cycle-1",
                tasks_total=1,
                tasks_completed=1,
                repaired=["repair-1"],
            )

    class FakeApp:
        evolution = FakeEvolution()

    monkeypatch.setattr(evolution_routes, "_safe_broadcast", capture)

    response = await evolution_routes.run_evolution_cycle(FakeApp())

    assert response.cycle_id == "cycle-1"
    assert response.tasks_total == 1
    assert events == [
        (
            "evolution_cycle_done",
            {
                "cycle_id": "cycle-1",
                "tasks_total": 1,
                "tasks_completed": 1,
                "tasks_failed": 0,
                "repaired": 1,
                "deprecated": 0,
                "merged": 0,
                "split": 0,
                "errors": [],
                "timestamp": events[0][1]["timestamp"],
            },
        )
    ]


@pytest.mark.asyncio
async def test_safe_broadcast_does_not_raise_when_websocket_fails(monkeypatch) -> None:
    async def failing_broadcast(event, payload):  # noqa: ANN001
        raise RuntimeError("websocket unavailable")

    monkeypatch.setattr(evolution_routes, "broadcast", failing_broadcast)

    await evolution_routes._safe_broadcast("health_critical", {"skill_id": "skill-1"})


@pytest.mark.asyncio
async def test_emit_health_event_broadcasts_degraded_and_critical(monkeypatch) -> None:
    events = []

    async def capture(event, payload):  # noqa: ANN001
        events.append((event, payload))

    monkeypatch.setattr(evolution_routes, "_safe_broadcast", capture)
    evolution_routes._last_health_event_at.clear()
    degraded = SkillHealthReport(
        skill_id="degraded-1",
        skill_name="degraded_skill",
        status=HealthStatus.DEGRADED,
        success_rate=0.66,
        usage_count=10,
        avg_latency_ms=100.0,
    )
    critical = SkillHealthReport(
        skill_id="critical-1",
        skill_name="critical_skill",
        status=HealthStatus.CRITICAL,
        success_rate=0.2,
        usage_count=10,
        avg_latency_ms=100.0,
    )

    await evolution_routes._emit_health_event(degraded)
    await evolution_routes._emit_health_event(critical)

    assert [event for event, _ in events] == ["health_degraded", "health_critical"]
    assert events[0][1]["skill_id"] == "degraded-1"
    assert events[1][1]["skill_id"] == "critical-1"


@pytest.mark.asyncio
async def test_emit_health_event_uses_short_cooldown(monkeypatch) -> None:
    events = []

    async def capture(event, payload):  # noqa: ANN001
        events.append((event, payload))

    monkeypatch.setattr(evolution_routes, "_safe_broadcast", capture)
    evolution_routes._last_health_event_at.clear()
    degraded = SkillHealthReport(
        skill_id="degraded-1",
        skill_name="degraded_skill",
        status=HealthStatus.DEGRADED,
        success_rate=0.66,
        usage_count=10,
        avg_latency_ms=100.0,
    )

    await evolution_routes._emit_health_event(degraded)
    await evolution_routes._emit_health_event(degraded)

    assert len(events) == 1


@pytest.mark.asyncio
async def test_emit_system_health_events_broadcasts_only_attention_states(monkeypatch) -> None:
    events = []

    async def capture(event, payload):  # noqa: ANN001
        events.append((event, payload))

    monkeypatch.setattr(evolution_routes, "_safe_broadcast", capture)
    evolution_routes._last_health_event_at.clear()
    report = SystemHealthReport(
        total_skills=2,
        degraded_count=1,
        critical_count=1,
        skill_reports=[
            SkillHealthReport(
                skill_id="degraded-1",
                skill_name="degraded_skill",
                status=HealthStatus.DEGRADED,
                success_rate=0.66,
                usage_count=10,
                avg_latency_ms=100.0,
            ),
            SkillHealthReport(
                skill_id="critical-1",
                skill_name="critical_skill",
                status=HealthStatus.CRITICAL,
                success_rate=0.2,
                usage_count=10,
                avg_latency_ms=100.0,
            ),
        ],
    )

    await evolution_routes._emit_system_health_events(report)

    assert [event for event, _ in events] == ["health_critical", "health_degraded"]
    assert events[0][1]["affected_skills"][0]["skill_id"] == "critical-1"
    assert events[1][1]["affected_skills"][0]["skill_id"] == "degraded-1"
