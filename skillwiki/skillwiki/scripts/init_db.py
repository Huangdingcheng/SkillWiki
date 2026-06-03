"""数据库初始化脚本 — 创建表结构、约束、索引和种子数据。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# 确保包路径可用
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import click

from skillwiki.config import get_config_manager
from skillwiki.models import (
    MetaSkillCategory,
    Skill,
    SkillEdge,
    SkillImplementation,
    SkillInterface,
    SkillState,
    SkillType,
)
from skillwiki.models.graph_model import EdgeType
from skillwiki.storage import (
    Neo4jConnection,
    PostgresConnection,
    RedisConnection,
    SkillGraphRepository,
    SkillRepository,
)
from skillwiki.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Seed Data
# ---------------------------------------------------------------------------

SEED_SKILLS = [
    Skill(
        name="click_element",
        version="1.0.0",
        description="点击页面上的指定元素",
        skill_type=SkillType.ATOMIC,
        domain="web",
        granularity_level=1,
        state=SkillState.RELEASED,
        tags=["web", "click", "interaction"],
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS 选择器或 XPath"},
                    "timeout_ms": {"type": "integer", "default": 5000},
                },
                "required": ["selector"],
            },
            output_schema={
                "type": "object",
                "properties": {"clicked": {"type": "boolean"}},
            },
            preconditions=["目标元素在页面上可见且可交互"],
            postconditions=["元素已被点击，触发相应事件"],
        ),
        implementation=SkillImplementation(
            language="python",
            code='await page.click(input_data["selector"])',
            tool_calls=["playwright"],
        ),
    ),
    Skill(
        name="type_text",
        version="1.0.0",
        description="在输入框中输入文本",
        skill_type=SkillType.ATOMIC,
        domain="web",
        granularity_level=1,
        state=SkillState.RELEASED,
        tags=["web", "input", "text"],
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "text": {"type": "string"},
                    "clear_first": {"type": "boolean", "default": True},
                },
                "required": ["selector", "text"],
            },
            output_schema={
                "type": "object",
                "properties": {"typed": {"type": "boolean"}},
            },
            preconditions=["目标输入框存在且可编辑"],
            postconditions=["文本已输入到指定输入框"],
        ),
        implementation=SkillImplementation(
            language="python",
            code='await page.fill(input_data["selector"], input_data["text"])',
            tool_calls=["playwright"],
        ),
    ),
    Skill(
        name="locate_element",
        version="1.0.0",
        description="在页面上定位指定元素，返回元素信息",
        skill_type=SkillType.ATOMIC,
        domain="web",
        granularity_level=1,
        state=SkillState.RELEASED,
        tags=["web", "locate", "dom"],
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {
                    "description": {"type": "string", "description": "元素的自然语言描述"},
                    "selector_hint": {"type": "string"},
                },
                "required": ["description"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "selector": {"type": "string"},
                    "found": {"type": "boolean"},
                    "element_type": {"type": "string"},
                },
            },
            preconditions=["页面已加载完成"],
            postconditions=["返回元素的 CSS 选择器"],
        ),
        implementation=SkillImplementation(
            language="python",
            prompt_template="在页面上找到描述为 '{description}' 的元素，返回其 CSS 选择器。",
        ),
    ),
    Skill(
        name="fill_form",
        version="1.0.0",
        description="填写页面上的结构化表单",
        skill_type=SkillType.COMPOSITE,
        domain="web",
        granularity_level=2,
        state=SkillState.RELEASED,
        tags=["web", "form", "input", "composite"],
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {
                    "fields": {
                        "type": "object",
                        "description": "字段名到值的映射",
                        "additionalProperties": {"type": "string"},
                    },
                    "submit": {"type": "boolean", "default": False},
                },
                "required": ["fields"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "filled_count": {"type": "integer"},
                    "submitted": {"type": "boolean"},
                },
            },
            preconditions=["页面上存在可编辑的表单字段"],
            postconditions=["所有指定字段已填写完毕"],
            side_effects=["如果 submit=true，表单将被提交"],
        ),
        implementation=SkillImplementation(
            language="python",
            sub_skill_ids=[],  # 将在种子数据插入后填充
            prompt_template="对表单中的每个字段 {fields}，先定位字段，再输入对应值。",
        ),
    ),
    Skill(
        name="skill_lifecycle_manager",
        version="1.0.0",
        description="管理 Skill 的完整生命周期，包括创建、验证、发布和废弃",
        skill_type=SkillType.META,
        meta_category=MetaSkillCategory.LIFECYCLE,
        domain="skillwiki",
        granularity_level=4,
        state=SkillState.RELEASED,
        tags=["meta", "lifecycle", "management"],
        interface=SkillInterface(
            input_schema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "validate", "release", "deprecate", "archive"],
                    },
                    "skill_id": {"type": "string"},
                    "params": {"type": "object"},
                },
                "required": ["action"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "new_state": {"type": "string"},
                    "message": {"type": "string"},
                },
            },
            preconditions=["目标 Skill 存在于 SkillWiki 中"],
            postconditions=["Skill 状态已按照生命周期规则转换"],
        ),
        implementation=SkillImplementation(
            language="python",
            prompt_template="执行 Skill 生命周期操作 {action}，遵循状态机规则。",
        ),
    ),
]

SEED_EDGES = [
    # fill_form depends_on locate_element, type_text, click_element
    # (source_id 和 target_id 将在插入后填充)
]


# ---------------------------------------------------------------------------
# Init Functions
# ---------------------------------------------------------------------------

async def init_postgres(dsn: str) -> None:
    """初始化 PostgreSQL 表结构。"""
    logger.info("初始化 PostgreSQL...")
    conn = PostgresConnection(dsn)
    await conn.connect()
    logger.info("PostgreSQL 表结构创建完成")
    await conn.disconnect()


async def init_neo4j(uri: str, user: str, password: str) -> None:
    """初始化 Neo4j 约束和索引。"""
    logger.info("初始化 Neo4j...")
    conn = Neo4jConnection(uri, user, password)
    await conn.connect()
    logger.info("Neo4j 约束和索引创建完成")
    await conn.disconnect()


async def init_redis(host: str, port: int, password: Optional[str] = None) -> None:
    """验证 Redis 连接。"""
    from typing import Optional
    logger.info("验证 Redis 连接...")
    conn = RedisConnection(host=host, port=port, password=password)
    await conn.connect()
    ok = await conn.health_check()
    if ok:
        logger.info("Redis 连接正常")
    await conn.disconnect()


async def seed_data(postgres_dsn: str, neo4j_uri: str, neo4j_user: str, neo4j_password: str) -> None:
    """插入种子数据。"""
    logger.info("插入种子数据...")

    pg_conn = PostgresConnection(postgres_dsn)
    await pg_conn.connect()
    skill_repo = SkillRepository(pg_conn)

    neo4j_conn = Neo4jConnection(neo4j_uri, neo4j_user, neo4j_password)
    await neo4j_conn.connect()
    graph_repo = SkillGraphRepository(neo4j_conn)

    inserted_ids: dict = {}
    for skill in SEED_SKILLS:
        existing = await skill_repo.get(skill.skill_id)
        if not existing:
            await skill_repo.create(skill)
            await graph_repo.upsert_node(skill)
            inserted_ids[skill.name] = skill.skill_id
            logger.info(f"  已插入 Skill: {skill.name}")
        else:
            inserted_ids[skill.name] = existing.skill_id
            logger.info(f"  已存在 Skill: {skill.name}，跳过")

    # 创建 fill_form 的依赖边
    fill_form_id = inserted_ids.get("fill_form")
    if fill_form_id:
        for dep_name in ["locate_element", "type_text", "click_element"]:
            dep_id = inserted_ids.get(dep_name)
            if dep_id:
                edge = SkillEdge(
                    source_id=fill_form_id,
                    target_id=dep_id,
                    edge_type=EdgeType.DEPENDS_ON,
                    weight=1.0,
                    description=f"fill_form 依赖 {dep_name}",
                )
                try:
                    await graph_repo.create_edge(edge)
                    logger.info(f"  已创建边: fill_form → {dep_name}")
                except Exception as e:
                    logger.warning(f"  边创建失败（可能已存在）: {e}")

    await pg_conn.disconnect()
    await neo4j_conn.disconnect()
    logger.info("种子数据插入完成")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@click.command()
@click.option("--postgres-dsn", default="postgresql+asyncpg://postgres:password@localhost:5432/skillos")
@click.option("--neo4j-uri", default="bolt://localhost:7687")
@click.option("--neo4j-user", default="neo4j")
@click.option("--neo4j-password", default="password")
@click.option("--redis-host", default="localhost")
@click.option("--redis-port", default=6379, type=int)
@click.option("--seed/--no-seed", default=True, help="是否插入种子数据")
def main(
    postgres_dsn: str,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    redis_host: str,
    redis_port: int,
    seed: bool,
) -> None:
    """初始化 SkillWiki 数据库（PostgreSQL + Neo4j + Redis）。"""

    async def _run() -> None:
        await init_postgres(postgres_dsn)
        await init_neo4j(neo4j_uri, neo4j_user, neo4j_password)
        try:
            await init_redis(redis_host, redis_port)
        except Exception as e:
            logger.warning(f"Redis 初始化跳过: {e}")
        if seed:
            await seed_data(postgres_dsn, neo4j_uri, neo4j_user, neo4j_password)
        logger.info("数据库初始化完成！")

    asyncio.run(_run())


if __name__ == "__main__":
    main()
