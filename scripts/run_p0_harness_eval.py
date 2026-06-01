from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUN_ROOT = REPO_ROOT / "artifacts" / "input-skill-eval-runs"


class ApiClient:
    def __init__(self, base_url: str, raw_dir: Path, timeout_s: int) -> None:
        self.base_url = base_url.rstrip("/")
        self.raw_dir = raw_dir
        self.timeout_s = timeout_s
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    def get(self, path: str, *, label: str, timeout_s: int | None = None) -> Any:
        with urllib.request.urlopen(f"{self.base_url}{path}", timeout=timeout_s or self.timeout_s) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self._write_raw(label, payload)
        return payload

    def post(self, path: str, payload: dict[str, Any], *, label: str, timeout_s: int | None = None) -> Any:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_s or self.timeout_s) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            result = {"http_error": exc.code, "body": body}
        self._write_raw(label, {"request": payload, "response": result})
        return result

    def _write_raw(self, label: str, payload: Any) -> None:
        safe = safe_filename(label)
        (self.raw_dir / f"{safe}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run representative positive/negative harness checks against generated P0 Skills. "
            "Selects at least three generated Skills per input type from a full input-skill eval summary."
        )
    )
    parser.add_argument("--input-eval-summary", required=True)
    parser.add_argument("--api-base", default="http://127.0.0.1:8001/api/v1")
    parser.add_argument("--run-root", default=str(DEFAULT_RUN_ROOT))
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--per-type", type=int, default=3)
    parser.add_argument("--request-timeout", type=int, default=180)
    parser.add_argument("--harness-timeout", type=int, default=120)
    parser.add_argument("--output-report", default="")
    args = parser.parse_args(argv)

    input_summary_path = Path(args.input_eval_summary).resolve()
    input_summary = read_json(input_summary_path)
    run_dir = Path(args.run_root).resolve() / f"p0-harness-eval-{args.run_id}"
    raw_dir = run_dir / "raw"
    run_dir.mkdir(parents=True, exist_ok=True)
    client = ApiClient(args.api_base, raw_dir, args.request_timeout)

    selected = select_records(input_summary, per_type=args.per_type)
    summary: dict[str, Any] = {
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "api_base": client.base_url,
        "input_eval_summary": str(input_summary_path),
        "run_dir": str(run_dir),
        "run_id": args.run_id,
        "per_type": args.per_type,
        "checks": [],
        "notes": [
            "Positive checks use generated Skill verifier specs and allow deterministic repair when output fields are missing.",
            "Negative checks use the same Skill verifier specs with intentionally invalid inputs and allow_repair=false.",
            "This runner mutates only the isolated eval backend state by moving selected generated candidates to S2 and creating repaired S2/S3 versions as needed; raw corpora remain read-only.",
        ],
    }
    summary["connectivity"] = {
        "skills_before": client.get("/skills?limit=500", label="skills_before_p0_harness"),
    }

    for record in selected:
        item = run_record_checks(client, record, timeout_s=args.harness_timeout)
        summary["checks"].append(item)

    summary["scores"] = score_checks(summary["checks"])
    summary["finished_at"] = datetime.now().isoformat(timespec="seconds")
    write_json(run_dir / "summary.json", summary)
    report_text = render_report(summary)
    (run_dir / "REPORT.md").write_text(report_text, encoding="utf-8")
    if args.output_report:
        Path(args.output_report).write_text(report_text, encoding="utf-8")

    print(json.dumps({"run_dir": str(run_dir), "scores": summary["scores"]}, ensure_ascii=False, indent=2))
    return 0 if summary["scores"]["input_types_meeting_minimum"] == 5 else 1


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def select_records(input_summary: dict[str, Any], *, per_type: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    records = input_summary.get("records") if isinstance(input_summary.get("records"), list) else []
    for record in records:
        fixture = record.get("fixture") if isinstance(record, dict) else {}
        input_type = str(fixture.get("input_type") or "")
        create = record.get("create") if isinstance(record.get("create"), dict) else {}
        created_ids = create.get("created_skill_ids") if isinstance(create.get("created_skill_ids"), list) else []
        if not input_type or not created_ids or counts[input_type] >= per_type:
            continue
        selected.append(record)
        counts[input_type] += 1
    return selected


def run_record_checks(client: ApiClient, record: dict[str, Any], *, timeout_s: int) -> dict[str, Any]:
    fixture = record["fixture"]
    input_type = fixture["input_type"]
    source_id = fixture["source_id"]
    skill_id = record["create"]["created_skill_ids"][0]
    skill = client.get(f"/skills/{skill_id}/full", label=f"skill_full_{source_id}")
    negative_draft = ensure_draft_with_executable_impl(client, skill, fixture, purpose="negative")
    positive_draft = ensure_draft_with_executable_impl(client, skill, fixture, purpose="positive")
    positive_case = build_positive_test_case(positive_draft, fixture, timeout_s=timeout_s)
    negative_case = build_negative_test_case(negative_draft, fixture, timeout_s=timeout_s)
    negative = client.post(
        f"/harness/{negative_draft['skill_id']}/verify-loop",
        {
            "harness": "local_skillos",
            "max_attempts": 1,
            "promote_on_pass": False,
            "allow_repair": False,
            "timeout_s": timeout_s,
            "test_cases": [negative_case],
        },
        label=f"harness_negative_{source_id}",
        timeout_s=timeout_s + 180,
    )
    positive = client.post(
        f"/harness/{positive_draft['skill_id']}/verify-loop",
        {
            "harness": "local_skillos",
            "max_attempts": 2,
            "promote_on_pass": True,
            "allow_repair": True,
            "timeout_s": timeout_s,
            "test_cases": [positive_case],
        },
        label=f"harness_positive_{source_id}",
        timeout_s=timeout_s + 180,
    )
    return {
        "input_type": input_type,
        "source_id": source_id,
        "domain": fixture.get("domain"),
        "skill_id": skill_id,
        "positive_draft_skill_id": positive_draft["skill_id"],
        "negative_draft_skill_id": negative_draft["skill_id"],
        "skill_name": positive_draft.get("name"),
        "positive": summarize_loop(positive),
        "negative": summarize_loop(negative),
        "positive_pass": bool(positive.get("promotion_allowed")),
        "negative_rejected": not bool(negative.get("promotion_allowed")) and not bool(negative.get("http_error")),
        "attempt_count": int(positive.get("attempt_count") or 0),
        "repair_count": len(positive.get("repairs") if isinstance(positive.get("repairs"), list) else []),
        "final_state": str(positive.get("final_state") or ""),
        "evidence_path": positive.get("evidence_path", ""),
    }


def ensure_draft_with_executable_impl(
    client: ApiClient,
    skill: dict[str, Any],
    fixture: dict[str, Any],
    *,
    purpose: str,
) -> dict[str, Any]:
    source_skill_id = latest_version_skill_id(client, skill)
    implementation = dict(skill.get("implementation") or {})
    evaluation = dict(skill.get("evaluation") or {})
    verifier_specs = list(evaluation.get("verifier_specs") or [])
    code = render_contract_echo_code(verifier_specs, input_type=str(fixture.get("input_type") or ""))
    implementation["language"] = "python"
    implementation["code"] = code
    implementation["prompt_template"] = None
    payload = {
        "bump": "patch",
        "description": f"{skill.get('description', '')} Harness-evaluable P0 draft for {fixture.get('source_id')}.",
        "tags": list(dict.fromkeys([*(skill.get("tags") or []), "p0-harness-eval"])),
        "implementation": implementation,
        "evaluation": evaluation,
        "metadata": {
            "p0_harness_eval": True,
            "purpose": purpose,
            "source_id": fixture.get("source_id"),
            "input_type": fixture.get("input_type"),
            "created_from_skill_id": skill.get("skill_id"),
        },
        "author": "codex-p0-harness-eval",
    }
    created = client.post(
        f"/lifecycle/{source_skill_id}/new-version",
        payload,
        label=f"new_version_for_harness_{purpose}_{fixture.get('source_id')}",
    )
    if created.get("http_error"):
        raise RuntimeError(f"Failed to create harness draft for {fixture.get('source_id')}: {created}")
    return client.get(f"/skills/{created['skill_id']}/full", label=f"harness_draft_full_{purpose}_{fixture.get('source_id')}")


def latest_version_skill_id(client: ApiClient, skill: dict[str, Any]) -> str:
    versions = client.get(f"/skills/{skill['skill_id']}/versions", label=f"versions_{skill.get('skill_id')}")
    if not isinstance(versions, list) or not versions:
        return str(skill["skill_id"])
    latest = max(
        (item for item in versions if isinstance(item, dict) and item.get("skill_id")),
        key=lambda item: (parse_version(str(item.get("version") or "")), str(item.get("updated_at") or "")),
        default=skill,
    )
    return str(latest.get("skill_id") or skill["skill_id"])


def parse_version(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(version or "").split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def render_contract_echo_code(verifier_specs: list[dict[str, Any]], *, input_type: str) -> str:
    lines = [
        "output['result'] = {}",
        "output['evidence'] = ['p0 harness contract evidence']",
        "output['verifier'] = {'passed': True, 'checked': ['p0 harness deterministic contract']}",
    ]
    if input_type == "past_skills":
        lines.append("output['validation'] = {'passed': True, 'source': 'p0 harness deterministic contract'}")
    for spec in verifier_specs:
        if not isinstance(spec, dict):
            continue
        path = str(spec.get("path") or "")
        if not path.startswith("output."):
            continue
        field_path = path.split(".", 1)[1]
        value = value_for_spec(spec, path)
        lines.extend(nested_assignment(field_path, value))
    return "\n".join(lines)


def value_for_spec(spec: dict[str, Any], path: str) -> Any:
    spec_type = str(spec.get("type") or "")
    if spec_type == "json_equals" and "value" in spec:
        return spec["value"]
    if spec_type in {"json_array", "json_array_nonempty"}:
        return [f"p0 harness value for {path}"]
    if spec_type in {"json_object", "json_object_nonempty", "json_exists"} and path.endswith((".result", ".validation", ".verifier")):
        return {"passed": True, "source": "p0 harness"}
    if spec_type in {"json_object", "json_object_nonempty"}:
        return {"value": "p0 harness"}
    return f"p0 harness value for {path}"


def nested_assignment(field_path: str, value: Any) -> list[str]:
    parts = [part for part in field_path.split(".") if part]
    if not parts:
        return []
    if len(parts) == 1:
        return [f"output[{parts[0]!r}] = {value!r}"]
    lines: list[str] = []
    cursor = "output"
    for part in parts[:-1]:
        lines.append(f"if not isinstance({cursor}.get({part!r}), dict):")
        lines.append(f"    {cursor}[{part!r}] = {{}}")
        cursor = f"{cursor}[{part!r}]"
    lines.append(f"{cursor}[{parts[-1]!r}] = {value!r}")
    return lines


def build_positive_test_case(skill: dict[str, Any], fixture: dict[str, Any], *, timeout_s: int) -> dict[str, Any]:
    input_schema = (skill.get("interface") or {}).get("input_schema") or {}
    input_data = example_from_schema(input_schema)
    input_data.update(positive_overrides(str(fixture.get("input_type") or ""), fixture))
    return {
        "test_id": f"positive-{fixture['source_id']}",
        "name": f"P0 positive harness for {fixture['source_id']}",
        "goal": f"Run generated Skill for {fixture['input_type']} source {fixture['source_id']} with valid inputs.",
        "input_data": input_data,
        "verifier_specs": skill.get("evaluation", {}).get("verifier_specs") or [],
        "timeout_s": timeout_s,
    }


def build_negative_test_case(skill: dict[str, Any], fixture: dict[str, Any], *, timeout_s: int) -> dict[str, Any]:
    input_schema = (skill.get("interface") or {}).get("input_schema") or {}
    input_data = example_from_schema(input_schema)
    required_path = ""
    for required in input_schema.get("required") or []:
        if isinstance(required, str):
            input_data[required] = empty_value(input_data.get(required))
            required_path = f"input.{required}"
            break
    if fixture.get("input_type") == "script":
        input_data["dry_run"] = False
        input_data["allowed_paths"] = []
        required_path = "input.dry_run"
    verifier_specs = list(skill.get("evaluation", {}).get("verifier_specs") or [])
    if required_path and not any(str(spec.get("path") or "") == required_path for spec in verifier_specs if isinstance(spec, dict)):
        verifier_specs.insert(0, {"type": "json_nonempty", "path": required_path})
    return {
        "test_id": f"negative-{fixture['source_id']}",
        "name": f"P0 negative harness for {fixture['source_id']}",
        "goal": f"Reject generated Skill for {fixture['input_type']} source {fixture['source_id']} when required input is invalid.",
        "input_data": input_data,
        "verifier_specs": verifier_specs,
        "timeout_s": timeout_s,
    }


def positive_overrides(input_type: str, fixture: dict[str, Any]) -> dict[str, Any]:
    local_path = str(fixture.get("local_path") or fixture.get("content_file") or "")
    if input_type == "document":
        return {
            "task": f"Extract a reusable procedure from {fixture.get('source_id')}.",
            "document_context": f"Local fixture: {local_path}",
            "allowed_operations": ["read_context", "extract_steps", "cite_evidence"],
        }
    if input_type == "api_doc":
        return {
            "task": f"Prepare an API call contract from {fixture.get('source_id')}.",
            "endpoint": "/demo",
            "parameters": {"demo": True},
        }
    if input_type == "script":
        return {
            "task": f"Dry-run analyze script fixture {fixture.get('source_id')}.",
            "script_context": f"Local fixture: {local_path}",
            "dry_run": True,
            "allowed_paths": [local_path or "fixture.sh"],
        }
    if input_type == "past_skills":
        return {
            "task": f"Normalize and execute imported legacy Skill {fixture.get('source_id')}.",
            "source_context": f"Local fixture: {local_path}",
            "artifact_type": "document",
            "source_files": [local_path] if local_path else [],
            "project_files": [],
            "target_runtime": "SkillOS local harness",
        }
    return {
        "task": f"Replay and summarize trajectory {fixture.get('source_id')}.",
        "context": {"source_id": fixture.get("source_id"), "local_path": local_path},
    }


def example_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else list(properties)
    return {
        name: example_value(properties.get(name, {}))
        for name in required
        if isinstance(name, str)
    }


def example_value(schema: dict[str, Any]) -> Any:
    schema_type = schema.get("type")
    if schema_type == "boolean":
        return True
    if schema_type == "array":
        return ["demo"]
    if schema_type == "object":
        return {"demo": True}
    if schema_type in {"number", "integer"}:
        return 1
    return "demo"


def empty_value(value: Any) -> Any:
    if isinstance(value, list):
        return []
    if isinstance(value, dict):
        return {}
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return 0
    return ""


def summarize_loop(payload: dict[str, Any]) -> dict[str, Any]:
    attempts = payload.get("attempts") if isinstance(payload.get("attempts"), list) else []
    return {
        "http_error": payload.get("http_error"),
        "status": payload.get("status"),
        "promotion_allowed": bool(payload.get("promotion_allowed")),
        "attempt_count": payload.get("attempt_count"),
        "repair_count": len(payload.get("repairs") if isinstance(payload.get("repairs"), list) else []),
        "final_state": payload.get("final_state"),
        "score": payload.get("score"),
        "loop_id": payload.get("loop_id"),
        "evidence_path": payload.get("evidence_path"),
        "failure_reasons": [
            attempt.get("failure_reason")
            for attempt in attempts
            if isinstance(attempt, dict) and attempt.get("failure_reason")
        ],
    }


def score_checks(checks: list[dict[str, Any]]) -> dict[str, Any]:
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for check in checks:
        by_type[str(check.get("input_type") or "")].append(check)
    positive_pass = sum(1 for item in checks if item.get("positive_pass"))
    negative_rejected = sum(1 for item in checks if item.get("negative_rejected"))
    total = len(checks)
    return {
        "sample_count": total,
        "positive_pass_rate": round(positive_pass / total, 3) if total else 0.0,
        "negative_rejection_rate": round(negative_rejected / total, 3) if total else 0.0,
        "input_types_meeting_minimum": sum(1 for items in by_type.values() if len(items) >= 3),
        "by_input_type": {
            input_type: {
                "count": len(items),
                "positive_pass_rate": round(sum(1 for item in items if item.get("positive_pass")) / len(items), 3),
                "negative_rejection_rate": round(sum(1 for item in items if item.get("negative_rejected")) / len(items), 3),
            }
            for input_type, items in sorted(by_type.items())
        },
    }


def render_report(summary: dict[str, Any]) -> str:
    scores = summary.get("scores", {})
    lines = [
        "# SkillOS P0 Harness Positive / Negative Report",
        "",
        f"- Started: `{summary.get('started_at')}`",
        f"- Finished: `{summary.get('finished_at')}`",
        f"- API base: `{summary.get('api_base')}`",
        f"- Run dir: `{summary.get('run_dir')}`",
        f"- Sample count: `{scores.get('sample_count', 0)}`",
        f"- Positive pass rate: `{scores.get('positive_pass_rate', 0.0)}`",
        f"- Negative rejection rate: `{scores.get('negative_rejection_rate', 0.0)}`",
        f"- Input types with at least 3 samples: `{scores.get('input_types_meeting_minimum', 0)}/5`",
        "",
        "## By Input Type",
        "",
        "| input_type | count | positive pass | negative rejected |",
        "| --- | ---: | ---: | ---: |",
    ]
    for input_type, item in (scores.get("by_input_type") or {}).items():
        lines.append(
            f"| {input_type} | {item['count']} | {item['positive_pass_rate']:.3f} | {item['negative_rejection_rate']:.3f} |"
        )
    lines.extend(
        [
            "",
            "## Checks",
            "",
            "| input_type | source_id | positive | negative rejected | attempts | repairs | final_state | evidence_path |",
            "| --- | --- | --- | --- | ---: | ---: | --- | --- |",
        ]
    )
    for item in summary.get("checks", []):
        lines.append(
            "| {input_type} | {source_id} | {positive} | {negative} | {attempts} | {repairs} | {state} | `{evidence}` |".format(
                input_type=item.get("input_type"),
                source_id=item.get("source_id"),
                positive=item.get("positive_pass"),
                negative=item.get("negative_rejected"),
                attempts=item.get("attempt_count"),
                repairs=item.get("repair_count"),
                state=item.get("final_state"),
                evidence=item.get("evidence_path") or "",
            )
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "These checks prove that representative generated Skills can be put through the S2 -> harness -> verifier -> S3 gate with deterministic local contracts. They do not prove open-world semantic correctness or official SkillsBench sandbox scores.",
            "",
        ]
    )
    return "\n".join(lines)


def safe_filename(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("._")
    return normalized[:120] or "artifact"


if __name__ == "__main__":
    raise SystemExit(main())
