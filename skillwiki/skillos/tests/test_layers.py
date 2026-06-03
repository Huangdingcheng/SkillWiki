"""Phase 2-4 层测试套件（不依赖真实 DB/LLM）。"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from skillos.layers.input_knowledge import DocParser, ParseResult, ScriptAnalyzer, TrajectoryParser
from skillos.layers.skill_construction import CandidateMiner, SkillFormalizer, SkillValidator
from skillos.layers.skill_repository import SearchQuery, SkillSearchEngine
from skillos.models import (
    ExperienceSourceType,
    ExperienceUnit,
    Skill,
    SkillEdge,
    SkillGraphNode,
    SkillImplementation,
    SkillInterface,
    SkillProposal,
    SkillState,
    SkillSubgraph,
    SkillType,
    TrajectoryStep,
)
from skillos.models.graph_model import EdgeType


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture
def mock_llm():
    """返回一个 Mock LLM 客户端。"""
    llm = MagicMock()
    response = MagicMock()
    response.content = "{}"
    llm.chat.return_value = response
    return llm


@pytest.fixture
def sample_skill():
    return Skill(
        name="fill_form",
        version="1.0.0",
        description="填写页面上的结构化表单",
        skill_type=SkillType.FUNCTIONAL,
        domain="web",
        state=SkillState.RELEASED,
        tags=["web", "form", "input"],
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {"fields": {"type": "object"}},
                "required": ["fields"],
            },
            output_schema={
                "type": "object",
                "properties": {"filled_count": {"type": "integer"}},
            },
            preconditions=["页面上存在可编辑的表单字段"],
            postconditions=["所有字段已填写"],
        ),
        implementation=SkillImplementation(
            sub_skill_ids=["id1", "id2"],
            prompt_template="填写表单字段 {fields}",
        ),
    )


@pytest.fixture
def sample_experience():
    return ExperienceUnit(
        source_type=ExperienceSourceType.BROWSER_TRAJECTORY,
        title="填写登录表单",
        description="在登录页面填写用户名和密码",
        task_description="完成用户登录",
        domain="web",
        tags=["web", "login", "form"],
        steps=[
            TrajectoryStep(step_index=0, action_type="navigate", action_target="https://example.com/login"),
            TrajectoryStep(step_index=1, action_type="type", action_target="#username", action_value="user@example.com"),
            TrajectoryStep(step_index=2, action_type="type", action_target="#password", action_value="password"),
            TrajectoryStep(step_index=3, action_type="click", action_target="#submit"),
        ],
    )


# ===========================================================================
# TrajectoryParser Tests
# ===========================================================================

class TestTrajectoryParser:

    def test_parse_json_trajectory_direct(self, mock_llm):
        parser = TrajectoryParser(mock_llm)
        json_input = json.dumps([
            {"type": "click", "selector": "#btn", "success": True},
            {"type": "type", "selector": "#input", "value": "hello", "success": True},
        ])
        # 同步测试 JSON 解析（不调用 LLM）
        steps = parser._try_parse_json_trajectory(json_input)
        assert steps is not None
        assert len(steps) == 2
        assert steps[0]["type"] == "click"

    def test_parse_non_json_returns_none(self, mock_llm):
        parser = TrajectoryParser(mock_llm)
        result = parser._try_parse_json_trajectory("点击登录按钮，然后输入用户名")
        assert result is None

    def test_build_unit_from_json(self, mock_llm):
        parser = TrajectoryParser(mock_llm)
        json_steps = [
            {"type": "click", "selector": "#btn"},
            {"type": "type", "selector": "#input", "value": "test"},
        ]
        unit = parser._build_unit_from_json(json_steps, {"title": "测试轨迹", "domain": "web"})
        assert unit.title == "测试轨迹"
        assert unit.step_count == 2
        assert unit.domain == "web"
        assert unit.source_type == ExperienceSourceType.BROWSER_TRAJECTORY

    def test_extract_json_from_code_block(self, mock_llm):
        parser = TrajectoryParser(mock_llm)
        text = '```json\n{"key": "value"}\n```'
        result = parser._extract_json(text)
        assert result == {"key": "value"}

    def test_extract_json_direct(self, mock_llm):
        parser = TrajectoryParser(mock_llm)
        result = parser._extract_json('{"steps": []}')
        assert result == {"steps": []}

    def test_extract_json_embedded(self, mock_llm):
        parser = TrajectoryParser(mock_llm)
        text = 'Here is the result: {"steps": [{"action": "click"}]} done.'
        result = parser._extract_json(text)
        assert result is not None
        assert "steps" in result

    @pytest.mark.asyncio
    async def test_parse_json_input_no_llm_call(self, mock_llm):
        """JSON 格式输入不应调用 LLM。"""
        parser = TrajectoryParser(mock_llm)
        json_input = json.dumps([{"type": "click", "selector": "#btn"}])
        result = await parser.parse(json_input, title="测试")
        assert result.success
        assert result.unit_count == 1
        mock_llm.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_parse_text_calls_llm(self, mock_llm):
        """文本格式输入应调用 LLM。"""
        mock_llm.chat.return_value.content = json.dumps({
            "task_description": "登录操作",
            "domain": "web",
            "tags": ["web"],
            "steps": [
                {"action_type": "click", "action_target": "#btn", "success": True}
            ],
        })
        parser = TrajectoryParser(mock_llm)
        result = await parser.parse("点击登录按钮")
        assert result.unit_count >= 1


# ===========================================================================
# DocParser Tests
# ===========================================================================

class TestDocParser:

    def test_detect_format_openapi_json(self, mock_llm):
        parser = DocParser(mock_llm)
        content = '{"openapi": "3.0.0", "paths": {}}'
        assert parser._detect_format(content) == "openapi"

    def test_detect_format_openapi_yaml(self, mock_llm):
        parser = DocParser(mock_llm)
        content = "openapi: 3.0.0\npaths:\n  /api:\n    get:\n      summary: test"
        assert parser._detect_format(content) == "openapi"

    def test_detect_format_markdown(self, mock_llm):
        parser = DocParser(mock_llm)
        content = "# API Documentation\n\n## Endpoints\n\n### GET /users"
        assert parser._detect_format(content) == "markdown"

    def test_detect_format_text(self, mock_llm):
        parser = DocParser(mock_llm)
        content = "This is a plain text description of the API."
        assert parser._detect_format(content) == "text"

    def test_summarize_openapi(self, mock_llm):
        parser = DocParser(mock_llm)
        spec = {
            "openapi": "3.0.0",
            "paths": {
                "/users": {"get": {"summary": "List users"}},
                "/users/{id}": {"get": {"summary": "Get user"}},
            },
        }
        summary = parser._summarize_openapi(json.dumps(spec))
        assert "GET /users" in summary
        assert "List users" in summary

    def test_params_to_steps(self, mock_llm):
        parser = DocParser(mock_llm)
        params = [
            {"name": "user_id", "type": "string", "required": True},
            {"name": "limit", "type": "integer", "required": False},
        ]
        steps = parser._params_to_steps(params)
        assert len(steps) == 2
        assert steps[0].action_target == "user_id"
        assert steps[0].action_type == "set_parameter"

    @pytest.mark.asyncio
    async def test_parse_general_doc(self, mock_llm):
        mock_llm.chat.return_value.content = json.dumps({
            "operations": [
                {
                    "title": "get_user",
                    "description": "获取用户信息",
                    "domain": "api",
                    "tags": ["api", "user"],
                    "input_params": [{"name": "user_id", "type": "string", "required": True}],
                    "output_description": "用户对象",
                    "preconditions": [],
                }
            ]
        })
        parser = DocParser(mock_llm)
        result = await parser.parse("GET /users/{id} - 获取用户信息")
        assert result.unit_count == 1
        assert result.experience_units[0].title == "get_user"


# ===========================================================================
# ScriptAnalyzer Tests
# ===========================================================================

class TestScriptAnalyzer:

    def test_detect_language_python(self, mock_llm):
        analyzer = ScriptAnalyzer(mock_llm)
        code = "def hello():\n    print('hello')\n"
        assert analyzer._detect_language(code) == "python"

    def test_detect_language_javascript(self, mock_llm):
        analyzer = ScriptAnalyzer(mock_llm)
        code = "function hello() { console.log('hello'); }"
        assert analyzer._detect_language(code) == "javascript"

    def test_detect_language_shell(self, mock_llm):
        analyzer = ScriptAnalyzer(mock_llm)
        code = "#!/bin/bash\necho hello"
        assert analyzer._detect_language(code) == "shell"

    def test_analyze_python_ast_simple(self, mock_llm):
        analyzer = ScriptAnalyzer(mock_llm)
        code = '''
def click_element(selector: str, timeout: int = 5000) -> bool:
    """点击页面元素。"""
    pass

def type_text(selector: str, text: str) -> None:
    """在输入框中输入文本。"""
    pass
'''
        units = analyzer._analyze_python_ast(code)
        assert len(units) == 2
        names = [u.title for u in units]
        assert "click_element" in names
        assert "type_text" in names

    def test_analyze_python_ast_skips_private(self, mock_llm):
        analyzer = ScriptAnalyzer(mock_llm)
        code = '''
def public_func():
    pass

def _private_func():
    pass

def __dunder__():
    pass
'''
        units = analyzer._analyze_python_ast(code)
        assert len(units) == 1
        assert units[0].title == "public_func"

    def test_extract_python_params(self, mock_llm):
        import ast
        analyzer = ScriptAnalyzer(mock_llm)
        code = "def func(a: str, b: int = 5, c: bool = True): pass"
        tree = ast.parse(code)
        func_node = tree.body[0]
        params = analyzer._extract_python_params(func_node)
        assert len(params) == 3
        assert params[0]["name"] == "a"
        assert params[0]["required"] is True
        assert params[1]["name"] == "b"
        assert params[1]["required"] is False

    def test_analyze_python_ast_invalid_syntax(self, mock_llm):
        analyzer = ScriptAnalyzer(mock_llm)
        units = analyzer._analyze_python_ast("def broken(: pass")
        assert units == []


# ===========================================================================
# CandidateMiner Tests
# ===========================================================================

class TestCandidateMiner:

    def test_quick_filter_same_domain(self, mock_llm, sample_experience):
        miner = CandidateMiner(mock_llm)
        proposal = SkillProposal(
            source_experience_id=sample_experience.experience_id,
            proposed_name="fill_form",
            proposed_description="填写表单",
            proposed_domain="web",
            proposed_tags=["web", "form"],
        )
        existing = [
            {"skill_id": "id1", "name": "fill_form", "domain": "web", "tags": ["web", "form"]},
            {"skill_id": "id2", "name": "click_button", "domain": "web", "tags": ["web"]},
            {"skill_id": "id3", "name": "read_file", "domain": "file", "tags": ["file"]},
        ]
        filtered = miner._quick_filter(proposal, existing)
        # 同领域且有 token 重叠的才返回
        assert any(e["skill_id"] == "id1" for e in filtered)
        assert all(e["skill_id"] != "id3" for e in filtered)  # 不同领域排除

    def test_summarize_steps(self, mock_llm, sample_experience):
        miner = CandidateMiner(mock_llm)
        summary = miner._summarize_steps(sample_experience)
        assert "navigate" in summary
        assert "type" in summary

    def test_summarize_steps_no_steps(self, mock_llm):
        miner = CandidateMiner(mock_llm)
        exp = ExperienceUnit(
            source_type=ExperienceSourceType.DOCUMENTATION,
            raw_content="API 文档内容",
        )
        summary = miner._summarize_steps(exp)
        assert "API 文档内容" in summary

    @pytest.mark.asyncio
    async def test_mine_returns_proposals(self, mock_llm, sample_experience):
        mock_llm.chat.return_value.content = json.dumps({
            "candidates": [
                {
                    "proposed_name": "fill_login_form",
                    "proposed_description": "填写登录表单",
                    "proposed_type": "functional",
                    "proposed_domain": "web",
                    "proposed_tags": ["web", "login"],
                    "input_schema_draft": {"type": "object", "properties": {}},
                    "output_schema_draft": {"type": "object", "properties": {}},
                    "preconditions_draft": ["登录页面已打开"],
                    "postconditions_draft": ["已登录"],
                    "confidence": 0.9,
                }
            ]
        })
        miner = CandidateMiner(mock_llm)
        proposals = await miner.mine(sample_experience)
        assert len(proposals) == 1
        assert proposals[0].proposed_name == "fill_login_form"
        assert proposals[0].confidence == 0.9

    @pytest.mark.asyncio
    async def test_mine_filters_low_confidence(self, mock_llm, sample_experience):
        mock_llm.chat.return_value.content = json.dumps({
            "candidates": [
                {
                    "proposed_name": "low_conf_skill",
                    "proposed_description": "低置信度",
                    "proposed_type": "atomic",
                    "proposed_domain": "web",
                    "proposed_tags": [],
                    "confidence": 0.3,  # 低于默认阈值 0.6
                }
            ]
        })
        miner = CandidateMiner(mock_llm, min_confidence=0.6)
        proposals = await miner.mine(sample_experience)
        assert len(proposals) == 0

    @pytest.mark.asyncio
    async def test_mine_empty_candidates(self, mock_llm, sample_experience):
        mock_llm.chat.return_value.content = json.dumps({
            "candidates": [],
            "skip_reason": "操作过于简单，不值得封装",
        })
        miner = CandidateMiner(mock_llm)
        proposals = await miner.mine(sample_experience)
        assert proposals == []


# ===========================================================================
# SkillFormalizer Tests
# ===========================================================================

class TestSkillFormalizer:

    @pytest.fixture
    def sample_proposal(self, sample_experience):
        return SkillProposal(
            source_experience_id=sample_experience.experience_id,
            proposed_name="fill_login_form",
            proposed_description="填写登录表单的用户名和密码",
            proposed_type="functional",
            proposed_domain="web",
            proposed_tags=["web", "login", "form"],
            input_schema_draft={
                "type": "object",
                "properties": {
                    "username": {"type": "string"},
                    "password": {"type": "string"},
                },
                "required": ["username", "password"],
            },
            output_schema_draft={
                "type": "object",
                "properties": {"logged_in": {"type": "boolean"}},
            },
            preconditions_draft=["登录页面已打开"],
            postconditions_draft=["用户已登录"],
            confidence=0.9,
        )

    @pytest.mark.asyncio
    async def test_formalize_creates_draft_skill(self, mock_llm, sample_proposal, sample_experience):
        mock_llm.chat.return_value.content = json.dumps({
            "name": "fill_login_form",
            "version": "1.0.0",
            "display_name": "Fill Login Form",
            "description": "填写登录表单的用户名和密码字段",
            "skill_type": "functional",
            "domain": "web",
            "granularity_level": 2,
            "tags": ["web", "login"],
            "interface": {
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "username": {"type": "string"},
                        "password": {"type": "string"},
                    },
                    "required": ["username", "password"],
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"logged_in": {"type": "boolean"}},
                },
                "preconditions": ["登录页面已打开"],
                "postconditions": ["用户已登录"],
                "side_effects": [],
            },
            "implementation": {
                "language": "python",
                "sub_skill_ids": [],
                "prompt_template": "填写用户名 {username} 和密码",
            },
            "test_cases": [
                {
                    "name": "test_basic_login",
                    "description": "基础登录测试",
                    "input_data": {"username": "user@test.com", "password": "pass123"},
                    "expected_output": {"logged_in": True},
                    "tags": ["basic"],
                }
            ],
        })
        formalizer = SkillFormalizer(mock_llm)
        skill = await formalizer.formalize(sample_proposal, sample_experience)

        assert skill.name == "fill_login_form"
        assert skill.state == SkillState.DRAFT
        assert skill.skill_type == SkillType.FUNCTIONAL
        assert skill.granularity_level == 2
        assert len(skill.test_cases) == 1
        assert skill.provenance is not None

    @pytest.mark.asyncio
    async def test_formalize_fallback_on_llm_failure(self, mock_llm, sample_proposal):
        """LLM 返回无效 JSON 时应降级处理。"""
        mock_llm.chat.return_value.content = "这不是 JSON"
        formalizer = SkillFormalizer(mock_llm)
        skill = await formalizer.formalize(sample_proposal)
        # 降级方案：使用提案数据
        assert skill.name == "fill_login_form"
        assert skill.state == SkillState.DRAFT


# ===========================================================================
# SkillValidator Tests
# ===========================================================================

class TestSkillValidator:

    @pytest.mark.asyncio
    async def test_validate_good_skill(self, mock_llm, sample_skill):
        mock_llm.chat.return_value.content = json.dumps({
            "is_valid": True,
            "overall_score": 0.9,
            "issues": [],
            "suggestions": [],
            "granularity_assessment": {"suggested_level": 2, "reason": "合适"},
        })
        validator = SkillValidator(mock_llm)
        result = await validator.validate(sample_skill)
        assert result.is_valid
        assert result.overall_score > 0.6

    @pytest.mark.asyncio
    async def test_validate_skill_without_implementation(self, mock_llm):
        skill = Skill(name="no_impl_skill", description="缺少实现的 Skill")
        mock_llm.chat.return_value.content = json.dumps({
            "overall_score": 0.3,
            "issues": [{"severity": "error", "field": "implementation", "message": "缺少实现"}],
            "suggestions": [],
        })
        validator = SkillValidator(mock_llm)
        result = await validator.validate(skill)
        assert not result.is_valid
        assert len(result.errors) > 0

    def test_check_python_syntax_valid(self, mock_llm):
        validator = SkillValidator(mock_llm)
        ok, err = validator._check_python_syntax("def hello():\n    return 'world'")
        assert ok
        assert err == ""

    def test_check_python_syntax_invalid(self, mock_llm):
        validator = SkillValidator(mock_llm)
        ok, err = validator._check_python_syntax("def broken(: pass")
        assert not ok
        assert err != ""

    @pytest.mark.asyncio
    async def test_validate_and_advance_valid_skill(self, mock_llm, sample_skill):
        mock_llm.chat.return_value.content = json.dumps({
            "is_valid": True,
            "overall_score": 0.85,
            "issues": [],
            "suggestions": [],
        })
        # 先把 skill 设为 VERIFIED 状态（sample_skill 是 RELEASED）
        skill = Skill(
            name="test_skill",
            description="测试 Skill，足够长的描述",
            state=SkillState.DRAFT,
            interface=SkillInterface(
                input_schema={"type": "object", "properties": {}},
                output_schema={"type": "object", "properties": {}},
                preconditions=["条件1"],
            ),
            implementation=SkillImplementation(prompt_template="执行操作"),
        )
        validator = SkillValidator(mock_llm, min_score=0.6)
        advanced_skill, result = await validator.validate_and_advance(skill)
        assert result.is_valid
        assert advanced_skill.state == SkillState.VERIFIED


# ===========================================================================
# SkillSearchEngine Tests (no DB)
# ===========================================================================

class TestSkillSearchEngine:

    def test_text_relevance_exact_match(self, mock_llm):
        engine = SkillSearchEngine.__new__(SkillSearchEngine)
        skill = Skill(name="fill_form", description="填写表单")
        score = engine._text_relevance(skill, "fill_form")
        assert score == 1.0

    def test_text_relevance_partial_match(self, mock_llm):
        engine = SkillSearchEngine.__new__(SkillSearchEngine)
        skill = Skill(name="fill_form_v2", description="填写表单")
        score = engine._text_relevance(skill, "fill form")
        assert score > 0.3

    def test_text_relevance_no_match(self, mock_llm):
        engine = SkillSearchEngine.__new__(SkillSearchEngine)
        skill = Skill(name="click_button", description="点击按钮")
        score = engine._text_relevance(skill, "read_file")
        assert score == 0.0

    def test_extract_keywords(self, mock_llm):
        engine = SkillSearchEngine.__new__(SkillSearchEngine)
        keywords = engine._extract_keywords("how to fill a web form with user data")
        assert "fill" in keywords
        assert "form" in keywords
        assert "how" not in keywords  # 停用词
        assert "to" not in keywords   # 停用词

    def test_score_released_skill_higher(self, mock_llm):
        engine = SkillSearchEngine.__new__(SkillSearchEngine)
        query = SearchQuery(text="fill_form")
        released = Skill(name="fill_form", state=SkillState.RELEASED,
                        description="填写表单",
                        interface=SkillInterface(input_schema={"type": "object", "properties": {}}))
        draft = Skill(name="fill_form", state=SkillState.DRAFT,
                     description="填写表单",
                     interface=SkillInterface(input_schema={"type": "object", "properties": {}}))
        r_released = engine._score(released, query)
        r_draft = engine._score(draft, query)
        assert r_released.score > r_draft.score
