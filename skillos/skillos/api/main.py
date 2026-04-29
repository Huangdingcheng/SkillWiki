"""SkillOS FastAPI 应用入口。"""

from __future__ import annotations

import argparse
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ..utils.llm_client import LLMClient
from .deps import app_state
from .memory_store import MemoryGraphManager, MemoryWikiManager
from .routes import evolution, execution, graph, ingest, lifecycle, skills, ws


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    llm_cfg = app.state.llm_cfg
    llm = LLMClient(llm_cfg)
    wiki = MemoryWikiManager()
    graph_mgr = MemoryGraphManager()

    app_state.initialize(llm=llm, wiki=wiki, graph=graph_mgr)

    # 预加载 demo 数据
    await _seed_demo_skills(wiki, graph_mgr)

    # 注入 WebSocket 广播到 executor
    from .routes.ws import broadcast
    if app_state.executor:
        async def ws_callback(event_type: str, data: Dict[str, Any]) -> None:
            await broadcast(event_type, data)
        app_state.executor.add_event_callback(ws_callback)

    yield


async def _seed_demo_skills(wiki: MemoryWikiManager, graph: MemoryGraphManager) -> None:
    """预加载 demo Skill + 12 个 Meta-Skill（Strategic）。"""
    from ..models.skill_model import (
        Skill, SkillInterface, SkillImplementation,
        SkillState, SkillType, SkillProvenance,
    )
    from ..models.graph_model import SkillEdge
    from ..models.skill_model import EdgeType

    def iface(inputs: list, outputs: list, pre: list = [], post: list = []) -> SkillInterface:
        return SkillInterface(
            input_schema={
                "type": "object",
                "properties": {p["name"]: {"type": p["type"], "description": p.get("description", "")} for p in inputs},
                "required": [p["name"] for p in inputs if p.get("required")],
            },
            output_schema={
                "type": "object",
                "properties": {p["name"]: {"type": p["type"], "description": p.get("description", "")} for p in outputs},
            },
            preconditions=pre,
            postconditions=post,
        )

    # ── 基础 Demo Skills ──────────────────────────────────────────────────────
    demos = [
        dict(
            name="click_element",
            description="在网页上点击指定元素",
            skill_type=SkillType.ATOMIC,
            tags=["web", "ui", "interaction"],
            interface=iface(
                [{"name": "selector", "type": "string", "description": "CSS 选择器", "required": True}],
                [{"name": "success", "type": "boolean", "description": "是否成功"}],
                pre=["页面已加载"], post=["元素已被点击"],
            ),
            implementation=SkillImplementation(
                language="python",
                code='output["success"] = True  # 模拟点击 selector',
            ),
        ),
        dict(
            name="type_text",
            description="在输入框中输入文本",
            skill_type=SkillType.ATOMIC,
            tags=["web", "ui", "input"],
            interface=iface(
                [{"name": "selector", "type": "string", "required": True}, {"name": "text", "type": "string", "required": True}],
                [{"name": "success", "type": "boolean"}],
            ),
            implementation=SkillImplementation(language="python", code='output["success"] = True'),
        ),
        dict(
            name="fill_form",
            description="填写并提交表单（组合 click + type）",
            skill_type=SkillType.FUNCTIONAL,
            tags=["web", "form", "functional"],
            interface=iface(
                [{"name": "form_data", "type": "object", "description": "表单字段字典", "required": True}],
                [{"name": "submitted", "type": "boolean"}],
            ),
            implementation=SkillImplementation(language="python", sub_skill_ids=["click_element", "type_text"]),
        ),
        dict(
            name="locate_element",
            description="在页面上定位元素并返回其属性",
            skill_type=SkillType.ATOMIC,
            tags=["web", "ui", "query"],
            interface=iface(
                [{"name": "description", "type": "string", "required": True}],
                [{"name": "selector", "type": "string"}],
            ),
            implementation=SkillImplementation(
                language="python",
                prompt_template="在页面上找到描述为 '{description}' 的元素，返回其 CSS 选择器。只输出选择器字符串，不要其他内容。",
            ),
        ),
    ]

    # ── 12 个 Meta-Skill（Strategic L3）────────────────────────────────────────
    meta_skills = [
        dict(
            name="generate_skill_from_task",
            description="从任务描述自动生成 Skill 定义草稿",
            skill_type=SkillType.STRATEGIC,
            meta_category="generation",
            tags=["meta", "generation", "strategic"],
            interface=iface(
                [{"name": "task_description", "type": "string", "description": "任务描述", "required": True},
                 {"name": "context", "type": "object", "description": "上下文信息"}],
                [{"name": "skill_name", "type": "string"}, {"name": "skill_draft", "type": "object"},
                 {"name": "confidence", "type": "number"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "你是 SkillOS Skill Builder。从以下任务描述中提取可复用的 Skill：\n\n"
                    "任务：{task_description}\n\n"
                    "请生成一个 JSON 格式的 Skill 定义，包含 name、description、input_schema、output_schema、prompt_template。"
                ),
            ),
        ),
        dict(
            name="generate_skill_from_trajectory",
            description="从执行轨迹中提取并生成 Skill",
            skill_type=SkillType.STRATEGIC,
            meta_category="generation",
            tags=["meta", "generation", "trajectory", "strategic"],
            interface=iface(
                [{"name": "trajectory", "type": "string", "description": "执行轨迹文本", "required": True}],
                [{"name": "skill_name", "type": "string"}, {"name": "skill_draft", "type": "object"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "你是 SkillOS Skill Builder。分析以下执行轨迹，提取可复用的操作模式并生成 Skill：\n\n"
                    "轨迹：{trajectory}\n\n"
                    "输出 JSON 格式的 Skill 定义。"
                ),
            ),
        ),
        dict(
            name="formalize_skill_schema",
            description="将非正式 Skill 描述规范化为标准 JSON Schema",
            skill_type=SkillType.STRATEGIC,
            meta_category="knowledge_management",
            tags=["meta", "schema", "formalization", "strategic"],
            interface=iface(
                [{"name": "informal_description", "type": "string", "required": True}],
                [{"name": "input_schema", "type": "object"}, {"name": "output_schema", "type": "object"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "将以下非正式 Skill 描述规范化为标准 JSON Schema：\n\n"
                    "{informal_description}\n\n"
                    "输出包含 input_schema 和 output_schema 的 JSON 对象。"
                ),
            ),
        ),
        dict(
            name="generate_skill_tests",
            description="为 Skill 自动生成测试用例",
            skill_type=SkillType.STRATEGIC,
            meta_category="quality_assurance",
            tags=["meta", "testing", "quality", "strategic"],
            interface=iface(
                [{"name": "skill_name", "type": "string", "required": True},
                 {"name": "skill_description", "type": "string", "required": True},
                 {"name": "input_schema", "type": "object"}],
                [{"name": "test_cases", "type": "array"}, {"name": "test_count", "type": "integer"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "为以下 Skill 生成测试用例：\n\n"
                    "Skill 名称：{skill_name}\n"
                    "描述：{skill_description}\n"
                    "输入 Schema：{input_schema}\n\n"
                    "生成 3-5 个测试用例，包含正常情况、边界情况和异常情况。输出 JSON 数组。"
                ),
            ),
        ),
        dict(
            name="audit_skill_safety",
            description="审计 Skill 的安全性，检测潜在危险操作",
            skill_type=SkillType.STRATEGIC,
            meta_category="quality_assurance",
            tags=["meta", "safety", "audit", "strategic"],
            interface=iface(
                [{"name": "skill_name", "type": "string", "required": True},
                 {"name": "implementation_code", "type": "string"}],
                [{"name": "is_safe", "type": "boolean"}, {"name": "risks", "type": "array"},
                 {"name": "audit_score", "type": "number"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "审计以下 Skill 的安全性：\n\n"
                    "Skill：{skill_name}\n"
                    "实现代码：{implementation_code}\n\n"
                    "检查：代码注入、权限越界、资源滥用、数据泄露风险。"
                    "输出 JSON：{{is_safe, risks: [], audit_score: 0.0-1.0}}"
                ),
            ),
        ),
        dict(
            name="verify_skill_postcondition",
            description="验证 Skill 执行结果是否满足后置条件",
            skill_type=SkillType.STRATEGIC,
            meta_category="quality_assurance",
            tags=["meta", "verification", "postcondition", "strategic"],
            interface=iface(
                [{"name": "skill_name", "type": "string", "required": True},
                 {"name": "postconditions", "type": "array", "required": True},
                 {"name": "execution_output", "type": "object", "required": True}],
                [{"name": "satisfied", "type": "boolean"}, {"name": "violations", "type": "array"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "验证 Skill '{skill_name}' 的执行结果是否满足后置条件：\n\n"
                    "后置条件：{postconditions}\n"
                    "执行输出：{execution_output}\n\n"
                    "输出 JSON：{{satisfied: bool, violations: []}}"
                ),
            ),
        ),
        dict(
            name="repair_failed_skill",
            description="分析 Skill 失败原因并生成修复方案",
            skill_type=SkillType.STRATEGIC,
            meta_category="maintenance",
            tags=["meta", "repair", "maintenance", "strategic"],
            interface=iface(
                [{"name": "skill_name", "type": "string", "required": True},
                 {"name": "failure_info", "type": "string", "required": True},
                 {"name": "current_implementation", "type": "string"}],
                [{"name": "repaired_implementation", "type": "string"}, {"name": "repair_notes", "type": "string"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "修复失败的 Skill '{skill_name}'：\n\n"
                    "失败信息：{failure_info}\n"
                    "当前实现：{current_implementation}\n\n"
                    "分析根因并提供修复后的实现。输出 JSON：{{repaired_implementation, repair_notes}}"
                ),
            ),
        ),
        dict(
            name="split_oversized_skill",
            description="将功能过于复杂的 Skill 拆分为多个子 Skill",
            skill_type=SkillType.STRATEGIC,
            meta_category="maintenance",
            tags=["meta", "split", "decomposition", "strategic"],
            interface=iface(
                [{"name": "skill_name", "type": "string", "required": True},
                 {"name": "skill_description", "type": "string", "required": True},
                 {"name": "split_reason", "type": "string"}],
                [{"name": "sub_skills", "type": "array"}, {"name": "split_count", "type": "integer"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "将以下过于复杂的 Skill 拆分为多个子 Skill：\n\n"
                    "Skill：{skill_name}\n"
                    "描述：{skill_description}\n"
                    "拆分原因：{split_reason}\n\n"
                    "输出 JSON 数组，每个元素包含 name、description、prompt_template。"
                ),
            ),
        ),
        dict(
            name="merge_redundant_skills",
            description="将功能重复的多个 Skill 合并为一个统一 Skill",
            skill_type=SkillType.STRATEGIC,
            meta_category="maintenance",
            tags=["meta", "merge", "deduplication", "strategic"],
            interface=iface(
                [{"name": "skill_names", "type": "array", "description": "待合并的 Skill 名称列表", "required": True},
                 {"name": "skill_descriptions", "type": "array"}],
                [{"name": "merged_skill", "type": "object"}, {"name": "merge_notes", "type": "string"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "将以下功能重复的 Skill 合并为一个统一 Skill：\n\n"
                    "待合并：{skill_names}\n"
                    "描述：{skill_descriptions}\n\n"
                    "输出合并后的 Skill JSON 定义，包含 name、description、prompt_template。"
                ),
            ),
        ),
        dict(
            name="deprecate_low_utility_skill",
            description="识别并废弃低使用率/低质量的 Skill",
            skill_type=SkillType.STRATEGIC,
            meta_category="lifecycle",
            tags=["meta", "deprecation", "maintenance", "strategic"],
            interface=iface(
                [{"name": "skill_name", "type": "string", "required": True},
                 {"name": "usage_count", "type": "integer", "required": True},
                 {"name": "success_rate", "type": "number", "required": True},
                 {"name": "last_used_days_ago", "type": "integer"}],
                [{"name": "should_deprecate", "type": "boolean"}, {"name": "reason", "type": "string"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "评估 Skill '{skill_name}' 是否应该废弃：\n\n"
                    "使用次数：{usage_count}\n"
                    "成功率：{success_rate}\n"
                    "最后使用：{last_used_days_ago} 天前\n\n"
                    "输出 JSON：{{should_deprecate: bool, reason: string}}"
                ),
            ),
        ),
        dict(
            name="update_skill_wiki_page",
            description="更新 Skill Wiki 页面的描述、标签和文档",
            skill_type=SkillType.STRATEGIC,
            meta_category="knowledge_management",
            tags=["meta", "wiki", "documentation", "strategic"],
            interface=iface(
                [{"name": "skill_id", "type": "string", "required": True},
                 {"name": "update_reason", "type": "string", "required": True},
                 {"name": "new_description", "type": "string"},
                 {"name": "new_tags", "type": "array"}],
                [{"name": "updated", "type": "boolean"}, {"name": "wiki_url", "type": "string"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "为 Skill '{skill_id}' 生成更新后的 Wiki 页面内容：\n\n"
                    "更新原因：{update_reason}\n"
                    "新描述：{new_description}\n\n"
                    "输出 Markdown 格式的 Wiki 页面。"
                ),
            ),
        ),
        dict(
            name="update_skill_graph_relation",
            description="更新 Skill Graph 中的关系边（依赖/组合/替代）",
            skill_type=SkillType.STRATEGIC,
            meta_category="graph",
            tags=["meta", "graph", "relations", "strategic"],
            interface=iface(
                [{"name": "source_skill", "type": "string", "required": True},
                 {"name": "target_skill", "type": "string", "required": True},
                 {"name": "relation_type", "type": "string", "description": "depends_on/composes/replaces", "required": True},
                 {"name": "weight", "type": "number"}],
                [{"name": "edge_added", "type": "boolean"}, {"name": "graph_updated", "type": "boolean"}],
            ),
            implementation=SkillImplementation(
                prompt_template=(
                    "分析 Skill '{source_skill}' 和 '{target_skill}' 之间的 '{relation_type}' 关系：\n\n"
                    "验证这个关系是否合理，并说明理由。"
                    "输出 JSON：{{valid: bool, reasoning: string}}"
                ),
            ),
        ),
    ]

    test_graph_skills = [
        dict(
            skill_id="test_graph_collect_requirements",
            name="test_graph_collect_requirements",
            description="Test graph node: collect task requirements before planning.",
            skill_type=SkillType.ATOMIC,
            tags=["test", "graph", "requirements"],
            interface=iface(
                [{"name": "brief", "type": "string", "required": True}],
                [{"name": "requirements", "type": "array"}],
            ),
            implementation=SkillImplementation(language="python", code='output["requirements"] = []'),
        ),
        dict(
            skill_id="test_graph_parse_requirements",
            name="test_graph_parse_requirements",
            description="Test graph node: normalize requirements into actionable tasks.",
            skill_type=SkillType.FUNCTIONAL,
            tags=["test", "graph", "analysis"],
            interface=iface(
                [{"name": "requirements", "type": "array", "required": True}],
                [{"name": "tasks", "type": "array"}],
            ),
            implementation=SkillImplementation(language="python", code='output["tasks"] = []'),
        ),
        dict(
            skill_id="test_graph_design_plan",
            name="test_graph_design_plan",
            description="Test graph node: create an implementation plan from parsed tasks.",
            skill_type=SkillType.STRATEGIC,
            meta_category="generation",
            tags=["test", "graph", "planning"],
            interface=iface(
                [{"name": "tasks", "type": "array", "required": True}],
                [{"name": "plan", "type": "object"}],
            ),
            implementation=SkillImplementation(language="python", code='output["plan"] = {}'),
        ),
        dict(
            skill_id="test_graph_build_demo",
            name="test_graph_build_demo",
            description="Test graph node: build a runnable demo from the plan.",
            skill_type=SkillType.FUNCTIONAL,
            tags=["test", "graph", "demo"],
            interface=iface(
                [{"name": "plan", "type": "object", "required": True}],
                [{"name": "demo_ready", "type": "boolean"}],
            ),
            implementation=SkillImplementation(language="python", code='output["demo_ready"] = True'),
        ),
        dict(
            skill_id="test_graph_review_output",
            name="test_graph_review_output",
            description="Test graph node: review demo output and record feedback.",
            skill_type=SkillType.FUNCTIONAL,
            tags=["test", "graph", "review"],
            interface=iface(
                [{"name": "demo_ready", "type": "boolean", "required": True}],
                [{"name": "review_passed", "type": "boolean"}],
            ),
            implementation=SkillImplementation(language="python", code='output["review_passed"] = True'),
        ),
    ]

    test_graph_edges = [
        ("test_graph_parse_requirements", "test_graph_collect_requirements", EdgeType.DEPENDS_ON, 0.95),
        ("test_graph_design_plan", "test_graph_parse_requirements", EdgeType.DEPENDS_ON, 0.85),
        ("test_graph_build_demo", "test_graph_design_plan", EdgeType.COMPOSES_WITH, 0.65),
        ("test_graph_review_output", "test_graph_build_demo", EdgeType.DEPENDS_ON, 0.45),
        ("test_graph_review_output", "test_graph_design_plan", EdgeType.SIMILAR_TO, 0.25),
    ]

    all_skills = demos + meta_skills + test_graph_skills
    for d in all_skills:
        skill = Skill(
            **d,
            provenance=SkillProvenance(source_type="demo", author="system"),
        )
        skill.transition_to(SkillState.VERIFIED)
        skill.transition_to(SkillState.RELEASED)
        for _ in range(20):
            skill.record_execution(success=True, latency_ms=120.0)
        for _ in range(2):
            skill.record_execution(success=False, latency_ms=500.0)
        try:
            await wiki.create(skill)
        except ValueError:
            pass

    for source_id, target_id, edge_type, weight in test_graph_edges:
        await graph.create_edge(SkillEdge(
            source_id=source_id,
            target_id=target_id,
            edge_type=edge_type,
            weight=weight,
            description="Demo edge for testing graph attraction and repulsion controls.",
            metadata={"demo": True, "test_graph": True},
            created_by="demo_seed",
        ))


def create_app(api_key: str, model: str = "claude-sonnet-4-6") -> FastAPI:
    from ..config.llm_config import LLMConfig

    llm_cfg = LLMConfig(api_key=api_key, model=model)

    app = FastAPI(
        title="SkillOS API",
        description="Skill-Centric Operating System for Self-Evolving Agents",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.state.llm_cfg = llm_cfg

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=500, content={"ok": False, "error": str(exc)})

    app.include_router(skills.router, prefix="/api/v1")
    app.include_router(lifecycle.router, prefix="/api/v1")
    app.include_router(graph.router, prefix="/api/v1")
    app.include_router(execution.router, prefix="/api/v1")
    app.include_router(evolution.router, prefix="/api/v1")
    app.include_router(ingest.router, prefix="/api/v1")
    app.include_router(ws.router)

    @app.get("/")
    async def root() -> Dict[str, str]:
        return {"name": "SkillOS", "version": "1.0.0", "status": "running"}

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="SkillOS API Server")
    parser.add_argument("--api-key", required=True, help="Anthropic API Key")
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    app = create_app(api_key=args.api_key, model=args.model)
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
