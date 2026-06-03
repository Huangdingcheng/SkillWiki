"""Phase 1 tests for D-task self-management agent hardening."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from skillos.api.deps import get_app_state
from skillos.api.main import _seed_demo_skills, create_app
from skillos.api.memory_store import MemoryGraphManager, MemoryWikiManager
from skillos.api.routes import evolution as evolution_routes
from skillos.api.routes import lifecycle as lifecycle_routes
from skillos.api.schemas import (
    EvolutionCycleResponse,
    HealthReportResponse,
    MaintenanceProposalListResponse,
    SystemHealthResponse,
)
from skillos.layers.feedback_evolution import (
    EvolutionAction,
    EvolutionEngine,
    EvolutionReport,
    EvolutionTask,
    HealthStatus,
    RepairResult,
    SkillHealthReport,
    SkillMonitor,
    SkillRepair,
    SystemHealthReport,
)
from skillos.layers.skill_management import (
    MaintenanceAction,
    SkillAuditorAgent,
    SkillBuilderAgent,
    SkillMaintainerAgent,
)
from skillos.layers.skill_governance import SkillMerger
from skillos.models.skill_model import (
    EdgeType,
    MetaSkillCategory,
    Skill,
    SkillEvaluation,
    SkillImplementation,
    SkillInterface,
    SkillProvenance,
    SkillState,
    SkillType,
)
from skillos.models.maintenance_model import (
    MaintenanceProposal,
    MaintenanceProposalStatus,
    MaintenanceValidationStatus,
    MaintenanceRecommendedAction,
    MaintenanceTrigger,
    ReflectionMemoryStatus,
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


@pytest.fixture(autouse=True)
def clear_evolution_proposal_queue():
    original_proposal_path = evolution_routes._proposal_store_path
    original_reflection_path = evolution_routes._reflection_store_path
    evolution_routes._proposal_queue.clear()
    evolution_routes._reflection_memory.clear()
    evolution_routes._proposal_store_path = None
    evolution_routes._reflection_store_path = None
    yield
    evolution_routes._proposal_queue.clear()
    evolution_routes._reflection_memory.clear()
    evolution_routes._proposal_store_path = original_proposal_path
    evolution_routes._reflection_store_path = original_reflection_path


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


def test_auditor_warns_for_missing_provenance_without_failing_draft() -> None:
    skill = Skill(
        name="extract_summary",
        description="Extract a concise summary from a source document.",
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"document_text": {"type": "string"}},
                "required": ["document_text"],
            },
            output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        ),
        implementation=SkillImplementation(prompt_template="Summarize {document_text}."),
    )

    result = SkillAuditorAgent(FakeLLM(should_raise=True)).audit(skill)

    assert result.passed
    assert any("provenance" in warning for warning in result.warnings)
    assert any("verifier placeholder" in warning for warning in result.warnings)


def test_auditor_fails_skill_missing_implementation() -> None:
    skill = Skill(
        name="missing_implementation",
        description="Invalid skill with schema but no executable implementation.",
        interface=SkillInterface(
            input_schema={"type": "object", "properties": {"document_text": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        ),
        provenance=SkillProvenance(source_type="manual", created_by_agent="test"),
    )

    result = SkillAuditorAgent(FakeLLM(should_raise=True)).audit(skill)

    assert not result.passed
    assert not result.postcondition_ok
    assert any("implementation is missing" in issue for issue in result.issues)


def test_auditor_fails_released_skill_without_verification_contract() -> None:
    skill = Skill(
        name="release_without_verifier",
        description="Released skill missing postconditions and evaluation evidence.",
        state=SkillState.RELEASED,
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"document_text": {"type": "string"}},
                "required": ["document_text"],
            },
            output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        ),
        implementation=SkillImplementation(prompt_template="Summarize {document_text}."),
        provenance=SkillProvenance(source_type="manual", created_by_agent="test"),
    )

    result = SkillAuditorAgent(FakeLLM(should_raise=True)).audit(skill)

    assert not result.passed
    assert not result.postcondition_ok
    assert any("verified/released Skill" in issue for issue in result.issues)


def test_auditor_rejects_summary_only_validation_for_trusted_skill() -> None:
    skill = Skill(
        name="release_with_summary_only",
        description="Released skill with only a narrative validation summary.",
        state=SkillState.RELEASED,
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"document_text": {"type": "string"}},
                "required": ["document_text"],
            },
            output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        ),
        implementation=SkillImplementation(prompt_template="Summarize {document_text}."),
        evaluation=SkillEvaluation(validation_summary="manual smoke check passed"),
        provenance=SkillProvenance(source_type="manual", created_by_agent="test"),
    )

    result = SkillAuditorAgent(FakeLLM(should_raise=True)).audit(skill)

    assert not result.passed
    assert not result.postcondition_ok
    assert any("verified/released Skill" in issue for issue in result.issues)


def test_auditor_rejects_placeholder_only_verifier_for_trusted_skill() -> None:
    skill = Skill(
        name="release_with_placeholder_verifier",
        description="Released skill with only a placeholder verifier.",
        state=SkillState.RELEASED,
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"document_text": {"type": "string"}},
                "required": ["document_text"],
            },
            output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        ),
        implementation=SkillImplementation(prompt_template="Summarize {document_text}."),
        evaluation=SkillEvaluation(verifier_specs=[{"type": "placeholder"}]),
        provenance=SkillProvenance(source_type="manual", created_by_agent="test"),
    )

    result = SkillAuditorAgent(FakeLLM(should_raise=True)).audit(skill)

    assert not result.passed
    assert not result.postcondition_ok
    assert any("verified/released Skill" in issue for issue in result.issues)


def test_auditor_fails_trusted_skill_missing_provenance() -> None:
    skill = Skill(
        name="release_without_provenance",
        description="Released skill missing provenance traceability.",
        state=SkillState.RELEASED,
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"document_text": {"type": "string"}},
                "required": ["document_text"],
            },
            output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        ),
        implementation=SkillImplementation(prompt_template="Summarize {document_text}."),
        evaluation=SkillEvaluation(verifier_specs=[{"type": "json_exists", "path": "output.summary"}]),
    )

    result = SkillAuditorAgent(FakeLLM(should_raise=True)).audit(skill)

    assert not result.passed
    assert not result.schema_ok
    assert any("provenance is missing" in issue for issue in result.issues)


def test_auditor_fails_verified_skill_without_verification_contract() -> None:
    skill = Skill(
        name="verified_without_verifier",
        description="Verified skill missing postconditions and evaluation evidence.",
        state=SkillState.VERIFIED,
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"document_text": {"type": "string"}},
                "required": ["document_text"],
            },
            output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        ),
        implementation=SkillImplementation(prompt_template="Summarize {document_text}."),
        provenance=SkillProvenance(source_type="manual", created_by_agent="test"),
    )

    result = SkillAuditorAgent(FakeLLM(should_raise=True)).audit(skill)

    assert not result.passed
    assert not result.postcondition_ok
    assert any("verified/released Skill" in issue for issue in result.issues)


def test_auditor_accepts_released_skill_with_v02_evaluation_evidence() -> None:
    skill = Skill(
        name="release_with_verifier",
        description="Released skill with deterministic evaluation evidence.",
        state=SkillState.RELEASED,
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"document_text": {"type": "string"}},
                "required": ["document_text"],
            },
            output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        ),
        implementation=SkillImplementation(prompt_template="Summarize {document_text}."),
        evaluation=SkillEvaluation(verifier_specs=[{"type": "json_exists", "path": "output.summary"}]),
        provenance=SkillProvenance(source_type="manual", created_by_agent="test"),
    )

    result = SkillAuditorAgent(FakeLLM(should_raise=True)).audit(skill)

    assert result.passed
    assert not result.issues
    assert not result.warnings


def test_auditor_accepts_verified_skill_with_postcondition_contract() -> None:
    skill = Skill(
        name="verified_with_postcondition",
        description="Verified skill with an explicit postcondition contract.",
        state=SkillState.VERIFIED,
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"document_text": {"type": "string"}},
                "required": ["document_text"],
            },
            output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
            postconditions=["output.summary exists"],
        ),
        implementation=SkillImplementation(prompt_template="Summarize {document_text}."),
        provenance=SkillProvenance(source_type="manual", created_by_agent="test"),
    )

    result = SkillAuditorAgent(FakeLLM(should_raise=True)).audit(skill)

    assert result.passed
    assert result.postcondition_ok
    assert not result.issues
    assert not result.warnings


def test_auditor_fails_atomic_skill_with_only_subskills() -> None:
    skill = Skill(
        name="atomic_composition",
        description="Invalid atomic skill that only references child skills.",
        skill_type=SkillType.ATOMIC,
        interface=SkillInterface(
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        ),
        implementation=SkillImplementation(sub_skill_ids=["child_skill"]),
        provenance=SkillProvenance(source_type="manual", created_by_agent="test"),
    )

    result = SkillAuditorAgent(FakeLLM(should_raise=True)).audit(skill)

    assert not result.passed
    assert any("atomic Skill" in issue for issue in result.issues)


def test_auditor_fails_functional_skill_without_subskills_or_workflow_prompt() -> None:
    skill = Skill(
        name="functional_code_only",
        description="Invalid functional skill that lacks composition or workflow prompt.",
        skill_type=SkillType.FUNCTIONAL,
        interface=SkillInterface(
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        ),
        implementation=SkillImplementation(code="output['ok'] = True"),
        provenance=SkillProvenance(source_type="manual", created_by_agent="test"),
    )

    result = SkillAuditorAgent(FakeLLM(should_raise=True)).audit(skill)

    assert not result.passed
    assert any("functional Skill" in issue for issue in result.issues)


def test_auditor_fails_strategic_skill_without_meta_category() -> None:
    skill = Skill(
        name="strategic_without_category",
        description="Invalid strategic skill without a routing meta category.",
        skill_type=SkillType.ATOMIC,
        interface=SkillInterface(
            input_schema={"type": "object", "properties": {"goal": {"type": "string"}}},
            output_schema={"type": "object", "properties": {"plan": {"type": "string"}}},
        ),
        implementation=SkillImplementation(prompt_template="Plan how to achieve {goal}."),
        provenance=SkillProvenance(source_type="manual", created_by_agent="test"),
    )
    object.__setattr__(skill, "skill_type", SkillType.STRATEGIC)
    object.__setattr__(skill, "meta_category", None)

    result = SkillAuditorAgent(FakeLLM(should_raise=True)).audit(skill)

    assert not result.passed
    assert any("strategic Skill" in issue for issue in result.issues)


@pytest.mark.asyncio
async def test_lifecycle_release_blocks_skill_without_verification_contract() -> None:
    wiki = MemoryWikiManager()
    skill = Skill(
        name="release_api_without_verifier",
        description="Draft skill that should not be released without verifier evidence.",
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"document_text": {"type": "string"}},
                "required": ["document_text"],
            },
            output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        ),
        implementation=SkillImplementation(prompt_template="Summarize {document_text}."),
        provenance=SkillProvenance(source_type="manual", created_by_agent="test"),
    )
    await wiki.create(skill)

    app = FastAPI()
    app.include_router(lifecycle_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_app_state] = lambda: SimpleNamespace(
        wiki=wiki,
        auditor=SkillAuditorAgent(FakeLLM(should_raise=True)),
    )

    with TestClient(app) as client:
        response = client.post(f"/api/v1/lifecycle/{skill.skill_id}/release", json={})

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["message"] == "Skill failed release audit"
    assert any("verified/released Skill" in issue for issue in detail["issues"])
    stored = await wiki.get(skill.skill_id)
    assert stored is not None
    assert stored.state == SkillState.DRAFT


@pytest.mark.asyncio
async def test_lifecycle_transition_to_verified_blocks_missing_contract() -> None:
    wiki = MemoryWikiManager()
    skill = Skill(
        name="transition_api_without_verifier",
        description="Draft skill that should not enter S3 without verifier evidence.",
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"document_text": {"type": "string"}},
                "required": ["document_text"],
            },
            output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        ),
        implementation=SkillImplementation(prompt_template="Summarize {document_text}."),
        provenance=SkillProvenance(source_type="manual", created_by_agent="test"),
    )
    await wiki.create(skill)

    app = FastAPI()
    app.include_router(lifecycle_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_app_state] = lambda: SimpleNamespace(
        wiki=wiki,
        auditor=SkillAuditorAgent(FakeLLM(should_raise=True)),
    )

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/lifecycle/{skill.skill_id}/transition",
            json={"new_state": SkillState.VERIFIED.value},
        )

    assert response.status_code == 400
    assert any("verified/released Skill" in issue for issue in response.json()["detail"]["issues"])
    stored = await wiki.get(skill.skill_id)
    assert stored is not None
    assert stored.state == SkillState.DRAFT


@pytest.mark.asyncio
async def test_lifecycle_release_accepts_skill_with_deterministic_evidence() -> None:
    wiki = MemoryWikiManager()
    skill = Skill(
        name="release_api_with_verifier",
        description="Draft skill with deterministic verifier evidence.",
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"document_text": {"type": "string"}},
                "required": ["document_text"],
            },
            output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        ),
        implementation=SkillImplementation(prompt_template="Summarize {document_text}."),
        evaluation=SkillEvaluation(verifier_specs=[{"type": "json_exists", "path": "output.summary"}]),
        provenance=SkillProvenance(source_type="manual", created_by_agent="test"),
    )
    await wiki.create(skill)

    app = FastAPI()
    app.include_router(lifecycle_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_app_state] = lambda: SimpleNamespace(
        wiki=wiki,
        auditor=SkillAuditorAgent(FakeLLM(should_raise=True)),
    )

    with TestClient(app) as client:
        response = client.post(f"/api/v1/lifecycle/{skill.skill_id}/release", json={})

    assert response.status_code == 200
    stored = await wiki.get(skill.skill_id)
    assert stored is not None
    assert stored.state == SkillState.RELEASED


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
async def test_skill_merger_merge_creates_replaces_and_similarity_edges() -> None:
    skill_a = _maintainer_source_skill("extract_report_facts")
    skill_b = _maintainer_source_skill("summarize_report_facts")
    llm = FakeLLM(
        json.dumps(
            {
                "merged_name": "process_report_facts",
                "merged_description": "Process report facts across extraction and summary.",
                "merged_type": "functional",
                "merged_domain": "general",
                "merged_granularity_level": 2,
                "merged_tags": ["report", "facts"],
                "merged_interface": {
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "object"},
                },
                "merged_implementation": {
                    "language": "python",
                    "prompt_template": "Extract and summarize facts from {report_text}.",
                },
                "merge_rationale": "The two skills overlap on reusable report fact processing.",
                "confidence": 0.91,
            }
        )
    )

    result = await SkillMerger(llm).merge(skill_a, skill_b)

    assert result.success
    assert result.merged_skill is not None
    assert result.merged_skill.implementation is not None
    assert result.merged_skill.implementation.prompt_template is not None
    edges = {(edge.source_id, edge.target_id, edge.edge_type) for edge in result.edges_to_create}
    assert (result.merged_skill.skill_id, skill_a.skill_id, EdgeType.REPLACES) in edges
    assert (result.merged_skill.skill_id, skill_b.skill_id, EdgeType.REPLACES) in edges
    assert (skill_a.skill_id, skill_b.skill_id, EdgeType.SIMILAR_TO) in edges
    similarity = next(edge for edge in result.edges_to_create if edge.edge_type == EdgeType.SIMILAR_TO)
    assert similarity.weight == pytest.approx(0.91)
    assert similarity.metadata["maintenance_action"] == "merge"


@pytest.mark.asyncio
async def test_skill_merger_split_creates_composition_edges_for_children() -> None:
    parent = _maintainer_source_skill("process_report")
    llm = FakeLLM(
        json.dumps(
            {
                "sub_skills": [
                    {
                        "name": "extract_report_facts",
                        "description": "Extract reusable facts from a report.",
                        "skill_type": "atomic",
                        "granularity_level": 1,
                        "interface": {"input_schema": {"type": "object"}, "output_schema": {"type": "object"}},
                        "implementation": {"prompt_template": "Extract facts from {report_text}."},
                    },
                    {
                        "name": "summarize_report_facts",
                        "description": "Summarize extracted report facts.",
                        "skill_type": "atomic",
                        "granularity_level": 1,
                        "interface": {"input_schema": {"type": "object"}, "output_schema": {"type": "object"}},
                        "implementation": {"prompt_template": "Summarize facts from {facts}."},
                    },
                ],
                "split_rationale": "Separate extraction and summarization into reusable atomic skills.",
                "composition_order": ["extract_report_facts", "summarize_report_facts"],
            }
        )
    )

    result = await SkillMerger(llm).split(parent)

    assert result.success
    assert len(result.sub_skills) == 2
    edges = {(edge.source_id, edge.target_id, edge.edge_type) for edge in result.edges_to_create}
    for child in result.sub_skills:
        assert (child.skill_id, parent.skill_id, EdgeType.EVOLVED_FROM) in edges
        assert (parent.skill_id, child.skill_id, EdgeType.COMPOSES_WITH) in edges
    composition_edges = [edge for edge in result.edges_to_create if edge.edge_type == EdgeType.COMPOSES_WITH]
    assert [edge.metadata["composition_order"] for edge in composition_edges] == [0, 1]


@pytest.mark.asyncio
async def test_evolution_deprecate_task_records_replacement_edge() -> None:
    wiki = MemoryWikiManager()
    graph = MemoryGraphManager()
    old_skill = _maintainer_source_skill("old_process_report")
    replacement = _maintainer_source_skill("replacement_process_report")
    old_skill.transition_to(SkillState.VERIFIED)
    old_skill.transition_to(SkillState.RELEASED)
    await wiki.create(old_skill)
    await wiki.create(replacement)
    await graph.sync_skill(old_skill)
    await graph.sync_skill(replacement)

    engine = EvolutionEngine(
        monitor=SkillMonitor(),
        repair=SimpleNamespace(),
        merger=None,
        wiki_manager=wiki,
        graph_manager=graph,
    )
    report = EvolutionReport(cycle_id="cycle-deprecate")
    task = EvolutionTask(
        task_id="task-deprecate",
        action=EvolutionAction.DEPRECATE,
        skill_ids=[old_skill.skill_id, replacement.skill_id],
        reason="superseded by broader validated skill",
    )

    await engine._do_deprecate(task, report)

    stored = await wiki.get(old_skill.skill_id)
    assert stored is not None
    assert stored.state == SkillState.DEPRECATED
    assert stored.replacement_skill_id == replacement.skill_id
    subgraph = await graph.get_subgraph([old_skill.skill_id, replacement.skill_id], depth=1)
    assert any(
        edge.source_id == replacement.skill_id
        and edge.target_id == old_skill.skill_id
        and edge.edge_type == EdgeType.REPLACES
        for edge in subgraph.edges
    )
    assert task.result == {
        "replacement_skill_id": replacement.skill_id,
        "graph_edge_created": True,
    }


def test_maintenance_proposal_serializes_and_tracks_review_status() -> None:
    proposal = MaintenanceProposal(
        skill_id="skill-1",
        trigger=MaintenanceTrigger.VERIFIER_FAILED,
        recommended_action=MaintenanceRecommendedAction.REPAIR,
        evidence=["json path output.success was false"],
        root_cause="json path output.success was false",
        patch_hint="Repair postcondition handling.",
        feedback_sources=["deterministic_verifier"],
        targets_to_fix=["json path output.success was false"],
        invariants_to_preserve=["existing interface"],
        validation_plan=["rerun verifier"],
        validation_status=MaintenanceValidationStatus.UNTESTED,
        attempt_count=1,
        max_attempts=3,
        reviewer_notes="needs human review",
        confidence=0.82,
    )

    payload = proposal.model_dump(mode="json")

    assert payload["status"] == "pending"
    assert payload["requires_human_review"] is True
    assert payload["root_cause"] == "json path output.success was false"
    assert payload["feedback_sources"] == ["deterministic_verifier"]
    assert payload["targets_to_fix"] == ["json path output.success was false"]
    assert payload["invariants_to_preserve"] == ["existing interface"]
    assert payload["validation_plan"] == ["rerun verifier"]
    assert payload["validation_status"] == "untested"
    assert payload["attempt_count"] == 1
    assert payload["max_attempts"] == 3
    assert payload["reviewer_notes"] == "needs human review"
    json.dumps(payload)

    proposal.record_attempt()
    assert proposal.attempt_count == 2

    proposal.accept()
    assert proposal.status == MaintenanceProposalStatus.ACCEPTED


def test_verifier_failure_can_create_repair_proposal() -> None:
    proposal = MaintenanceProposal.from_verifier_failure(
        skill_id="skill-a",
        issues=["Path not found: output.final_state.submitted"],
        suggestions=["Add a submitted flag to the final state."],
    )

    assert proposal.trigger == MaintenanceTrigger.VERIFIER_FAILED
    assert proposal.recommended_action == MaintenanceRecommendedAction.REPAIR
    assert proposal.source == "runtime_verifier"
    assert proposal.evidence == ["Path not found: output.final_state.submitted"]
    assert proposal.root_cause == "Path not found: output.final_state.submitted"
    assert "submitted flag" in proposal.patch_hint
    assert proposal.feedback_sources == ["deterministic_verifier"]
    assert proposal.targets_to_fix == ["Path not found: output.final_state.submitted"]
    assert proposal.invariants_to_preserve
    assert proposal.validation_status == MaintenanceValidationStatus.UNTESTED
    assert any("verifier" in step for step in proposal.validation_plan)
    assert proposal.requires_human_review is True


def test_monitor_low_success_rate_generates_repair_proposal() -> None:
    skill = _maintainer_source_skill("unstable_report_processor")
    for index in range(6):
        skill.record_execution(success=index == 0, latency_ms=100.0)

    proposal = SkillMonitor().propose_maintenance(skill)

    assert proposal is not None
    assert proposal.skill_id == skill.skill_id
    assert proposal.trigger == MaintenanceTrigger.LOW_SUCCESS_RATE
    assert proposal.recommended_action == MaintenanceRecommendedAction.REPAIR
    assert proposal.root_cause
    assert proposal.feedback_sources == ["health_monitor"]
    assert proposal.targets_to_fix
    assert proposal.invariants_to_preserve
    assert proposal.validation_plan
    assert proposal.metadata["health_status"] == "critical"
    assert proposal.confidence >= 0.4


@pytest.mark.asyncio
async def test_demo_seed_includes_degraded_skill_with_repair_proposal() -> None:
    wiki = MemoryWikiManager()

    await _seed_demo_skills(wiki)
    skill = await wiki.get("demo_degraded_submit_form")

    assert skill is not None
    assert skill.state == SkillState.DEGRADED
    report = SkillMonitor().evaluate_skill(skill)
    assert report.status == HealthStatus.CRITICAL
    response = evolution_routes._health_response(report)
    assert response.maintenance_proposal is not None
    assert response.maintenance_proposal.recommended_action == MaintenanceRecommendedAction.REPAIR
    assert response.maintenance_proposal.requires_human_review is True


@pytest.mark.asyncio
async def test_health_api_returns_seeded_degraded_proposal_for_frontend() -> None:
    wiki = MemoryWikiManager()
    await _seed_demo_skills(wiki)

    app = FastAPI()
    app.include_router(evolution_routes.router, prefix="/api/v1")
    app.dependency_overrides[get_app_state] = lambda: SimpleNamespace(
        wiki=wiki,
        monitor=SkillMonitor(),
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/evolution/health/demo_degraded_submit_form")

    assert response.status_code == 200
    payload = response.json()
    assert payload["skill_id"] == "demo_degraded_submit_form"
    assert payload["status"] in {HealthStatus.DEGRADED.value, HealthStatus.CRITICAL.value}
    proposal = payload["maintenance_proposal"]
    assert proposal is not None
    assert proposal["skill_id"] == "demo_degraded_submit_form"
    assert proposal["recommended_action"] == MaintenanceRecommendedAction.REPAIR.value
    assert proposal["requires_human_review"] is True
    assert proposal["evidence"]
    assert proposal["patch_hint"]


def test_maintainer_failed_repair_result_carries_proposal() -> None:
    result = SkillMaintainerAgent(FakeLLM("not json")).repair(
        _maintainer_source_skill(),
        failure_info="deterministic verifier failed",
    )

    assert not result.success
    assert result.proposal is not None
    assert result.proposal.trigger == MaintenanceTrigger.RUNTIME_FAILURE
    assert result.proposal.recommended_action == MaintenanceRecommendedAction.REPAIR
    assert result.proposal.evidence == ["deterministic verifier failed"]
    assert result.proposal.root_cause == "deterministic verifier failed"
    assert "runtime_failure" in result.proposal.feedback_sources
    assert result.proposal.targets_to_fix == ["deterministic verifier failed"]
    assert result.proposal.invariants_to_preserve
    assert result.proposal.validation_plan


def test_maintainer_successful_repair_returns_review_proposal_not_live_update() -> None:
    result = SkillMaintainerAgent(
        FakeLLM(
            """
            {
              "repaired_prompt_template": "Process {report_text} with stricter validation.",
              "repaired_code": null,
              "repair_notes": "Tightened the prompt.",
              "confidence": 0.76
            }
            """
        )
    ).repair(_maintainer_source_skill(), failure_info="wrong normalized result")

    assert result.success
    assert result.updated_skill is None
    assert result.proposal is not None
    assert result.proposal.requires_human_review is True
    assert result.details["requires_human_review"] is True
    assert result.details["candidate_updated_skill"]["implementation"]["prompt_template"].startswith(
        "Process {report_text}"
    )


def test_api_maintenance_proposal_list_counts_pending() -> None:
    pending = MaintenanceProposal(
        skill_id="skill-pending",
        trigger=MaintenanceTrigger.RUNTIME_FAILURE,
        evidence=["failed step"],
    )
    accepted = MaintenanceProposal(
        skill_id="skill-accepted",
        trigger=MaintenanceTrigger.LOW_SUCCESS_RATE,
        evidence=["low success rate"],
    )
    accepted.accept()

    response = MaintenanceProposalListResponse.from_proposals([pending, accepted])

    assert response.total == 2
    assert response.pending_count == 1
    assert [proposal.skill_id for proposal in response.proposals] == [
        "skill-pending",
        "skill-accepted",
    ]


@pytest.mark.asyncio
async def test_health_response_persists_deduplicated_maintenance_proposal() -> None:
    report = SkillHealthReport(
        skill_id="unstable-skill",
        skill_name="unstable_skill",
        status=HealthStatus.CRITICAL,
        success_rate=0.2,
        usage_count=10,
        avg_latency_ms=100.0,
        issues=["very low success rate"],
        recommendations=["repair the failing selector"],
    )

    first = evolution_routes._health_response(report, persist_proposal=True)
    second = evolution_routes._health_response(report, persist_proposal=True)
    listed = await evolution_routes.list_maintenance_proposals()

    assert first.maintenance_proposal is not None
    assert second.maintenance_proposal is not None
    assert first.maintenance_proposal.proposal_id == second.maintenance_proposal.proposal_id
    assert listed.total == 1
    assert listed.pending_count == 1
    assert listed.proposals[0].skill_id == "unstable-skill"
    assert listed.proposals[0].recommended_action == MaintenanceRecommendedAction.REPAIR


@pytest.mark.asyncio
async def test_reflection_memory_creates_proposal_after_repeated_failure_signature() -> None:
    base = {
        "skill_id": "submit_form",
        "goal": "submit onboarding form",
        "success": False,
        "failure_signature": "postcondition: output.submitted false",
        "evidence": ["output.submitted was false"],
        "reflection_text": "The submit step completed but the verifier rejected the postcondition.",
        "trajectory_summary": "submit_form returned submitted=false",
    }

    first = await evolution_routes.record_reflection_memory(
        evolution_routes.ReflectionMemoryRequest(task_id="task-1", **base)
    )
    second = await evolution_routes.record_reflection_memory(
        evolution_routes.ReflectionMemoryRequest(task_id="task-2", **base)
    )
    third = await evolution_routes.record_reflection_memory(
        evolution_routes.ReflectionMemoryRequest(task_id="task-3", **base)
    )
    listed = await evolution_routes.list_maintenance_proposals()

    assert first.proposal is None
    assert second.proposal is None
    assert third.proposal is not None
    assert third.occurrence_count == 3
    assert third.threshold == 3
    assert third.proposal.skill_id == "submit_form"
    assert third.proposal.trigger == MaintenanceTrigger.RUNTIME_FAILURE
    assert third.proposal.recommended_action == MaintenanceRecommendedAction.REPAIR
    assert third.proposal.feedback_sources == ["runtime_reflection_memory"]
    assert third.proposal.metadata["failure_signature"] == "postcondition: output.submitted false"
    assert third.proposal.metadata["occurrence_count"] == 3
    assert len(third.proposal.metadata["reflection_memory_ids"]) == 3
    assert listed.total == 1
    assert all(
        memory.status == ReflectionMemoryStatus.PROPOSED
        for memory in evolution_routes._reflection_memory.values()
    )


@pytest.mark.asyncio
async def test_reflection_memory_keeps_different_failure_signatures_separate() -> None:
    common = {
        "skill_id": "submit_form",
        "goal": "submit onboarding form",
        "success": False,
        "evidence": ["form submission failed"],
    }
    for index in range(3):
        await evolution_routes.record_reflection_memory(
            evolution_routes.ReflectionMemoryRequest(
                task_id=f"selector-{index}",
                failure_signature="selector_not_found",
                **common,
            )
        )
    for index in range(3):
        await evolution_routes.record_reflection_memory(
            evolution_routes.ReflectionMemoryRequest(
                task_id=f"postcondition-{index}",
                failure_signature="postcondition_false",
                **common,
            )
        )

    listed = await evolution_routes.list_maintenance_proposals()

    assert listed.total == 2
    signatures = {proposal.metadata["failure_signature"] for proposal in listed.proposals}
    assert signatures == {"selector_not_found", "postcondition_false"}


@pytest.mark.asyncio
async def test_reflection_memory_derives_signature_when_request_omits_one() -> None:
    response = await evolution_routes.record_reflection_memory(
        evolution_routes.ReflectionMemoryRequest(
            task_id="task-derive",
            skill_id="submit_form",
            success=False,
            evidence=["  Timeout waiting for submit button  "],
        )
    )

    assert response.memory.failure_signature == "timeout waiting for submit button"
    assert response.proposal is None


def test_reflection_memory_http_endpoint_and_json_persistence(tmp_path) -> None:
    evolution_routes.configure_persistent_stores(tmp_path)
    app = FastAPI()
    app.include_router(evolution_routes.router, prefix="/api/v1")
    client = TestClient(app)

    payload = {
        "skill_id": "submit_form",
        "goal": "submit onboarding form",
        "success": False,
        "failure_signature": "postcondition_false",
        "evidence": ["output.submitted was false"],
        "reflection_text": "Verifier failed after submit.",
    }

    for index in range(3):
        response = client.post(
            "/api/v1/evolution/reflection-memory",
            json={"task_id": f"task-{index}", **payload},
        )
        assert response.status_code == 200

    final_payload = response.json()
    assert final_payload["occurrence_count"] == 3
    assert final_payload["proposal"]["recommended_action"] == "repair"

    proposal_path = tmp_path / "metadata" / "maintenance" / "proposal_queue.json"
    reflection_path = tmp_path / "metadata" / "maintenance" / "reflection_memory.json"
    assert proposal_path.exists()
    assert reflection_path.exists()

    evolution_routes._proposal_queue.clear()
    evolution_routes._reflection_memory.clear()
    evolution_routes.configure_persistent_stores(tmp_path)

    assert len(evolution_routes._proposal_queue) == 1
    assert len(evolution_routes._reflection_memory) == 3
    assert next(iter(evolution_routes._proposal_queue.values())).metadata["failure_signature"] == (
        "postcondition_false"
    )


def test_create_app_configures_d_maintenance_store(tmp_path) -> None:
    app = create_app(
        api_key="test-key",
        repository_backend="memory",
        skill_storage_dir=tmp_path,
        seed_demo=False,
    )

    assert app.state.skill_storage_dir == tmp_path.resolve()
    assert evolution_routes._proposal_store_path == (
        tmp_path.resolve() / "metadata" / "maintenance" / "proposal_queue.json"
    )
    assert evolution_routes._reflection_store_path == (
        tmp_path.resolve() / "metadata" / "maintenance" / "reflection_memory.json"
    )


@pytest.mark.asyncio
async def test_maintenance_proposal_queue_accept_reject_and_filter() -> None:
    pending = evolution_routes._store_proposal(
        MaintenanceProposal(
            skill_id="skill-pending",
            trigger=MaintenanceTrigger.RUNTIME_FAILURE,
            evidence=["runtime failed"],
        )
    )
    rejected_candidate = evolution_routes._store_proposal(
        MaintenanceProposal(
            skill_id="skill-rejected",
            trigger=MaintenanceTrigger.LOW_SUCCESS_RATE,
            evidence=["low success rate"],
        )
    )
    assert pending is not None
    assert rejected_candidate is not None

    accepted = await evolution_routes.accept_maintenance_proposal(pending.proposal_id)
    assert accepted.status == MaintenanceProposalStatus.ACCEPTED
    assert accepted.next_action is not None
    assert accepted.next_action.action == "create_review_bundle"
    assert accepted.next_action.method == "POST"
    assert accepted.next_action.endpoint == (
        "/api/v1/lifecycle/skill-pending/propose-maintenance-change"
    )
    assert "patched_skill" in accepted.next_action.required_payload_fields

    pending_only = await evolution_routes.list_maintenance_proposals(
        status=MaintenanceProposalStatus.PENDING
    )
    assert pending_only.total == 1
    assert pending_only.proposals[0].proposal_id == rejected_candidate.proposal_id

    rejected = await evolution_routes.reject_maintenance_proposal(rejected_candidate.proposal_id)
    assert rejected.status == MaintenanceProposalStatus.REJECTED

    all_proposals = await evolution_routes.list_maintenance_proposals()
    assert all_proposals.total == 2
    assert all_proposals.pending_count == 0


def test_maintenance_proposal_queue_http_endpoints() -> None:
    first = evolution_routes._store_proposal(
        MaintenanceProposal(
            skill_id="skill-http-accept",
            trigger=MaintenanceTrigger.RUNTIME_FAILURE,
            evidence=["runtime failed"],
        )
    )
    second = evolution_routes._store_proposal(
        MaintenanceProposal(
            skill_id="skill-http-reject",
            trigger=MaintenanceTrigger.LOW_SUCCESS_RATE,
            evidence=["low success rate"],
        )
    )
    assert first is not None
    assert second is not None

    app = FastAPI()
    app.include_router(evolution_routes.router, prefix="/api/v1")
    client = TestClient(app)

    listed = client.get("/api/v1/evolution/proposals")
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert listed.json()["pending_count"] == 2

    accepted = client.post(f"/api/v1/evolution/proposals/{first.proposal_id}/accept")
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"

    pending_only = client.get(
        "/api/v1/evolution/proposals",
        params={"status": "pending"},
    )
    assert pending_only.status_code == 200
    assert pending_only.json()["total"] == 1
    assert pending_only.json()["proposals"][0]["proposal_id"] == second.proposal_id

    rejected = client.post(f"/api/v1/evolution/proposals/{second.proposal_id}/reject")
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    missing = client.post("/api/v1/evolution/proposals/missing-proposal/accept")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_accept_missing_or_already_rejected_proposal_returns_api_errors() -> None:
    with pytest.raises(HTTPException) as missing:
        await evolution_routes.accept_maintenance_proposal("missing-proposal")
    assert missing.value.status_code == 404

    proposal = evolution_routes._store_proposal(
        MaintenanceProposal(
            skill_id="skill-rejected",
            trigger=MaintenanceTrigger.RUNTIME_FAILURE,
            evidence=["manual rejection"],
        )
    )
    assert proposal is not None
    await evolution_routes.reject_maintenance_proposal(proposal.proposal_id)

    with pytest.raises(HTTPException) as conflict:
        await evolution_routes.accept_maintenance_proposal(proposal.proposal_id)
    assert conflict.value.status_code == 409


@pytest.mark.asyncio
async def test_evolution_cycle_persists_report_maintenance_proposals(monkeypatch) -> None:
    proposal = MaintenanceProposal(
        skill_id="skill-cycle",
        trigger=MaintenanceTrigger.LOW_SUCCESS_RATE,
        evidence=["cycle found low success rate"],
    )
    events = []

    async def capture(event, payload):  # noqa: ANN001
        events.append((event, payload))

    class FakeEvolution:
        async def run_evolution_cycle(self) -> EvolutionReport:
            return EvolutionReport(
                cycle_id="cycle-with-proposal",
                tasks_total=1,
                tasks_completed=1,
                maintenance_proposals=[proposal],
            )

    class FakeApp:
        evolution = FakeEvolution()

    monkeypatch.setattr(evolution_routes, "_safe_broadcast", capture)

    response = await evolution_routes.run_evolution_cycle(FakeApp())
    listed = await evolution_routes.list_maintenance_proposals()

    assert response.maintenance_proposals[0].proposal_id == proposal.proposal_id
    assert listed.total == 1
    assert listed.proposals[0].proposal_id == proposal.proposal_id
    assert events[0][1]["maintenance_proposals"] == 1


@pytest.mark.asyncio
async def test_evolution_repair_task_records_maintenance_proposal() -> None:
    skill = _maintainer_source_skill("unstable_evolution_skill")
    skill.transition_to(SkillState.VERIFIED)
    skill.transition_to(SkillState.RELEASED)
    for index in range(6):
        skill.record_execution(success=index == 0, latency_ms=100.0)

    class FakeWiki:
        async def get(self, skill_id: str) -> Skill | None:
            return skill if skill_id == skill.skill_id else None

    class FakeRepair:
        called = False

        async def repair(self, skill_arg: Skill, health: SkillHealthReport) -> RepairResult:
            self.called = True
            raise AssertionError("repair should wait for human review")

    repair = FakeRepair()
    engine = EvolutionEngine(
        monitor=SkillMonitor(),
        repair=repair,
        merger=None,
        wiki_manager=FakeWiki(),
        graph_manager=SimpleNamespace(),
    )
    report = EvolutionReport(cycle_id="cycle-1")
    task = EvolutionTask(
        task_id="task-1",
        action=EvolutionAction.REPAIR,
        skill_ids=[skill.skill_id],
        reason="critical success rate",
    )

    await engine._do_repair(task, report)

    assert len(report.maintenance_proposals) == 1
    proposal = report.maintenance_proposals[0]
    assert proposal.skill_id == skill.skill_id
    assert proposal.trigger == MaintenanceTrigger.LOW_SUCCESS_RATE
    assert proposal.recommended_action == MaintenanceRecommendedAction.REPAIR
    assert proposal.metadata["evolution_task_id"] == "task-1"
    assert report.repaired == []
    assert repair.called is False
    assert task.result == {
        "proposal_id": proposal.proposal_id,
        "recommended_action": "repair",
        "requires_human_review": True,
    }


@pytest.mark.asyncio
async def test_evolution_cycle_response_returns_seeded_degraded_proposal(monkeypatch) -> None:
    wiki = MemoryWikiManager()
    await _seed_demo_skills(wiki)

    class FailRepair:
        async def repair(self, skill_arg: Skill, health: SkillHealthReport) -> RepairResult:
            raise AssertionError("cycle should emit a proposal, not auto-repair")

    engine = EvolutionEngine(
        monitor=SkillMonitor(),
        repair=FailRepair(),
        merger=None,
        wiki_manager=wiki,
        graph_manager=SimpleNamespace(),
    )
    events = []

    async def fake_broadcast(event: str, payload: dict) -> None:
        events.append((event, payload))

    monkeypatch.setattr(evolution_routes, "broadcast", fake_broadcast)

    response = await evolution_routes.run_evolution_cycle(SimpleNamespace(evolution=engine))

    seeded_proposals = [
        proposal
        for proposal in response.maintenance_proposals
        if proposal.skill_id == "demo_degraded_submit_form"
    ]
    assert seeded_proposals
    assert seeded_proposals[0].recommended_action == MaintenanceRecommendedAction.REPAIR
    assert response.repaired == []
    assert events[0][0] == "evolution_cycle_done"
    assert events[0][1]["maintenance_proposals"] >= 1


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
        "maintenance_proposal",
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
        "maintenance_proposals",
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
        maintenance_proposals=[],
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
        maintenance_proposals=[
            MaintenanceProposal(
                skill_id="skill-1",
                trigger=MaintenanceTrigger.LOW_SUCCESS_RATE,
                evidence=["low success rate"],
            )
        ],
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
    assert payload["maintenance_proposals"] == 1
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
                "maintenance_proposals": 0,
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
