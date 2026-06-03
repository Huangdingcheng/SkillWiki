"""SkillWiki CLI"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import click

from .config.config_manager import ConfigManager, reset_config_manager
from .config.llm_config import LLMConfig
from .utils.logger import get_logger

logger = get_logger(__name__)

BASE_URL_DEFAULT = "http://127.0.0.1:8001"
API_PREFIX = "/api/v1"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _api(ctx: click.Context, path: str, method: str = "GET", json_body=None):
    """Make a request to the running SkillWiki API."""
    import httpx
    base = ctx.obj.get("api_url", BASE_URL_DEFAULT)
    url = f"{base}{API_PREFIX}{path}"
    try:
        with httpx.Client(timeout=60) as client:
            if method == "GET":
                r = client.get(url)
            elif method == "POST":
                r = client.post(url, json=json_body)
            else:
                raise ValueError(f"Unsupported method: {method}")
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        click.echo(click.style(f"✗ Cannot connect to SkillWiki API at {base}", fg="red"), err=True)
        click.echo("  Start the backend first: skillwiki serve", err=True)
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        detail = e.response.text
        click.echo(click.style(f"✗ API error {e.response.status_code}: {detail}", fg="red"), err=True)
        sys.exit(1)


def _print_json(data):
    click.echo(json.dumps(data, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# root group
# ---------------------------------------------------------------------------

@click.group()
@click.option("--api-url", default=BASE_URL_DEFAULT, show_default=True, help="SkillWiki API base URL")
@click.option("--config", default="config.yaml", show_default=True, help="Config file path")
@click.option("--debug", is_flag=True, default=False, help="Enable debug mode")
@click.version_option(version="0.1.0", prog_name="skillwiki")
@click.pass_context
def cli(ctx: click.Context, api_url: str, config: str, debug: bool) -> None:
    """SkillWiki - A Skill-Centric Knowledge Wiki for Self-Evolving Agents"""
    ctx.ensure_object(dict)
    ctx.obj["api_url"] = api_url
    ctx.obj["config_file"] = config
    ctx.obj["debug"] = debug


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8001, show_default=True)
@click.option("--backend", default="memory", show_default=True,
              help="Repository backend: memory | sqlite | postgres")
@click.option("--api-key", default=None, envvar="LLM_API_KEY", help="LLM API key")
@click.pass_context
def serve(ctx: click.Context, host: str, port: int, backend: str, api_key: Optional[str]) -> None:
    """Start the SkillWiki API server."""
    import subprocess
    cmd = [
        sys.executable, "-m", "skillwiki.api.main",
        "--host", host, "--port", str(port),
        "--repository-backend", backend,
    ]
    if api_key:
        cmd += ["--api-key", api_key]
    click.echo(f"Starting SkillWiki API on {host}:{port} (backend: {backend})")
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        click.echo("\nServer stopped.")
    except subprocess.CalledProcessError as e:
        click.echo(click.style(f"✗ Server exited with code {e.returncode}", fg="red"), err=True)
        sys.exit(e.returncode)


# ---------------------------------------------------------------------------
# ingest group
# ---------------------------------------------------------------------------

VALID_SOURCE_TYPES = ["trajectory", "document", "api_doc", "script", "past_skills"]


@cli.group()
def ingest():
    """Ingest raw experience or skills into SkillWiki."""


@ingest.command("run")
@click.argument("source_type", metavar="SOURCE_TYPE",
                type=click.Choice(VALID_SOURCE_TYPES, case_sensitive=False))
@click.argument("input_", metavar="INPUT")
@click.option("--max-candidates", type=int, default=None,
              help="Max candidates to extract (past_skills only)")
@click.option("--create", is_flag=True, default=False,
              help="Automatically create S1 candidates after parsing")
@click.pass_context
def ingest_run(ctx: click.Context, source_type: str, input_: str,
               max_candidates: Optional[int], create: bool) -> None:
    """Parse INPUT (file path or raw text) and extract skill candidates.

    \b
    SOURCE_TYPE choices:
      trajectory   - operation sequences / conversation traces (.txt, .md)
      document     - knowledge docs, tutorials (.md, .txt)
      api_doc      - API specs (.md, .txt, .yaml)
      script       - shell / automation scripts (.sh, .md)
      past_skills  - existing skill definitions (.json, .jsonl)

    \b
    Examples:
      skillwiki ingest run document ./tutorial.md
      skillwiki ingest run script ./installer.sh
      skillwiki ingest run past_skills ./skills.json --max-candidates 20
      skillwiki ingest run trajectory "open browser -> search -> copy link"
      skillwiki ingest run past_skills ./batch.jsonl --create
    """
    p = Path(input_)
    if p.exists():
        content = p.read_text(encoding="utf-8")
        click.echo(f"Reading {p} ({len(content)} chars)")
    else:
        content = input_

    if not content.strip():
        click.echo(click.style("✗ Input is empty.", fg="red"), err=True)
        sys.exit(1)

    metadata: dict = {}
    if max_candidates is not None:
        metadata["max_candidates"] = max_candidates

    endpoint = "/ingest/parse-and-create" if create else "/ingest/parse"
    body = {"source_type": source_type, "content": content, "metadata": metadata}

    click.echo(f"Ingesting as '{source_type}'...")
    result = _api(ctx, endpoint, method="POST", json_body=body)

    candidates = result.get("candidates") or result.get("units") or []
    click.echo(click.style(f"✓ Extracted {len(candidates)} candidate(s)", fg="green"))
    for i, c in enumerate(candidates):
        cid = c.get("id") or c.get("skill_id") or f"#{i}"
        name = c.get("name") or c.get("skill_name") or "(unnamed)"
        state = c.get("state") or c.get("lifecycle_state") or ""
        click.echo(f"  [{cid}] {name} {state}")


@ingest.command("status")
@click.argument("candidate_id")
@click.pass_context
def ingest_status(ctx: click.Context, candidate_id: str) -> None:
    """Show current lifecycle state of a candidate skill."""
    result = _api(ctx, f"/skills/{candidate_id}")
    state = result.get("lifecycle_state") or result.get("state") or "unknown"
    name = result.get("name") or result.get("skill_name") or candidate_id
    click.echo(f"{name}")
    click.echo(f"  State : {state}")
    click.echo(f"  ID    : {candidate_id}")
    tags = result.get("tags") or []
    if tags:
        click.echo(f"  Tags  : {', '.join(tags)}")


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("candidate_id")
@click.pass_context
def audit(ctx: click.Context, candidate_id: str) -> None:
    """Run static audit on a candidate skill (schema, safety, postconditions)."""
    skill = _api(ctx, f"/skills/{candidate_id}/full")
    body = {
        "source_type": skill.get("source_type", "document"),
        "raw_content": "",
        "skill": skill,
    }
    result = _api(ctx, "/ingest/audit-candidate", method="POST", json_body=body)
    passed = result.get("passed", False)
    score = result.get("score", 0)
    icon = click.style("✓", fg="green") if passed else click.style("✗", fg="red")
    click.echo(f"{icon} Audit {'passed' if passed else 'failed'} (score: {score:.2f})")
    for issue in result.get("issues") or []:
        click.echo(f"  - {issue}")


# ---------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("skill_id")
@click.option("--harness", default="mock", show_default=True,
              type=click.Choice(["mock", "claude_code", "codex"], case_sensitive=False),
              help="Execution harness to use")
@click.option("--max-retries", default=3, show_default=True, help="Max repair+retry attempts")
@click.option("--promote/--no-promote", default=True, show_default=True,
              help="Auto-promote to S3 on pass")
@click.option("--watch", is_flag=True, default=False, help="Print each attempt result")
@click.option("--timeout", default=60, show_default=True, help="Per-attempt timeout (seconds)")
@click.pass_context
def verify(ctx: click.Context, skill_id: str, harness: str, max_retries: int,
           promote: bool, watch: bool, timeout: int) -> None:
    """Execute-verify loop until postconditions pass or retries exhausted.

    \b
    Examples:
      skillwiki verify abc123
      skillwiki verify abc123 --harness claude_code --max-retries 5 --watch
      skillwiki verify abc123 --no-promote
    """
    body = {
        "harness": harness,
        "max_attempts": max_retries,
        "promote_on_pass": promote,
        "allow_repair": True,
        "timeout_s": timeout,
    }

    if watch:
        click.echo(
            f"Running verify loop for {skill_id} "
            f"(harness={harness}, max_retries={max_retries})..."
        )

    result = _api(ctx, f"/harness/{skill_id}/verify-loop", method="POST", json_body=body)

    status = result.get("status", "unknown")
    score = result.get("score", 0)
    attempts = result.get("attempt_count", 0)
    final_state = result.get("final_state") or ""

    if watch:
        for i, attempt in enumerate(result.get("attempts") or []):
            a_passed = attempt.get("passed", False)
            a_icon = click.style("✓", fg="green") if a_passed else click.style("✗", fg="red")
            click.echo(f"  Attempt {i + 1}: {a_icon} score={attempt.get('score', 0):.2f}")

    if status == "passed":
        click.echo(click.style(
            f"✓ Verified in {attempts} attempt(s) — score: {score:.2f}", fg="green"
        ))
        if promote and final_state:
            click.echo(f"  State promoted to: {final_state}")
    else:
        click.echo(click.style(
            f"✗ Verification failed after {attempts} attempt(s) — score: {score:.2f}", fg="red"
        ))
        repairs = result.get("repairs") or []
        if repairs:
            click.echo(f"  Repair attempts: {len(repairs)}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("skill_id")
@click.argument("target_state")
@click.pass_context
def promote(ctx: click.Context, skill_id: str, target_state: str) -> None:
    """Manually advance a skill's lifecycle state.

    \b
    States: S0 (raw) → S1 (candidate) → S2 (draft) → S3 (verified) → S4 (released)

    \b
    Examples:
      skillwiki promote abc123 S3
      skillwiki promote abc123 released
    """
    body = {"target_state": target_state}
    result = _api(ctx, f"/lifecycle/{skill_id}/transition", method="POST", json_body=body)
    new_state = result.get("lifecycle_state") or result.get("state") or target_state
    click.echo(click.style(f"✓ {skill_id} → {new_state}", fg="green"))


# ---------------------------------------------------------------------------
# skill group
# ---------------------------------------------------------------------------

@cli.group()
def skill():
    """Query and execute skills."""


@skill.command("list")
@click.option("--state", default=None, help="Filter by lifecycle state (e.g. S3, released)")
@click.option("--tag", default=None, help="Filter by tag")
@click.option("--limit", default=20, show_default=True)
@click.pass_context
def skill_list(ctx: click.Context, state: Optional[str], tag: Optional[str], limit: int) -> None:
    """List skills in the wiki."""
    params = []
    if state:
        params.append(f"state={state}")
    if tag:
        params.append(f"tag={tag}")
    if limit:
        params.append(f"limit={limit}")
    qs = ("?" + "&".join(params)) if params else ""
    skills = _api(ctx, f"/skills{qs}")
    if not skills:
        click.echo("No skills found.")
        return
    click.echo(f"{'ID':<36} {'Name':<30} {'State':<12} Tags")
    click.echo("-" * 90)
    for s in skills:
        sid = (s.get("id") or s.get("skill_id") or "")[:36]
        name = (s.get("name") or s.get("skill_name") or "")[:30]
        st = (s.get("lifecycle_state") or s.get("state") or "")[:12]
        tags = ", ".join(s.get("tags") or [])
        click.echo(f"{sid:<36} {name:<30} {st:<12} {tags}")


@skill.command("get")
@click.argument("skill_id")
@click.option("--full", is_flag=True, default=False,
              help="Show full schema including implementation")
@click.pass_context
def skill_get(ctx: click.Context, skill_id: str, full: bool) -> None:
    """Show details of a skill."""
    endpoint = f"/skills/{skill_id}/full" if full else f"/skills/{skill_id}"
    result = _api(ctx, endpoint)
    _print_json(result)


@skill.command("exec")
@click.argument("skill_id")
@click.option("--input", "input_data", default="{}", show_default=True,
              help="JSON input object")
@click.pass_context
def skill_exec(ctx: click.Context, skill_id: str, input_data: str) -> None:
    """Execute a skill with the given JSON input.

    \b
    Examples:
      skillwiki skill exec abc123 --input '{"url": "https://example.com"}'
    """
    try:
        payload = json.loads(input_data)
    except json.JSONDecodeError as e:
        click.echo(click.style(f"✗ Invalid JSON input: {e}", fg="red"), err=True)
        sys.exit(1)

    body = {"skill_id": skill_id, "input": payload}
    click.echo(f"Executing skill {skill_id}...")
    result = _api(ctx, "/execution/run", method="POST", json_body=body)
    status = result.get("status") or "unknown"
    icon = click.style("✓", fg="green") if status == "success" else click.style("✗", fg="red")
    click.echo(f"{icon} Status: {status}")
    output = result.get("output") or result.get("result")
    if output:
        click.echo("Output:")
        _print_json(output)


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("task")
@click.option("--verbose", is_flag=True, default=False, help="Show execution plan steps")
@click.pass_context
def run(ctx: click.Context, task: str, verbose: bool) -> None:
    """Execute a natural language task via the full agent pipeline.

    \b
    Examples:
      skillwiki run "analyze the attached PDF and summarize key points"
      skillwiki run "create an Excel report from this data" --verbose
    """
    body = {"task": task}
    click.echo(f"Running task: {task}")
    result = _api(ctx, "/execution/task", method="POST", json_body=body)
    status = result.get("status") or "unknown"
    icon = click.style("✓", fg="green") if status == "success" else click.style("✗", fg="red")
    click.echo(f"{icon} {status}")
    if verbose:
        for step in (result.get("plan") or []):
            click.echo(f"  → {step}")
    output = result.get("output") or result.get("result")
    if output:
        if isinstance(output, (dict, list)):
            _print_json(output)
        else:
            click.echo(output)


# ---------------------------------------------------------------------------
# legacy config commands
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--api-key", required=True, help="LLM API key")
@click.option("--api-url", default="https://yunwu.ai", show_default=True)
@click.option("--model", default="gpt-5.4-nano", show_default=True)
@click.option("--temperature", type=float, default=None)
@click.option("--max-tokens", type=int, default=None)
@click.pass_context
def init(ctx: click.Context, api_key: str, api_url: str, model: str,
         temperature: Optional[float], max_tokens: Optional[int]) -> None:
    """Initialize SkillWiki LLM configuration."""
    cli_args: dict = {"api_key": api_key, "api_url": api_url, "model": model}
    if temperature is not None:
        cli_args["temperature"] = temperature
    if max_tokens is not None:
        cli_args["max_tokens"] = max_tokens
    try:
        reset_config_manager()
        mgr = ConfigManager(ctx.obj["config_file"], cli_args)
        llm = mgr.get_global_llm_config()
        click.echo(click.style("✓ Config loaded", fg="green"))
        click.echo(f"  API URL : {llm.api_url}")
        click.echo(f"  Model   : {llm.model}")
        click.echo(f"  Agents  : {len(mgr.list_agent_types())}")
    except Exception as e:
        click.echo(click.style(f"✗ {e}", fg="red"), err=True)
        if ctx.obj.get("debug"):
            raise
        sys.exit(1)


@cli.command("ping")
@click.option("--api-key", required=True, help="LLM API key")
@click.option("--api-url", default=None)
@click.option("--model", default=None)
@click.pass_context
def ping(ctx: click.Context, api_key: str, api_url: Optional[str],
         model: Optional[str]) -> None:
    """Test LLM API connectivity."""
    from .utils.validators import test_llm_connectivity
    try:
        reset_config_manager()
        cli_args: dict = {"api_key": api_key}
        if api_url:
            cli_args["api_url"] = api_url
        if model:
            cli_args["model"] = model
        mgr = ConfigManager(ctx.obj["config_file"], cli_args)
        cfg = mgr.get_global_llm_config()
        ok, msg = test_llm_connectivity(cfg)
        if ok:
            click.echo(click.style(f"✓ {msg}", fg="green"))
        else:
            click.echo(click.style(f"✗ {msg}", fg="red"), err=True)
            sys.exit(1)
    except Exception as e:
        click.echo(click.style(f"✗ {e}", fg="red"), err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
