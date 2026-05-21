"""Host information survey routes.

These endpoints collect read-only host context for the graph. The survey is a
kernel-mode knowledge acquisition flow: an agent may propose terminal commands,
but every command is validated against a narrow allowlist before execution.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ...models.graph_model import (
    GraphNodeType,
    GraphRelationType,
    HeterogeneousGraphEdge,
    HeterogeneousGraphNode,
)
from ...utils.llm_client import Message
from ..deps import AppState, get_app_state

router = APIRouter(prefix="/host-info", tags=["host-info"])


class HostSurveyRequest(BaseModel):
    task_ids: Optional[List[str]] = None
    use_llm: bool = True
    persist: bool = True
    max_output_chars: int = Field(default=4000, ge=500, le=20000)


class HostSurveyCommandOut(BaseModel):
    task_id: str
    name: str
    description: str
    command: List[str]
    command_source: str
    status: str
    summary: str = ""
    node_id: Optional[str] = None
    stdout_preview: str = ""
    error: Optional[str] = None


class HostSurveyResponse(BaseModel):
    success: bool
    run_id: str
    created_nodes: int
    created_edges: int
    commands: List[HostSurveyCommandOut]
    agent_trace: List[Dict[str, Any]] = Field(default_factory=list)


HOST_SURVEY_PRESETS: List[Dict[str, Any]] = [
    {
        "task_id": "system_overview",
        "name": "Host System Overview",
        "description": "Collect macOS version, kernel, architecture, CPU, and hostname.",
        "fallback_command": ["system_profiler", "SPSoftwareDataType", "SPHardwareDataType"],
        "labels": ["host", "system", "macos", "hardware"],
    },
    {
        "task_id": "installed_applications",
        "name": "Installed GUI Applications",
        "description": "List installed macOS applications for later app-launch task grounding.",
        "fallback_command": ["find", "/Applications", "-maxdepth", "2", "-name", "*.app", "-print"],
        "labels": ["host", "applications", "software", "gui"],
    },
    {
        "task_id": "developer_tools",
        "name": "Developer Tool Availability",
        "description": "Discover common command-line developer tools available on PATH.",
        "fallback_command": ["which", "python3", "node", "npm", "git", "code", "conda"],
        "labels": ["host", "developer-tools", "terminal", "path"],
    },
    {
        "task_id": "desktop_shallow_index",
        "name": "Desktop Shallow File Index",
        "description": "Index top-level and one-level-deep Desktop files/folders without reading file contents.",
        "fallback_command": ["find", "~/Desktop", "-maxdepth", "2", "-print"],
        "labels": ["host", "files", "desktop", "finder"],
    },
    {
        "task_id": "downloads_recent_index",
        "name": "Downloads Recent File Index",
        "description": "Index shallow Downloads files/folders as reusable context for file-opening tasks.",
        "fallback_command": ["find", "~/Downloads", "-maxdepth", "2", "-print"],
        "labels": ["host", "files", "downloads", "finder"],
    },
    {
        "task_id": "safe_environment_summary",
        "name": "Safe Environment Summary",
        "description": "Collect non-secret environment context after redacting credentials and tokens.",
        "fallback_command": ["printenv"],
        "labels": ["host", "environment", "shell", "terminal"],
    },
]

SAFE_EXECUTABLES = {
    "arch",
    "df",
    "find",
    "hostname",
    "ls",
    "printenv",
    "pwd",
    "sw_vers",
    "sysctl",
    "system_profiler",
    "uname",
    "which",
}

SECRET_KEY_PATTERN = re.compile(r"(key|token|secret|password|passwd|credential|auth|bearer)", re.IGNORECASE)


@router.get("/presets", response_model=List[Dict[str, Any]])
async def list_host_survey_presets() -> List[Dict[str, Any]]:
    return [
        {
            "task_id": item["task_id"],
            "name": item["name"],
            "description": item["description"],
            "labels": item["labels"],
            "fallback_command": item["fallback_command"],
        }
        for item in HOST_SURVEY_PRESETS
    ]


@router.post("/survey", response_model=HostSurveyResponse)
async def run_host_survey(
    req: HostSurveyRequest,
    app: AppState = Depends(get_app_state),
) -> HostSurveyResponse:
    if not app.graph or not hasattr(app.graph, "upsert_node"):
        raise HTTPException(status_code=503, detail="Graph manager does not support host information nodes.")

    selected = _selected_presets(req.task_ids)
    run_id = f"host_survey:{uuid.uuid4()}"
    agent_node_id = "agent:host_survey_agent"
    created_nodes = 0
    created_edges = 0
    outputs: List[HostSurveyCommandOut] = []
    trace: List[Dict[str, Any]] = []

    if req.persist:
        await app.graph.upsert_node(HeterogeneousGraphNode(
            node_id=agent_node_id,
            node_type=GraphNodeType.AGENT,
            name="Host Survey Agent",
            description="Kernel-mode agent that translates safe host-inspection tasks into read-only terminal commands.",
            labels=["kernel", "host-survey", "agent"],
            metadata={"visibility": "kernel", "mode": "host_information_collection"},
        ))
        created_nodes += 1

    for preset in selected:
        command, source, reason = _plan_command(app, preset, use_llm=req.use_llm)
        trace.append({
            "agent": "HostSurveyAgent",
            "action": "plan_safe_terminal_command",
            "status": source,
            "details": {
                "task_id": preset["task_id"],
                "command": command,
                "reason": reason,
            },
        })

        safe_command, safe_source = _validate_or_fallback(command, source, preset["fallback_command"])
        status, stdout, error = _run_safe_command(safe_command, max_chars=req.max_output_chars)
        summary = _summarize_host_output(preset, stdout, error)
        node_id = f"host_info:{preset['task_id']}:{_stable_suffix(summary or stdout or error)}"

        if req.persist:
            await app.graph.upsert_node(HeterogeneousGraphNode(
                node_id=node_id,
                node_type=GraphNodeType.HOST_INFORMATION,
                name=preset["name"],
                description=summary or preset["description"],
                labels=preset["labels"],
                source_type="host_survey",
                metadata={
                    "visibility": "kernel",
                    "task_id": preset["task_id"],
                    "command": safe_command,
                    "command_source": safe_source,
                    "collected_at": datetime.utcnow().isoformat(),
                    "stdout_preview": stdout[: req.max_output_chars],
                    "error": error,
                    "embedding_name": _embedding_name(preset),
                },
            ))
            created_nodes += 1
            await app.graph.upsert_edge(HeterogeneousGraphEdge(
                edge_id=f"{run_id}:produced_by:{preset['task_id']}",
                source_id=node_id,
                target_id=agent_node_id,
                relation_type=GraphRelationType.PRODUCED_BY,
                weight=0.92,
                confidence=0.96 if status == "success" else 0.55,
                description="Host information node produced by the Host Survey Agent.",
                metadata={"source": "host_survey", "task_id": preset["task_id"]},
                created_by="HostSurveyAgent",
            ))
            created_edges += 1

        outputs.append(HostSurveyCommandOut(
            task_id=preset["task_id"],
            name=preset["name"],
            description=preset["description"],
            command=safe_command,
            command_source=safe_source,
            status=status,
            summary=summary,
            node_id=node_id if req.persist else None,
            stdout_preview=stdout[: req.max_output_chars],
            error=error,
        ))

    return HostSurveyResponse(
        success=all(item.status == "success" for item in outputs),
        run_id=run_id,
        created_nodes=created_nodes,
        created_edges=created_edges,
        commands=outputs,
        agent_trace=trace,
    )


def _selected_presets(task_ids: Optional[List[str]]) -> List[Dict[str, Any]]:
    if not task_ids:
        return HOST_SURVEY_PRESETS
    wanted = set(task_ids)
    selected = [item for item in HOST_SURVEY_PRESETS if item["task_id"] in wanted]
    missing = wanted - {item["task_id"] for item in selected}
    if missing:
        raise HTTPException(status_code=404, detail=f"Unknown host survey task(s): {sorted(missing)}")
    return selected


def _plan_command(app: AppState, preset: Dict[str, Any], *, use_llm: bool) -> tuple[List[str], str, str]:
    if not use_llm or not app.llm:
        return list(preset["fallback_command"]), "fallback", "LLM planning disabled."

    prompt = f"""
You are SkillOS HostSurveyAgent. Propose exactly one read-only macOS terminal command for this host-information task.

Task ID: {preset['task_id']}
Task name: {preset['name']}
Task description: {preset['description']}

Allowed executables: {sorted(SAFE_EXECUTABLES)}

Rules:
- Return JSON only.
- The command must be an argv array, not a shell string.
- No pipes, redirection, command substitution, sudo, network calls, file reads, deletes, writes, or chmod/chown.
- Prefer concise commands that list metadata, not file contents.

Output:
{{"command": ["find", "/Applications", "-maxdepth", "2", "-name", "*.app", "-print"], "reason": "why this is safe and useful"}}
"""
    try:
        response = app.llm.chat(
            [
                Message.system("You are a careful host-survey command planner. Return strict JSON."),
                Message.user(prompt),
            ],
            temperature=0.0,
            max_tokens=220,
        )
        data = _extract_json(response.content)
        command = data.get("command") if isinstance(data, dict) else None
        reason = str(data.get("reason", "")) if isinstance(data, dict) else ""
        if isinstance(command, list) and all(isinstance(item, str) for item in command):
            return command, "llm", reason or "LLM proposed an allowlisted read-only command."
    except Exception as exc:
        return list(preset["fallback_command"]), "fallback", f"LLM planning failed: {exc}"

    return list(preset["fallback_command"]), "fallback", "LLM response was not a valid command array."


def _validate_or_fallback(command: List[str], source: str, fallback: List[str]) -> tuple[List[str], str]:
    if _is_safe_command(command):
        return command, source
    return list(fallback), "fallback_safety"


def _is_safe_command(command: List[str]) -> bool:
    if not command:
        return False
    executable = Path(command[0]).name
    if executable not in SAFE_EXECUTABLES:
        return False
    forbidden = {";", "&&", "||", "|", ">", ">>", "<", "`", "$(", "sudo", "rm", "mv", "cp", "curl", "wget", "chmod", "chown"}
    return not any(any(token in part for token in forbidden) for part in command)


def _run_safe_command(command: List[str], *, max_chars: int) -> tuple[str, str, Optional[str]]:
    expanded = [_expand_arg(part) for part in command]
    try:
        completed = subprocess.run(
            expanded,
            capture_output=True,
            text=True,
            timeout=12,
            check=False,
        )
    except Exception as exc:
        return "failed", "", str(exc)

    stdout = _sanitize_output(completed.stdout or "", max_chars=max_chars)
    stderr = _sanitize_output(completed.stderr or "", max_chars=1000)
    if completed.returncode != 0 and not stdout:
        return "failed", stdout, stderr or f"Command exited with status {completed.returncode}"
    return "success", stdout, stderr or None


def _expand_arg(value: str) -> str:
    if value.startswith("~/"):
        return str(Path.home() / value[2:])
    return value


def _sanitize_output(raw: str, *, max_chars: int) -> str:
    lines: List[str] = []
    for line in raw.splitlines():
        if SECRET_KEY_PATTERN.search(line):
            key = line.split("=", 1)[0] if "=" in line else line[:80]
            lines.append(f"{key}=<redacted>")
            continue
        if re.search(r"(Serial Number|Hardware UUID|Provisioning UDID|Activation Lock|User Name|Computer Name)", line, re.IGNORECASE):
            key = line.split(":", 1)[0].strip() if ":" in line else line[:80]
            lines.append(f"{key}: <redacted>")
            continue
        lines.append(line)
        if sum(len(item) + 1 for item in lines) >= max_chars:
            lines.append("<truncated>")
            break
    return "\n".join(lines)[:max_chars]


def _summarize_host_output(preset: Dict[str, Any], stdout: str, error: Optional[str]) -> str:
    if error and not stdout:
        return f"{preset['name']} collection failed: {error}"
    lines = [line for line in stdout.splitlines() if line.strip()]
    if preset["task_id"] == "installed_applications":
        apps = [Path(line).stem for line in lines[:18]]
        return f"Found {len(lines)} installed application path(s). Examples: {', '.join(apps[:12])}."
    if preset["task_id"] in {"desktop_shallow_index", "downloads_recent_index"}:
        return f"Indexed {len(lines)} shallow path(s) for {preset['name'].replace(' Shallow File Index', '')}."
    if preset["task_id"] == "developer_tools":
        return f"Detected developer tool paths: {', '.join(lines[:12]) or 'none'}."
    if preset["task_id"] == "safe_environment_summary":
        return f"Collected {len(lines)} sanitized environment variable line(s), with secret-like keys redacted."
    return " ".join(lines[:8])[:700] or preset["description"]


def _embedding_name(preset: Dict[str, Any]) -> str:
    return f"{preset['name']} / {preset['task_id']} / {' '.join(preset['labels'])}"


def _stable_suffix(value: str) -> str:
    import hashlib

    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]


def _extract_json(text: str) -> Dict[str, Any]:
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{[\s\S]+\}", text)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except Exception:
        return {}
