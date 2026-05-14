"""验证工具 - 生产级别"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from pydantic import ValidationError

from ..config.llm_config import (
    AgentLLMConfig,
    DatabaseConfig,
    GlobalConfig,
    LLMConfig,
    LoggingConfig,
)

# 已知合法 Agent 类型
KNOWN_AGENT_TYPES: frozenset[str] = frozenset({
    "trajectory_parser",
    "doc_parser",
    "script_analyzer",
    "candidate_miner",
    "formalizer",
    "draft_generator",
    "validator",
    "reviewer",
    "executor",
    "planner",
    "monitor",
    "evolution_engine",
    "meta_agent",
    "skill_generator",
    "skill_merger",
    "skill_splitter",
    "skill_auditor",
    "skill_tester",
    "skill_documenter",
})


# ---------------------------------------------------------------------------
# LLM 配置验证
# ---------------------------------------------------------------------------

def validate_llm_config(config: LLMConfig) -> List[str]:
    """
    验证 LLMConfig，返回错误列表（空列表表示通过）。
    """
    errors: List[str] = []

    if not config.api_key or not config.api_key.strip():
        errors.append("api_key 不能为空")

    if not re.match(r"^https?://", config.api_url):
        errors.append(f"api_url 格式无效: {config.api_url!r}")

    if not config.model or not config.model.strip():
        errors.append("model 不能为空")

    if not (0.0 <= config.temperature <= 2.0):
        errors.append(f"temperature 必须在 [0, 2] 范围内，当前值: {config.temperature}")

    if config.max_tokens < 1:
        errors.append(f"max_tokens 必须 >= 1，当前值: {config.max_tokens}")

    if config.timeout < 1:
        errors.append(f"timeout 必须 >= 1，当前值: {config.timeout}")

    if config.retry_count < 0:
        errors.append(f"retry_count 必须 >= 0，当前值: {config.retry_count}")

    return errors


# ---------------------------------------------------------------------------
# 全局配置验证
# ---------------------------------------------------------------------------

def validate_global_config(config: GlobalConfig) -> List[str]:
    """验证 GlobalConfig，返回所有错误。"""
    errors: List[str] = []
    errors.extend(validate_llm_config(config.llm))

    db = config.database
    if not db.postgres.host:
        errors.append("database.postgres.host 不能为空")
    if not (1 <= db.postgres.port <= 65535):
        errors.append(f"database.postgres.port 无效: {db.postgres.port}")
    if not db.neo4j.uri:
        errors.append("database.neo4j.uri 不能为空")
    if not db.redis.host:
        errors.append("database.redis.host 不能为空")

    return errors


# ---------------------------------------------------------------------------
# Skill schema 验证
# ---------------------------------------------------------------------------

def validate_skill_schema(schema: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """
    验证 Skill 的 input/output schema（JSON Schema 子集）。

    Returns:
        (is_valid, errors)
    """
    errors: List[str] = []

    if not isinstance(schema, dict):
        return False, ["schema 必须是字典类型"]

    if "type" not in schema:
        errors.append("schema 缺少 'type' 字段")
    elif schema["type"] not in ("object", "array", "string", "number", "boolean", "null"):
        errors.append(f"schema.type 无效: {schema['type']!r}")

    if schema.get("type") == "object":
        if "properties" not in schema:
            errors.append("object 类型的 schema 必须包含 'properties' 字段")
        elif not isinstance(schema["properties"], dict):
            errors.append("schema.properties 必须是字典类型")

    return len(errors) == 0, errors


# ---------------------------------------------------------------------------
# Agent 类型验证
# ---------------------------------------------------------------------------

def validate_agent_type(agent_type: str) -> bool:
    """验证 Agent 类型是否合法（已知类型或自定义类型均可）。"""
    if not agent_type or not agent_type.strip():
        return False
    # 已知类型直接通过
    if agent_type in KNOWN_AGENT_TYPES:
        return True
    # 自定义类型：只允许 snake_case 格式
    return bool(re.match(r"^[a-z][a-z0-9_]*$", agent_type))


# ---------------------------------------------------------------------------
# 配置字典验证（用于 YAML 加载后的原始字典）
# ---------------------------------------------------------------------------

def validate_config_dict(config_dict: Dict[str, Any]) -> List[str]:
    """
    验证原始配置字典（YAML 加载后），返回错误列表。
    """
    errors: List[str] = []

    # LLM 配置
    llm = config_dict.get("llm", {})
    if not isinstance(llm, dict):
        errors.append("llm 配置必须是字典类型")
    else:
        if not llm.get("api_key"):
            errors.append("llm.api_key 未提供（请通过 --api-key 命令行参数传入）")
        if "api_url" in llm and not re.match(r"^https?://", str(llm["api_url"])):
            errors.append(f"llm.api_url 格式无效: {llm['api_url']!r}")
        if "temperature" in llm:
            try:
                t = float(llm["temperature"])
                if not (0.0 <= t <= 2.0):
                    errors.append(f"llm.temperature 必须在 [0, 2] 范围内，当前值: {t}")
            except (ValueError, TypeError):
                errors.append(f"llm.temperature 必须是数字，当前值: {llm['temperature']!r}")
        if "max_tokens" in llm:
            try:
                mt = int(llm["max_tokens"])
                if mt < 1:
                    errors.append(f"llm.max_tokens 必须 >= 1，当前值: {mt}")
            except (ValueError, TypeError):
                errors.append(f"llm.max_tokens 必须是整数，当前值: {llm['max_tokens']!r}")

    # Agent 配置
    agents = config_dict.get("agents", {})
    if not isinstance(agents, dict):
        errors.append("agents 配置必须是字典类型")
    else:
        for agent_type, agent_cfg in agents.items():
            if not validate_agent_type(agent_type):
                errors.append(
                    f"agents.{agent_type}: agent_type 格式无效（应为 snake_case）"
                )
            if not isinstance(agent_cfg, dict):
                errors.append(f"agents.{agent_type}: 配置必须是字典类型")
                continue
            if "temperature" in agent_cfg:
                try:
                    t = float(agent_cfg["temperature"])
                    if not (0.0 <= t <= 2.0):
                        errors.append(
                            f"agents.{agent_type}.temperature 必须在 [0, 2] 范围内，当前值: {t}"
                        )
                except (ValueError, TypeError):
                    errors.append(
                        f"agents.{agent_type}.temperature 必须是数字"
                    )

    return errors


# ---------------------------------------------------------------------------
# LLM 连通性测试
# ---------------------------------------------------------------------------

def test_llm_connectivity(config: LLMConfig) -> Tuple[bool, str]:
    """
    测试 LLM API 连通性。

    Returns:
        (success, message)
    """
    from .llm_client import LLMClient, LLMAuthError, LLMError  # noqa: PLC0415

    client = LLMClient(config)
    try:
        ok = client.ping()
        if ok:
            return True, f"连接成功（{config.api_url}，模型: {config.model}）"
        return False, "连接失败（未知原因）"
    except LLMAuthError as e:
        return False, f"认证失败: {e}"
    except LLMError as e:
        return False, f"连接失败: {e}"
    except Exception as e:
        return False, f"未知错误: {e}"
