"""Skill 生命周期管理路由。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..deps import AppState, get_app_state
from ..schemas import (
    DeprecateRequest,
    NewVersionRequest,
    OKResponse,
    ReleaseRequest,
    SkillSummary,
    TransitionRequest,
    MergeSkillsRequest,
    MergeUpdateRequest,
    SplitSkillRequest,
)

router = APIRouter(prefix="/lifecycle", tags=["lifecycle"])


def _to_summary(skill):
    from .skills import _to_summary as ts
    return ts(skill)

@router.post("/merge", response_model=Dict[str, Any])
async def merge_skills(
    req: MergeSkillsRequest,
    app: AppState = Depends(get_app_state),
) -> Dict[str, Any]:
    if not app.merger:
        raise HTTPException(status_code=500, detail="SkillMerger 未初始化")

    skill_a = await app.wiki.get(req.skill_a_id)
    skill_b = await app.wiki.get(req.skill_b_id)
    if not skill_a:
        raise HTTPException(status_code=404, detail=f"Skill {req.skill_a_id} 不存在")
    if not skill_b:
        raise HTTPException(status_code=404, detail=f"Skill {req.skill_b_id} 不存在")
    if getattr(skill_a, "is_locked", False) or getattr(skill_b, "is_locked", False):
        raise HTTPException(status_code=409, detail="Final immutable Skills cannot be merged or modified")

    result = await app.merger.merge(skill_a, skill_b)
    if not result.success or not result.merged_skill:
        raise HTTPException(status_code=400, detail=result.error or "Skill 合并失败")

    created_skill = result.merged_skill
    if req.persist:
        created_skill = await app.wiki.create(result.merged_skill)
        if app.graph:
            try:
                await app.graph.sync_skill(created_skill)
                for edge in result.edges_to_create:
                    if hasattr(app.graph, "add_edge"):
                        await app.graph.add_edge(edge)
            except Exception:
                pass

    return {
        "success": True,
        "merged_skill": _to_summary(created_skill),
        "source_skill_ids": result.source_skill_ids,
        "rationale": result.rationale,
        "edges_to_create": [
            edge.model_dump(mode="json") if hasattr(edge, "model_dump") else edge.__dict__
            for edge in result.edges_to_create
        ],
    }


@router.post("/{skill_id}/split", response_model=Dict[str, Any])
async def split_skill(
    skill_id: str,
    req: SplitSkillRequest,
    app: AppState = Depends(get_app_state),
) -> Dict[str, Any]:
    if not app.merger:
        raise HTTPException(status_code=500, detail="SkillMerger 未初始化")

    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")
    if getattr(skill, "is_locked", False):
        raise HTTPException(status_code=409, detail="Final immutable Skill cannot be split")

    result = await app.merger.split(skill)
    if not result.success:
        raise HTTPException(status_code=400, detail=result.error or "Skill 拆分失败")

    created_skills = []
    if req.persist:
        for sub_skill in result.sub_skills:
            created = await app.wiki.create(sub_skill)
            created_skills.append(created)
            if app.graph:
                try:
                    await app.graph.sync_skill(created)
                except Exception:
                    pass
    else:
        created_skills = result.sub_skills

    return {
        "success": True,
        "source_skill_id": result.source_skill_id,
        "sub_skills": [_to_summary(skill) for skill in created_skills],
        "rationale": result.rationale,
        "composition_order": result.composition_order,
        "edges_to_create": [
            edge.model_dump(mode="json") if hasattr(edge, "model_dump") else edge.__dict__
            for edge in result.edges_to_create
        ],
    }

def _business_skill_diff(old_skill: Any, new_skill: Any) -> List[Dict[str, Any]]:
    """生成面向用户的业务字段 diff。

    过滤系统元数据，只比较真正代表 Skill 内容变化的字段。
    """
    lines: List[Dict[str, Any]] = []

    simple_fields = [
        "name",
        "display_name",
        "description",
        "skill_type",
        "domain",
        "granularity_level",
        "tags",
    ]

    for field in simple_fields:
        old_val = getattr(old_skill, field, None)
        new_val = getattr(new_skill, field, None)

        old_val = _normalize_diff_value(old_val)
        new_val = _normalize_diff_value(new_val)

        if old_val != new_val:
            lines.append(_make_diff_line(field, old_val, new_val))

    old_interface = (
        old_skill.interface.model_dump(mode="json")
        if getattr(old_skill, "interface", None)
        else {}
    )
    new_interface = (
        new_skill.interface.model_dump(mode="json")
        if getattr(new_skill, "interface", None)
        else {}
    )

    for key in [
        "input_schema",
        "output_schema",
        "preconditions",
        "postconditions",
        "side_effects",
    ]:
        old_val = old_interface.get(key)
        new_val = new_interface.get(key)
        if old_val != new_val:
            lines.append(_make_diff_line(f"interface.{key}", old_val, new_val))

    old_impl = (
        old_skill.implementation.model_dump(mode="json")
        if getattr(old_skill, "implementation", None)
        else {}
    )
    new_impl = (
        new_skill.implementation.model_dump(mode="json")
        if getattr(new_skill, "implementation", None)
        else {}
    )

    for key in [
        "language",
        "code",
        "prompt_template",
        "tool_calls",
        "sub_skill_ids",
        "execution_order",
    ]:
        old_val = old_impl.get(key)
        new_val = new_impl.get(key)
        if old_val != new_val:
            lines.append(_make_diff_line(f"implementation.{key}", old_val, new_val))

    return lines


def _normalize_diff_value(value: Any) -> Any:
    """把 Enum 等对象转换成适合比较和展示的值。"""
    if hasattr(value, "value"):
        return value.value
    return value


def _make_diff_line(field: str, old_value: Any, new_value: Any) -> Dict[str, Any]:
    old_str = _pretty_diff_value(old_value)
    new_str = _pretty_diff_value(new_value)

    if old_value in (None, "", [], {}) and new_value not in (None, "", [], {}):
        change_type = "added"
    elif new_value in (None, "", [], {}) and old_value not in (None, "", [], {}):
        change_type = "removed"
    else:
        change_type = "modified"

    return {
        "field": field,
        "type": change_type,
        "old_value": old_str,
        "new_value": new_str,
        "old_lines": old_str.splitlines() if old_str else [],
        "new_lines": new_str.splitlines() if new_str else [],
    }


def _pretty_diff_value(value: Any) -> str:
    import json

    value = _normalize_diff_value(value)

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _infer_change_type(diff_lines: List[Dict[str, Any]]) -> str:
    fields = [item.get("field", "") for item in diff_lines]

    if any(field.startswith("interface.") for field in fields):
        return "interface_changed"
    if any(field.startswith("implementation.") for field in fields):
        return "implementation_changed"
    if "description" in fields:
        return "description_updated"
    if "tags" in fields:
        return "tags_updated"
    if any(field in fields for field in ("skill_type", "domain", "granularity_level")):
        return "metadata_updated"
    if not diff_lines:
        return "metadata_only"

    return "updated"


def _is_breaking_diff(diff_lines: List[Dict[str, Any]]) -> bool:
    """接口变化视为 breaking change。"""
    return any(
        item.get("field", "").startswith("interface.")
        for item in diff_lines
    )


def _suggest_bump_from_diff(diff_lines: List[Dict[str, Any]]) -> str:
    if _is_breaking_diff(diff_lines):
        return "major"

    fields = [item.get("field", "") for item in diff_lines]
    if any(field in fields for field in ("skill_type", "domain", "granularity_level")):
        return "minor"

    return "patch"


def _summarize_diff(
    from_version: str,
    to_version: str,
    diff_lines: List[Dict[str, Any]],
) -> str:
    if not diff_lines:
        return f"版本 {from_version} → {to_version}：仅版本号、状态或系统元数据变化"

    fields = [item.get("field", "") for item in diff_lines]
    shown = ", ".join(fields[:3])
    suffix = " ..." if len(fields) > 3 else ""

    return f"版本 {from_version} → {to_version}：修改 {shown}{suffix}"


def _get_skill_author(skill: Any) -> str:
    provenance = getattr(skill, "provenance", None)
    if provenance and getattr(provenance, "created_by_agent", None):
        return provenance.created_by_agent
    return "repository"


def _merge_schema_properties(target: Dict[str, Any], sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(target or {"type": "object", "properties": {}})
    merged.setdefault("type", "object")
    merged.setdefault("properties", {})
    merged_required = set(merged.get("required") or [])
    for schema in sources:
        if not isinstance(schema, dict):
            continue
        for key, spec in (schema.get("properties") or {}).items():
            merged["properties"].setdefault(key, spec)
        merged_required.update(schema.get("required") or [])
    if merged_required:
        merged["required"] = sorted(merged_required)
    return merged


def _dedupe(items: List[Any]) -> List[Any]:
    seen = set()
    result = []
    for item in items:
        key = str(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _score_ratio(score: float) -> float:
    return score / 10.0 if score > 1.0 else score


def _merged_description(target: Any, sources: List[Any], manual_description: Optional[str]) -> str:
    if manual_description:
        return manual_description
    source_lines = [
        f"- {source.name}: {source.description}"
        for source in sources
        if getattr(source, "description", "")
    ]
    if not source_lines:
        return target.description
    return (
        f"{target.description}\n\n"
        "Merged workflow knowledge:\n"
        + "\n".join(source_lines)
    )


def _skill_tool_calls(skill: Any) -> List[str]:
    implementation = getattr(skill, "implementation", None)
    return list(getattr(implementation, "tool_calls", []) or [])


def _extract_default_from_schema(skill: Any, field_name: str) -> Any:
    interface = getattr(skill, "interface", None)
    if not interface:
        return None
    schema = getattr(interface, "input_schema", {}) or {}
    props = schema.get("properties") or {}
    value = props.get(field_name, {}).get("default")
    if value:
        return value
    implementation = getattr(skill, "implementation", None)
    code = getattr(implementation, "code", "") if implementation else ""
    if not code:
        return None
    import re

    pattern = rf'input_data\.get\("{re.escape(field_name)}"\)\s+or\s+"([^"]+)"'
    match = re.search(pattern, code)
    return match.group(1) if match else None


def _generic_merge_for_common_tool(target: Any, sources: List[Any], req: MergeUpdateRequest) -> Optional[Dict[str, Any]]:
    """Generalize common host-tool Skills into parameterized workflows.

    This is intentionally more opinionated than a raw field union. Merging
    `open_chatgpt` and `open_hitwh` should produce a reusable URL-opening Skill
    with `url` as input, not a bag of two hard-coded websites.
    """
    from ...models.skill_model import SkillImplementation, SkillInterface

    all_skills = [target] + sources
    tool_sets = [set(_skill_tool_calls(skill)) for skill in all_skills]
    common_tools = set.intersection(*tool_sets) if tool_sets and all(tool_sets) else set()
    if not common_tools:
        return None

    if "host.open_url_in_chrome" in common_tools:
        examples = _dedupe(
            [
                value
                for skill in all_skills
                for value in [_extract_default_from_schema(skill, "url")]
                if value
            ]
        )
        example_text = f" Examples seen during merge: {', '.join(map(str, examples[:4]))}." if examples else ""
        description = req.description or (
            "Open Google Chrome and navigate to a target URL chosen by the execution agent from the user's task."
            + example_text
        )
        interface = SkillInterface(
            input_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Target URL generated or verified by the execution agent from the user task.",
                    },
                    "goal": {
                        "type": "string",
                        "description": "Original user task, used by the agent to resolve the URL when it is not provided.",
                    },
                },
                "required": [],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "launched": {"type": "boolean", "description": "Whether Chrome accepted the open request."},
                    "url": {"type": "string", "description": "The URL opened in Chrome."},
                    "host_action": {"type": "string", "description": "Host tool action that was executed."},
                },
            },
            preconditions=[
                "The execution agent has resolved or generated the target URL from the task.",
                "The selected Skill is used as an execution pattern, not as a hard-coded destination.",
            ],
            postconditions=[
                "Chrome is open on the agent-selected URL.",
                "The runtime records the actual URL for validation and future learning.",
            ],
            side_effects=["Opens or focuses Google Chrome."],
        )
        implementation = SkillImplementation(
            language="python",
            code='output["launched"] = True\noutput["url"] = input_data.get("url") or input_data.get("resolved_url")',
            prompt_template=(
                "Use this Skill only after the agent resolves a concrete target URL. "
                "Do not reuse example URLs unless the current task asks for that same site."
            ),
            tool_calls=["host.open_url_in_chrome"],
        )
        return {
            "description": description,
            "tags": _dedupe(target.tags + [tag for source in sources for tag in source.tags] + ["generic", "parameterized", "agent-generalized", "url"]),
            "interface": interface,
            "implementation": implementation,
            "domain": "web",
            "granularity_level": 1,
        }

    if "host.open_file" in common_tools:
        description = req.description or "Open a target local file or folder selected by the execution agent from the user's task."
        interface = SkillInterface(
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute, home-relative, or agent-resolved file/folder path.",
                    },
                    "goal": {"type": "string", "description": "Original user task used to infer the path."},
                },
                "required": [],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "launched": {"type": "boolean"},
                    "path": {"type": "string"},
                    "host_action": {"type": "string"},
                },
            },
            preconditions=["The execution agent has resolved the requested file/folder path."],
            postconditions=["The host OS receives an open request for the resolved path."],
            side_effects=["Opens a local file or folder with the default application."],
        )
        implementation = SkillImplementation(
            language="python",
            code='output["launched"] = True\noutput["path"] = input_data.get("path") or input_data.get("resolved_path")',
            prompt_template="Use the current task to resolve the path; do not hard-code example paths from prior imports.",
            tool_calls=["host.open_file"],
        )
        return {
            "description": description,
            "tags": _dedupe(target.tags + [tag for source in sources for tag in source.tags] + ["generic", "parameterized", "agent-generalized", "file"]),
            "interface": interface,
            "implementation": implementation,
            "domain": "file",
            "granularity_level": 1,
        }

    return None


def _merge_skill_content(target: Any, sources: List[Any], req: MergeUpdateRequest) -> Dict[str, Any]:
    from ...models.skill_model import SkillImplementation, SkillInterface, SkillProvenance

    input_schemas = [source.interface.input_schema for source in sources if getattr(source, "interface", None)]
    output_schemas = [source.interface.output_schema for source in sources if getattr(source, "interface", None)]
    generalized = _generic_merge_for_common_tool(target, sources, req) if req.merge_strategy == "agent_generalize" else None
    interface = SkillInterface(
        input_schema=_merge_schema_properties(target.interface.input_schema, input_schemas),
        output_schema=_merge_schema_properties(target.interface.output_schema, output_schemas),
        preconditions=_dedupe(target.interface.preconditions + [item for source in sources for item in source.interface.preconditions]),
        postconditions=_dedupe(target.interface.postconditions + [item for source in sources for item in source.interface.postconditions]),
        side_effects=_dedupe(target.interface.side_effects + [item for source in sources for item in source.interface.side_effects]),
    )

    target_impl = target.implementation
    tool_calls = _dedupe(
        (target_impl.tool_calls if target_impl else [])
        + [tool for source in sources if source.implementation for tool in source.implementation.tool_calls]
    )
    sub_skill_ids = _dedupe(
        (target_impl.sub_skill_ids if target_impl else [])
        + [source.skill_id for source in sources]
    )
    implementation = SkillImplementation(
        language=(target_impl.language if target_impl else "python"),
        code=(target_impl.code if target_impl else None),
        prompt_template=(target_impl.prompt_template if target_impl else None) or _merged_description(target, sources, req.description),
        tool_calls=tool_calls,
        sub_skill_ids=sub_skill_ids,
        execution_order=(target_impl.execution_order if target_impl else None),
    )
    if generalized:
        interface = generalized["interface"]
        implementation = generalized["implementation"]

    parent_ids = _dedupe([target.skill_id] + [source.skill_id for source in sources])
    provenance = target.provenance.model_copy(deep=True) if target.provenance else SkillProvenance(source_type="merge_update")
    provenance.source_type = "merge_update"
    provenance.parent_skill_ids = _dedupe((provenance.parent_skill_ids or []) + parent_ids)
    provenance.creation_context.update({
        "merged_skill_ids": [source.skill_id for source in sources],
        "merged_skill_names": [source.name for source in sources],
    })

    overrides = {
        "description": generalized.get("description") if generalized else _merged_description(target, sources, req.description),
        "tags": generalized.get("tags") if generalized else _dedupe(target.tags + [tag for source in sources for tag in source.tags] + ["merged", "workflow"]),
        "interface": interface,
        "implementation": implementation,
        "domain": generalized.get("domain") if generalized else target.domain,
        "granularity_level": generalized.get("granularity_level") if generalized else max([target.granularity_level] + [source.granularity_level for source in sources]),
        "provenance": provenance,
        "dependency_ids": _dedupe(target.dependency_ids + [source.skill_id for source in sources]),
        "component_ids": _dedupe(target.component_ids + [source.skill_id for source in sources]),
        "tool_refs": _dedupe(target.tool_refs + [ref for source in sources for ref in source.tool_refs]),
        "doc_refs": _dedupe(target.doc_refs + [ref for source in sources for ref in source.doc_refs]),
        "trajectory_refs": _dedupe(target.trajectory_refs + [ref for source in sources for ref in source.trajectory_refs]),
    }
    if req.description is not None:
        overrides["description"] = req.description
    if req.tags is not None:
        overrides["tags"] = req.tags
    if req.interface is not None:
        overrides["interface"] = req.interface
    if req.implementation is not None:
        overrides["implementation"] = req.implementation
    if req.test_cases is not None:
        overrides["test_cases"] = req.test_cases
    else:
        overrides["test_cases"] = target.test_cases + [case for source in sources for case in source.test_cases]
    return overrides


async def _sync_skill_after_lifecycle(app: AppState, skill: Any) -> None:
    if app.graph:
        try:
            await app.graph.sync_skill(skill)
            if hasattr(app.graph, "sync_auto_edges"):
                skills = await app.wiki.list(limit=10000)
                await app.graph.sync_auto_edges(skill, [item.skill_id for item in skills])
        except Exception:
            pass
    if hasattr(app.wiki, "invalidate"):
        await app.wiki.invalidate(skill.skill_id)


@router.post("/{skill_id}/merge-update", response_model=Dict[str, Any])
async def merge_update_skill(
    skill_id: str,
    req: MergeUpdateRequest,
    app: AppState = Depends(get_app_state),
) -> Dict[str, Any]:
    target = await app.wiki.get(skill_id)
    if not target:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")
    if getattr(target, "is_locked", False):
        raise HTTPException(status_code=409, detail="Final immutable Skill cannot be merge-updated")
    source_ids = [source_id for source_id in req.source_skill_ids if source_id != skill_id]
    if not source_ids:
        raise HTTPException(status_code=400, detail="At least one source Skill is required for merge update")
    source_map = await app.wiki.get_many(source_ids)
    sources = [source_map[source_id] for source_id in source_ids if source_map.get(source_id)]
    if len(sources) != len(source_ids):
        missing = [source_id for source_id in source_ids if not source_map.get(source_id)]
        raise HTTPException(status_code=404, detail=f"Source Skills do not exist: {missing}")
    if any(getattr(source, "is_locked", False) for source in sources):
        raise HTTPException(status_code=409, detail="Final immutable Skills cannot be merge-updated")

    overrides = _merge_skill_content(target, sources, req)
    try:
        new_skill = await app.wiki.create_new_version(skill_id, req.bump, **overrides)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await _sync_skill_after_lifecycle(app, new_skill)
    diff_lines = _business_skill_diff(target, new_skill)
    return {
        "success": True,
        "updated_skill": _to_summary(new_skill),
        "merged_skills": [_to_summary(source) for source in sources],
        "rationale": "Merged selected Skills into a new version of the target Skill.",
        "diff": diff_lines,
        "summary": _summarize_diff(target.version, new_skill.version, diff_lines),
    }



@router.post("/{skill_id}/transition", response_model=SkillSummary)
async def transition_state(
    skill_id: str,
    req: TransitionRequest,
    app: AppState = Depends(get_app_state),
) -> SkillSummary:
    try:
        skill = await app.wiki.transition_state(skill_id, req.new_state, req.reason)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_summary(skill)


@router.post("/{skill_id}/release", response_model=SkillSummary)
async def release_skill(
    skill_id: str,
    req: Optional[ReleaseRequest] = None,
    app: AppState = Depends(get_app_state),
) -> SkillSummary:
    """发布 Skill。若当前为 Draft，自动推进到 Verified 再发布。"""
    from ...models.skill_model import SkillState
    try:
        skill = await app.wiki.get(skill_id)
        if not skill:
            raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")
        if skill.state == SkillState.DRAFT:
            await app.wiki.transition_state(skill_id, SkillState.VERIFIED)
        skill = await app.wiki.release(skill_id)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_summary(skill)


@router.post("/{skill_id}/deprecate", response_model=SkillSummary)
async def deprecate_skill(
    skill_id: str,
    req: DeprecateRequest,
    app: AppState = Depends(get_app_state),
) -> SkillSummary:
    try:
        skill = await app.wiki.deprecate(skill_id, req.reason, req.replacement_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _to_summary(skill)

@router.post("/{skill_id}/new-version", response_model=SkillSummary)
async def create_new_version(
    skill_id: str,
    req: NewVersionRequest,
    app: AppState = Depends(get_app_state),
) -> SkillSummary:
    """基于当前 Skill 创建一个新版本。

    语义：
    - bump 负责版本号递增：major / minor / patch
    - description / tags / interface / implementation 等字段作为新版本覆盖内容
    - 原版本不变，新版本以新的 skill_id 存储
    """
    old_skill = await app.wiki.get(skill_id)
    if not old_skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")
    if getattr(old_skill, "is_locked", False):
        raise HTTPException(status_code=409, detail="Final immutable Skill cannot create a new version")

    overrides: Dict[str, Any] = {}

    if req.description is not None:
        overrides["description"] = req.description
    if req.tags is not None:
        overrides["tags"] = req.tags
    if req.interface is not None:
        overrides["interface"] = req.interface
    if req.implementation is not None:
        overrides["implementation"] = req.implementation
    if req.test_cases is not None:
        overrides["test_cases"] = req.test_cases
    if req.domain is not None:
        overrides["domain"] = req.domain
    if req.granularity_level is not None:
        overrides["granularity_level"] = req.granularity_level

    try:
        new_skill = await app.wiki.create_new_version(
            skill_id,
            req.bump,
            **overrides,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 记录内存态变更记录，供当前进程内 VersionController 使用。
    # 注意：真正稳定的 diff 仍然应该从 Git 版本文件 / skill 快照中计算。
    if app.version_ctrl:
        try:
            diff = app.version_ctrl.compute_diff(old_skill, new_skill)
            change_type = app.version_ctrl.determine_change_type(diff)
            app.version_ctrl.record_change(
                new_skill,
                change_type=change_type,
                summary=_summarize_diff(old_skill.version, new_skill.version, _business_skill_diff(old_skill, new_skill)),
                diff=diff,
                author=req.author,
                from_version=old_skill.version,
            )
        except Exception:
            pass

    return _to_summary(new_skill)


@router.post("/{skill_id}/review", response_model=dict)
async def review_skill(
    skill_id: str,
    auto_apply: bool = Query(False, description="When true, degrade unqualified Skills according to review result."),
    app: AppState = Depends(get_app_state),
) -> dict:
    if not app.reviewer:
        raise HTTPException(status_code=500, detail="SkillReviewer 未初始化")

    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")

    result = await app.reviewer.review(skill)
    updated_skill = None
    lifecycle_action = "none"
    score_ratio = _score_ratio(float(result.overall_score))
    if auto_apply and not result.is_approved and not getattr(skill, "is_locked", False):
        from ...models.skill_model import SkillState

        degrade_target = None
        if skill.state == SkillState.RELEASED:
            degrade_target = SkillState.DEGRADED
        elif skill.state == SkillState.VERIFIED:
            degrade_target = SkillState.DRAFT
        elif skill.state == SkillState.DRAFT:
            degrade_target = SkillState.SKILL_CANDIDATE
        elif skill.state == SkillState.DEGRADED and score_ratio < 0.45:
            degrade_target = SkillState.DEPRECATED
        if degrade_target:
            try:
                updated_skill = await app.wiki.transition_state(
                    skill_id,
                    degrade_target,
                    reason=f"Review downgrade: {result.summary}",
                )
                lifecycle_action = f"downgraded_to_{degrade_target.value}"
                await _sync_skill_after_lifecycle(app, updated_skill)
            except ValueError:
                lifecycle_action = "downgrade_not_allowed"

    return {
        "review_id": result.review_id,
        "status": result.status.value,
        "overall_score": result.overall_score,
        "score_ratio": score_ratio,
        "summary": result.summary,
        "comments": [
            {
                "field": c.field,
                "severity": c.severity,
                "message": c.message,
                "suggestion": c.suggestion,
            }
            for c in result.comments
        ],
        "auto_fix_suggestions": result.auto_fix_suggestions,
        "is_approved": result.is_approved,
        "lifecycle_action": lifecycle_action,
        "updated_skill": _to_summary(updated_skill) if updated_skill else None,
    }

@router.post("/{skill_id}/review-and-release", response_model=SkillSummary)
async def review_and_release(
    skill_id: str,
    app: AppState = Depends(get_app_state),
) -> SkillSummary:
    if not app.reviewer:
        raise HTTPException(status_code=500, detail="SkillReviewer 未初始化")

    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")

    try:
        reviewed_skill, result = await app.reviewer.review_and_release(skill)
        if not result.is_approved:
            raise HTTPException(
                status_code=400,
                detail=f"审核未通过: {result.status.value}, score={result.overall_score}",
            )

        updated = await app.wiki.update(
            skill_id,
            state=reviewed_skill.state,
            released_at=reviewed_skill.released_at,
        )

        return _to_summary(updated or reviewed_skill)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/{skill_id}/record-execution", response_model=OKResponse)
async def record_execution(
    skill_id: str,
    success: bool,
    latency_ms: float,
    app: AppState = Depends(get_app_state),
) -> OKResponse:
    await app.wiki.record_execution(skill_id, success, latency_ms)
    return OKResponse(message="执行记录已更新")


def _infer_change_type(diff: List[Dict[str, Any]]) -> str:
    fields = [item["field"] for item in diff]
    if any(field.startswith("interface.") for field in fields):
        return "interface_changed"
    if any(field.startswith("implementation.") for field in fields):
        return "implementation_changed"
    if "description" in fields:
        return "description_updated"
    if "tags" in fields:
        return "tags_updated"
    return "version"


def _summarize_diff(from_version: str, to_version: str, diff: List[Dict[str, Any]]) -> str:
    if not diff:
        return f"版本 {from_version} → {to_version}：仅版本/状态元数据变化"
    fields = ", ".join(item["field"] for item in diff[:3])
    suffix = " ..." if len(diff) > 3 else ""
    return f"版本 {from_version} → {to_version}：修改 {fields}{suffix}"

@router.get("/{skill_id}/diff", response_model=Dict[str, Any])
async def get_skill_diff(
    skill_id: str,
    compare_to: Optional[str] = None,
    app: AppState = Depends(get_app_state),
) -> Dict[str, Any]:
    """获取 Skill 的业务级 diff。

    设计原则：
    - 不直接返回完整 JSON 文件 diff
    - 默认过滤 skill_id、version、state、created_at、updated_at、released_at、metrics、provenance 等系统元数据
    - 只展示用户真正关心的业务字段变化，例如 description、tags、interface、implementation
    """
    skill = await app.wiki.get(skill_id)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} 不存在")

    # 对比指定版本 / 指定 skill_id
    if compare_to:
        other = await app.wiki.get(compare_to)
        if not other:
            raise HTTPException(status_code=404, detail=f"对比 Skill {compare_to} 不存在")

        diff_lines = _business_skill_diff(other, skill)

        return {
            "skill_id": skill_id,
            "compare_to": compare_to,
            "skill_name": skill.name,
            "current_version": skill.version,
            "source": "business_diff",
            "diff": diff_lines,
            "suggested_bump": _suggest_bump_from_diff(diff_lines),
            "summary": _summarize_diff(other.version, skill.version, diff_lines),
        }

    # 默认：返回同名 Skill 的完整版本历史，并对相邻版本做业务字段 diff
    if hasattr(app.wiki, "get_version_history"):
        try:
            repo_history = await app.wiki.get_version_history(skill.name)
            repo_history = sorted(
                repo_history,
                key=lambda item: tuple(map(int, item.version.split("."))),
            )

            rows: List[Dict[str, Any]] = []

            for i, item in enumerate(repo_history):
                if i == 0:
                    rows.append({
                        "record_id": f"{item.name}:{item.version}",
                        "from_version": "",
                        "to_version": item.version,
                        "change_type": "created",
                        "summary": item.description or f"创建版本 {item.version}",
                        "author": _get_skill_author(item),
                        "created_at": item.created_at.isoformat(),
                        "diff": [],
                        "is_breaking": False,
                        "skill_id": item.skill_id,
                        "state": item.state.value,
                    })
                    continue

                prev = repo_history[i - 1]
                diff_lines = _business_skill_diff(prev, item)

                rows.append({
                    "record_id": f"{item.name}:{prev.version}->{item.version}",
                    "from_version": prev.version,
                    "to_version": item.version,
                    "change_type": _infer_change_type(diff_lines),
                    "summary": _summarize_diff(prev.version, item.version, diff_lines),
                    "author": _get_skill_author(item),
                    "created_at": item.created_at.isoformat(),
                    "diff": diff_lines,
                    "is_breaking": _is_breaking_diff(diff_lines),
                    "skill_id": item.skill_id,
                    "state": item.state.value,
                })

            return {
                "skill_id": skill_id,
                "skill_name": skill.name,
                "current_version": skill.version,
                "source": "business_diff",
                "history": list(reversed(rows)),
            }
        except Exception:
            pass

    # fallback：如果 Git 版本历史不可用，则退回 VersionController 的内存记录
    history = app.version_ctrl.get_history(skill_id) if app.version_ctrl else []

    return {
        "skill_id": skill_id,
        "skill_name": skill.name,
        "current_version": skill.version,
        "source": "version_controller",
        "history": [
            {
                "record_id": r.record_id,
                "from_version": r.from_version,
                "to_version": r.to_version,
                "change_type": r.change_type.value,
                "summary": r.summary,
                "author": r.author,
                "created_at": r.created_at.isoformat(),
                "diff": _format_diff(r.diff),
                "is_breaking": r.is_breaking(),
            }
            for r in history
        ],
    }


@router.get("/{skill_id}/diff/versions", response_model=Dict[str, Any])
async def diff_two_versions(
    skill_id: str,
    version_a: str,
    version_b: str,
    app: AppState = Depends(get_app_state),
) -> Dict[str, Any]:
    """对比同一 Skill 的两个版本（通过版本历史中的快照）。"""
    history = app.version_ctrl.get_history(skill_id) if app.version_ctrl else []
    records_a = [r for r in history if r.to_version == version_a or r.from_version == version_a]
    records_b = [r for r in history if r.to_version == version_b or r.from_version == version_b]

    return {
        "skill_id": skill_id,
        "version_a": version_a,
        "version_b": version_b,
        "changes_in_a": [r.summary for r in records_a],
        "changes_in_b": [r.summary for r in records_b],
        "history_count": len(history),
    }


def _format_diff(diff: Dict[str, Any]) -> List[Dict[str, Any]]:
    """将 diff 字典格式化为前端友好的行列表。"""
    lines = []
    for field, change in diff.items():
        if isinstance(change, dict) and "old" in change and "new" in change:
            old_str = str(change["old"])
            new_str = str(change["new"])
            lines.append({
                "field": field,
                "type": "modified",
                "old_value": old_str,
                "new_value": new_str,
                "old_lines": old_str.splitlines() or [old_str],
                "new_lines": new_str.splitlines() or [new_str],
            })
        else:
            lines.append({
                "field": field,
                "type": "added",
                "old_value": "",
                "new_value": str(change),
                "old_lines": [],
                "new_lines": str(change).splitlines() or [str(change)],
            })
    return lines


def _format_unified_diff(raw_diff: str) -> List[Dict[str, Any]]:
    """Format a unified diff string for the existing frontend diff viewer."""
    if not raw_diff:
        return []

    rows: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {
        "field": "skill_json",
        "type": "modified",
        "old_value": "",
        "new_value": "",
        "old_lines": [],
        "new_lines": [],
    }
    for line in raw_diff.splitlines():
        if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
            continue
        if line.startswith("-"):
            current["old_lines"].append(line[1:])
        elif line.startswith("+"):
            current["new_lines"].append(line[1:])
    current["old_value"] = "\n".join(current["old_lines"])
    current["new_value"] = "\n".join(current["new_lines"])
    rows.append(current)
    return rows
