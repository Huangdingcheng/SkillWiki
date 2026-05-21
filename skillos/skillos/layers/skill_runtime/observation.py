"""Kernel-mode observation providers for execution feedback loops.

Observations are runtime evidence, not user-facing Skills.  Skills may hint at
what evidence is useful, but the execution kernel owns collection so every task
gets consistent, auditable state snapshots.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...models.skill_model import Skill
from ...utils.logger import get_logger
from .planner import PlanStep

logger = get_logger(__name__)


class ObservationManager:
    """Collect before/after observations around a single execution step."""

    def collect(
        self,
        *,
        phase: str,
        step: PlanStep,
        skill: Optional[Skill],
        state: Dict[str, Any],
        output: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        tool_calls = _tool_calls(skill)
        input_data = {**state, **(step.input_mapping or {})}
        observations: List[Dict[str, Any]] = []
        observations.append(_runtime_observation(phase, step, skill, output=output, error=error))

        if _needs_filesystem(tool_calls, input_data, output):
            observations.append(_filesystem_observation(input_data, output=output))
        if _needs_terminal(tool_calls, input_data, output):
            observations.append(_terminal_observation(input_data, output=output, error=error))
        if _needs_browser(tool_calls, input_data, output):
            observations.append(_browser_observation(input_data, output=output))
        if _needs_application(tool_calls, input_data, output):
            observations.append(_application_observation(input_data, output=output))

        return {
            "phase": phase,
            "step_id": step.step_id,
            "skill_name": step.skill_name,
            "collected_at": datetime.utcnow().isoformat(),
            "observations": [item for item in observations if item],
        }


def judge_step_observation(step: PlanStep, output: Dict[str, Any], observation_packet: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic first pass judgment for the execution loop.

    This is intentionally simple and structured. A later ValidationAgent can
    consume the same observation packet and make richer LLM judgments.
    """
    host_action = str(output.get("host_action") or "")
    success = bool(output.get("success") or output.get("launched"))
    issues: List[str] = []
    confidence = 0.85 if success else 0.2

    if host_action == "move_to_trash":
        fs = _first_observation(observation_packet, "filesystem")
        exists_after = fs.get("evidence", {}).get("exists")
        if exists_after is True:
            issues.append("source path still exists after move_to_trash")
            confidence = 0.35
        elif exists_after is False and success:
            confidence = 0.96
    elif host_action == "create_wps_document_from_text_file":
        fs = _first_observation(observation_packet, "filesystem")
        exists_after = fs.get("evidence", {}).get("exists")
        if exists_after is not True:
            issues.append("generated document was not observed on disk")
            confidence = 0.35
        elif success:
            confidence = 0.94
    elif host_action == "run_terminal_command":
        terminal = _first_observation(observation_packet, "terminal")
        if terminal.get("status") != "success":
            issues.append("terminal observation did not report success")
            confidence = 0.4
    elif host_action == "browser_gui_workflow":
        workflow_done = bool(output.get("success"))
        confidence = 0.7 if workflow_done else 0.45
        if not workflow_done:
            issues.append("browser workflow needs DOM/screenshot-driven controller to continue")
    elif host_action in {"open_file", "open_or_create_file_in_vscode"}:
        fs = _first_observation(observation_packet, "filesystem")
        if fs.get("status") == "missing":
            issues.append("target path is missing")
            confidence = 0.3

    matches = success and not issues
    return {
        "matches_step_goal": matches,
        "confidence": confidence,
        "next_action": "continue" if matches else "repair",
        "reason": "Structured observation supports the step result." if matches else "; ".join(issues) or "Step output did not report success.",
        "host_action": host_action,
    }


def _runtime_observation(
    phase: str,
    step: PlanStep,
    skill: Optional[Skill],
    *,
    output: Optional[Dict[str, Any]],
    error: Optional[str],
) -> Dict[str, Any]:
    return {
        "type": "runtime",
        "source": "RuntimeObservationProvider",
        "target": step.skill_name,
        "status": "error" if error else "success",
        "evidence": {
            "phase": phase,
            "step_status": step.status.value if hasattr(step.status, "value") else str(step.status),
            "skill_id": skill.skill_id if skill else step.skill_id,
            "skill_name": skill.name if skill else step.skill_name,
            "host_action": (output or {}).get("host_action"),
            "error": error,
        },
        "confidence": 0.9 if not error else 0.2,
    }


def _filesystem_observation(input_data: Dict[str, Any], *, output: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    raw_path = str(
        (output or {}).get("output_path")
        or (output or {}).get("path")
        or input_data.get("output_path")
        or input_data.get("path")
        or input_data.get("file_path")
        or ""
    )
    path = _expand_path(raw_path)
    if not path:
        return {
            "type": "filesystem",
            "source": "FileSystemObservationProvider",
            "target": raw_path,
            "status": "missing",
            "evidence": {"path": raw_path, "exists": False},
            "confidence": 0.2,
        }
    exists = path.exists()
    evidence: Dict[str, Any] = {
        "path": str(path),
        "exists": exists,
        "parent": str(path.parent),
    }
    if exists:
        try:
            stat = path.stat()
            evidence.update({
                "is_file": path.is_file(),
                "is_dir": path.is_dir(),
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
        except OSError as exc:
            evidence["stat_error"] = str(exc)
    return {
        "type": "filesystem",
        "source": "FileSystemObservationProvider",
        "target": str(path),
        "status": "success" if exists else "missing",
        "evidence": evidence,
        "confidence": 0.95,
    }


def _terminal_observation(
    input_data: Dict[str, Any],
    *,
    output: Optional[Dict[str, Any]],
    error: Optional[str],
) -> Dict[str, Any]:
    output = output or {}
    return {
        "type": "terminal",
        "source": "TerminalObservationProvider",
        "target": str(output.get("command") or input_data.get("command") or ""),
        "status": "error" if error else ("success" if output.get("success") else "unknown"),
        "evidence": {
            "command": output.get("command") or input_data.get("command"),
            "stdout_preview": output.get("stdout_preview"),
            "stderr_preview": output.get("stderr_preview"),
            "sensitive_output_redacted": output.get("sensitive_output_redacted"),
            "error": error,
        },
        "confidence": 0.9 if output.get("success") else 0.35,
    }


def _browser_observation(input_data: Dict[str, Any], *, output: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    output = output or {}
    return {
        "type": "browser",
        "source": "BrowserObservationProvider",
        "target": str(output.get("url") or input_data.get("url") or input_data.get("query") or ""),
        "status": "success" if output.get("success") or output.get("launched") else "unknown",
        "evidence": {
            "url": output.get("url") or input_data.get("url"),
            "query": output.get("query") or input_data.get("query"),
            "search_url": output.get("search_url"),
            "application": output.get("application"),
            "workflow_observations": output.get("observations"),
            "workflow_actions": output.get("actions"),
            "requires_visual_controller": output.get("requires_visual_controller"),
        },
        "confidence": 0.78,
        "fallback_available": ["dom_snapshot", "screenshot", "ocr"],
    }


def _application_observation(input_data: Dict[str, Any], *, output: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    output = output or {}
    app = str(output.get("application") or input_data.get("application") or "")
    frontmost = _frontmost_application() if platform.system().lower() == "darwin" else ""
    return {
        "type": "application",
        "source": "ApplicationObservationProvider",
        "target": app,
        "status": "success" if output.get("success") or output.get("launched") else "unknown",
        "evidence": {
            "application": app,
            "frontmost_app": frontmost,
            "launcher": output.get("launcher") or input_data.get("launcher"),
        },
        "confidence": 0.82 if frontmost or app else 0.45,
        "fallback_available": ["accessibility_tree", "screenshot", "ocr"],
    }


def _frontmost_application() -> str:
    if not shutil.which("osascript"):
        return ""
    command = [
        "osascript",
        "-e",
        'tell application "System Events" to get name of first application process whose frontmost is true',
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=2)
    except Exception as exc:
        logger.debug("frontmost app observation failed: %s", exc)
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _needs_filesystem(tool_calls: set[str], input_data: Dict[str, Any], output: Optional[Dict[str, Any]]) -> bool:
    return bool(
        input_data.get("path")
        or input_data.get("file_path")
        or input_data.get("source_path")
        or input_data.get("output_path")
        or (output or {}).get("path")
        or (output or {}).get("output_path")
        or tool_calls & {
            "host.open_file",
            "host.move_to_trash",
            "host.open_or_create_file_in_vscode",
            "host.write_downloads_text_file",
            "host.create_wps_document_from_text_file",
        }
    )


def _needs_terminal(tool_calls: set[str], input_data: Dict[str, Any], output: Optional[Dict[str, Any]]) -> bool:
    return bool(input_data.get("command") or (output or {}).get("command") or tool_calls & {"host.run_terminal_command", "host.run_terminal_top"})


def _needs_browser(tool_calls: set[str], input_data: Dict[str, Any], output: Optional[Dict[str, Any]]) -> bool:
    return bool(input_data.get("url") or input_data.get("query") or (output or {}).get("url") or tool_calls & {
        "host.open_chrome",
        "host.open_url_in_chrome",
        "host.open_search_first_result",
        "host.complete_chatgpt_note_task",
        "host.browser_gui_workflow",
    })


def _needs_application(tool_calls: set[str], input_data: Dict[str, Any], output: Optional[Dict[str, Any]]) -> bool:
    return bool(input_data.get("application") or (output or {}).get("application") or tool_calls & {
        "host.open_application",
        "host.open_chrome",
        "host.open_or_create_file_in_vscode",
        "host.create_wps_document_from_text_file",
    })


def _tool_calls(skill: Optional[Skill]) -> set[str]:
    if not skill or not skill.implementation:
        return set()
    return {str(item).strip().lower() for item in skill.implementation.tool_calls}


def _first_observation(packet: Dict[str, Any], observation_type: str) -> Dict[str, Any]:
    for item in packet.get("observations", []):
        if item.get("type") == observation_type:
            return item
    return {}


def _expand_path(raw_path: str) -> Optional[Path]:
    value = raw_path.strip()
    if not value:
        return None
    return Path(value).expanduser().resolve()
