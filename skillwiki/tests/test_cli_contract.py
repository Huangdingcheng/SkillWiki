"""CLI against real FastAPI routes; model calls prohibited."""

import json
import subprocess
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from skillwiki.api.main import create_app
from skillwiki.cli import cli
from skillwiki.utils.llm_client import LLMClient


@pytest.fixture
def api(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No model calls in CLI contract tests")

    monkeypatch.setattr(LLMClient, "_do_request", forbidden)
    monkeypatch.setattr(LLMClient, "_stream_request", forbidden)
    governance = tmp_path / "governance"
    governance.mkdir()
    subprocess.run(["git", "init", str(governance)], check=True, capture_output=True)
    monkeypatch.setenv("SKILLOS_GOVERNANCE_REPO", str(governance))
    app = create_app(
        api_key="offline",
        repository_backend="git",
        skill_storage_dir=tmp_path / "storage",
        seed_demo=False,
        llm_retry_count=0,
    )
    client = TestClient(app)

    class Adapter:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            return client.get(url)

        def post(self, url, json):
            return client.post(url, json=json)

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: Adapter())
    with client:
        response = client.post(
            "/api/v1/skills",
            json={
                "name": "cli_contract",
                "description": "Return the supplied result",
                "tags": ["tag & Unicode 中文"],
                "interface": {
                    "input_schema": {"type": "object", "properties": {}},
                    "output_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
                },
                "implementation": {
                    "prompt_template": 'Return {"ok": true}',
                    "prompt_format": "literal",
                },
            },
        )
        assert response.status_code == 201, response.text
        yield client, response.json()["skill_id"]


def invoke(*args):
    return CliRunner().invoke(cli, ["--api-url", "http://testserver", *args])


def test_real_audit_and_tag_filter(api):
    client, sid = api
    r = invoke("audit", sid, "--json")
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["passed"] is True
    assert "cli_contract" in invoke("skill", "list", "--tag", "tag & Unicode 中文").output
    assert "cli_contract" not in invoke("skill", "list", "--tag", "absent").output
    before = client.get(f"/api/v1/skills/{sid}/full").json()
    assert before["implementation"]["prompt_format"] == "literal"
    assert before["state"] == "S2"


def test_failed_native_verification_is_not_cli_success(api):
    _, sid = api
    r = invoke("verify", sid, "--json")
    assert r.exit_code == 1, r.output
    result = json.loads(r.output)
    assert result["attempt_count"] == 1
    assert result["promotion_allowed"] is False
    assert result["final_state"] == "S2"
    assert result["repairs"] == []
    assert result["attempts"][0]["harness"] == "local_skillwiki"
    r = invoke("verify", sid, "--watch")
    assert r.exit_code == 1
    assert "Score:" in r.output and "TypeError" not in r.output


def test_native_version_and_failed_evidence_release(api, tmp_path):
    client, sid = api
    f = tmp_path / "version.json"
    f.write_text(
        json.dumps({"bump": "patch", "description": "Updated contract description"}),
        encoding="utf-8",
    )
    r = invoke("lifecycle", "new-version", sid, "--file", str(f))
    assert r.exit_code == 0, r.output
    new = json.loads(r.output)
    assert new["skill_id"] != sid and new["version"] == "1.0.1" and new["state"] == "S2"
    assert client.get(f"/api/v1/skills/{sid}").json()["version"] == "1.0.0"
    assert invoke("skill", "versions", sid).exit_code == 0
    f.write_text("{}", encoding="utf-8")
    r = invoke("lifecycle", "release", new["skill_id"], "--file", str(f))
    assert r.exit_code == 1 and "422" in r.output
    assert client.get(f'/api/v1/skills/{new["skill_id"]}').json()["state"] == "S2"


def test_create_candidate_file_without_id(api, tmp_path):
    _, _sid = api
    f = tmp_path / "candidate.json"
    f.write_text(
        json.dumps(
            {
                "source_type": "document",
                "unit_id": "source-1",
                "name": "reviewed_cli",
                "description": "Return a structured result",
                "prompt_template": 'Return {"ok": true}',
                "prompt_format": "literal",
            }
        ),
        encoding="utf-8",
    )
    r = invoke("ingest", "audit-candidate", "--file", str(f))
    assert r.exit_code == 0, r.output
    r = invoke("ingest", "create-candidate", "--file", str(f))
    assert r.exit_code == 0, r.output
    assert json.loads(r.output)["created_skill"]["state"] == "S1"
    sid = json.loads(r.output)["created_skill"]["skill_id"]
    r = invoke("promote", sid, "S2")
    assert r.exit_code == 0, r.output


def test_execution_contract_and_invalid_input(api):
    _, sid = api
    r = invoke("skill", "exec", sid, "--input", "[]")
    assert r.exit_code == 2
    r = invoke("skill", "exec", sid, "--input", "{}")
    assert r.exit_code == 1 and "API error 409:" in r.output, r.output
    assert invoke("execution", "history").exit_code == 0
    r = invoke("promote", sid, "S4")
    assert r.exit_code == 1 and "API error 422:" not in r.output, r.output


def test_snapshot_and_recovery_preserve_live_identity(api, tmp_path):
    client, sid = api
    f = tmp_path / "snapshot.json"
    f.write_text('{"author":"cli-test","message":"isolated snapshot"}', encoding="utf-8")
    r = invoke("lifecycle", "snapshot", sid, "--file", str(f))
    assert r.exit_code == 0, r.output
    commit = json.loads(r.output)["commit"]
    r = invoke("lifecycle", "history", sid)
    assert r.exit_code == 0 and commit in r.output, r.output
    r = invoke("lifecycle", "diff", sid, "--from-ref", commit)
    assert r.exit_code == 0, r.output
    f.write_text(
        json.dumps({"source_ref": commit, "source_version": "1.0.0", "reason": "test recovery"}),
        encoding="utf-8",
    )
    r = invoke("lifecycle", "rollback", sid, "--file", str(f))
    assert r.exit_code == 0, r.output
    restored = json.loads(r.output)
    assert restored["restored_skill_id"] != sid
    assert restored["restored_state"] == "S2" and restored["live_skill_updated"] is False
    assert client.get(f"/api/v1/skills/{sid}").json()["version"] == "1.0.0"


def test_http_timeout_does_not_resubmit(monkeypatch):
    calls = []

    class TimeoutClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, json):
            calls.append(url)
            raise httpx.ReadTimeout("fixture timeout")

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: TimeoutClient())
    r = invoke("verify", "identity")
    assert r.exit_code == 1 and "inspect server receipts" in r.output
    assert len(calls) == 1


def test_native_deterministic_verification_and_task_failure(api, tmp_path):
    client, _ = api
    response = client.post(
        "/api/v1/skills",
        json={
            "name": "cli_code_contract",
            "description": "Return a boolean without a model",
            "implementation": {"code": "output['ok'] = True"},
            "interface": {
                "input_schema": {"type": "object", "properties": {}},
                "output_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
            },
        },
    )
    assert response.status_code == 201, response.text
    sid = response.json()["skill_id"]
    f = tmp_path / "cases.json"
    f.write_text(
        json.dumps(
            [
                {
                    "test_id": "ok",
                    "name": "boolean exists",
                    "goal": "Return true",
                    "input_data": {},
                    "verifier_specs": [{"type": "json_exists", "path": "output.ok"}],
                }
            ]
        ),
        encoding="utf-8",
    )
    r = invoke("verify", sid, "--test-cases", str(f), "--json")
    assert r.exit_code == 0, r.output
    result = json.loads(r.output)
    assert result["status"] == "verified" and result["attempt_count"] == 1
    assert client.get(f"/api/v1/skills/{sid}").json()["state"] == "S2"
    r = invoke("audit", sid)
    assert r.exit_code == 1 and "cannot preserve" in r.output
    r = invoke("run", "No released skills available", "--verbose")
    assert (
        r.exit_code == 1 and "API error 404:" not in r.output and "API error 422:" not in r.output
    ), r.output


def test_serve_forwards_explicit_storage_and_no_secret_argv(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append((cmd, kw)))
    repo = tmp_path / "governance"
    repo.mkdir()
    r = invoke(
        "serve",
        "--storage-dir",
        str(tmp_path / "space 中文"),
        "--governance-repo",
        str(repo),
        "--model",
        "offline",
        "--llm-api-url",
        "https://example.invalid",
        "--api-key",
        "fixture-key",
    )
    assert r.exit_code == 0, r.output
    cmd, kw = calls[0]
    assert cmd[cmd.index("--repository-backend") + 1] == "git"
    assert cmd[cmd.index("--llm-retry-count") + 1] == "0"
    assert "--no-seed-demo" in cmd and "fixture-key" not in cmd
    assert kw["env"]["LLM_API_KEY"] == "fixture-key"
    assert kw["env"]["SKILLOS_GOVERNANCE_REPO"] == str(repo.resolve())
    assert invoke("serve", "--backend", "sqlite").exit_code == 2


@pytest.mark.parametrize(
    "args",
    [
        ("verify", "id", "--max-attempts", "0"),
        ("verify", "id", "--harness", "mock"),
        ("audit",),
        ("audit", "id", "--candidate-file", __file__),
    ],
)
def test_invalid_cli_options(args):
    assert invoke(*args).exit_code == 2
