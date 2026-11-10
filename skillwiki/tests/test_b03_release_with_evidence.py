from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillwiki.api.deps import get_app_state
from skillwiki.api.memory_store import MemoryWikiManager
from skillwiki.api.routes.lifecycle import router
from skillwiki.layers.skill_governance import canonical_skill_artifact_hash
from skillwiki.models.skill_model import (
    Skill,
    SkillImplementation,
    SkillInterface,
    SkillProvenance,
    SkillState,
    SkillType,
)


class PassingAuditor:
    def audit(self, skill: Skill) -> SimpleNamespace:
        return SimpleNamespace(
            passed=True,
            issues=[],
            warnings=[],
            recommendations=[],
        )


def _skill() -> Skill:
    return Skill(
        skill_id="skill-b03-release",
        name="b03_release_skill",
        version="1.0.0",
        description="A skill used for evidence-bound release tests.",
        skill_type=SkillType.ATOMIC,
        domain="test",
        state=SkillState.DRAFT,
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
            },
            postconditions=["result is present"],
        ),
        implementation=SkillImplementation(prompt_template="Return {query}."),
    )


def _evidence(skill: Skill) -> dict:
    content_hash = canonical_skill_artifact_hash(skill)
    manifest = {
        "artifact_id": f"{skill.skill_id}@{skill.version}",
        "skill_id": skill.skill_id,
        "version": skill.version,
        "content_hash": {
            "algorithm": "sha256",
            "encoding": "utf-8-lf-canonical",
            "value": content_hash,
        },
        "source_snapshot": "snapshot-b03",
        "contract_version": "contract-v1",
        "dependency_ids": [],
        "license_refs": ["MIT"],
    }
    traces = [
        {
            "run_id": "run-b03",
            "artifact_hash": content_hash,
            "task_id": "task-b03",
            "input_ref": "fixture-b03",
            "environment_fingerprint": "env-b03",
            "model_harness_config": "fixture:deterministic-v1",
            "verifier_version": "verifier-v1",
            "status": "passed",
            "failure_class": "",
            "unknown_reason": "",
            "evidence_path": "private/raw/trace.json",
            "evidence_spans": [{"verifier_path": "output.result", "passed": True}],
        }
    ]
    evaluation = {
        "evaluation_id": "eval-b03",
        "artifact_hash": content_hash,
        "trace_ids": ["run-b03"],
        "applicability": {
            "contract_version": "contract-v1",
            "environment_fingerprint": "env-b03",
            "verifier_version": "verifier-v1",
        },
        "result": "pass",
        "decision": "accept",
        "reason": "fixture passed",
        "actor": "ci",
        "created_at": "2026-09-22T00:00:00Z",
    }
    return {"schema_version": "m1-evidence-release-v1", "manifest": manifest, "traces": traces, "evaluation": evaluation, "author": "b03-test"}


@pytest.fixture
def release_context() -> tuple[TestClient, MemoryWikiManager, Skill]:
    wiki = MemoryWikiManager()
    skill = _skill()
    asyncio.run(wiki.create(skill))
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_app_state] = lambda: SimpleNamespace(
        wiki=wiki,
        auditor=PassingAuditor(),
        graph=None,
    )
    return TestClient(app), wiki, skill


def test_release_with_evidence_accepts_and_persists_compact_reference(
    release_context: tuple[TestClient, MemoryWikiManager, Skill],
) -> None:
    client, wiki, skill = release_context
    payload = _evidence(skill)

    response = client.post(
        f"/api/v1/lifecycle/{skill.skill_id}/release-with-evidence",
        json=payload,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "accept"
    assert body["released"] is True
    assert body["state"] == "S4"
    reference = body["evidence_reference"]
    payload_hash = payload["manifest"]["content_hash"]["value"]
    assert reference["artifact_hash"] == payload_hash
    assert reference["evaluation_id"] == "eval-b03"
    assert reference["trace_ids"] == ["run-b03"]
    assert reference["actor"] == "b03-test"
    assert "evidence_path" not in reference

    released = asyncio.run(wiki.get(skill.skill_id))
    assert released is not None
    stored = released.evaluation.harness_validation
    assert stored == {"evidence_decision": reference}
    assert "private/raw/trace.json" not in str(stored)


def test_release_with_evidence_rejects_current_identity_or_version_mismatch(
    release_context: tuple[TestClient, MemoryWikiManager, Skill],
) -> None:
    client, wiki, skill = release_context
    payload = _evidence(skill)
    payload["manifest"]["version"] = "9.9.9"

    response = client.post(
        f"/api/v1/lifecycle/{skill.skill_id}/release-with-evidence",
        json=payload,
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["decision"] == "reject"
    assert "version" in detail["reason"]
    assert asyncio.run(wiki.get(skill.skill_id)).state == SkillState.DRAFT


def test_release_with_evidence_rejects_same_identity_with_changed_content_hash(
    release_context: tuple[TestClient, MemoryWikiManager, Skill],
) -> None:
    client, wiki, skill = release_context
    payload = _evidence(skill)
    asyncio.run(wiki.update(skill.skill_id, description="changed after evidence was produced"))

    response = client.post(
        f"/api/v1/lifecycle/{skill.skill_id}/release-with-evidence",
        json=payload,
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["decision"] == "reject"
    assert "content hash" in detail["reason"]
    assert asyncio.run(wiki.get(skill.skill_id)).state == SkillState.DRAFT


def test_release_with_evidence_defers_environment_unknown(
    release_context: tuple[TestClient, MemoryWikiManager, Skill],
) -> None:
    client, wiki, skill = release_context
    payload = _evidence(skill)
    payload["traces"][0].update(status="unknown", unknown_reason="sandbox did not start")

    response = client.post(
        f"/api/v1/lifecycle/{skill.skill_id}/release-with-evidence",
        json=payload,
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["decision"] == "defer"
    assert "unknown" in detail["reason"]
    assert asyncio.run(wiki.get(skill.skill_id)).state == SkillState.DRAFT


def test_release_with_evidence_rejects_skill_failure(
    release_context: tuple[TestClient, MemoryWikiManager, Skill],
) -> None:
    client, wiki, skill = release_context
    payload = _evidence(skill)
    payload["traces"][0].update(status="failed", failure_class="skill_output")

    response = client.post(
        f"/api/v1/lifecycle/{skill.skill_id}/release-with-evidence",
        json=payload,
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["decision"] == "reject"
    assert asyncio.run(wiki.get(skill.skill_id)).state == SkillState.DRAFT


def test_legacy_release_remains_explicit_unbound_boundary(
    release_context: tuple[TestClient, MemoryWikiManager, Skill],
) -> None:
    client, wiki, skill = release_context

    response = client.post(
        f"/api/v1/lifecycle/{skill.skill_id}/release",
        json={"author": "legacy-test"},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "S4"
    assert response.json()["evaluation"]["harness_validation"] == {}
    assert asyncio.run(wiki.get(skill.skill_id)).state == SkillState.RELEASED


def test_release_with_evidence_matches_direct_gate_inputs(
    release_context: tuple[TestClient, MemoryWikiManager, Skill],
) -> None:
    from skillwiki.layers.skill_governance import decide_evidence_gate

    client, _, skill = release_context
    payload = _evidence(skill)
    direct = decide_evidence_gate(payload["manifest"], payload["traces"], payload["evaluation"])

    response = client.post(
        f"/api/v1/lifecycle/{skill.skill_id}/release-with-evidence",
        json=payload,
    )

    assert direct.decision == "accept"
    assert response.status_code == 200
    assert response.json()["decision"] == direct.decision


def test_registered_release_contract_cannot_be_narrowed(
    release_context: tuple[TestClient, MemoryWikiManager, Skill],
) -> None:
    client, wiki, skill = release_context
    stored = asyncio.run(wiki.get(skill.skill_id))
    assert stored is not None
    registered = SkillProvenance(
        source_type="manual",
        creation_context={
            "evidence_release_contract": {
                "complete_U": [["task-b03", "fixture-b03"], ["task-b03-2", "fixture-b03-2"]],
                "source_snapshot": "snapshot-b03",
                "contract_version": "contract-v1",
                "environment_fingerprint": "env-b03",
                "verifier_version": "verifier-v1",
            }
        },
    )
    stored = asyncio.run(wiki.update(skill.skill_id, provenance=registered))
    assert stored is not None and stored.provenance is not None
    payload = _evidence(stored)
    payload["manifest"]["required_U"] = [["task-b03", "fixture-b03"]]

    response = client.post(
        f"/api/v1/lifecycle/{skill.skill_id}/release-with-evidence",
        json=payload,
    )

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["decision"] == "defer"
    assert "qualification U" in detail["reason"]
    assert asyncio.run(wiki.get(skill.skill_id)).state == SkillState.DRAFT
