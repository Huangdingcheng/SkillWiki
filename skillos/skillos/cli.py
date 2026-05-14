"""CLI 工具 - 生产级别"""

from __future__ import annotations

import sys
from typing import Optional

import click

from .config.config_manager import ConfigManager, reset_config_manager
from .config.llm_config import LLMConfig
from .utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# 全局选项
# ---------------------------------------------------------------------------

@click.group()
@click.option("--config", default="config.yaml", show_default=True, help="配置文件路径")
@click.option("--debug", is_flag=True, default=False, help="启用调试模式")
@click.version_option(version="0.1.0", prog_name="skillos")
@click.pass_context
def cli(ctx: click.Context, config: str, debug: bool) -> None:
    """SkillOS - A Skill-Centric Operating System for Self-Evolving Agents"""
    ctx.ensure_object(dict)
    ctx.obj["config_file"] = config
    ctx.obj["debug"] = debug


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--api-key", required=True, help="LLM API key（必须）")
@click.option("--api-url", default="https://api.deepseek.com", show_default=True, help="LLM API 地址")
@click.option("--model", default="deepseek-v4-pro", show_default=True, help="LLM 模型名称")
@click.option("--temperature", type=float, default=None, help="全局温度参数（0-2）")
@click.option("--max-tokens", type=int, default=None, help="全局最大 token 数")
@click.pass_context
def init(
    ctx: click.Context,
    api_key: str,
    api_url: str,
    model: str,
    temperature: Optional[float],
    max_tokens: Optional[int],
) -> None:
    """初始化 SkillOS 配置并验证连通性。"""
    cli_args = {"api_key": api_key, "api_url": api_url, "model": model}
    if temperature is not None:
        cli_args["temperature"] = temperature
    if max_tokens is not None:
        cli_args["max_tokens"] = max_tokens

    try:
        reset_config_manager()
        mgr = ConfigManager(ctx.obj["config_file"], cli_args)
        llm = mgr.get_global_llm_config()

        click.echo(click.style("✓ 配置加载成功", fg="green"))
        click.echo(f"  API URL  : {llm.api_url}")
        click.echo(f"  模型     : {llm.model}")
        click.echo(f"  温度     : {llm.temperature}")
        click.echo(f"  Max Tokens: {llm.max_tokens}")
        click.echo(f"  超时     : {llm.timeout}s")
        click.echo(f"  重试次数 : {llm.retry_count}")
        click.echo(f"  Agent 数 : {len(mgr.list_agent_types())}")
    except ValueError as e:
        click.echo(click.style(f"✗ 配置错误: {e}", fg="red"), err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(click.style(f"✗ 初始化失败: {e}", fg="red"), err=True)
        if ctx.obj.get("debug"):
            raise
        sys.exit(1)


# ---------------------------------------------------------------------------
# test-config
# ---------------------------------------------------------------------------

@cli.command("test-config")
@click.option("--api-key", required=True, help="LLM API key")
@click.option("--connectivity", is_flag=True, default=False, help="同时测试 API 连通性")
@click.pass_context
def test_config(ctx: click.Context, api_key: str, connectivity: bool) -> None:
    """验证配置文件格式，可选测试 API 连通性。"""
    from .utils.validators import validate_global_config, test_llm_connectivity

    try:
        reset_config_manager()
        mgr = ConfigManager(ctx.obj["config_file"], {"api_key": api_key})
        errors = validate_global_config(mgr.get_global_config())

        if errors:
            click.echo(click.style("✗ 配置验证失败：", fg="red"), err=True)
            for err in errors:
                click.echo(f"  - {err}", err=True)
            sys.exit(1)

        click.echo(click.style("✓ 配置格式验证通过", fg="green"))

        if connectivity:
            click.echo("  正在测试 API 连通性...")
            ok, msg = test_llm_connectivity(mgr.get_global_llm_config())
            if ok:
                click.echo(click.style(f"  ✓ {msg}", fg="green"))
            else:
                click.echo(click.style(f"  ✗ {msg}", fg="red"), err=True)
                sys.exit(1)

    except ValueError as e:
        click.echo(click.style(f"✗ 配置错误: {e}", fg="red"), err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# show-config
# ---------------------------------------------------------------------------

@cli.command("show-config")
@click.option("--api-key", required=True, help="LLM API key")
@click.option("--agent-type", default=None, help="只显示指定 Agent 的配置")
@click.pass_context
def show_config(ctx: click.Context, api_key: str, agent_type: Optional[str]) -> None:
    """显示当前生效的配置（隐藏 API key）。"""
    import json

    try:
        reset_config_manager()
        mgr = ConfigManager(ctx.obj["config_file"], {"api_key": api_key})

        if agent_type:
            cfg = mgr.get_agent_llm_config(agent_type)
            data = cfg.model_dump()
            data["api_key"] = "***"
            click.echo(f"Agent '{agent_type}' 最终配置：")
            click.echo(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            data = mgr.to_dict(mask_secrets=True)
            click.echo(json.dumps(data, indent=2, ensure_ascii=False))

    except ValueError as e:
        click.echo(click.style(f"✗ 配置错误: {e}", fg="red"), err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# list-agents
# ---------------------------------------------------------------------------

@cli.command("list-agents")
@click.option("--api-key", required=True, help="LLM API key")
@click.pass_context
def list_agents(ctx: click.Context, api_key: str) -> None:
    """列出所有已配置的 Agent 类型及其 LLM 配置摘要。"""
    try:
        reset_config_manager()
        mgr = ConfigManager(ctx.obj["config_file"], {"api_key": api_key})
        agent_types = mgr.list_agent_types()

        if not agent_types:
            click.echo("未找到任何 Agent 配置（使用全局配置）")
            return

        click.echo(f"已配置的 Agent（共 {len(agent_types)} 个）：")
        click.echo(f"{'Agent 类型':<30} {'模型':<20} {'温度':>6} {'Max Tokens':>12}")
        click.echo("-" * 72)
        for at in sorted(agent_types):
            cfg = mgr.get_agent_llm_config(at)
            click.echo(f"{at:<30} {cfg.model:<20} {cfg.temperature:>6.2f} {cfg.max_tokens:>12}")

    except ValueError as e:
        click.echo(click.style(f"✗ 配置错误: {e}", fg="red"), err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--api-key", required=True, help="LLM API key")
@click.option("--agent-type", default=None, help="测试指定 Agent 的 LLM 配置")
@click.pass_context
def ping(ctx: click.Context, api_key: str, agent_type: Optional[str]) -> None:
    """测试 LLM API 连通性。"""
    from .utils.validators import test_llm_connectivity

    try:
        reset_config_manager()
        mgr = ConfigManager(ctx.obj["config_file"], {"api_key": api_key})

        if agent_type:
            cfg = mgr.get_agent_llm_config(agent_type)
            label = f"Agent '{agent_type}'"
        else:
            cfg = mgr.get_global_llm_config()
            label = "全局配置"

        click.echo(f"正在测试 {label} 的连通性（{cfg.api_url}，模型: {cfg.model}）...")
        ok, msg = test_llm_connectivity(cfg)

        if ok:
            click.echo(click.style(f"✓ {msg}", fg="green"))
        else:
            click.echo(click.style(f"✗ {msg}", fg="red"), err=True)
            sys.exit(1)

    except ValueError as e:
        click.echo(click.style(f"✗ 配置错误: {e}", fg="red"), err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# benchmark-runtime
# ---------------------------------------------------------------------------

@cli.command("benchmark-runtime")
@click.option("--api-key", required=True, help="DeepSeek API key")
@click.option("--api-url", default=None, help="LLM API address; defaults to config file")
@click.option("--model", default=None, help="LLM model; defaults to config file")
@click.pass_context
def benchmark_runtime(
    ctx: click.Context,
    api_key: str,
    api_url: Optional[str],
    model: Optional[str],
) -> None:
    """Run the formal Runtime benchmark and print scores in the terminal."""
    from .evals.runtime_benchmark import run_runtime_benchmark
    from .utils.llm_client import LLMClient

    cli_args = {"api_key": api_key}
    if api_url:
        cli_args["api_url"] = api_url
    if model:
        cli_args["model"] = model

    try:
        reset_config_manager()
        mgr = ConfigManager(ctx.obj["config_file"], cli_args)
        llm = LLMClient(mgr.get_global_llm_config())
        result = run_runtime_benchmark(llm)
        click.echo(result.format_report())
    except ValueError as e:
        click.echo(click.style(f"Config error: {e}", fg="red"), err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(click.style(f"Benchmark failed: {e}", fg="red"), err=True)
        if ctx.obj.get("debug"):
            raise
        sys.exit(1)


# ---------------------------------------------------------------------------
# set-agent-model
# ---------------------------------------------------------------------------

@cli.command("set-agent-model")
@click.option("--api-key", required=True, help="LLM API key")
@click.option("--agent-type", required=True, help="Agent 类型")
@click.option("--model", required=True, help="新的模型名称")
@click.option("--api-url", default=None, help="新的 API 地址（可选）")
@click.pass_context
def set_agent_model(
    ctx: click.Context,
    api_key: str,
    agent_type: str,
    model: str,
    api_url: Optional[str],
) -> None:
    """为指定 Agent 设置模型（运行时覆盖，不修改配置文件）。"""
    try:
        reset_config_manager()
        mgr = ConfigManager(ctx.obj["config_file"], {"api_key": api_key})

        current = mgr.get_agent_llm_config(agent_type)
        new_cfg = LLMConfig(
            api_url=api_url or current.api_url,
            model=model,
            api_key=current.api_key,
            temperature=current.temperature,
            max_tokens=current.max_tokens,
            timeout=current.timeout,
            retry_count=current.retry_count,
        )
        mgr.set_agent_llm_config(agent_type, new_cfg)

        click.echo(click.style(f"✓ Agent '{agent_type}' 模型已更新", fg="green"))
        click.echo(f"  模型: {current.model} → {model}")
        if api_url:
            click.echo(f"  API URL: {current.api_url} → {api_url}")

    except ValueError as e:
        click.echo(click.style(f"✗ 配置错误: {e}", fg="red"), err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
