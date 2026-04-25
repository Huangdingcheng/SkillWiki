"""脚本分析器 — 从 Python/JS 代码中提取可复用操作模式。"""

from __future__ import annotations

import ast
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

_SCRIPT_ANALYZE_PROMPT = """
请分析以下代码，识别其中可以封装为可复用 Skill 的操作模式。

## 代码
```{language}
{code}
```

## 任务
1. 识别代码中的主要功能/操作（每个独立功能对应一个操作）
2. 对每个操作提取：
   - title: 操作名称（snake_case）
   - description: 功能描述
   - domain: 领域（web/api/file/code/system）
   - tags: 相关标签
   - input_params: 输入参数
   - output_description: 输出描述
   - preconditions: 前置条件
   - code_snippet: 核心代码片段

## 输出格式（严格 JSON）
{{
  "operations": [
    {{
      "title": "function_name",
      "description": "功能描述",
      "domain": "code",
      "tags": ["python", "utility"],
      "input_params": [
        {{"name": "param", "type": "str", "description": "...", "required": true}}
      ],
      "output_description": "返回值描述",
      "preconditions": [],
      "code_snippet": "def function_name(...):\\n    ..."
    }}
  ]
}}

只输出 JSON，不要其他内容。
"""


class ScriptAnalyzer(BaseParser):
    """代码脚本分析器。

    支持：
    - Python 脚本（AST 静态分析 + LLM 语义理解）
    - JavaScript/TypeScript 脚本（LLM 分析）
    - Shell 脚本（LLM 分析）
    """

    def __init__(self, llm_client: LLMClient) -> None:
        super().__init__(llm_client)

    def _build_system_prompt(self) -> str:
        return (
            "你是 SkillOS 的代码分析专家，擅长从代码中识别可复用的操作模式。"
            "请精确提取每个函数/方法的接口规范和功能描述。"
            "严格按照 JSON 格式输出。"
        )

    async def parse(self, raw_input: str, **kwargs: Any) -> ParseResult:
        """解析代码输入。

        Args:
            raw_input: 代码内容
            **kwargs: language（python/javascript/shell）
        """
        result = ParseResult(raw_input=raw_input)
        language = kwargs.get("language", self._detect_language(raw_input))

        try:
            # Python 优先使用 AST 静态分析
            if language == "python":
                static_units = self._analyze_python_ast(raw_input)
                if static_units:
                    result.experience_units.extend(static_units)
                    result.metadata["parse_mode"] = "ast_static"
                    # 同时用 LLM 补充语义信息
                    llm_units = await self._llm_analyze(raw_input, language)
                    self._merge_units(result.experience_units, llm_units)
                    return result

            # 其他语言直接用 LLM
            units = await self._llm_analyze(raw_input, language)
            result.experience_units.extend(units)
            result.metadata["parse_mode"] = "llm"
            result.metadata["language"] = language

        except Exception as e:
            logger.error(f"脚本分析失败: {e}")
            result.errors.append(str(e))
            unit = ExperienceUnit(
                source_type=ExperienceSourceType.CODE_EXECUTION,
                raw_content=raw_input[:10000],
                raw_content_format="code",
                domain="code",
                metadata={"language": language},
            )
            result.experience_units.append(unit)

        return result

    def _analyze_python_ast(self, code: str) -> List[ExperienceUnit]:
        """使用 Python AST 静态分析提取函数信息。"""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            logger.warning(f"Python AST 解析失败: {e}")
            return []

        units = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            # 跳过私有方法和特殊方法
            if node.name.startswith("_"):
                continue

            # 提取参数
            params = self._extract_python_params(node)
            # 提取 docstring
            docstring = ast.get_docstring(node) or ""
            # 提取代码片段
            code_lines = code.split("\n")
            start = node.lineno - 1
            end = node.end_lineno if hasattr(node, "end_lineno") else start + 20
            snippet = "\n".join(code_lines[start:end])

            steps = [
                TrajectoryStep(
                    step_index=i,
                    action_type="call_function",
                    action_target=node.name,
                    action_value=p["name"],
                    metadata=p,
                )
                for i, p in enumerate(params)
            ]

            unit = ExperienceUnit(
                source_type=ExperienceSourceType.CODE_EXECUTION,
                title=node.name,
                description=docstring[:256] if docstring else f"Python 函数: {node.name}",
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
            units.append(unit)

        logger.debug(f"AST 分析提取 {len(units)} 个函数")
        return units

    def _extract_python_params(self, node: ast.FunctionDef) -> List[Dict[str, Any]]:
        """提取函数参数信息。"""
        params = []
        args = node.args
        defaults_offset = len(args.args) - len(args.defaults)

        for i, arg in enumerate(args.args):
            if arg.arg == "self" or arg.arg == "cls":
                continue
            param: Dict[str, Any] = {
                "name": arg.arg,
                "type": ast.unparse(arg.annotation) if arg.annotation else "Any",
                "required": i < defaults_offset,
                "description": "",
            }
            if i >= defaults_offset:
                default_node = args.defaults[i - defaults_offset]
                param["default"] = ast.unparse(default_node)
            params.append(param)

        return params

    async def _llm_analyze(self, code: str, language: str) -> List[ExperienceUnit]:
        """使用 LLM 分析代码。"""
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
                    step_index=i,
                    action_type="call_function",
                    action_target=op.get("title", "unknown"),
                    action_value=p.get("name", ""),
                    metadata=p,
                )
                for i, p in enumerate(op.get("input_params", []))
            ]
            unit = ExperienceUnit(
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
            units.append(unit)
        return units

    def _merge_units(
        self,
        ast_units: List[ExperienceUnit],
        llm_units: List[ExperienceUnit],
    ) -> None:
        """将 LLM 分析结果合并到 AST 结果中（补充语义信息）。"""
        llm_by_name = {u.title: u for u in llm_units}
        for unit in ast_units:
            if unit.title in llm_by_name:
                llm_unit = llm_by_name[unit.title]
                # 用 LLM 的描述补充 AST 的结构信息
                if not unit.description and llm_unit.description:
                    unit.description = llm_unit.description
                if llm_unit.tags:
                    unit.tags = list(set(unit.tags + llm_unit.tags))
                unit.metadata.update({
                    k: v for k, v in llm_unit.metadata.items()
                    if k not in unit.metadata
                })

    def _detect_language(self, code: str) -> str:
        """自动检测代码语言。"""
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
