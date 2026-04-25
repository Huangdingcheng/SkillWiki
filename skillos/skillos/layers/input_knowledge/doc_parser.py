"""文档/API 解析器 — 从技术文档、API 规范中提取 ExperienceUnit。

支持格式：
- OpenAPI / Swagger JSON/YAML
- Markdown 技术文档
- 纯文本 API 说明
- Python 函数/类文档字符串
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from ...models.experience_model import (
    ExperienceSourceType,
    ExperienceUnit,
    TrajectoryStep,
)
from ...utils.llm_client import LLMClient
from ...utils.logger import get_logger
from .base_parser import BaseParser, ParseResult

logger = get_logger(__name__)

_DOC_EXTRACT_PROMPT = """
请分析以下技术文档或 API 说明，提取可以封装为 Skill 的操作模式。

## 文档内容
{doc_content}

## 任务
1. 识别文档中描述的操作/功能（每个操作对应一个 ExperienceUnit）
2. 对每个操作提取：
   - title: 操作名称
   - description: 功能描述
   - task_description: 完成的任务
   - domain: 领域（web/api/file/code/system）
   - tags: 相关标签
   - input_params: 输入参数列表（name, type, description, required）
   - output_description: 输出描述
   - preconditions: 前置条件
   - example_usage: 使用示例（可选）

## 输出格式（严格 JSON）
{{
  "operations": [
    {{
      "title": "操作名称",
      "description": "功能描述",
      "task_description": "完成的任务",
      "domain": "api",
      "tags": ["tag1"],
      "input_params": [
        {{"name": "param1", "type": "string", "description": "...", "required": true}}
      ],
      "output_description": "返回值描述",
      "preconditions": ["条件1"],
      "example_usage": "示例代码或说明"
    }}
  ]
}}

只输出 JSON，不要其他内容。
"""

_OPENAPI_EXTRACT_PROMPT = """
请分析以下 OpenAPI 规范，为每个 API 端点提取操作信息。

## OpenAPI 规范（摘要）
{openapi_summary}

## 输出格式（严格 JSON）
{{
  "operations": [
    {{
      "title": "端点操作名",
      "description": "功能描述",
      "method": "GET/POST/...",
      "path": "/api/...",
      "domain": "api",
      "tags": ["tag1"],
      "input_params": [],
      "output_description": "响应描述",
      "preconditions": []
    }}
  ]
}}

只输出 JSON，不要其他内容。
"""


class DocParser(BaseParser):
    """技术文档和 API 规范解析器。"""

    def __init__(self, llm_client: LLMClient) -> None:
        super().__init__(llm_client)

    def _build_system_prompt(self) -> str:
        return (
            "你是 SkillOS 的文档分析专家，擅长从技术文档中识别可复用的操作模式。"
            "请精确提取每个操作的接口规范和使用方式。"
            "严格按照 JSON 格式输出。"
        )

    async def parse(self, raw_input: str, **kwargs: Any) -> ParseResult:
        """解析文档输入。

        Args:
            raw_input: 文档内容
            **kwargs: format（文档格式: markdown/openapi/text）, domain
        """
        result = ParseResult(raw_input=raw_input)
        doc_format = kwargs.get("format", self._detect_format(raw_input))

        try:
            if doc_format == "openapi":
                units = await self._parse_openapi(raw_input, kwargs)
            else:
                units = await self._parse_general_doc(raw_input, kwargs)

            result.experience_units.extend(units)
            result.metadata["doc_format"] = doc_format
            logger.info(f"文档解析完成: {len(units)} 个操作单元")
        except Exception as e:
            logger.error(f"文档解析失败: {e}")
            result.errors.append(str(e))
            # 降级：整体作为一个 ExperienceUnit
            unit = ExperienceUnit(
                source_type=ExperienceSourceType.DOCUMENTATION,
                raw_content=raw_input[:10000],
                raw_content_format=doc_format,
                domain=kwargs.get("domain", "general"),
                title=kwargs.get("title", "文档"),
            )
            result.experience_units.append(unit)

        return result

    async def _parse_general_doc(
        self,
        content: str,
        kwargs: Dict[str, Any],
    ) -> List[ExperienceUnit]:
        """解析通用文档（Markdown/文本）。"""
        prompt = _DOC_EXTRACT_PROMPT.format(doc_content=content[:8000])
        response = await self._call_llm(prompt, self._build_system_prompt())
        data = self._extract_json(response)

        if not data or "operations" not in data:
            return []

        units = []
        for op in data["operations"]:
            # 将操作转换为 ExperienceUnit（用 steps 表示参数调用）
            steps = self._params_to_steps(op.get("input_params", []))
            unit = ExperienceUnit(
                source_type=ExperienceSourceType.DOCUMENTATION,
                title=op.get("title", "未知操作"),
                description=op.get("description", ""),
                steps=steps,
                raw_content=json.dumps(op, ensure_ascii=False),
                raw_content_format="json",
                task_description=op.get("task_description"),
                domain=op.get("domain", kwargs.get("domain", "general")),
                tags=op.get("tags", []),
                metadata={
                    "input_params": op.get("input_params", []),
                    "output_description": op.get("output_description", ""),
                    "preconditions": op.get("preconditions", []),
                    "example_usage": op.get("example_usage", ""),
                },
            )
            units.append(unit)
        return units

    async def _parse_openapi(
        self,
        content: str,
        kwargs: Dict[str, Any],
    ) -> List[ExperienceUnit]:
        """解析 OpenAPI 规范。"""
        # 提取关键信息（避免超出 token 限制）
        summary = self._summarize_openapi(content)
        prompt = _OPENAPI_EXTRACT_PROMPT.format(openapi_summary=summary[:6000])
        response = await self._call_llm(prompt, self._build_system_prompt())
        data = self._extract_json(response)

        if not data or "operations" not in data:
            return []

        units = []
        for op in data["operations"]:
            unit = ExperienceUnit(
                source_type=ExperienceSourceType.API_INTERACTION,
                title=op.get("title", f"{op.get('method', 'GET')} {op.get('path', '/')}"),
                description=op.get("description", ""),
                raw_content=json.dumps(op, ensure_ascii=False),
                raw_content_format="json",
                task_description=op.get("description"),
                domain="api",
                tags=op.get("tags", ["api"]),
                metadata={
                    "method": op.get("method"),
                    "path": op.get("path"),
                    "input_params": op.get("input_params", []),
                    "output_description": op.get("output_description", ""),
                },
            )
            units.append(unit)
        return units

    def _detect_format(self, content: str) -> str:
        """自动检测文档格式。"""
        content_stripped = content.strip()
        # OpenAPI JSON
        if content_stripped.startswith("{") and (
            '"openapi"' in content or '"swagger"' in content
        ):
            return "openapi"
        # OpenAPI YAML
        if content_stripped.startswith("openapi:") or content_stripped.startswith("swagger:"):
            return "openapi"
        # Markdown
        if re.search(r"^#{1,6}\s", content, re.MULTILINE):
            return "markdown"
        return "text"

    def _summarize_openapi(self, content: str) -> str:
        """提取 OpenAPI 规范的关键信息（路径和操作）。"""
        try:
            spec = json.loads(content)
            paths = spec.get("paths", {})
            summary_parts = []
            for path, methods in list(paths.items())[:30]:  # 最多 30 个路径
                for method, op in methods.items():
                    if method in ("get", "post", "put", "delete", "patch"):
                        summary_parts.append(
                            f"{method.upper()} {path}: {op.get('summary', op.get('description', ''))}"
                        )
            return "\n".join(summary_parts)
        except Exception:
            return content[:4000]

    def _params_to_steps(self, params: List[Dict[str, Any]]) -> List[TrajectoryStep]:
        """将参数列表转换为 TrajectoryStep（用于表示调用过程）。"""
        steps = []
        for i, param in enumerate(params):
            step = TrajectoryStep(
                step_index=i,
                action_type="set_parameter",
                action_target=param.get("name", f"param_{i}"),
                action_value=f"<{param.get('type', 'any')}>",
                metadata={
                    "description": param.get("description", ""),
                    "required": param.get("required", False),
                },
            )
            steps.append(step)
        return steps

    def _extract_json(self, text: str) -> Optional[Dict[str, Any]]:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        match = re.search(r"\{[\s\S]+\}", text)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None
