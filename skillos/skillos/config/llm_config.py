"""LLM 配置系统 - 生产级别"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class LLMProvider(str, Enum):
    YUNWU = "yunwu"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    CUSTOM = "custom"


# 已知合法模型名称前缀（宽松白名单，允许自定义模型）
_KNOWN_MODEL_PREFIXES = ("gpt-", "claude-", "gemini-", "llama-", "mistral-", "qwen-")

_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_VALID_LOG_FORMATS = {"json", "text"}
_VALID_ENVIRONMENTS = {"development", "staging", "production", "test"}


class LLMConfig(BaseModel):
    """单个 LLM 端点配置"""

    api_url: str = Field(
        default="https://yunwu.ai",
        description="LLM API 基础地址",
    )
    model: str = Field(
        default="gpt-5.4",
        description="模型名称",
    )
    api_key: str = Field(
        description="API 密钥（通过 --api-key 命令行参数传入）",
    )
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="采样温度（0-2）",
    )
    max_tokens: int = Field(
        default=2000,
        ge=1,
        le=128000,
        description="单次请求最大 token 数",
    )
    timeout: int = Field(
        default=30,
        ge=1,
        le=600,
        description="请求超时时间（秒）",
    )
    retry_count: int = Field(
        default=3,
        ge=0,
        le=10,
        description="失败重试次数",
    )
    retry_delay: float = Field(
        default=1.0,
        ge=0.0,
        le=60.0,
        description="重试间隔基础时间（秒，指数退避）",
    )
    stream: bool = Field(
        default=False,
        description="是否启用流式输出",
    )

    @field_validator("api_url")
    @classmethod
    def validate_api_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not re.match(r"^https?://", v):
            raise ValueError(f"api_url 必须以 http:// 或 https:// 开头，当前值: {v!r}")
        return v

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("api_key 不能为空字符串")
        return v

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("model 不能为空字符串")
        return v

    model_config = {"populate_by_name": True}


class AgentLLMConfig(BaseModel):
    """Agent 级别的 LLM 配置覆盖（所有字段可选，None 表示继承全局配置）"""

    agent_type: str = Field(description="Agent 类型标识符")
    api_url: Optional[str] = Field(default=None, description="覆盖全局 API 地址")
    model: Optional[str] = Field(default=None, description="覆盖全局模型名称")
    api_key: Optional[str] = Field(default=None, description="覆盖全局 API 密钥")
    temperature: Optional[float] = Field(
        default=None, ge=0.0, le=2.0, description="覆盖全局温度"
    )
    max_tokens: Optional[int] = Field(
        default=None, ge=1, le=128000, description="覆盖全局 max_tokens"
    )
    timeout: Optional[int] = Field(
        default=None, ge=1, le=600, description="覆盖全局超时"
    )
    retry_count: Optional[int] = Field(
        default=None, ge=0, le=10, description="覆盖全局重试次数"
    )
    retry_delay: Optional[float] = Field(
        default=None, ge=0.0, le=60.0, description="覆盖全局重试间隔"
    )
    stream: Optional[bool] = Field(default=None, description="覆盖全局流式设置")

    @field_validator("api_url")
    @classmethod
    def validate_api_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().rstrip("/")
        if not re.match(r"^https?://", v):
            raise ValueError(f"api_url 必须以 http:// 或 https:// 开头，当前值: {v!r}")
        return v

    def merge_with_global(self, global_cfg: LLMConfig) -> LLMConfig:
        """将本 Agent 配置与全局配置合并，Agent 字段优先。"""
        return LLMConfig(
            api_url=self.api_url or global_cfg.api_url,
            model=self.model or global_cfg.model,
            api_key=self.api_key or global_cfg.api_key,
            temperature=self.temperature if self.temperature is not None else global_cfg.temperature,
            max_tokens=self.max_tokens if self.max_tokens is not None else global_cfg.max_tokens,
            timeout=self.timeout if self.timeout is not None else global_cfg.timeout,
            retry_count=self.retry_count if self.retry_count is not None else global_cfg.retry_count,
            retry_delay=self.retry_delay if self.retry_delay is not None else global_cfg.retry_delay,
            stream=self.stream if self.stream is not None else global_cfg.stream,
        )

    model_config = {"populate_by_name": True}


class PostgresConfig(BaseModel):
    host: str = Field(default="localhost")
    port: int = Field(default=5432, ge=1, le=65535)
    database: str = Field(default="skillos")
    user: str = Field(default="postgres")
    password: str = Field(default="")
    pool_size: int = Field(default=10, ge=1, le=100)
    max_overflow: int = Field(default=20, ge=0, le=200)
    pool_timeout: int = Field(default=30, ge=1)
    echo: bool = Field(default=False, description="是否打印 SQL 语句（调试用）")

    @property
    def dsn(self) -> str:
        return (
            f"postgresql+psycopg2://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )


class Neo4jConfig(BaseModel):
    uri: str = Field(default="bolt://localhost:7687")
    user: str = Field(default="neo4j")
    password: str = Field(default="")
    max_connection_pool_size: int = Field(default=50, ge=1)
    connection_timeout: float = Field(default=30.0, ge=1.0)
    encrypted: bool = Field(default=False)


class MongoConfig(BaseModel):
    uri: str = Field(default="mongodb://localhost:27017")
    database: str = Field(default="skillos")
    max_pool_size: int = Field(default=100, ge=1)
    server_selection_timeout_ms: int = Field(default=5000, ge=100)


class RedisConfig(BaseModel):
    host: str = Field(default="localhost")
    port: int = Field(default=6379, ge=1, le=65535)
    db: int = Field(default=0, ge=0, le=15)
    password: Optional[str] = Field(default=None)
    max_connections: int = Field(default=50, ge=1)
    socket_timeout: float = Field(default=5.0, ge=0.1)
    decode_responses: bool = Field(default=True)

    @property
    def url(self) -> str:
        auth = f":{self.password}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class DatabaseConfig(BaseModel):
    postgres: PostgresConfig = Field(default_factory=PostgresConfig)
    neo4j: Neo4jConfig = Field(default_factory=Neo4jConfig)
    mongodb: MongoConfig = Field(default_factory=MongoConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)


class LoggingConfig(BaseModel):
    level: str = Field(default="INFO")
    format: str = Field(default="json")
    file: str = Field(default="logs/skillos.log")
    max_bytes: int = Field(default=100 * 1024 * 1024, description="单个日志文件最大字节数")
    backup_count: int = Field(default=10, ge=0)
    console: bool = Field(default=True, description="是否同时输出到控制台")

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        v = v.upper()
        if v not in _VALID_LOG_LEVELS:
            raise ValueError(f"日志级别必须是 {_VALID_LOG_LEVELS} 之一，当前值: {v!r}")
        return v

    @field_validator("format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        v = v.lower()
        if v not in _VALID_LOG_FORMATS:
            raise ValueError(f"日志格式必须是 {_VALID_LOG_FORMATS} 之一，当前值: {v!r}")
        return v


class GlobalConfig(BaseModel):
    llm: LLMConfig
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    debug: bool = Field(default=False)
    environment: str = Field(default="development")

    @field_validator("environment")
    @classmethod
    def validate_environment(cls, v: str) -> str:
        v = v.lower()
        if v not in _VALID_ENVIRONMENTS:
            raise ValueError(
                f"environment 必须是 {_VALID_ENVIRONMENTS} 之一，当前值: {v!r}"
            )
        return v

    model_config = {"populate_by_name": True}
