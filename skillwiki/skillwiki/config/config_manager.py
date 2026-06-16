"""配置管理器 - 生产级别"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .llm_config import (
    AgentLLMConfig,
    DatabaseConfig,
    GlobalConfig,
    LLMConfig,
    LoggingConfig,
    MongoConfig,
    Neo4jConfig,
    PostgresConfig,
    RedisConfig,
)


def _resolve_env_vars(value: Any) -> Any:
    """递归解析配置值中的 ${VAR} 环境变量占位符。"""
    if isinstance(value, str):
        def _replace(m: re.Match) -> str:
            var = m.group(1)
            result = os.environ.get(var, "")
            return result
        return re.sub(r"\$\{([^}]+)\}", _replace, value)
    if isinstance(value, dict):
        return {k: _resolve_env_vars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_env_vars(i) for i in value]
    return value


class ConfigManager:
    """
    配置管理器。

    优先级（高 → 低）：
      1. 命令行参数（cli_args）
      2. 环境变量
      3. 配置文件（config.yaml）
      4. Pydantic 字段默认值
    """

    def __init__(
        self,
        config_file: Optional[str] = None,
        cli_args: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._config_file = config_file or self._find_config_file()
        self._cli_args: Dict[str, Any] = cli_args or {}
        self._raw: Dict[str, Any] = {}          # 合并后的原始字典（不含 api_key）
        self._global_config: GlobalConfig
        self._agent_configs: Dict[str, AgentLLMConfig] = {}
        self._load()

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def get_global_llm_config(self) -> LLMConfig:
        return self._global_config.llm

    def get_agent_llm_config(self, agent_type: str) -> LLMConfig:
        """返回 Agent 的最终 LLM 配置（Agent 覆盖 + 全局）。"""
        if agent_type in self._agent_configs:
            return self._agent_configs[agent_type].merge_with_global(
                self._global_config.llm
            )
        return self._global_config.llm

    def set_agent_llm_config(self, agent_type: str, config: LLMConfig) -> None:
        """运行时覆盖某个 Agent 的完整 LLM 配置。"""
        self._agent_configs[agent_type] = AgentLLMConfig(
            agent_type=agent_type,
            api_url=config.api_url,
            model=config.model,
            api_key=config.api_key,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            timeout=config.timeout,
            retry_count=config.retry_count,
            retry_delay=config.retry_delay,
            stream=config.stream,
        )

    def list_agent_types(self) -> List[str]:
        """返回所有已配置的 Agent 类型列表。"""
        return list(self._agent_configs.keys())

    def get_database_config(self) -> DatabaseConfig:
        return self._global_config.database

    def get_logging_config(self) -> LoggingConfig:
        return self._global_config.logging

    def get_global_config(self) -> GlobalConfig:
        return self._global_config

    def reload(self) -> None:
        """重新从文件和环境变量加载配置（保留 cli_args）。"""
        self._agent_configs.clear()
        self._load()

    def to_dict(self, mask_secrets: bool = True) -> Dict[str, Any]:
        """序列化为字典；mask_secrets=True 时隐藏 api_key。"""
        data = self._global_config.model_dump()
        if mask_secrets:
            data["llm"]["api_key"] = "***"
            for agent_cfg in data.get("agents", {}).values():
                if agent_cfg.get("api_key"):
                    agent_cfg["api_key"] = "***"
        data["agents"] = {
            k: v.model_dump() for k, v in self._agent_configs.items()
        }
        return data

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    @staticmethod
    def _find_config_file() -> str:
        candidates = [
            "config.yaml",
            "config.yml",
            Path.home() / ".skillwiki" / "config.yaml",
        ]
        for c in candidates:
            if Path(c).exists():
                return str(c)
        return "config.yaml"

    def _load(self) -> None:
        file_dict = self._load_file()
        env_dict = self._build_env_dict()
        merged = self._deep_merge(file_dict, env_dict)
        merged = self._apply_cli(merged)
        merged = _resolve_env_vars(merged)
        self._raw = merged
        self._global_config = self._build_global_config(merged)
        self._load_agent_configs(merged)

    def _load_file(self) -> Dict[str, Any]:
        path = Path(self._config_file)
        if not path.exists():
            return {}
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    @staticmethod
    def _build_env_dict() -> Dict[str, Any]:
        """将环境变量映射到配置字典结构。"""
        d: Dict[str, Any] = {}

        # LLM
        llm: Dict[str, Any] = {}
        _set_if(llm, "api_url", os.getenv("LLM_API_URL"))
        _set_if(llm, "model", os.getenv("LLM_MODEL"))
        _set_if(llm, "api_key", os.getenv("LLM_API_KEY"))
        _set_if_cast(llm, "temperature", os.getenv("LLM_TEMPERATURE"), float)
        _set_if_cast(llm, "max_tokens", os.getenv("LLM_MAX_TOKENS"), int)
        _set_if_cast(llm, "timeout", os.getenv("LLM_TIMEOUT"), int)
        _set_if_cast(llm, "retry_count", os.getenv("LLM_RETRY_COUNT"), int)
        if llm:
            d["llm"] = llm

        # Database - Postgres
        pg: Dict[str, Any] = {}
        _set_if(pg, "host", os.getenv("DB_POSTGRES_HOST"))
        _set_if_cast(pg, "port", os.getenv("DB_POSTGRES_PORT"), int)
        _set_if(pg, "database", os.getenv("DB_POSTGRES_DATABASE"))
        _set_if(pg, "user", os.getenv("DB_POSTGRES_USER"))
        _set_if(pg, "password", os.getenv("DB_POSTGRES_PASSWORD"))
        if pg:
            d.setdefault("database", {})["postgres"] = pg

        # Database - Neo4j
        neo: Dict[str, Any] = {}
        _set_if(neo, "uri", os.getenv("DB_NEO4J_URI"))
        _set_if(neo, "user", os.getenv("DB_NEO4J_USER"))
        _set_if(neo, "password", os.getenv("DB_NEO4J_PASSWORD"))
        if neo:
            d.setdefault("database", {})["neo4j"] = neo

        # Database - Redis
        redis: Dict[str, Any] = {}
        _set_if(redis, "host", os.getenv("DB_REDIS_HOST"))
        _set_if_cast(redis, "port", os.getenv("DB_REDIS_PORT"), int)
        _set_if(redis, "password", os.getenv("DB_REDIS_PASSWORD"))
        if redis:
            d.setdefault("database", {})["redis"] = redis

        return d

    def _apply_cli(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """将 cli_args 写入配置字典（最高优先级）。"""
        if not self._cli_args:
            return d
        llm = d.setdefault("llm", {})
        for key in ("api_key", "api_url", "model", "temperature", "max_tokens", "timeout", "retry_count"):
            if key in self._cli_args and self._cli_args[key] is not None:
                llm[key] = self._cli_args[key]
        return d

    @staticmethod
    def _deep_merge(base: Dict, override: Dict) -> Dict:
        """递归合并两个字典，override 优先。"""
        result = dict(base)
        for k, v in override.items():
            if k in result and isinstance(result[k], dict) and isinstance(v, dict):
                result[k] = ConfigManager._deep_merge(result[k], v)
            else:
                result[k] = v
        return result

    @staticmethod
    def _build_global_config(d: Dict[str, Any]) -> GlobalConfig:
        llm_dict = d.get("llm", {})
        if not llm_dict.get("api_key"):
            raise ValueError(
                "LLM API key 未提供。请通过 --api-key 命令行参数传入，"
                "或设置环境变量 LLM_API_KEY。"
            )

        db_dict = d.get("database", {})
        database = DatabaseConfig(
            postgres=PostgresConfig(**db_dict.get("postgres", {})),
            neo4j=Neo4jConfig(**db_dict.get("neo4j", {})),
            mongodb=MongoConfig(**db_dict.get("mongodb", {})),
            redis=RedisConfig(**db_dict.get("redis", {})),
        )

        return GlobalConfig(
            llm=LLMConfig(**llm_dict),
            database=database,
            logging=LoggingConfig(**d.get("logging", {})),
            debug=d.get("debug", False),
            environment=d.get("environment", "development"),
        )

    def _load_agent_configs(self, d: Dict[str, Any]) -> None:
        for agent_type, agent_dict in d.get("agents", {}).items():
            if not isinstance(agent_dict, dict):
                continue
            self._agent_configs[agent_type] = AgentLLMConfig(
                agent_type=agent_type, **agent_dict
            )


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------

def _set_if(d: Dict, key: str, value: Optional[str]) -> None:
    if value is not None:
        d[key] = value


def _set_if_cast(d: Dict, key: str, value: Optional[str], cast: type) -> None:
    if value is not None:
        try:
            d[key] = cast(value)
        except (ValueError, TypeError):
            pass  # 无效值忽略，让 Pydantic 使用默认值


# ------------------------------------------------------------------
# 全局单例
# ------------------------------------------------------------------

_config_manager: Optional[ConfigManager] = None


def get_config_manager(
    config_file: Optional[str] = None,
    cli_args: Optional[Dict[str, Any]] = None,
) -> ConfigManager:
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager(config_file, cli_args)
    return _config_manager


def reset_config_manager() -> None:
    global _config_manager
    _config_manager = None
