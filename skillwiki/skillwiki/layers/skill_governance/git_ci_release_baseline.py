"""Strict, input-driven Git/CI release-evidence baseline."""
from __future__ import annotations
from typing import Any, Mapping
import math
import re
from datetime import datetime

DECISIONS = {"accept", "reject", "defer", "rollback"}
REQUIRED_FIELDS = ("source_identity", "version", "license", "manifest", "environment", "validator", "release_action")

def _defer(reason: str, evidence: Mapping[str, Any], errors: list[str] | None = None) -> dict[str, Any]:
    return {"decision": "defer", "reason": reason, "schema_errors": errors or [], "missing_fields": [f for f in REQUIRED_FIELDS if f not in evidence], "evidence_fields_present": sorted(str(k) for k in evidence), "release_executed": False}

def _validate(evidence: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    identity = evidence.get("source_identity")
    if not isinstance(identity, Mapping): errors.append("source_identity must be an object")
    else:
        for field in ("repository", "commit", "version", "license"):
            if not isinstance(identity.get(field), str) or not identity[field].strip(): errors.append(f"source_identity.{field} must be a nonempty string")
        if type(identity.get("matches")) is not bool: errors.append("source_identity.matches must be boolean")
    for field in ("version", "license"):
        if not isinstance(evidence.get(field), str) or not evidence[field].strip(): errors.append(f"{field} must be a nonempty string")
    manifest = evidence.get("manifest")
    if not isinstance(manifest, Mapping) or type(manifest.get("present")) is not bool: errors.append("manifest.present must be boolean")
    elif not isinstance(manifest.get("package_digest"), str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", manifest["package_digest"]) or not isinstance(manifest.get("file_hashes"), Mapping) or not manifest["file_hashes"] or any(not isinstance(k, str) or not k or not isinstance(v, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", v) for k, v in manifest["file_hashes"].items()): errors.append("manifest digest and file_hashes must be nonempty sha256 values")
    environment = evidence.get("environment")
    if not isinstance(environment, Mapping) or environment.get("status") not in {"known", "unknown"} or not isinstance(environment.get("dependency_manifest"), str) or not environment["dependency_manifest"].strip(): errors.append("environment.status and dependency_manifest are required")
    validator = evidence.get("validator")
    if not isinstance(validator, Mapping): errors.append("validator must be an object")
    else:
        if validator.get("status") not in {"success", "failed"}: errors.append("validator.status must be success or failed")
        command = validator.get("command")
        if not isinstance(command, (list, tuple)) or not command or any(not isinstance(item, str) or not item for item in command): errors.append("validator.command must be nonempty argv")
        for field in ("stdout", "stderr", "termination", "started_at", "ended_at"):
            if not isinstance(validator.get(field), str): errors.append(f"validator.{field} must be a string")
        for field in ("started_at", "ended_at"):
            if isinstance(validator.get(field), str):
                try: datetime.fromisoformat(validator[field].replace("Z", "+00:00"))
                except ValueError: errors.append(f"validator.{field} must be ISO timestamp")
        if type(validator.get("exit_code")) is not int: errors.append("validator.exit_code must be an integer")
        if not isinstance(validator.get("duration_seconds"), (int, float)) or isinstance(validator.get("duration_seconds"), bool) or not math.isfinite(validator.get("duration_seconds", float("nan"))) or validator.get("duration_seconds", -1) < 0: errors.append("validator.duration_seconds must be finite nonnegative numeric")
    action = evidence.get("release_action")
    if not isinstance(action, Mapping) or action.get("action") not in {"release", "rollback"}: errors.append("release_action.action must be release or rollback")
    return errors

def git_ci_release_decision(evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Return a stable decision without executing a release."""
    if not isinstance(evidence, Mapping): return _defer("evidence must be an object", {}, ["evidence object required"])
    if any(field not in evidence for field in REQUIRED_FIELDS): return _defer("missing evidence fields", evidence)
    errors = _validate(evidence)
    if errors: return _defer("invalid evidence schema", evidence, errors)
    validator = evidence["validator"]
    if validator["status"] != "success" or validator["exit_code"] != 0: decision, reason = "reject", "validator failed"
    elif evidence["release_action"]["action"] == "rollback": decision, reason = "rollback", "explicit rollback request"
    elif evidence["license"] in {"missing", "uncertain"}: decision, reason = "defer", "license unresolved"
    elif not evidence["manifest"]["present"]: decision, reason = "reject", "manifest absent"
    elif evidence["source_identity"].get("matches") is False: decision, reason = "reject", "source identity mismatch"
    elif evidence["environment"]["status"] == "unknown": decision, reason = "defer", "environment/dependency unresolved"
    else: decision, reason = "accept", "all release evidence fields pass"
    return {"decision": decision, "reason": reason, "schema_errors": [], "missing_fields": [], "evidence_fields_present": sorted(str(k) for k in evidence), "release_executed": False}

def compare_release_evidence_paths(*, baseline_evidence: Mapping[str, Any], skillwiki_decision: str) -> dict[str, Any]:
    if skillwiki_decision not in DECISIONS: raise ValueError(f"unknown SkillWiki decision: {skillwiki_decision}")
    baseline = git_ci_release_decision(baseline_evidence)
    return {"baseline_decision": baseline["decision"], "skillwiki_decision": skillwiki_decision, "same_decision": baseline["decision"] == skillwiki_decision, "baseline": baseline, "shared_input_fields": sorted(str(k) for k in baseline_evidence)}
