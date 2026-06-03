"""Script analyzer for extracting reusable operations from code."""

from __future__ import annotations

import ast
import json
import re
from typing import Any, Dict, List, Optional

from ...models.experience_model import ExperienceSourceType, ExperienceUnit, TrajectoryStep
from ...utils.llm_client import LLMClient
from ...utils.logger import get_logger
from .base_parser import BaseParser, ParseResult

logger = get_logger(__name__)

_SCRIPT_ANALYZE_PROMPT = """
Analyze the following code and identify reusable operations that could become Skill candidates.

Code:
```{language}
{code}
```

Return only valid JSON with this shape:
{{
  "operations": [
    {{
      "title": "function_name",
      "description": "what the operation does",
      "domain": "code",
      "tags": ["python", "utility"],
      "input_params": [
        {{"name": "param", "type": "str", "description": "...", "required": true}}
      ],
      "output_description": "return value description",
      "preconditions": [],
      "code_snippet": "def function_name(...):\\n    ..."
    }}
  ]
}}

Rules:
- title should be snake_case.
- domain must be one of web, api, file, code, system, other.
- Return JSON only.
"""


class ScriptAnalyzer(BaseParser):
    """Analyze Python, JavaScript/TypeScript, and shell scripts."""

    def __init__(self, llm_client: LLMClient) -> None:
        super().__init__(llm_client)

    def _build_system_prompt(self) -> str:
        return (
            "You are the SkillWiki code analysis expert. "
            "Extract reusable functions or workflows with clear interfaces. "
            "Return valid JSON only."
        )

    async def parse(self, raw_input: str, **kwargs: Any) -> ParseResult:
        result = ParseResult(raw_input=raw_input)
        language = kwargs.get("language", self._detect_language(raw_input))

        try:
            if language == "python":
                static_units = self._analyze_python_ast(raw_input)
                if static_units:
                    result.experience_units.extend(static_units)
                    result.metadata["parse_mode"] = "ast_static"
                    llm_units = await self._llm_analyze(raw_input, language)
                    self._merge_units(result.experience_units, llm_units)
                    return result

            units = await self._llm_analyze(raw_input, language)
            result.experience_units.extend(units)
            result.metadata["parse_mode"] = "llm"
            result.metadata["language"] = language

        except Exception as exc:
            logger.error("Script analysis failed: %s", exc)
            result.errors.append(str(exc))
            result.experience_units.append(
                ExperienceUnit(
                    source_type=ExperienceSourceType.CODE_EXECUTION,
                    raw_content=raw_input[:10000],
                    raw_content_format="code",
                    domain="code",
                    metadata={"language": language},
                )
            )

        return result

    def _analyze_python_ast(self, code: str) -> List[ExperienceUnit]:
        try:
            tree = ast.parse(code)
        except SyntaxError as exc:
            logger.warning("Python AST parsing failed: %s", exc)
            return []

        units = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue

            params = self._extract_python_params(node)
            docstring = ast.get_docstring(node) or ""
            code_lines = code.split("\n")
            start = node.lineno - 1
            end = node.end_lineno if hasattr(node, "end_lineno") else start + 20
            snippet = "\n".join(code_lines[start:end])

            steps = [
                TrajectoryStep(
                    step_index=index,
                    action_type="call_function",
                    action_target=node.name,
                    action_value=param["name"],
                    metadata=param,
                )
                for index, param in enumerate(params)
            ]

            units.append(
                ExperienceUnit(
                    source_type=ExperienceSourceType.CODE_EXECUTION,
                    title=node.name,
                    description=docstring[:256] if docstring else f"Python function: {node.name}",
                    steps=steps,
                    raw_content=snippet,
                    raw_content_format="code",
                    domain="code",
                    tags=["python", "function"],
                    metadata={
                        "function_name": node.name,
                        "is_async": isinstance(node, ast.AsyncFunctionDef),
                        "params": params,
                        "docstring": docstring,
                        "line_start": node.lineno,
                    },
                )
            )

        logger.debug("AST analysis extracted %s functions", len(units))
        return units

    def _extract_python_params(self, node: ast.AST) -> List[Dict[str, Any]]:
        params = []
        args = node.args  # type: ignore[attr-defined]
        defaults_offset = len(args.args) - len(args.defaults)

        for index, arg in enumerate(args.args):
            if arg.arg in {"self", "cls"}:
                continue
            param: Dict[str, Any] = {
                "name": arg.arg,
                "type": ast.unparse(arg.annotation) if arg.annotation else "Any",
                "required": index < defaults_offset,
                "description": "",
            }
            if index >= defaults_offset:
                default_node = args.defaults[index - defaults_offset]
                param["default"] = ast.unparse(default_node)
            params.append(param)

        return params

    async def _llm_analyze(self, code: str, language: str) -> List[ExperienceUnit]:
        prompt = _SCRIPT_ANALYZE_PROMPT.format(
            language=language,
            code=code[:6000],
        )
        response = await self._call_llm(prompt, self._build_system_prompt())
        data = self._extract_json(response)

        if not data or "operations" not in data:
            return []

        units = []
        for op in data["operations"]:
            steps = [
                TrajectoryStep(
                    step_index=index,
                    action_type="call_function",
                    action_target=op.get("title", "unknown"),
                    action_value=param.get("name", ""),
                    metadata=param,
                )
                for index, param in enumerate(op.get("input_params", []))
            ]
            units.append(
                ExperienceUnit(
                    source_type=ExperienceSourceType.CODE_EXECUTION,
                    title=op.get("title", "unknown"),
                    description=op.get("description", ""),
                    steps=steps,
                    raw_content=op.get("code_snippet", ""),
                    raw_content_format="code",
                    domain=op.get("domain", "code"),
                    tags=op.get("tags", ["code"]),
                    metadata={
                        "input_params": op.get("input_params", []),
                        "output_description": op.get("output_description", ""),
                        "preconditions": op.get("preconditions", []),
                        "language": language,
                    },
                )
            )
        return units

    def _merge_units(
        self,
        ast_units: List[ExperienceUnit],
        llm_units: List[ExperienceUnit],
    ) -> None:
        llm_by_name = {unit.title: unit for unit in llm_units}
        for unit in ast_units:
            if unit.title in llm_by_name:
                llm_unit = llm_by_name[unit.title]
                if not unit.description and llm_unit.description:
                    unit.description = llm_unit.description
                if llm_unit.tags:
                    unit.tags = list(set(unit.tags + llm_unit.tags))
                unit.metadata.update(
                    {
                        key: value
                        for key, value in llm_unit.metadata.items()
                        if key not in unit.metadata
                    }
                )

    def _detect_language(self, code: str) -> str:
        if re.search(r"^(def |class |import |from |async def )", code, re.MULTILINE):
            return "python"
        if re.search(r"(function\s+\w+|const\s+\w+\s*=|=>|require\()", code):
            return "javascript"
        if re.search(r"^#!/bin/(bash|sh|zsh)", code):
            return "shell"
        return "unknown"

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
