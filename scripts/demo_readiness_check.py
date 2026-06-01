from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_ROOT = REPO_ROOT / "artifacts" / "eval-readiness-runs"


class ApiClient:
    def __init__(self, api_base: str, raw_dir: Path, timeout_s: int = 60) -> None:
        self.api_base = api_base.rstrip("/")
        self.raw_dir = raw_dir
        self.timeout_s = timeout_s
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def get(self, path: str, label: str) -> dict[str, Any]:
        return self._request("GET", path, None, label)

    def post(self, path: str, payload: dict[str, Any], label: str) -> dict[str, Any]:
        return self._request("POST", path, payload, label)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
        label: str,
    ) -> dict[str, Any]:
        url = path if path.startswith(("http://", "https://")) else f"{self.api_base}{path}"
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, method=method)
        if label == "frontend_home":
            request.add_header("Accept", "text/html,*/*")
        else:
            request.add_header("Accept", "application/json")
        if payload is not None:
            request.add_header("Content-Type", "application/json")
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                text = response.read().decode("utf-8", errors="replace")
                status = response.status
        except urllib.error.HTTPError as exc:
            text = exc.read().decode("utf-8", errors="replace")
            status = exc.code
        except Exception as exc:
            text = json.dumps({"error": str(exc)}, ensure_ascii=False)
            status = 0
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        parsed = _parse_json(text)
        envelope = {
            "label": label,
            "method": method,
            "url": url,
            "status_code": status,
            "elapsed_ms": elapsed_ms,
            "request": _preview(payload),
            "body": parsed,
        }
        (self.raw_dir / f"{_safe_name(label)}.json").write_text(
            json.dumps(envelope, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return envelope


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an isolated SkillOS demo readiness check.")
    parser.add_argument("--api-base", default="http://127.0.0.1:8001/api/v1")
    parser.add_argument("--frontend-base", default="http://127.0.0.1:5174")
    parser.add_argument("--run-root", type=Path, default=DEFAULT_RUN_ROOT)
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--skip-mutating-probe", action="store_true")
    args = parser.parse_args()

    run_dir = args.run_root / f"readiness-{args.run_id}"
    raw_dir = run_dir / "raw"
    run_dir.mkdir(parents=True, exist_ok=True)
    client = ApiClient(args.api_base, raw_dir, timeout_s=args.timeout)

    summary: dict[str, Any] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "api_base": args.api_base,
        "frontend_base": args.frontend_base,
        "run_dir": str(run_dir),
        "isolation": {
            "raw_fixtures_modified": False,
            "writes_only_under_run_dir": str(run_dir),
            "mutating_probe": not args.skip_mutating_probe,
            "governance_repo": os.environ.get("SKILLOS_GOVERNANCE_REPO", ""),
        },
        "checks": [],
    }

    api_root = _api_root(args.api_base)
    frontend_base = args.frontend_base.rstrip("/")

    _record(summary, "backend_health", client.get(f"{api_root}/health", "backend_health"), {200})
    _record(summary, "frontend_home", client.get(f"{frontend_base}/", "frontend_home"), {200})
    _record(
        summary,
        "frontend_proxy",
        client.get(f"{frontend_base}/api/v1/skills?limit=1", "frontend_proxy"),
        {200},
    )
    _record(summary, "skills_list", client.get("/skills?limit=1", "skills_list"), {200})
    _record(summary, "graph_view", client.get("/graph/view?view=skill_only&limit=10", "graph_view"), {200})
    _record(summary, "evaluation_dashboard", client.get("/evaluation/dashboard", "evaluation_dashboard"), {200})
    _record(summary, "version_repo_status", client.get("/lifecycle/repository/status", "version_repo_status"), {200, 400})
    _record(summary, "llm_config", _llm_config_envelope(), {200, 204})

    if not args.skip_mutating_probe:
        parse = client.post("/ingest/parse", _probe_parse_payload(), "ingest_parse_probe")
        _record(summary, "ingest_parse", parse, {200})
        unit = _first_unit(parse)
        if unit:
            create = client.post("/ingest/create-candidate", _candidate_payload(unit), "create_candidate_probe")
            _record(summary, "create_candidate", create, {200, 201})
            skill_id = _body(create).get("created_skill", {}).get("skill_id")
            if isinstance(skill_id, str) and skill_id:
                _record(summary, "version_diff", client.get(f"/lifecycle/{skill_id}/diff", "version_diff_probe"), {200})
                _record(summary, "version_snapshot", client.post(
                    f"/lifecycle/{skill_id}/snapshot",
                    {"author": "demo_readiness_check", "message": "readiness probe snapshot"},
                    "version_snapshot_probe",
                ), {200, 400})
                draft = client.post(
                    f"/lifecycle/{skill_id}/new-version",
                    _harness_draft_payload(),
                    "new_harness_draft_probe",
                )
                _record(
                    summary,
                    "new_harness_draft",
                    draft,
                    {200},
                    semantic_ok=_body(draft).get("state") == "S2",
                )
                harness_skill_id = _body(draft).get("skill_id") or skill_id
                harness = client.post(
                    f"/harness/{harness_skill_id}/verify-loop",
                    {
                        "harness": "local_skillos",
                        "max_attempts": 2,
                        "promote_on_pass": True,
                        "allow_repair": True,
                        "test_cases": [
                            {
                                "test_id": "readiness-email-positive",
                                "name": "Readiness email extraction positive case",
                                "goal": "Extract an email address from text.",
                                "input_data": {"text": "Contact readiness@example.com"},
                                "verifier_specs": [{"type": "json_exists", "path": "output.email"}],
                                "timeout_s": 20,
                            }
                        ],
                    },
                    "harness_verify_loop_probe",
                )
                _record(
                    summary,
                    "harness_verify_loop",
                    harness,
                    {200},
                    semantic_ok=_body(harness).get("status") == "verified"
                    and _body(harness).get("promotion_allowed") is True,
                )
        _record(summary, "execution_plan", client.post(
            "/execution/plan",
            {"goal": "Extract an email address from a JSON payload.", "context": {"text": "user@example.com"}},
            "execution_plan_probe",
        ), {200})

    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
    summary["scores"] = _score(summary)
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (run_dir / "REPORT.md").write_text(_report(summary), encoding="utf-8")
    print(json.dumps({"run_dir": str(run_dir), "scores": summary["scores"]}, indent=2, ensure_ascii=False))
    return 0 if summary["scores"]["overall"] >= 0.85 else 1


def _record(
    summary: dict[str, Any],
    name: str,
    envelope: dict[str, Any],
    ok_statuses: set[int],
    *,
    semantic_ok: bool | None = None,
) -> None:
    status = int(envelope.get("status_code") or 0)
    body = _body(envelope)
    passed = status in ok_statuses and (True if semantic_ok is None else semantic_ok)
    summary["checks"].append({
        "name": name,
        "passed": passed,
        "status_code": status,
        "elapsed_ms": envelope.get("elapsed_ms"),
        "semantic_ok": semantic_ok,
        "error": body.get("error") or body.get("detail") if isinstance(body, dict) else None,
    })


def _score(summary: dict[str, Any]) -> dict[str, Any]:
    checks = summary.get("checks", [])
    total = len(checks)
    passed = sum(1 for check in checks if check.get("passed"))
    return {
        "passed": passed,
        "total": total,
        "overall": round(passed / total, 4) if total else 0.0,
    }


def _api_root(api_base: str) -> str:
    stripped = api_base.rstrip("/")
    suffix = "/api/v1"
    if stripped.endswith(suffix):
        return stripped[: -len(suffix)]
    return stripped


def _llm_config_envelope() -> dict[str, Any]:
    configured = bool(os.environ.get("LLM_API_URL") and os.environ.get("LLM_MODEL"))
    has_key = bool(os.environ.get("LLM_API_KEY"))
    return {
        "label": "llm_config",
        "method": "ENV",
        "url": "environment",
        "status_code": 200 if configured and has_key else 204,
        "elapsed_ms": 0.0,
        "request": None,
        "body": {
            "configured": configured,
            "api_url_configured": bool(os.environ.get("LLM_API_URL")),
            "api_key_configured": has_key,
            "model": os.environ.get("LLM_MODEL", ""),
            "note": "Readiness does not print or persist API keys.",
        },
    }


def _report(summary: dict[str, Any]) -> str:
    lines = [
        "# SkillOS Demo Readiness Report",
        "",
        f"- API base: `{summary['api_base']}`",
        f"- Frontend base: `{summary['frontend_base']}`",
        f"- Run directory: `{summary['run_dir']}`",
        f"- Overall: `{summary['scores']['overall']}` ({summary['scores']['passed']}/{summary['scores']['total']})",
        "",
        "## Isolation",
        "",
        "- Raw fixtures are not modified.",
        f"- Request/response artifacts are written only under `{summary['run_dir']}`.",
        "- Mutating probes create only readiness-tagged candidate data in the target backend.",
        "",
        "## Checks",
        "",
        "| Check | Passed | HTTP | Latency ms | Error |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in summary.get("checks", []):
        lines.append(
            f"| {check['name']} | {check['passed']} | {check['status_code']} | "
            f"{check.get('elapsed_ms', '')} | {check.get('error') or ''} |"
        )
    lines.append("")
    return "\n".join(lines)


def _probe_parse_payload() -> dict[str, Any]:
    return {
        "source_type": "past_skills",
        "content": json.dumps({
            "name": "readiness_email_extract_probe",
            "description": "Extract one email address from input text for readiness checks.",
            "inputs": {"text": "string"},
            "outputs": {"email": "string"},
            "steps": ["Read input text", "Find the first email address", "Return it as output.email"],
        }, ensure_ascii=False),
        "metadata": {
            "source_id": "readiness-email-extract-probe",
            "domain": "software",
            "max_candidates": 1,
        },
    }


def _candidate_payload(unit: dict[str, Any]) -> dict[str, Any]:
    metadata = unit.get("metadata") if isinstance(unit.get("metadata"), dict) else {}
    interface = metadata.get("candidate_interface") if isinstance(metadata.get("candidate_interface"), dict) else {}
    implementation = metadata.get("candidate_implementation") if isinstance(metadata.get("candidate_implementation"), dict) else {}
    unique_name = _safe_name(
        f"{unit.get('proposed_skill_name') or 'readiness_email_extract_probe'}_{uuid4().hex[:8]}"
    )
    return {
        "source_type": "past_skills",
        "unit_id": unit.get("unit_id"),
        "raw_content": unit.get("raw_content", ""),
        "name": unique_name,
        "description": unit.get("proposed_description") or unit.get("summary") or "Readiness probe skill.",
        "skill_type": "atomic",
        "tags": ["readiness-probe", "isolated-eval"],
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
        "output_schema": {"type": "object", "properties": {"email": {"type": "string"}}},
        "preconditions": ["input.text is provided"],
        "postconditions": ["output.email is present when an email exists"],
        "prompt_template": "Extract the first email from {text}.",
        "evaluation": {
            "verifier_specs": [{"type": "json_exists", "path": "output.email"}],
            "benchmark_task_ids": ["readiness-email-extract-probe"],
        },
        "provenance": {
            "source_type": "past_skills",
            "source_ids": ["readiness-email-extract-probe"],
            "parent_skill_ids": [],
            "created_by_agent": "demo_readiness_check",
            "creation_context": {"isolated_probe": True},
        },
        "author": "demo_readiness_check",
    }


def _harness_draft_payload() -> dict[str, Any]:
    return {
        "bump": "patch",
        "description": "Readiness harness Draft that extracts the first email address from input text.",
        "implementation": {
            "language": "python",
            "code": (
                "text = input_data.get('text', '')\n"
                "for token in str(text).replace('<', ' ').replace('>', ' ').split():\n"
                "    candidate = token.strip('.,;:()[]{}\\\"\\'')\n"
                "    if '@' in candidate and '.' in candidate.split('@')[-1]:\n"
                "        output['email'] = candidate\n"
                "        break\n"
            ),
        },
        "evaluation": {
            "verifier_specs": [{"type": "json_exists", "path": "output.email"}],
            "benchmark_task_ids": ["readiness-email-extract-probe"],
            "validation_summary": "Readiness harness Draft uses deterministic local postcondition verification.",
        },
        "test_cases": [
            {
                "name": "Readiness email extraction positive case",
                "input_data": {"text": "Contact readiness@example.com"},
                "expected_output": {"email": "readiness@example.com"},
            }
        ],
        "metadata": {
            "readiness_probe": True,
            "requires_harness_reverification": True,
        },
        "author": "demo_readiness_check",
    }


def _first_unit(envelope: dict[str, Any]) -> dict[str, Any] | None:
    body = _body(envelope)
    units = body.get("units") if isinstance(body, dict) else None
    if isinstance(units, list) and units and isinstance(units[0], dict):
        return units[0]
    return None


def _body(envelope: dict[str, Any]) -> dict[str, Any]:
    body = envelope.get("body")
    return body if isinstance(body, dict) else {}


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return text


def _preview(payload: dict[str, Any] | None) -> Any:
    if payload is None:
        return None
    text = json.dumps(payload, ensure_ascii=False)
    if len(text) <= 1000:
        return payload
    return {"preview": text[:1000], "truncated": True}


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in value)[:120]


if __name__ == "__main__":
    raise SystemExit(main())
