"""Document and API-spec parser for the input knowledge layer."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from ...models.experience_model import ExperienceSourceType, ExperienceUnit, TrajectoryStep
from ...utils.llm_client import LLMClient
from ...utils.logger import get_logger
from .base_parser import BaseParser, ParseResult

logger = get_logger(__name__)

_DOC_EXTRACT_PROMPT = """
Analyze the following technical document or operating guide and extract reusable operations.

Document:
{doc_content}

Return only valid JSON with this shape:
{{
  "operations": [
    {{
      "title": "operation_name",
      "description": "what the operation does",
      "task_description": "task completed by this operation",
      "domain": "api",
      "tags": ["api"],
      "input_params": [
        {{"name": "param1", "type": "string", "description": "...", "required": true}}
      ],
      "output_description": "what the operation returns",
      "preconditions": ["condition 1"],
      "example_usage": "optional example"
    }}
  ]
}}

Rules:
- domain must be one of web, api, file, code, system, other.
- title should be snake_case when possible.
- Return JSON only.
"""

_OPENAPI_EXTRACT_PROMPT = """
Analyze this OpenAPI summary and extract one operation per API endpoint.

OpenAPI summary:
{openapi_summary}

Return only valid JSON with this shape:
{{
  "operations": [
    {{
      "title": "endpoint_operation_name",
      "description": "what the endpoint does",
      "method": "GET",
      "path": "/api/example",
      "domain": "api",
      "tags": ["api"],
      "input_params": [],
      "output_description": "response description",
      "preconditions": []
    }}
  ]
}}
"""


class DocParser(BaseParser):
    """Parse Markdown, text, and OpenAPI-style documentation."""

    def __init__(self, llm_client: LLMClient) -> None:
        super().__init__(llm_client)

    def _build_system_prompt(self) -> str:
        return (
            "You are the SkillWiki document analysis expert. "
            "Identify reusable operations and their interface details from documentation. "
            "Return valid JSON only."
        )

    async def parse(self, raw_input: str, **kwargs: Any) -> ParseResult:
        result = ParseResult(raw_input=raw_input)
        doc_format = kwargs.get("format", self._detect_format(raw_input))

        try:
            if doc_format == "openapi":
                units = await self._parse_openapi(raw_input)
            else:
                units = await self._parse_general_doc(raw_input, kwargs)

            result.experience_units.extend(units)
            result.metadata["doc_format"] = doc_format
            logger.info("Document parsed: %s operations", len(units))
        except Exception as exc:
            logger.error("Document parsing failed: %s", exc)
            result.errors.append(str(exc))
            result.experience_units.append(
                ExperienceUnit(
                    source_type=ExperienceSourceType.DOCUMENTATION,
                    raw_content=raw_input[:10000],
                    raw_content_format=doc_format,
                    domain=kwargs.get("domain", "general"),
                    title=kwargs.get("title", "Document"),
                )
            )

        return result

    async def _parse_general_doc(
        self,
        content: str,
        kwargs: Dict[str, Any],
    ) -> List[ExperienceUnit]:
        prompt = _DOC_EXTRACT_PROMPT.format(doc_content=content[:8000])
        response = await self._call_llm(prompt, self._build_system_prompt())
        data = self._extract_json(response)

        if not data or "operations" not in data:
            return []

        units = []
        for op in data["operations"]:
            steps = self._params_to_steps(op.get("input_params", []))
            units.append(
                ExperienceUnit(
                    source_type=ExperienceSourceType.DOCUMENTATION,
                    title=op.get("title", "unknown_operation"),
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
            )
        return units

    async def _parse_openapi(self, content: str) -> List[ExperienceUnit]:
        summary = self._summarize_openapi(content)
        prompt = _OPENAPI_EXTRACT_PROMPT.format(openapi_summary=summary[:6000])
        response = await self._call_llm(prompt, self._build_system_prompt())
        data = self._extract_json(response)

        if not data or "operations" not in data:
            return []

        units = []
        for op in data["operations"]:
            units.append(
                ExperienceUnit(
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
                        "preconditions": op.get("preconditions", []),
                    },
                )
            )
        return units

    def _detect_format(self, content: str) -> str:
        content_stripped = content.strip()
        if content_stripped.startswith("{") and (
            '"openapi"' in content or '"swagger"' in content
        ):
            return "openapi"
        if content_stripped.startswith("openapi:") or content_stripped.startswith("swagger:"):
            return "openapi"
        if re.search(r"^#{1,6}\s", content, re.MULTILINE):
            return "markdown"
        return "text"

    def _summarize_openapi(self, content: str) -> str:
        try:
            spec = json.loads(content)
            paths = spec.get("paths", {})
            summary_parts = []
            for path, methods in list(paths.items())[:30]:
                for method, op in methods.items():
                    if method in ("get", "post", "put", "delete", "patch"):
                        summary_parts.append(
                            f"{method.upper()} {path}: {op.get('summary', op.get('description', ''))}"
                        )
            return "\n".join(summary_parts)
        except Exception:
            return content[:4000]

    def _params_to_steps(self, params: List[Dict[str, Any]]) -> List[TrajectoryStep]:
        steps = []
        for index, param in enumerate(params):
            steps.append(
                TrajectoryStep(
                    step_index=index,
                    action_type="set_parameter",
                    action_target=param.get("name", f"param_{index}"),
                    action_value=f"<{param.get('type', 'any')}>",
                    metadata={
                        "description": param.get("description", ""),
                        "required": param.get("required", False),
                    },
                )
            )
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
