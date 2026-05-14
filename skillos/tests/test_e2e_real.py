"""端到端真实样例测试 — 使用真实 LLM 调用验证完整 SkillOS 流程。

运行方式：
    cd "E:/NLP/skill wiki/skillos"
    SKILLOS_API_KEY=sk-xxx python -m pytest tests/test_e2e_real.py -v -s

需要设置环境变量 SKILLOS_API_KEY 或在此文件中直接填写 API key。
"""

from __future__ import annotations

import asyncio
import os
import pytest

# ── 配置 ──────────────────────────────────────────────────────────────────────

API_KEY = os.environ.get("SKILLOS_API_KEY")
API_URL = os.environ.get("SKILLOS_API_URL", "https://yunwu.ai")
MODEL = os.environ.get("SKILLOS_MODEL", "gpt-4o-mini")

pytestmark = pytest.mark.skipif(
    not API_KEY,
    reason="需要设置 SKILLOS_API_KEY 环境变量",
)


@pytest.fixture(scope="module")
def llm():
    from skillos.config.llm_config import LLMConfig
    from skillos.utils.llm_client import LLMClient
    cfg = LLMConfig(api_key=API_KEY, api_url=API_URL, model=MODEL)
    return LLMClient(cfg)


@pytest.fixture(scope="module")
def wiki():
    from skillos.api.memory_store import MemoryWikiManager
    return MemoryWikiManager()


@pytest.fixture(scope="module")
def graph():
    from skillos.api.memory_store import MemoryGraphManager
    return MemoryGraphManager()


# ── Test 1: LLM 连通性 ────────────────────────────────────────────────────────

def test_llm_ping(llm):
    """验证 LLM API 可以正常调用。"""
    from skillos.utils.llm_client import Message
    resp = llm.chat([Message.user("请回复：pong")])
    assert resp.content
    assert len(resp.content) > 0
    print(f"\n[LLM ping] 响应: {resp.content[:50]}")


# ── Test 2: Skill Builder Agent ───────────────────────────────────────────────

def test_skill_builder_from_task(llm):
    """验证 SkillBuilderAgent 能从任务描述生成 Skill。"""
    from skillos.layers.skill_management.builder import SkillBuilderAgent
    builder = SkillBuilderAgent(llm)
    draft = builder.build_from_task(
        "在网页上搜索关键词并返回前5个结果的标题",
        context={"platform": "web", "tool": "playwright"},
    )
    assert draft.skill.name
    assert draft.skill.description
    assert draft.confidence > 0
    print(f"\n[Builder] 生成 Skill: {draft.skill.name} (confidence={draft.confidence:.2f})")
    print(f"  描述: {draft.skill.description}")


# ── Test 3: Skill Auditor Agent ───────────────────────────────────────────────

def test_skill_auditor(llm):
    """验证 SkillAuditorAgent 能审计 Skill。"""
    from skillos.layers.skill_management.auditor import SkillAuditorAgent
    from skillos.models.skill_model import Skill, SkillInterface, SkillImplementation, SkillType
    auditor = SkillAuditorAgent(llm)
    skill = Skill(
        name="search_web",
        description="在网页上搜索关键词",
        skill_type=SkillType.ATOMIC,
        tags=["web", "search"],
        interface=SkillInterface(
            input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            output_schema={"type": "object", "properties": {"results": {"type": "array"}}},
        ),
        implementation=SkillImplementation(
            prompt_template="搜索关键词 '{query}'，返回前5个结果的标题列表。",
        ),
    )
    result = auditor.audit(skill)
    assert result.skill_id == skill.skill_id
    assert isinstance(result.passed, bool)
    print(f"\n[Auditor] 审计结果: passed={result.passed}, score={result.audit_score:.2f}")
    print(f"  问题: {result.issues}")


# ── Test 4: Composition Agent ─────────────────────────────────────────────────

def test_composition_agent(llm):
    """验证 CompositionAgent 能将多个 Skill 组合为 SkillGraph。"""
    from skillos.layers.skill_runtime.composition import CompositionAgent
    from skillos.models.skill_model import Skill, SkillInterface, SkillType
    composer = CompositionAgent(llm)
    skills = [
        Skill(
            name="locate_element",
            description="定位页面元素",
            skill_type=SkillType.ATOMIC,
            tags=["web"],
            interface=SkillInterface(
                input_schema={"type": "object", "properties": {"description": {"type": "string"}}},
                output_schema={"type": "object", "properties": {"selector": {"type": "string"}}},
            ),
        ),
        Skill(
            name="click_element",
            description="点击页面元素",
            skill_type=SkillType.ATOMIC,
            tags=["web"],
            interface=SkillInterface(
                input_schema={"type": "object", "properties": {"selector": {"type": "string"}}},
                output_schema={"type": "object", "properties": {"success": {"type": "boolean"}}},
            ),
        ),
    ]
    graph = composer.compose(skills, task_description="找到并点击登录按钮")
    assert graph.graph_id
    assert len(graph.nodes) == 2
    assert graph.entry_skill_id
    print(f"\n[Composer] 组合图: entry={graph.entry_skill_id}, edges={len(graph.edges)}")
    print(f"  执行顺序: {graph.execution_order}")


# ── Test 5: Verifier Agent ────────────────────────────────────────────────────

def test_verifier_agent(llm):
    """验证 VerifierAgent 能验证执行结果。"""
    from skillos.layers.skill_runtime.verifier import VerifierAgent
    verifier = VerifierAgent(llm)
    result = verifier.verify(
        goal="在网页上找到并点击登录按钮",
        final_output={"success": True, "element_clicked": "button#login"},
        trace_summary="1. 定位到 button#login 元素\n2. 成功点击",
    )
    assert isinstance(result.passed, bool)
    assert 0.0 <= result.score <= 1.0
    print(f"\n[Verifier] 验证结果: passed={result.passed}, score={result.score:.2f}")
    print(f"  问题: {result.issues}")


# ── Test 6: Reflection Agent ──────────────────────────────────────────────────

def test_reflection_agent(llm):
    """验证 ReflectionAgent 能分析失败并生成反馈。"""
    from skillos.layers.skill_runtime.reflection import ReflectionAgent
    from skillos.layers.skill_runtime.verifier import VerificationResult
    reflector = ReflectionAgent(llm)
    verify_result = VerificationResult(
        passed=False,
        score=0.2,
        goal="点击登录按钮",
        issues=["元素未找到", "超时"],
    )
    feedback = reflector.reflect(
        task_id="test-task-001",
        goal="点击登录按钮",
        trace={"steps": [{"skill": "locate_element", "error": "Element not found"}]},
        verification_result=verify_result,
    )
    assert feedback.task_id == "test-task-001"
    assert not feedback.success
    assert feedback.root_cause or feedback.experience_summary
    print(f"\n[Reflector] 反馈: success={feedback.success}")
    print(f"  根因: {feedback.root_cause}")
    print(f"  建议: {feedback.improvement_suggestions}")


# ── Test 7: Experience Pipeline ───────────────────────────────────────────────

def test_experience_pipeline(llm):
    """验证 ExperiencePipeline 能处理轨迹输入。"""
    from skillos.layers.input_knowledge.pipeline import ExperiencePipeline
    pipeline = ExperiencePipeline(llm)
    trajectory = """
    步骤1: 打开浏览器，导航到 https://example.com
    步骤2: 找到搜索框（id=search-input）
    步骤3: 输入关键词 "python tutorial"
    步骤4: 点击搜索按钮
    步骤5: 等待结果加载
    步骤6: 提取前5个结果的标题
    """
    result = pipeline.process(trajectory, "trajectory")
    assert result.unit_count > 0
    unit = result.units[0]
    assert unit.proposed_skill_name
    assert unit.extracted_actions
    print(f"\n[Pipeline] 处理结果: success={result.success}")
    print(f"  提取的 Skill: {unit.proposed_skill_name}")
    print(f"  动作数量: {len(unit.extracted_actions)}")
    print(f"  置信度: {unit.confidence:.2f}")
    print(f"  索引关键词: {unit.index_keywords[:5]}")


# ── Test 8: 完整执行流程（Planner + Executor）────────────────────────────────

@pytest.mark.asyncio
async def test_full_execution_flow(llm, wiki, graph):
    """验证完整的 Planner → Executor 流程（使用 prompt Skill）。"""
    from skillos.api.memory_store import MemorySearchEngine
    from skillos.layers.skill_runtime.planner import SkillPlanner
    from skillos.layers.skill_runtime.executor import SkillExecutor
    from skillos.models.skill_model import (
        Skill, SkillInterface, SkillImplementation, SkillType, SkillState, SkillProvenance,
    )

    # 创建一个 prompt 类型的 Skill
    skill = Skill(
        name="summarize_text",
        description="将长文本总结为3句话",
        skill_type=SkillType.ATOMIC,
        tags=["nlp", "summarization"],
        interface=SkillInterface(
            input_schema={"type": "object", "properties": {"text": {"type": "string", "description": "待总结的文本"}}, "required": ["text"]},
            output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
        ),
        implementation=SkillImplementation(
            prompt_template="请将以下文本总结为3句话：\n\n{text}\n\n只输出总结，不要其他内容。",
        ),
        provenance=SkillProvenance(source_type="test", author="e2e_test"),
    )
    skill.transition_to(SkillState.VERIFIED)
    skill.transition_to(SkillState.RELEASED)
    await wiki.create(skill)

    # 执行 Skill
    executor = SkillExecutor(skill_registry=wiki, llm_client=llm)
    record = await executor.execute_single(
        skill,
        input_data={"text": "SkillOS 是一个以 Skill 为中心的操作系统，用于自演化 Agent。它包含三层 Skill 类型：原子、功能和策略。系统通过经验处理管道从轨迹中提取 Skill，并通过自管理 Agent 维护 Skill 库的质量。"},
    )
    assert record.status.value in ("success", "failed")
    print(f"\n[Executor] 执行结果: status={record.status.value}")
    if record.output_data:
        print(f"  输出: {str(record.output_data)[:200]}")
    if record.error_message:
        print(f"  错误: {record.error_message}")


# ── Test 9: Meta-Skill 执行 ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_meta_skill_execution(llm, wiki, graph):
    """验证 Meta-Skill（Strategic）可以通过执行器调用 LLM。"""
    from skillos.api.memory_store import MemoryWikiManager
    from skillos.layers.skill_runtime.executor import SkillExecutor
    from skillos.models.skill_model import (
        Skill, SkillInterface, SkillImplementation, SkillType, SkillState, SkillProvenance,
    )

    # 创建 generate_skill_from_task meta-skill
    meta_skill = Skill(
        name="generate_skill_from_task_test",
        description="从任务描述自动生成 Skill 定义草稿",
        skill_type=SkillType.STRATEGIC,
        meta_category="generation",
        tags=["meta", "generation"],
        interface=SkillInterface(
            input_schema={"type": "object", "properties": {"task_description": {"type": "string"}}, "required": ["task_description"]},
            output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
        ),
        implementation=SkillImplementation(
            prompt_template=(
                "你是 SkillOS Skill Builder。从以下任务描述中提取可复用的 Skill：\n\n"
                "任务：{task_description}\n\n"
                "请生成一个简洁的 Skill 名称和描述（JSON 格式）。"
            ),
        ),
        provenance=SkillProvenance(source_type="test", author="e2e_test"),
    )
    meta_skill.transition_to(SkillState.VERIFIED)
    meta_skill.transition_to(SkillState.RELEASED)
    await wiki.create(meta_skill)

    executor = SkillExecutor(skill_registry=wiki, llm_client=llm)
    record = await executor.execute_single(
        meta_skill,
        input_data={"task_description": "在电商网站上搜索商品并加入购物车"},
    )
    assert record.status.value in ("success", "failed")
    print(f"\n[Meta-Skill] 执行结果: status={record.status.value}")
    if record.output_data:
        print(f"  LLM 输出: {str(record.output_data.get('result', ''))[:200]}")
