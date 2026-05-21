"""Experience Processing Pipeline — Extractor/Normalizer/Summarizer/Indexer 串联。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...models.experience_model import ExperienceUnit
from ...utils.llm_client import LLMClient, Message
from ...utils.logger import get_logger

logger = get_logger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────────────────

@dataclass
class StructuredExperience:
    unit_id: str
    source_type: str
    raw_content: str
    extracted_actions: List[str] = field(default_factory=list)
    normalized_actions: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    proposed_skill_name: Optional[str] = None
    proposed_description: Optional[str] = None
    proposed_type: Optional[str] = None
    confidence: float = 0.0
    index_keywords: List[str] = field(default_factory=list)
    index_embedding_hint: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    success: bool
    source_type: str
    unit_count: int
    token_usage: int
    errors: List[str] = field(default_factory=list)
    units: List[StructuredExperience] = field(default_factory=list)


# ── Extractor Agent ───────────────────────────────────────────────────────────

_EXTRACT_PROMPT = """
你是 SkillOS 的 Extractor Agent，从原始输入中提取操作动作序列。

## 输入类型
{source_type}

## 原始内容
{raw_content}

## 要求
识别所有可复用的操作动作，每个动作用一句话描述。

## 输出格式（严格 JSON）
{{
  "actions": ["动作1", "动作2", "动作3"],
  "proposed_skill_name": "snake_case_name",
  "confidence": 0.8
}}

只输出 JSON。
"""


class ExtractorAgent:
    """从原始输入中提取操作动作序列。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def extract(self, raw_content: str, source_type: str) -> Dict[str, Any]:
        prompt = _EXTRACT_PROMPT.format(
            source_type=source_type,
            raw_content=raw_content[:800],
        )
        try:
            resp = self._llm.chat([
                Message.system("你是 SkillOS Extractor Agent，严格输出 JSON。"),
                Message.user(prompt),
            ])
            data = _parse_json(resp.content)
            if data:
                return data
        except Exception as exc:
            logger.warning(f"Extractor LLM 失败: {exc}")
        return {"actions": [raw_content[:100]], "proposed_skill_name": "extracted_skill", "confidence": 0.3}


# ── Normalizer Agent ──────────────────────────────────────────────────────────

_NORMALIZE_PROMPT = """
你是 SkillOS 的 Normalizer Agent，将提取的动作规范化为标准格式。

## 原始动作列表
{actions}

## 要求
将每个动作规范化为包含 verb（动词）、object（对象）、condition（条件）的结构。

## 输出格式（严格 JSON）
{{
  "normalized": [
    {{"verb": "click", "object": "button", "condition": "page loaded", "description": "点击按钮"}}
  ]
}}

只输出 JSON。
"""


class NormalizerAgent:
    """将提取的动作规范化为标准结构。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def normalize(self, actions: List[str]) -> List[Dict[str, Any]]:
        if not actions:
            return []
        prompt = _NORMALIZE_PROMPT.format(actions=json.dumps(actions, ensure_ascii=False))
        try:
            resp = self._llm.chat([
                Message.system("你是 SkillOS Normalizer Agent，严格输出 JSON。"),
                Message.user(prompt),
            ])
            data = _parse_json(resp.content)
            if data and "normalized" in data:
                return data["normalized"]
        except Exception as exc:
            logger.warning(f"Normalizer LLM 失败: {exc}")
        return [{"verb": a.split()[0] if a else "do", "object": a, "description": a} for a in actions]


# ── Summarizer Agent ──────────────────────────────────────────────────────────

_SUMMARIZE_PROMPT = """
你是 SkillOS 的 Summarizer Agent，将规范化的动作序列总结为 Skill 描述。

## 规范化动作
{normalized_actions}

## 要求
生成简洁的 Skill 描述（一句话），以及建议的 Skill 类型（atomic/functional/strategic）。

## 输出格式（严格 JSON）
{{
  "description": "Skill 功能描述",
  "skill_type": "atomic",
  "tags": ["tag1", "tag2"],
  "confidence": 0.85
}}

只输出 JSON。
"""


class SummarizerAgent:
    """将规范化动作总结为 Skill 描述。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def summarize(self, normalized_actions: List[Dict[str, Any]], proposed_name: str = "") -> Dict[str, Any]:
        prompt = _SUMMARIZE_PROMPT.format(
            normalized_actions=json.dumps(normalized_actions, ensure_ascii=False)[:400],
        )
        try:
            resp = self._llm.chat([
                Message.system("你是 SkillOS Summarizer Agent，严格输出 JSON。"),
                Message.user(prompt),
            ])
            data = _parse_json(resp.content)
            if data:
                return data
        except Exception as exc:
            logger.warning(f"Summarizer LLM 失败: {exc}")
        return {
            "description": f"执行 {proposed_name or '操作序列'}",
            "skill_type": "atomic",
            "tags": [],
            "confidence": 0.3,
        }


# ── Indexer Agent ─────────────────────────────────────────────────────────────

class IndexerAgent:
    """为 Skill 生成索引关键词和检索提示。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client

    def index(self, skill_name: str, description: str, tags: List[str]) -> Dict[str, Any]:
        keywords = set(tags)
        keywords.update(description.lower().split()[:10])
        keywords.update(skill_name.replace("_", " ").split())
        return {
            "keywords": list(keywords),
            "embedding_hint": f"{skill_name}: {description}",
        }


# ── Pipeline ──────────────────────────────────────────────────────────────────

class ExperiencePipeline:
    """Extractor → Normalizer → Summarizer → Indexer 串联管道。"""

    def __init__(self, llm_client: LLMClient) -> None:
        self._llm = llm_client
        self._extractor = ExtractorAgent(llm_client)
        self._normalizer = NormalizerAgent(llm_client)
        self._summarizer = SummarizerAgent(llm_client)
        self._indexer = IndexerAgent(llm_client)

    def process(self, raw_content: str, source_type: str) -> PipelineResult:
        """处理单条原始输入，返回结构化经验。"""
        import uuid
        errors: List[str] = []

        try:
            natural_result = self._process_natural_language_workflow(raw_content, source_type)
            if natural_result:
                natural_result.units.extend(_derive_capability_units(raw_content, source_type, natural_result.units))
                natural_result.unit_count = len(natural_result.units)
                return natural_result

            static_result = _process_static_demo_source(raw_content, source_type)
            if static_result:
                static_result.units.extend(_derive_capability_units(raw_content, source_type, static_result.units))
                static_result.unit_count = len(static_result.units)
                if static_result.units:
                    static_result.success = True
                    if static_result.errors == ["No valid skills found in static demo input."]:
                        static_result.errors = []
                return static_result

            derived_units = _derive_capability_units(raw_content, source_type, [])
            if derived_units:
                return PipelineResult(
                    success=True,
                    source_type=source_type,
                    unit_count=len(derived_units),
                    token_usage=0,
                    units=derived_units,
                )

            # Stage 1: Extract
            extracted = self._extractor.extract(raw_content, source_type)
            actions = extracted.get("actions", [])
            proposed_name = extracted.get("proposed_skill_name", "unnamed")
            confidence = float(extracted.get("confidence", 0.5))

            # Stage 2: Normalize
            normalized = self._normalizer.normalize(actions)

            # Stage 3: Summarize
            summary_data = self._summarizer.summarize(normalized, proposed_name)
            description = summary_data.get("description", "")
            skill_type = summary_data.get("skill_type", "atomic")
            tags = summary_data.get("tags", [])
            confidence = max(confidence, float(summary_data.get("confidence", 0.5)))

            # Stage 4: Index
            index_data = self._indexer.index(proposed_name, description, tags)

            unit = StructuredExperience(
                unit_id=str(uuid.uuid4()),
                source_type=source_type,
                raw_content=raw_content[:200],
                extracted_actions=actions,
                normalized_actions=normalized,
                summary=description,
                proposed_skill_name=proposed_name,
                proposed_description=description,
                proposed_type=skill_type,
                confidence=confidence,
                index_keywords=index_data.get("keywords", []),
                index_embedding_hint=index_data.get("embedding_hint", ""),
            )

            return PipelineResult(
                success=True,
                source_type=source_type,
                unit_count=1,
                token_usage=0,
                units=[unit],
            )

        except Exception as exc:
            logger.error(f"ExperiencePipeline 处理失败: {exc}")
            errors.append(str(exc))
            return PipelineResult(
                success=False,
                source_type=source_type,
                unit_count=0,
                token_usage=0,
                errors=errors,
            )

    def _process_natural_language_workflow(self, raw_content: str, source_type: str) -> Optional[PipelineResult]:
        """Import a detailed natural-language workflow as one reusable Skill.

        This is the human-operated demo path: users describe the goal, inputs,
        outputs, and step-by-step execution logic in text. The agent turns that
        into a parameterized Skill contract instead of trying to record pixels or
        mouse coordinates.
        """
        normalized_type = str(source_type).lower()
        if normalized_type not in {"natural_language", "natural_workflow", "workflow_description"}:
            return None

        import uuid

        data = _parse_json(raw_content)
        if isinstance(data, dict):
            title = str(data.get("title") or data.get("name") or "Natural Workflow Skill")
            description_text = str(data.get("description") or "")
            workflow_text = str(data.get("workflow") or data.get("content") or data.get("steps") or raw_content)
        else:
            title = "Natural Workflow Skill"
            description_text = ""
            workflow_text = raw_content

        prompt = f"""
你是 SkillOS 的 Natural Workflow Import Agent。用户会用自然语言详细描述一个可复用流程。

请把流程抽象为“固定执行骨架 + 可由 agent 决定的参数”，不要把示例目标硬编码成唯一目标。

## 标题
{title}

## 描述
{description_text}

## 流程文本
{workflow_text[:1600]}

## 输出 JSON
{{
  "name": "snake_case_skill_name",
  "description": "一句话描述这个可复用 Skill",
  "skill_type": "atomic|functional|strategic",
  "tags": ["tag"],
  "parameters": [
    {{"name": "url", "type": "string", "description": "由 agent 根据任务生成的目标 URL", "required": false}}
  ],
  "outputs": [
    {{"name": "success", "type": "boolean", "description": "是否完成"}}
  ],
  "preconditions": ["执行前需要满足的条件"],
  "postconditions": ["执行后应满足的结果"],
  "side_effects": ["会打开浏览器/写文件等副作用"],
  "workflow_steps": ["规范化步骤1", "规范化步骤2"],
  "tool_calls": ["host.open_url_in_chrome"],
  "test_cases": [
    {{"name": "example", "input_data": {{}}, "expected_output": {{"success": true}}}}
  ],
  "confidence": 0.86
}}

只输出 JSON。
"""
        try:
            response = self._llm.chat([
                Message.system("你是 SkillOS Natural Workflow Import Agent，严格输出 JSON。"),
                Message.user(prompt),
            ])
            parsed = _parse_json(response.content) or {}
        except Exception as exc:
            logger.warning(f"Natural workflow import LLM 失败: {exc}")
            parsed = {}

        parsed = _fallback_natural_workflow(parsed, title, workflow_text)
        name = _normalize_skill_name(str(parsed.get("name") or title))
        actions = [str(item) for item in parsed.get("workflow_steps", []) if str(item).strip()]
        if not actions:
            actions = [line.strip(" -0123456789.、") for line in workflow_text.splitlines() if line.strip()]
        actions = actions[:12] or [workflow_text[:160]]
        tags = [str(tag).lower() for tag in parsed.get("tags", []) if str(tag).strip()]

        source_id = f"natural_language:{uuid.uuid4()}"
        metadata = {
            "source_id": source_id,
            "source_title": title,
            "source_description": description_text or workflow_text[:240],
            "source_type": normalized_type,
            "tools": _tools_from_implementation({"tool_calls": parsed.get("tool_calls") or []}),
            "tests": [str(case.get("name", "natural workflow example")) for case in parsed.get("test_cases", []) if isinstance(case, dict)],
            "version": "1.0.0",
            "interface": _interface_from_parameter_lists(parsed),
            "implementation": {
                "language": "natural_language",
                "prompt_template": (
                    "Follow this reusable workflow. Resolve task-specific parameters from the current user goal; "
                    "do not hard-code examples from the imported description.\n\n"
                    + "\n".join(f"{index + 1}. {step}" for index, step in enumerate(actions))
                ),
                "tool_calls": parsed.get("tool_calls") or [],
            },
            "raw_skill": parsed,
            "capability_scope": "generic",
            "capability_kind": "natural_workflow",
        }

        unit = StructuredExperience(
            unit_id=f"{source_id}:unit:1",
            source_type=normalized_type,
            raw_content=workflow_text[:500],
            extracted_actions=actions,
            normalized_actions=[
                {"verb": _first_word(action), "object": action, "description": action, "source": "natural_language_workflow"}
                for action in actions
            ],
            summary=str(parsed.get("description") or description_text or title),
            proposed_skill_name=name,
            proposed_description=str(parsed.get("description") or description_text or title),
            proposed_type=str(parsed.get("skill_type") or "functional").lower(),
            confidence=float(parsed.get("confidence") or 0.82),
            index_keywords=sorted(set(tags + name.split("_") + ["natural", "workflow"])),
            index_embedding_hint=f"{name}: {parsed.get('description') or workflow_text[:160]}",
            metadata=metadata,
        )
        return PipelineResult(
            success=True,
            source_type=normalized_type,
            unit_count=1,
            token_usage=0,
            units=[unit],
        )

    def process_batch(self, items: List[Dict[str, str]]) -> PipelineResult:
        """批量处理多条原始输入。"""
        all_units: List[StructuredExperience] = []
        errors: List[str] = []
        for item in items:
            result = self.process(item.get("content", ""), item.get("source_type", "unknown"))
            all_units.extend(result.units)
            errors.extend(result.errors)
        return PipelineResult(
            success=len(all_units) > 0,
            source_type="batch",
            unit_count=len(all_units),
            token_usage=0,
            errors=errors,
            units=all_units,
        )


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _process_static_demo_source(raw_content: str, source_type: str) -> Optional[PipelineResult]:
    """Parse fixed demo input without requiring live data collection or LLM calls.

    Research demos often use curated source files. If the input is JSON with a
    `skills` array, each item becomes a StructuredExperience unit and carries
    graph metadata for the ingest route to index.
    """
    import uuid

    data = _parse_json(raw_content)
    if not data or not isinstance(data.get("skills"), list):
        return None

    normalized_source_type = str(data.get("source_type") or source_type).lower()
    source_id = str(data.get("source_id") or f"{normalized_source_type}:{uuid.uuid4()}")
    title = str(data.get("title") or data.get("name") or normalized_source_type.replace("_", " ").title())
    content = str(data.get("content") or raw_content)

    units: List[StructuredExperience] = []
    for index, skill_data in enumerate(data["skills"]):
        if not isinstance(skill_data, dict):
            continue
        name = _normalize_skill_name(str(skill_data.get("name") or f"{normalized_source_type}_skill_{index + 1}"))
        description = str(skill_data.get("description") or f"Skill extracted from {title}.")
        skill_type = str(skill_data.get("skill_type") or skill_data.get("type") or "functional").lower()
        tags = [str(tag).lower() for tag in skill_data.get("tags", []) if str(tag).strip()]
        actions = [str(action) for action in skill_data.get("actions", []) if str(action).strip()]
        if not actions:
            actions = [description]

        normalized_actions = [
            {
                "verb": _first_word(action),
                "object": action,
                "description": action,
                "source": "static_demo",
            }
            for action in actions
        ]

        metadata = {
            "source_id": source_id,
            "source_title": title,
            "source_description": str(data.get("description") or content[:240]),
            "source_type": normalized_source_type,
            "tools": [str(item) for item in skill_data.get("tools", [])],
            "api_endpoints": [str(item) for item in skill_data.get("api_endpoints", [])],
            "tests": [str(item) for item in skill_data.get("tests", [])],
            "version": str(skill_data.get("version") or "1.0.0"),
            "interface": skill_data.get("interface") or {},
            "implementation": skill_data.get("implementation") or {},
            "graph_edges": skill_data.get("graph_edges") or [],
            "raw_skill": skill_data,
        }

        keywords = sorted(set(tags + name.replace("_", " ").split() + description.lower().split()[:8]))
        units.append(StructuredExperience(
            unit_id=str(skill_data.get("unit_id") or f"{source_id}:unit:{index + 1}"),
            source_type=normalized_source_type,
            raw_content=content[:500],
            extracted_actions=actions,
            normalized_actions=normalized_actions,
            summary=description,
            proposed_skill_name=name,
            proposed_description=description,
            proposed_type=skill_type,
            confidence=float(skill_data.get("confidence", 0.92)),
            index_keywords=keywords,
            index_embedding_hint=f"{name}: {description}",
            metadata=metadata,
        ))

    return PipelineResult(
        success=len(units) > 0,
        source_type=normalized_source_type,
        unit_count=len(units),
        token_usage=0,
        errors=[] if units else ["No valid skills found in static demo input."],
        units=units,
    )


def _derive_capability_units(
    raw_content: str,
    source_type: str,
    existing_units: List[StructuredExperience],
) -> List[StructuredExperience]:
    """Derive generic and scenario-specific capabilities from fixed sources.

    The imported source may say "open abc.json" or "go to https://...".
    For the demo we intentionally create two levels:
    - generic parameterized capability, e.g. open_specified_file(path)
    - special scene capability, e.g. open_abc_json_file with a default path
    """
    import uuid

    text = _flatten_source_text(raw_content)
    normalized_source_type = str(source_type).lower()
    source_id = _source_id_from_content(raw_content, normalized_source_type, uuid.uuid4())
    existing_names = {
        str(unit.proposed_skill_name)
        for unit in existing_units
        if getattr(unit, "proposed_skill_name", None)
    }
    units: List[StructuredExperience] = []

    file_targets = _find_file_targets(text)
    if file_targets and "open_specified_file" not in existing_names:
        units.append(_capability_unit(
            source_id=source_id,
            source_type=normalized_source_type,
            name="open_specified_file",
            description="Open a user-specified local file or folder using the host OS default application.",
            skill_type="atomic",
            actions=["infer or receive a file path", "call the host file opener", "record the opened path"],
            tags=["host", "file", "open", "generic", "parameterized"],
            capability_scope="generic",
            capability_kind="file_open",
            interface=_json_schema(
                {"path": ("string", "Absolute, home-relative, or inferred file/folder path", True)},
                {"launched": ("boolean", "Whether the OS accepted the open request", False), "path": ("string", "Opened path", False)},
            ),
            implementation={
                "language": "python",
                "code": 'output["launched"] = True\noutput["path"] = input_data.get("path")',
                "tool_calls": ["host.open_file"],
            },
        ))

    for target in file_targets[:3]:
        name = f"open_{_normalize_skill_name(target['name'])}_file"
        if name in existing_names:
            continue
        units.append(_capability_unit(
            source_id=source_id,
            source_type=normalized_source_type,
            name=name,
            description=f"Open the scenario-specific file or folder '{target['display']}' from the imported source.",
            skill_type="atomic",
            actions=[f"resolve scenario target {target['display']}", "call the host file opener"],
            tags=["host", "file", "open", "specialized", target["extension"].lstrip(".") or "folder"],
            capability_scope="specialized",
            capability_kind="file_open",
            target=target["display"],
            interface=_json_schema(
                {"path": ("string", f"Defaults to {target['path']}", False, target["path"])},
                {"launched": ("boolean", "Whether the OS accepted the open request", False), "path": ("string", "Opened path", False)},
            ),
            implementation={
                "language": "python",
                "code": f'output["launched"] = True\noutput["path"] = input_data.get("path") or "{target["path"]}"',
                "tool_calls": ["host.open_file"],
            },
        ))

    url_targets = _find_url_targets(text)
    if url_targets and "open_specified_url" not in existing_names:
        units.append(_capability_unit(
            source_id=source_id,
            source_type=normalized_source_type,
            name="open_specified_url",
            description="Open Chrome and navigate to a user-specified URL.",
            skill_type="atomic",
            actions=["infer or receive a target URL", "launch Chrome with the URL", "record the opened URL"],
            tags=["host", "browser", "chrome", "url", "generic", "parameterized"],
            capability_scope="generic",
            capability_kind="url_open",
            interface=_json_schema(
                {"url": ("string", "Target URL", True)},
                {"launched": ("boolean", "Whether Chrome accepted the URL open request", False), "url": ("string", "Opened URL", False)},
            ),
            implementation={
                "language": "python",
                "code": 'output["launched"] = True\noutput["url"] = input_data.get("url")',
                "tool_calls": ["host.open_url_in_chrome"],
            },
        ))

    for target in url_targets[:3]:
        name = f"open_{_normalize_skill_name(target['label'])}_url"
        if name in existing_names:
            continue
        units.append(_capability_unit(
            source_id=source_id,
            source_type=normalized_source_type,
            name=name,
            description=f"Open the scenario-specific website '{target['display']}' in Chrome.",
            skill_type="atomic",
            actions=[f"resolve scenario URL {target['display']}", "open Chrome with the target URL"],
            tags=["host", "browser", "chrome", "url", "website", "specialized"],
            capability_scope="specialized",
            capability_kind="url_open",
            target=target["display"],
            interface=_json_schema(
                {"url": ("string", f"Defaults to {target['url']}", False, target["url"])},
                {"launched": ("boolean", "Whether Chrome accepted the URL open request", False), "url": ("string", "Opened URL", False)},
            ),
            implementation={
                "language": "python",
                "code": f'output["launched"] = True\noutput["url"] = input_data.get("url") or "{target["url"]}"',
                "tool_calls": ["host.open_url_in_chrome"],
            },
        ))

    app_features = _find_application_features(text)
    for feature in app_features[:4]:
        name = f"open_{_normalize_skill_name(feature['application'])}_{_normalize_skill_name(feature['feature'])}"
        if name in existing_names:
            continue
        tool_call = "host.open_url_in_chrome" if feature.get("url") else "host.open_application"
        input_name = "url" if feature.get("url") else "application"
        default_value = str(feature.get("url") or feature["application"])
        units.append(_capability_unit(
            source_id=source_id,
            source_type=normalized_source_type,
            name=name,
            description=f"Open the '{feature['feature']}' function in {feature['application']} for this application scenario.",
            skill_type="functional",
            actions=[f"open {feature['application']}", f"navigate to or activate {feature['feature']}"],
            tags=["host", "application", "feature", "specialized", _normalize_skill_name(feature["application"])],
            capability_scope="specialized",
            capability_kind="application_feature",
            target=f"{feature['application']}::{feature['feature']}",
            interface=_json_schema(
                {input_name: ("string", f"Defaults to {default_value}", False, default_value)},
                {"launched": ("boolean", "Whether the feature open request was accepted", False)},
            ),
            implementation={
                "language": "python",
                "code": f'output["launched"] = True\noutput["{input_name}"] = input_data.get("{input_name}") or "{default_value}"',
                "tool_calls": [tool_call],
            },
        ))

    return units


def _capability_unit(
    *,
    source_id: str,
    source_type: str,
    name: str,
    description: str,
    skill_type: str,
    actions: List[str],
    tags: List[str],
    capability_scope: str,
    capability_kind: str,
    interface: Dict[str, Any],
    implementation: Dict[str, Any],
    target: str = "",
) -> StructuredExperience:
    import uuid

    metadata = {
        "source_id": source_id,
        "source_title": f"{source_type.title()} capability extraction",
        "source_description": description,
        "source_type": source_type,
        "tools": _tools_from_implementation(implementation),
        "version": "1.0.0",
        "interface": interface,
        "implementation": implementation,
        "capability_scope": capability_scope,
        "capability_kind": capability_kind,
        "target": target,
        "extraction_policy": "generic_and_specialized_capability_agent",
    }
    if target:
        metadata["tests"] = [f"{name} opens {target}"]

    return StructuredExperience(
        unit_id=f"{source_id}:capability:{name}:{uuid.uuid4().hex[:8]}",
        source_type=source_type,
        raw_content=description,
        extracted_actions=actions,
        normalized_actions=[
            {"verb": _first_word(action), "object": action, "description": action, "source": "capability_generalizer"}
            for action in actions
        ],
        summary=description,
        proposed_skill_name=name,
        proposed_description=description,
        proposed_type=skill_type,
        confidence=0.9 if capability_scope == "generic" else 0.86,
        index_keywords=sorted(set(tags + name.split("_") + [capability_scope, capability_kind])),
        index_embedding_hint=f"{name}: {description}",
        metadata=metadata,
    )


def _flatten_source_text(raw_content: str) -> str:
    data = _parse_json(raw_content)
    chunks = [raw_content]
    if isinstance(data, dict):
        for key in ("title", "description", "content"):
            if data.get(key):
                chunks.append(str(data[key]))
        for skill in data.get("skills", []) or []:
            if not isinstance(skill, dict):
                continue
            for key in ("name", "description"):
                if skill.get(key):
                    chunks.append(str(skill[key]))
            chunks.extend(str(action) for action in skill.get("actions", []) or [])
    return "\n".join(chunks)


def _source_id_from_content(raw_content: str, source_type: str, fallback: Any) -> str:
    data = _parse_json(raw_content)
    if isinstance(data, dict) and data.get("source_id"):
        return str(data["source_id"])
    return f"{source_type}:{fallback}"


def _find_file_targets(text: str) -> List[Dict[str, str]]:
    patterns = [
        r"(?:打开|open|load|read)\s+[\"'“”‘’]?([^\"'“”‘’\s，。]+?\.(?:json|csv|txt|md|py|pdf|docx|xlsx|yaml|yml))[\"'“”‘’]?",
        r"((?:~|/)?[A-Za-z0-9_\-./]+?\.(?:json|csv|txt|md|py|pdf|docx|xlsx|yaml|yml))",
    ]
    found: List[Dict[str, str]] = []
    seen = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            display = match.group(1).strip().strip(".,，。")
            if not display or display in seen:
                continue
            seen.add(display)
            name = re.sub(r"[^A-Za-z0-9]+", "_", display).strip("_") or "target"
            extension = "." + display.rsplit(".", 1)[-1].lower() if "." in display else ""
            found.append({
                "display": display,
                "name": name,
                "extension": extension,
                "path": _default_path_for_file(display),
            })
    return found


def _find_url_targets(text: str) -> List[Dict[str, str]]:
    found: List[Dict[str, str]] = []
    seen = set()
    for match in re.finditer(r"(https?://[^\s，。\"'“”‘’]+|chrome://[^\s，。\"'“”‘’]+)", text, flags=re.IGNORECASE):
        url = match.group(1).rstrip(".,")
        if url in seen:
            continue
        seen.add(url)
        label = re.sub(r"^https?://", "", url, flags=re.IGNORECASE).replace("chrome://", "chrome_")
        label = label.split("/")[0] if not url.startswith("chrome://") else label.replace("/", "_")
        found.append({"url": url, "display": url, "label": label or "website"})
    domain_aliases = {
        "openai": "https://openai.com/",
        "chatgpt": "https://chatgpt.com/",
        "github": "https://github.com/",
    }
    lowered = text.lower()
    for label, url in domain_aliases.items():
        if label in lowered and url not in seen:
            seen.add(url)
            found.append({"url": url, "display": url, "label": label})
    return found


def _find_application_features(text: str) -> List[Dict[str, str]]:
    lowered = text.lower()
    features: List[Dict[str, str]] = []
    seen = set()
    chrome_feature_urls = {
        "settings": "chrome://settings/",
        "设置": "chrome://settings/",
        "downloads": "chrome://downloads/",
        "下载": "chrome://downloads/",
        "history": "chrome://history/",
        "历史": "chrome://history/",
        "extensions": "chrome://extensions/",
        "扩展": "chrome://extensions/",
    }
    if "chrome" in lowered or "谷歌" in text or "浏览器" in text:
        for feature, url in chrome_feature_urls.items():
            if feature in lowered or feature in text:
                if url in seen:
                    continue
                seen.add(url)
                features.append({"application": "Chrome", "feature": str(feature), "url": url})
    if ("terminal" in lowered or "终端" in text) and ("top" in lowered or "进程" in text):
        features.append({"application": "Terminal", "feature": "top_process_monitor"})
    return features


def _json_schema(
    inputs: Dict[str, tuple],
    outputs: Dict[str, tuple],
) -> Dict[str, Any]:
    def props(items: Dict[str, tuple]) -> Dict[str, Any]:
        result = {}
        for name, spec in items.items():
            field: Dict[str, Any] = {"type": spec[0], "description": spec[1]}
            if len(spec) >= 4:
                field["default"] = spec[3]
            result[name] = field
        return result

    return {
        "input_schema": {
            "type": "object",
            "properties": props(inputs),
            "required": [name for name, spec in inputs.items() if len(spec) >= 3 and spec[2]],
        },
        "output_schema": {
            "type": "object",
            "properties": props(outputs),
        },
    }


def _fallback_natural_workflow(parsed: Dict[str, Any], title: str, workflow_text: str) -> Dict[str, Any]:
    if parsed.get("name") and parsed.get("description"):
        return parsed
    lowered = workflow_text.lower()
    tool_calls: List[str] = []
    parameters: List[Dict[str, Any]] = []
    if any(token in lowered for token in ("url", "website", "browser", "chrome", "网址", "网站", "浏览器")):
        tool_calls.append("host.open_url_in_chrome")
        parameters.append({
            "name": "url",
            "type": "string",
            "description": "Target URL resolved by the execution agent from the user task.",
            "required": False,
        })
    if any(token in lowered for token in ("file", "folder", "path", "文件", "文件夹", "路径")):
        tool_calls.append("host.open_file")
        parameters.append({
            "name": "path",
            "type": "string",
            "description": "Target local path resolved by the execution agent from the user task.",
            "required": False,
        })
    if any(token in lowered for token in ("terminal", "command", "终端", "命令")):
        tool_calls.append("host.run_terminal_command")
        parameters.append({
            "name": "command",
            "type": "string",
            "description": "Safe command generated by the execution agent.",
            "required": False,
        })
    lines = [line.strip(" -0123456789.、") for line in workflow_text.splitlines() if line.strip()]
    return {
        **parsed,
        "name": parsed.get("name") or _normalize_skill_name(title),
        "description": parsed.get("description") or f"Execute the reusable workflow described by '{title}'.",
        "skill_type": parsed.get("skill_type") or ("strategic" if len(lines) >= 5 else "functional"),
        "tags": parsed.get("tags") or ["natural-language", "workflow", "agent-imported"],
        "parameters": parsed.get("parameters") or parameters or [
            {"name": "goal", "type": "string", "description": "Original user task.", "required": False},
        ],
        "outputs": parsed.get("outputs") or [
            {"name": "success", "type": "boolean", "description": "Whether the workflow reached the expected outcome."},
        ],
        "workflow_steps": parsed.get("workflow_steps") or lines,
        "tool_calls": parsed.get("tool_calls") or _dedupe(tool_calls),
        "confidence": parsed.get("confidence") or 0.72,
    }


def _interface_from_parameter_lists(parsed: Dict[str, Any]) -> Dict[str, Any]:
    def to_properties(items: List[Dict[str, Any]]) -> Dict[str, Any]:
        props: Dict[str, Any] = {}
        for item in items:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            props[str(item["name"])] = {
                "type": str(item.get("type") or "string"),
                "description": str(item.get("description") or ""),
            }
            if "default" in item:
                props[str(item["name"])]["default"] = item["default"]
        return props

    parameters = [item for item in parsed.get("parameters", []) if isinstance(item, dict)]
    outputs = [item for item in parsed.get("outputs", []) if isinstance(item, dict)]
    return {
        "input_schema": {
            "type": "object",
            "properties": to_properties(parameters),
            "required": [str(item["name"]) for item in parameters if item.get("required") and item.get("name")],
        },
        "output_schema": {
            "type": "object",
            "properties": to_properties(outputs),
        },
        "preconditions": [str(item) for item in parsed.get("preconditions", []) if str(item).strip()],
        "postconditions": [str(item) for item in parsed.get("postconditions", []) if str(item).strip()],
        "side_effects": [str(item) for item in parsed.get("side_effects", []) if str(item).strip()],
    }


def _tools_from_implementation(implementation: Dict[str, Any]) -> List[str]:
    tool_names = [str(item) for item in implementation.get("tool_calls", [])]
    return [tool.replace("host.", "Host ") for tool in tool_names]


def _dedupe(items: List[Any]) -> List[Any]:
    seen = set()
    result: List[Any] = []
    for item in items:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _default_path_for_file(display: str) -> str:
    if display.startswith(("~", "/")):
        return display
    return f"~/Downloads/{display}"


def _normalize_skill_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value.strip()).strip("_").lower()
    if not cleaned:
        return "extracted_skill"
    if not cleaned[0].isalpha():
        cleaned = f"skill_{cleaned}"
    return cleaned


def _first_word(value: str) -> str:
    words = re.findall(r"[A-Za-z_]+", value.lower())
    return words[0] if words else "do"
