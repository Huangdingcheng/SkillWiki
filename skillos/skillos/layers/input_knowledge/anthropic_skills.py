"""Anthropic Agent Skills compatibility importer.

Anthropic skills are directory-based packages whose canonical contract is a
`SKILL.md` file with YAML frontmatter and markdown instructions. SkillOS stores
them as final immutable Skill records while preserving the original instructions
and resource inventory for retrieval/execution context.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from ...models.skill_model import (
    Skill,
    SkillImplementation,
    SkillInterface,
    SkillProvenance,
    SkillState,
    SkillType,
    SkillVisibility,
)


ANTHROPIC_SOURCE_FORMAT = "anthropic_agent_skill"


@dataclass
class AnthropicSkillImportResult:
    skills: List[Skill] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)


def discover_anthropic_skill_dirs(root: str | Path) -> List[Path]:
    """Return directories that contain an Anthropic-compatible SKILL.md."""
    root_path = Path(root).expanduser().resolve()
    if root_path.is_file() and root_path.name == "SKILL.md":
        return [root_path.parent]
    if not root_path.exists():
        raise FileNotFoundError(f"Anthropic skills path does not exist: {root_path}")
    search_root = root_path / "skills" if (root_path / "skills").is_dir() else root_path
    return sorted(path.parent for path in search_root.rglob("SKILL.md"))


def load_anthropic_skills(root: str | Path, *, namespace: str = "anthropic") -> AnthropicSkillImportResult:
    result = AnthropicSkillImportResult()
    try:
        skill_dirs = discover_anthropic_skill_dirs(root)
    except Exception as exc:
        result.errors.append(str(exc))
        return result

    for skill_dir in skill_dirs:
        try:
            if skill_dir.name in {"template"}:
                result.skipped.append(str(skill_dir))
                continue
            result.skills.append(convert_anthropic_skill_dir(skill_dir, namespace=namespace))
        except Exception as exc:
            result.errors.append(f"{skill_dir}: {exc}")
    return result


def convert_anthropic_skill_dir(skill_dir: str | Path, *, namespace: str = "anthropic") -> Skill:
    skill_path = Path(skill_dir).expanduser().resolve()
    skill_md = skill_path / "SKILL.md"
    if not skill_md.exists():
        raise FileNotFoundError(f"Missing SKILL.md in {skill_path}")
    raw = skill_md.read_text(encoding="utf-8")
    frontmatter, body = _parse_frontmatter(raw)
    original_name = str(frontmatter.get("name") or skill_path.name).strip()
    if not original_name:
        raise ValueError("Anthropic SKILL.md is missing a name")
    description = str(frontmatter.get("description") or _first_markdown_paragraph(body) or original_name).strip()
    license_text = str(frontmatter.get("license") or "").strip()
    resources = _collect_resources(skill_path)
    content_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    normalized_name = _normalize_skill_name(f"{namespace}_{original_name}")
    tags = _dedupe([
        "anthropic",
        "agent-skill",
        "final",
        "immutable",
        *_infer_tags(original_name, description, resources),
    ])
    domain = _infer_domain(original_name, description, resources)
    skill_type = _infer_skill_type(original_name, description, resources)
    prompt_template = _build_prompt_template(
        original_name=original_name,
        description=description,
        body=body,
        resources=resources,
    )

    creation_context: Dict[str, Any] = {
        "source_format": ANTHROPIC_SOURCE_FORMAT,
        "original_name": original_name,
        "source_directory": str(skill_path),
        "skill_md_sha256": content_hash,
        "license": license_text,
        "resource_files": resources,
        "frontmatter": frontmatter,
    }
    return Skill(
        name=normalized_name,
        version="1.0.0",
        display_name=f"Anthropic {original_name.replace('-', ' ').title()}",
        description=description,
        tags=tags,
        source_format=ANTHROPIC_SOURCE_FORMAT,
        is_final=True,
        immutable=True,
        skill_type=skill_type,
        domain=domain,
        granularity_level=2 if skill_type == SkillType.FUNCTIONAL else 3,
        visibility=SkillVisibility.USER,
        state=SkillState.RELEASED,
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "The user task that should be handled using this Anthropic Agent Skill.",
                    },
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional local files relevant to the task.",
                    },
                    "context": {
                        "type": "object",
                        "description": "Optional runtime context and constraints.",
                    },
                },
                "required": ["task"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "result": {"type": "string"},
                    "artifacts": {"type": "array", "items": {"type": "string"}},
                    "notes": {"type": "string"},
                },
            },
            preconditions=[
                "Load the original SKILL.md instructions before applying this skill.",
                "Use referenced resource files only when the task requires them.",
            ],
            postconditions=[
                "The result follows the original Anthropic Agent Skill instructions.",
            ],
            side_effects=[
                "May read or create local artifacts depending on the concrete task.",
            ],
        ),
        implementation=SkillImplementation(
            language="anthropic_agent_skill",
            prompt_template=prompt_template,
            tool_calls=_infer_tool_calls(original_name, description, resources),
        ),
        doc_refs=[str(skill_md), *[str(skill_path / rel) for rel in resources]],
        provenance=SkillProvenance(
            source_type=ANTHROPIC_SOURCE_FORMAT,
            source_ids=[str(skill_path)],
            created_by_agent="anthropic_skill_importer",
            creation_context=creation_context,
        ),
    )


def _parse_frontmatter(raw: str) -> tuple[Dict[str, Any], str]:
    if not raw.startswith("---"):
        return {}, raw
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", raw, flags=re.DOTALL)
    if not match:
        return {}, raw
    frontmatter_text, body = match.groups()
    return _parse_simple_yaml(frontmatter_text), body


def _parse_simple_yaml(text: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    current_key: Optional[str] = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith(" ") and current_key:
            data[current_key] = f"{data.get(current_key, '')}\n{line.strip()}".strip()
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current_key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] in {'"', "'"} and value[-1] == value[0]:
            value = value[1:-1]
        data[current_key] = value
    return data


def _collect_resources(skill_path: Path) -> List[str]:
    resources: List[str] = []
    for path in sorted(skill_path.rglob("*")):
        if not path.is_file() or path.name == "SKILL.md":
            continue
        if ".git" in path.parts:
            continue
        resources.append(path.relative_to(skill_path).as_posix())
    return resources


def _build_prompt_template(*, original_name: str, description: str, body: str, resources: List[str]) -> str:
    resource_text = "\n".join(f"- {item}" for item in resources) if resources else "- none"
    return (
        f"You are executing the Anthropic Agent Skill `{original_name}`.\n\n"
        f"Skill description:\n{description}\n\n"
        "Original SKILL.md instructions:\n"
        f"{body.strip()}\n\n"
        "Packaged resource files available in the original skill directory:\n"
        f"{resource_text}\n\n"
        "Runtime task:\n{task}\n\n"
        "Runtime files:\n{files}\n\n"
        "Runtime context:\n{context}\n\n"
        "Follow the original SKILL.md instructions first. Treat SkillOS graph and "
        "retrieved local skills as auxiliary context only when they do not conflict "
        "with this final Anthropic skill."
    )


def _first_markdown_paragraph(body: str) -> str:
    for block in re.split(r"\n\s*\n", body.strip()):
        cleaned = re.sub(r"^#+\s*", "", block.strip())
        if cleaned and not cleaned.startswith("```"):
            return cleaned[:280]
    return ""


def _normalize_skill_name(value: str) -> str:
    value = value.strip().lower().replace("-", "_")
    value = re.sub(r"[^a-z0-9_]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    if not value or not value[0].isalpha():
        value = f"skill_{value}"
    return value[:120]


def _infer_domain(name: str, description: str, resources: Iterable[str]) -> str:
    text = " ".join([name, description, *resources]).lower()
    if any(token in text for token in ["pdf", "docx", "xlsx", "pptx", "document"]):
        return "document"
    if any(token in text for token in ["frontend", "webapp", "html", "playwright", "artifact"]):
        return "web"
    if any(token in text for token in ["api", "mcp", "server"]):
        return "api"
    if any(token in text for token in ["design", "brand", "canvas", "theme", "art"]):
        return "design"
    return "general"


def _infer_skill_type(name: str, description: str, resources: Iterable[str]) -> SkillType:
    text = " ".join([name, description, *resources]).lower()
    if any(token in text for token in ["coauthoring", "factory", "builder", "testing", "creator"]):
        return SkillType.STRATEGIC
    return SkillType.FUNCTIONAL


def _infer_tags(name: str, description: str, resources: Iterable[str]) -> List[str]:
    text = " ".join([name, description, *resources]).lower()
    candidates = [
        "pdf", "docx", "xlsx", "pptx", "document", "frontend", "webapp",
        "playwright", "design", "brand", "canvas", "theme", "api", "mcp",
        "slack", "artifact", "testing", "claude",
    ]
    return [tag for tag in candidates if tag in text]


def _infer_tool_calls(name: str, description: str, resources: Iterable[str]) -> List[str]:
    text = " ".join([name, description, *resources]).lower()
    calls = ["llm.apply_anthropic_skill"]
    if "playwright" in text or "webapp" in text:
        calls.append("python.playwright")
    if any(ext in text for ext in ["pdf", "docx", "xlsx", "pptx"]):
        calls.append("python.document_processing")
    if "mcp" in text:
        calls.append("mcp.server_builder")
    return _dedupe(calls)


def _dedupe(values: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        normalized = str(value).strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result
