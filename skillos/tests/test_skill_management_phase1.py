"""Phase 1 tests for D-task self-management agent hardening."""

from __future__ import annotations

from datetime import UTC, datetime

from skillos.api.schemas import EvolutionCycleResponse, HealthReportResponse, SystemHealthResponse
from skillos.layers.feedback_evolution import HealthStatus, SkillHealthReport, SkillRepair
from skillos.layers.skill_management import SkillAuditorAgent, SkillBuilderAgent
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
