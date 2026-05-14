"""配置系统完整测试套件"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Generator

import pytest
import yaml

from skillos.config import (
    AgentLLMConfig,
    ConfigManager,
    GlobalConfig,
    LLMConfig,
    get_config_manager,
    reset_config_manager,
)
from skillos.utils import (
    KNOWN_AGENT_TYPES,
    validate_agent_type,
    validate_config_dict,
    validate_global_config,
    validate_llm_config,
    validate_skill_schema,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_singleton() -> Generator:
    """每个测试前后重置全局 ConfigManager 单例。"""
    reset_config_manager()
    yield
    reset_config_manager()


@pytest.fixture
def minimal_config() -> dict:
    return {
        "llm": {
            "api_url": "https://yunwu.ai",
            "model": "gpt-5.4-nano",
            "api_key": "test_key_12345",
        }
    }


@pytest.fixture
def full_config() -> dict:
    return {
        "llm": {
            "api_url": "https://yunwu.ai",
            "model": "gpt-5.4-nano",
            "api_key": "test_key_12345",
            "temperature": 0.7,
            "max_tokens": 2000,
            "timeout": 30,
            "retry_count": 3,
            "retry_delay": 1.0,
            "stream": False,
        },
        "database": {
            "postgres": {
                "host": "localhost",
                "port": 5432,
                "database": "skillos",
                "user": "postgres",
                "password": "test_pass",
            },
            "neo4j": {
                "uri": "bolt://localhost:7687",
                "user": "neo4j",
                "password": "test_pass",
            },
        },
        "logging": {
            "level": "INFO",
            "format": "json",
            "file": "logs/test.log",
        },
        "debug": False,
        "environment": "test",
        "agents": {
            "trajectory_parser": {
                "temperature": 0.5,
                "max_tokens": 3000,
            },
            "skill_generator": {
                "temperature": 0.8,
                "max_tokens": 4000,
            },
            "validator": {
                "temperature": 0.3,
                "max_tokens": 2000,
            },
        },
    }


@pytest.fixture
def config_file(full_config: dict, tmp_path: Path) -> str:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.dump(full_config), encoding="utf-8")
    return str(path)


@pytest.fixture
def minimal_config_file(minimal_config: dict, tmp_path: Path) -> str:
    path = tmp_path / "config_minimal.yaml"
    path.write_text(yaml.dump(minimal_config), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# LLMConfig 验证
# ---------------------------------------------------------------------------

class TestLLMConfig:

    def test_valid_config(self):
        cfg = LLMConfig(api_key="key", api_url="https://yunwu.ai", model="gpt-5.4-nano")
        assert cfg.api_key == "key"
        assert cfg.api_url == "https://yunwu.ai"

    def test_api_url_trailing_slash_stripped(self):
        cfg = LLMConfig(api_key="key", api_url="https://yunwu.ai/")
        assert not cfg.api_url.endswith("/")

    def test_invalid_api_url_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="api_url"):
            LLMConfig(api_key="key", api_url="not-a-url", model="gpt-5.4-nano")

    def test_empty_api_key_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="api_key"):
            LLMConfig(api_key="   ", api_url="https://yunwu.ai", model="gpt-5.4-nano")

    def test_temperature_out_of_range_raises(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LLMConfig(api_key="key", api_url="https://yunwu.ai", model="m", temperature=3.0)

    def test_max_tokens_minimum(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LLMConfig(api_key="key", api_url="https://yunwu.ai", model="m", max_tokens=0)


# ---------------------------------------------------------------------------
# AgentLLMConfig.merge_with_global
# ---------------------------------------------------------------------------

class TestAgentLLMConfigMerge:

    def test_merge_inherits_global(self):
        global_cfg = LLMConfig(api_key="global_key", api_url="https://yunwu.ai", model="gpt-5.4-nano")
        agent_cfg = AgentLLMConfig(agent_type="validator", temperature=0.3)
        merged = agent_cfg.merge_with_global(global_cfg)

        assert merged.temperature == 0.3          # Agent 覆盖
        assert merged.model == "gpt-5.4-nano"     # 继承全局
        assert merged.api_key == "global_key"     # 继承全局

    def test_merge_agent_api_url_overrides(self):
        global_cfg = LLMConfig(api_key="key", api_url="https://yunwu.ai", model="gpt-5.4-nano")
        agent_cfg = AgentLLMConfig(agent_type="custom", api_url="https://custom.ai")
        merged = agent_cfg.merge_with_global(global_cfg)
        assert merged.api_url == "https://custom.ai"

    def test_merge_none_fields_inherit_global(self):
        global_cfg = LLMConfig(api_key="key", api_url="https://yunwu.ai", model="gpt-5.4-nano", max_tokens=5000)
        agent_cfg = AgentLLMConfig(agent_type="planner")
        merged = agent_cfg.merge_with_global(global_cfg)
        assert merged.max_tokens == 5000


# ---------------------------------------------------------------------------
# ConfigManager 加载
# ---------------------------------------------------------------------------

class TestConfigManagerLoad:

    def test_load_from_file(self, config_file: str):
        mgr = ConfigManager(config_file, {"api_key": "test_key_12345"})
        assert mgr.get_global_llm_config().api_url == "https://yunwu.ai"

    def test_missing_api_key_raises(self, minimal_config_file: str, tmp_path: Path):
        # 写一个没有 api_key 的配置文件
        cfg = {"llm": {"api_url": "https://yunwu.ai", "model": "gpt-5.4-nano"}}
        path = tmp_path / "no_key.yaml"
        path.write_text(yaml.dump(cfg), encoding="utf-8")
        with pytest.raises(ValueError):
            ConfigManager(str(path))

    def test_cli_args_override_file(self, config_file: str):
        mgr = ConfigManager(config_file, {"api_key": "test_key_12345", "model": "gpt-5.4-turbo"})
        assert mgr.get_global_llm_config().model == "gpt-5.4-turbo"

    def test_env_var_override(self, config_file: str, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LLM_MODEL", "env-model")
        mgr = ConfigManager(config_file, {"api_key": "test_key_12345"})
        assert mgr.get_global_llm_config().model == "env-model"

    def test_cli_overrides_env(self, config_file: str, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("LLM_MODEL", "env-model")
        mgr = ConfigManager(config_file, {"api_key": "test_key_12345", "model": "cli-model"})
        assert mgr.get_global_llm_config().model == "cli-model"

    def test_nonexistent_file_uses_defaults(self, tmp_path: Path):
        mgr = ConfigManager(
            str(tmp_path / "nonexistent.yaml"),
            {"api_key": "key", "api_url": "https://yunwu.ai", "model": "gpt-5.4-nano"},
        )
        assert mgr.get_global_llm_config().model == "gpt-5.4-nano"


# ---------------------------------------------------------------------------
# ConfigManager Agent 配置
# ---------------------------------------------------------------------------

class TestConfigManagerAgents:

    def test_get_agent_specific_config(self, config_file: str):
        mgr = ConfigManager(config_file, {"api_key": "test_key_12345"})
        cfg = mgr.get_agent_llm_config("trajectory_parser")
        assert cfg.temperature == 0.5
        assert cfg.max_tokens == 3000
        assert cfg.api_url == "https://yunwu.ai"  # 继承全局

    def test_get_unknown_agent_returns_global(self, config_file: str):
        mgr = ConfigManager(config_file, {"api_key": "test_key_12345"})
        cfg = mgr.get_agent_llm_config("unknown_agent_xyz")
        assert cfg.temperature == 0.7  # 全局默认值

    def test_set_agent_config_runtime(self, config_file: str):
        mgr = ConfigManager(config_file, {"api_key": "test_key_12345"})
        new_cfg = LLMConfig(
            api_url="https://custom.ai",
            model="gpt-5.4-turbo",
            api_key="custom_key",
            temperature=0.9,
        )
        mgr.set_agent_llm_config("custom_agent", new_cfg)
        result = mgr.get_agent_llm_config("custom_agent")
        assert result.model == "gpt-5.4-turbo"
        assert result.api_url == "https://custom.ai"

    def test_list_agent_types(self, config_file: str):
        mgr = ConfigManager(config_file, {"api_key": "test_key_12345"})
        agents = mgr.list_agent_types()
        assert "trajectory_parser" in agents
        assert "skill_generator" in agents
        assert "validator" in agents

    def test_reload_clears_agent_configs(self, config_file: str):
        mgr = ConfigManager(config_file, {"api_key": "test_key_12345"})
        mgr.set_agent_llm_config("temp_agent", LLMConfig(
            api_key="key", api_url="https://yunwu.ai", model="m"
        ))
        assert "temp_agent" in mgr.list_agent_types()
        mgr.reload()
        assert "temp_agent" not in mgr.list_agent_types()


# ---------------------------------------------------------------------------
# ConfigManager to_dict
# ---------------------------------------------------------------------------

class TestConfigManagerToDict:

    def test_to_dict_masks_api_key(self, config_file: str):
        mgr = ConfigManager(config_file, {"api_key": "test_key_12345"})
        data = mgr.to_dict(mask_secrets=True)
        assert data["llm"]["api_key"] == "***"

    def test_to_dict_no_mask(self, config_file: str):
        mgr = ConfigManager(config_file, {"api_key": "test_key_12345"})
        data = mgr.to_dict(mask_secrets=False)
        assert data["llm"]["api_key"] == "test_key_12345"

    def test_to_dict_contains_agents(self, config_file: str):
        mgr = ConfigManager(config_file, {"api_key": "test_key_12345"})
        data = mgr.to_dict()
        assert "agents" in data
        assert "trajectory_parser" in data["agents"]


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

class TestGlobalSingleton:

    def test_singleton_returns_same_instance(self, config_file: str):
        m1 = get_config_manager(config_file, {"api_key": "key"})
        m2 = get_config_manager(config_file, {"api_key": "key"})
        assert m1 is m2

    def test_reset_creates_new_instance(self, config_file: str):
        m1 = get_config_manager(config_file, {"api_key": "key"})
        reset_config_manager()
        m2 = get_config_manager(config_file, {"api_key": "key"})
        assert m1 is not m2


# ---------------------------------------------------------------------------
# 验证工具
# ---------------------------------------------------------------------------

class TestValidators:

    def test_validate_llm_config_valid(self):
        cfg = LLMConfig(api_key="key", api_url="https://yunwu.ai", model="gpt-5.4-nano")
        errors = validate_llm_config(cfg)
        assert errors == []

    def test_validate_llm_config_empty_key(self):
        cfg = LLMConfig.__new__(LLMConfig)
        object.__setattr__(cfg, "api_key", "")
        object.__setattr__(cfg, "api_url", "https://yunwu.ai")
        object.__setattr__(cfg, "model", "gpt-5.4-nano")
        object.__setattr__(cfg, "temperature", 0.7)
        object.__setattr__(cfg, "max_tokens", 2000)
        object.__setattr__(cfg, "timeout", 30)
        object.__setattr__(cfg, "retry_count", 3)
        object.__setattr__(cfg, "retry_delay", 1.0)
        object.__setattr__(cfg, "stream", False)
        errors = validate_llm_config(cfg)
        assert any("api_key" in e for e in errors)

    def test_validate_skill_schema_valid(self):
        schema = {"type": "object", "properties": {"name": {"type": "string"}}}
        ok, errors = validate_skill_schema(schema)
        assert ok
        assert errors == []

    def test_validate_skill_schema_missing_type(self):
        schema = {"properties": {"name": {"type": "string"}}}
        ok, errors = validate_skill_schema(schema)
        assert not ok
        assert any("type" in e for e in errors)

    def test_validate_skill_schema_object_missing_properties(self):
        schema = {"type": "object"}
        ok, errors = validate_skill_schema(schema)
        assert not ok

    def test_validate_agent_type_known(self):
        for at in KNOWN_AGENT_TYPES:
            assert validate_agent_type(at), f"已知类型 {at!r} 应该通过验证"

    def test_validate_agent_type_custom_snake_case(self):
        assert validate_agent_type("my_custom_agent")

    def test_validate_agent_type_invalid(self):
        assert not validate_agent_type("")
        assert not validate_agent_type("My-Agent")
        assert not validate_agent_type("123agent")

    def test_validate_config_dict_missing_api_key(self):
        d = {"llm": {"api_url": "https://yunwu.ai", "model": "gpt-5.4-nano"}}
        errors = validate_config_dict(d)
        assert any("api_key" in e for e in errors)

    def test_validate_config_dict_invalid_temperature(self):
        d = {"llm": {"api_key": "key", "temperature": 5.0}}
        errors = validate_config_dict(d)
        assert any("temperature" in e for e in errors)

    def test_validate_config_dict_invalid_agent_type(self):
        d = {
            "llm": {"api_key": "key"},
            "agents": {"Invalid-Agent": {"temperature": 0.5}},
        }
        errors = validate_config_dict(d)
        assert any("Invalid-Agent" in e for e in errors)


# ---------------------------------------------------------------------------
# 环境变量解析
# ---------------------------------------------------------------------------

class TestEnvVarResolution:

    def test_env_var_in_config_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TEST_MODEL", "env-resolved-model")
        cfg = {
            "llm": {
                "api_url": "https://yunwu.ai",
                "model": "${TEST_MODEL}",
                "api_key": "key",
            }
        }
        path = tmp_path / "env_config.yaml"
        path.write_text(yaml.dump(cfg), encoding="utf-8")
        mgr = ConfigManager(str(path), {"api_key": "key"})
        assert mgr.get_global_llm_config().model == "env-resolved-model"

    def test_missing_env_var_resolves_to_empty(self, tmp_path: Path):
        cfg = {
            "llm": {
                "api_url": "https://yunwu.ai",
                "model": "gpt-5.4-nano",
                "api_key": "key",
                "temperature": 0.7,
            }
        }
        path = tmp_path / "cfg.yaml"
        path.write_text(yaml.dump(cfg), encoding="utf-8")
        mgr = ConfigManager(str(path), {"api_key": "key"})
        # 不应该崩溃
        assert mgr.get_global_llm_config().model == "gpt-5.4-nano"
