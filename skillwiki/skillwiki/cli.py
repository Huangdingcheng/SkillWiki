"""SkillWiki CLI"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

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
        with httpx.Client(timeout=ctx.obj.get("http_timeout", 600)) as client:
            if method == "GET":
                r = client.get(url)
            elif method == "POST":
                r = client.post(url, json=json_body)
            else:
                raise ValueError(f"Unsupported method: {method}")
        r.raise_for_status()
        return r.json()
    except httpx.TimeoutException as exc:
        raise click.ClickException(
            "API request timed out; inspect server receipts before retrying a paid operation."
        ) from exc
    except httpx.ConnectError:
        click.echo(
            click.style(f"[FAIL] Cannot connect to SkillWiki API at {base}", fg="red"), err=True
        )
        click.echo("  Start the backend first: skillwiki serve", err=True)
        sys.exit(1)
    except httpx.HTTPStatusError as e:
        detail = e.response.text
        click.echo(
            click.style(f"[FAIL] API error {e.response.status_code}: {detail}", fg="red"), err=True
        )
        sys.exit(1)


def _read_json(path, expected=dict):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise click.ClickException(f"Cannot read JSON file {path}: {exc}") from exc
    if not isinstance(value, expected):
        raise click.ClickException(f"JSON file must contain a {expected.__name__}")
    return value


def _print_json(data):
    click.echo(json.dumps(data, indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# root group
# ---------------------------------------------------------------------------


@click.group()
@click.option(
    "--api-url", default=BASE_URL_DEFAULT, show_default=True, help="SkillWiki API base URL"
)
@click.option("--config", default="config.yaml", show_default=True, help="Config file path")
@click.option(
    "--http-timeout",
    type=click.FloatRange(min=1),
    default=600,
    show_default=True,
    help="HTTP deadline; requests are never retried by CLI",
)
@click.option("--debug", is_flag=True, default=False, help="Enable debug mode")
@click.version_option(version="0.1.0", prog_name="skillwiki")
@click.pass_context
def cli(ctx: click.Context, api_url: str, config: str, debug: bool, http_timeout: float) -> None:
    """SkillWiki - A Skill-Centric Knowledge Wiki for Self-Evolving Agents"""
    ctx.ensure_object(dict)
    ctx.obj["api_url"] = api_url
    ctx.obj["config_file"] = config
    ctx.obj["debug"] = debug
    ctx.obj["http_timeout"] = http_timeout


# ---------------------------------------------------------------------------
# serve
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8001, show_default=True)
@click.option("--backend", type=click.Choice(["git", "memory"]), default="git", show_default=True)
@click.option(
    "--storage-dir",
    type=click.Path(path_type=Path),
    default=Path("skillwiki-data"),
    show_default=True,
)
@click.option(
    "--governance-repo",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Existing Git repository for lifecycle snapshots and releases",
)
@click.option("--model", default=None, help="LLM model (otherwise LLM_MODEL)")
@click.option("--llm-api-url", default=None, help="LLM endpoint (distinct from global API URL)")
@click.option("--llm-retries", type=click.IntRange(0, 10), default=0, show_default=True)
@click.option("--seed-demo/--no-seed-demo", default=False, show_default=True)
@click.option("--api-key", default=None, envvar="LLM_API_KEY", help="LLM API key")
@click.pass_context
def serve(
    ctx: click.Context,
    host: str,
    port: int,
    backend: str,
    api_key: Optional[str],
    storage_dir: Path,
    governance_repo: Optional[Path],
    model: Optional[str],
    llm_api_url: Optional[str],
    llm_retries: int,
    seed_demo: bool,
) -> None:
    """Start the SkillWiki API server."""
    import subprocess

    cmd = [
        sys.executable,
        "-m",
        "skillwiki.api.main",
        "--host",
        host,
        "--port",
        str(port),
        "--repository-backend",
        backend,
    ]
    cmd += [
        "--skill-storage-dir",
        str(storage_dir.resolve()),
        "--llm-retry-count",
        str(llm_retries),
        "--seed-demo" if seed_demo else "--no-seed-demo",
    ]
    if model:
        cmd += ["--model", model]
    if llm_api_url:
        cmd += ["--api-url", llm_api_url]
    child_env = os.environ.copy()
    if governance_repo:
        child_env["SKILLOS_GOVERNANCE_REPO"] = str(governance_repo.resolve())
    if api_key:
        child_env["LLM_API_KEY"] = api_key
    click.echo(f"Starting SkillWiki API on {host}:{port} (backend: {backend})")
    try:
        subprocess.run(cmd, check=True, env=child_env)
    except KeyboardInterrupt:
        click.echo("\nServer stopped.")
    except subprocess.CalledProcessError as e:
        click.echo(
            click.style(f"[FAIL] Server exited with code {e.returncode}", fg="red"), err=True
        )
        sys.exit(e.returncode)


# ---------------------------------------------------------------------------
# ingest group
# ---------------------------------------------------------------------------

VALID_SOURCE_TYPES = ["trajectory", "document", "api_doc", "script", "past_skills"]


@cli.group()
def ingest():
    """Ingest raw experience or skills into SkillWiki."""


@ingest.command("run")
@click.argument(
    "source_type",
    metavar="SOURCE_TYPE",
    type=click.Choice(VALID_SOURCE_TYPES, case_sensitive=False),
)
@click.argument("input_", metavar="INPUT")
@click.option(
    "--max-candidates", type=int, default=None, help="Max candidates to extract (past_skills only)"
)
@click.option(
    "--create", is_flag=True, default=False, help="Automatically create S1 candidates after parsing"
)
@click.pass_context
def ingest_run(
    ctx: click.Context, source_type: str, input_: str, max_candidates: Optional[int], create: bool
) -> None:
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
        click.echo(click.style("[FAIL] Input is empty.", fg="red"), err=True)
        sys.exit(1)

    metadata: dict = {}
    if max_candidates is not None:
        metadata["max_candidates"] = max_candidates

    endpoint = "/ingest/parse-and-create" if create else "/ingest/parse"
    body = {"source_type": source_type, "content": content, "metadata": metadata}

    click.echo(f"Ingesting as '{source_type}'...")
    result = _api(ctx, endpoint, method="POST", json_body=body)

    candidates = result.get("created_skills") if create else result.get("units")
    candidates = candidates or []
    for error in result.get("errors") or []:
        click.echo(f"  error: {error}", err=True)
    click.echo(click.style(f"[OK] Extracted {len(candidates)} candidate(s)", fg="green"))
    for i, c in enumerate(candidates):
        cid = c.get("id") or c.get("skill_id") or f"#{i}"
        name = c.get("name") or c.get("skill_name") or "(unnamed)"
        state = c.get("state") or c.get("lifecycle_state") or ""
        click.echo(f"  [{cid}] {name} {state}")

    if not result.get("success", True):
        ctx.exit(1)


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
@click.argument("candidate_id", required=False)
@click.option(
    "--candidate-file", type=click.Path(exists=True), help="Exact candidate-review API JSON payload"
)
@click.option("--json", "as_json", is_flag=True)
@click.pass_context
def audit(ctx, candidate_id, candidate_file, as_json):
    """Audit a candidate without persisting; configured server models may be used."""
    if bool(candidate_id) == bool(candidate_file):
        raise click.UsageError("Supply a stored skill ID or --candidate-file, exclusively")
    if candidate_file:
        body = _read_json(candidate_file)
    else:
        skill = _api(ctx, f"/skills/{candidate_id}/full")
        interface = skill.get("interface") or {}
        impl = skill.get("implementation") or {}
        if impl.get("code"):
            raise click.ClickException(
                "The candidate-review API accepts prompt-based drafts; it cannot preserve "
                "this stored code implementation. Use the native verification workflow."
            )
        provenance = skill.get("provenance") or {}
        context = provenance.get("creation_context") or {}
        source = context.get("source") or {}
        body = {
            key: skill[key]
            for key in (
                "name",
                "description",
                "skill_type",
                "tags",
                "evaluation",
                "dependency_ids",
                "component_ids",
            )
            if key in skill
        }
        body.update(
            {
                key: interface[key]
                for key in ("input_schema", "output_schema", "preconditions", "postconditions")
                if key in interface
            }
        )
        body.update(
            {
                key: impl[key]
                for key in (
                    "prompt_template",
                    "prompt_format",
                    "request_body_validation",
                    "tool_calls",
                    "sub_skill_ids",
                )
                if key in impl
            }
        )
        body.update(
            source_type=context.get("source_type") or provenance.get("source_type") or "document",
            unit_id=context.get("unit_id") or candidate_id,
            raw_content=source.get("content", ""),
            provenance=provenance,
        )
        # Generic API-created skills have no source material; audit their contract
        # as a document draft, without inventing source text or proof.
        if body["source_type"] == "api":
            body["source_type"] = "document"
    result = _api(ctx, "/ingest/audit-candidate", method="POST", json_body=body)
    if as_json:
        _print_json(result)
    else:
        click.echo(
            f"Audit {'passed' if result.get('passed') else 'failed'} (score: {result.get('audit_score', 0):.2f})"
        )
        for field in ("issues", "warnings", "recommendations"):
            for item in result.get(field) or []:
                click.echo(f"  {field}: {item}")
    if not result.get("passed"):
        ctx.exit(1)


@cli.command()
@click.argument("skill_id")
@click.option(
    "--harness",
    default="local_skillwiki",
    show_default=True,
    type=click.Choice(["local_skillwiki", "claude_code", "codex_cli", "codex"]),
)
@click.option(
    "--max-attempts",
    "--max-retries",
    default=1,
    type=click.IntRange(1, 5),
    show_default=True,
    help="Total attempts, including the first; --max-retries is a legacy alias",
)
@click.option("--allow-repair/--no-repair", default=False, show_default=True)
@click.option("--promote/--no-promote", default=False, show_default=True)
@click.option(
    "--test-cases",
    type=click.Path(exists=True),
    help="JSON array of native HarnessTestCase objects",
)
@click.option("--watch", is_flag=True)
@click.option("--json", "as_json", is_flag=True)
@click.option("--timeout", default=120, type=click.IntRange(1, 600), show_default=True)
@click.pass_context
def verify(
    ctx, skill_id, harness, max_attempts, allow_repair, promote, test_cases, watch, as_json, timeout
):
    """Run native verification; additional attempts/repair/promotion are opt-in."""
    body = dict(
        harness="codex_cli" if harness == "codex" else harness,
        max_attempts=max_attempts,
        promote_on_pass=promote,
        allow_repair=allow_repair,
        timeout_s=timeout,
    )
    if test_cases:
        body["test_cases"] = _read_json(test_cases, list)
    result = _api(ctx, f"/harness/{skill_id}/verify-loop", method="POST", json_body=body)
    if as_json:
        _print_json(result)
    else:
        click.echo(
            f"Verification: {result.get('status', 'unknown')}; attempts: {result.get('attempt_count', 0)}"
        )
        click.echo("Score: " + json.dumps(result.get("score", {}), ensure_ascii=False))
        click.echo(
            f"Promotion allowed: {result.get('promotion_allowed', False)}; state: {result.get('final_state', '')}"
        )
        click.echo(f"Evidence: {result.get('evidence_path', '')}")
        if watch:
            for attempt in result.get("attempts") or []:
                _print_json(attempt)
    if result.get("status") not in {"passed", "verified"}:
        ctx.exit(1)


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
    Use native state values S0 through S7; server governance gates still apply.

    \b
    Examples:
      skillwiki promote abc123 S3
      skillwiki promote abc123 S4
    """
    body = {"new_state": target_state}
    result = _api(ctx, f"/lifecycle/{skill_id}/transition", method="POST", json_body=body)
    new_state = result.get("lifecycle_state") or result.get("state") or target_state
    click.echo(click.style(f"[OK] {skill_id} -> {new_state}", fg="green"))


# ---------------------------------------------------------------------------
# skill group
# ---------------------------------------------------------------------------


@cli.group()
def skill():
    """Query and execute skills."""


@skill.command("list")
@click.option(
    "--state",
    type=click.Choice([f"S{i}" for i in range(8)]),
    help="Filter by native lifecycle state",
)
@click.option("--tag", default=None, help="Filter by tag")
@click.option("--limit", type=click.IntRange(1, 1000), default=20, show_default=True)
@click.pass_context
def skill_list(ctx: click.Context, state: Optional[str], tag: Optional[str], limit: int) -> None:
    """List skills in the wiki."""
    params = {"limit": limit}
    if state:
        params["state"] = state
    if tag:
        params["tags"] = tag
    qs = "?" + urlencode(params)
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
@click.option(
    "--full", is_flag=True, default=False, help="Show full schema including implementation"
)
@click.pass_context
def skill_get(ctx: click.Context, skill_id: str, full: bool) -> None:
    """Show details of a skill."""
    endpoint = f"/skills/{skill_id}/full" if full else f"/skills/{skill_id}"
    result = _api(ctx, endpoint)
    _print_json(result)


@skill.command("status")
@click.argument("skill_id")
@click.pass_context
def skill_status(ctx: click.Context, skill_id: str) -> None:
    """Show the current lifecycle state of a skill.

    \b
    Example:
      skillwiki skill status abc123
    """
    result = _api(ctx, f"/skills/{skill_id}")
    name = result.get("name") or result.get("skill_name") or skill_id
    state = result.get("lifecycle_state") or result.get("state") or "unknown"
    ver = result.get("version") or ""
    sr = result.get("success_rate")
    click.echo(f"Skill : {name}")
    click.echo(f"ID    : {skill_id}")
    click.echo(f"State : {state}" + (f"  (v{ver})" if ver else ""))
    if sr is not None:
        pct = sr * 100 if sr <= 1 else sr
        color = "green" if pct >= 90 else "yellow" if pct >= 70 else "red"
        click.echo(f"  Success rate : {click.style(f'{pct:.1f}%', fg=color)}")
    tags = result.get("tags") or []
    if tags:
        click.echo(f"  Tags  : {', '.join(tags)}")


@skill.command("exec")
@click.argument("skill_id")
@click.option("--input", "input_data", default="{}", show_default=True, help="JSON input object")
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
        click.echo(click.style(f"[FAIL] Invalid JSON input: {e}", fg="red"), err=True)
        sys.exit(1)

    if not isinstance(payload, dict):
        raise click.UsageError("--input must be a JSON object")
    body = {"skill_id": skill_id, "inputs": payload}
    click.echo(f"Executing skill {skill_id}...")
    result = _api(ctx, "/execution/skill", method="POST", json_body=body)
    status = result.get("status") or "unknown"
    icon = (
        click.style("[OK]", fg="green") if status == "success" else click.style("[FAIL]", fg="red")
    )
    click.echo(f"{icon} Status: {status}")
    output = result.get("outputs", result.get("output", result.get("result")))
    if output is not None:
        click.echo("Output:")
        _print_json(output)

    if status != "success":
        ctx.exit(1)


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
    body = {"goal": task}
    click.echo(f"Running task: {task}")
    result = _api(ctx, "/execution/plan", method="POST", json_body=body)
    status = result.get("status") or "unknown"
    icon = (
        click.style("[OK]", fg="green") if status == "success" else click.style("[FAIL]", fg="red")
    )
    click.echo(f"{icon} {status}")
    if verbose:
        for step in result.get("steps") or []:
            click.echo(f"  -> {step}")
    output = result.get("outputs", result.get("output", result.get("result")))
    if output is not None:
        if isinstance(output, (dict, list)):
            _print_json(output)
        else:
            click.echo(output)

    if status != "success":
        ctx.exit(1)


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("skill_id", required=False, default=None)
@click.option("--json", "as_json", is_flag=True, default=False, help="Output raw JSON")
@click.pass_context
def health(ctx: click.Context, skill_id: Optional[str], as_json: bool) -> None:
    """Show skill health (or system-wide health when no SKILL_ID given).

    \b
    Examples:
      skillwiki health
      skillwiki health abc123
      skillwiki health abc123 --json
    """
    if skill_id:
        result = _api(ctx, f"/evolution/health/{skill_id}")
        if as_json:
            _print_json(result)
            return
        name = result.get("skill_name") or skill_id
        status = result.get("status") or "unknown"
        sr = result.get("success_rate", 0.0)
        usage = result.get("usage_count", 0)
        latency = result.get("avg_latency_ms", 0.0)
        color = "green" if status == "healthy" else "yellow" if status == "degraded" else "red"
        click.echo(f"Skill   : {name}  ({skill_id})")
        click.echo(f"Status  : {click.style(status.upper(), fg=color)}")
        click.echo(f"  Success rate  : {sr * 100:.1f}%")
        click.echo(f"  Executions    : {usage}")
        click.echo(f"  Avg latency   : {latency:.0f}ms")
        for issue in result.get("issues") or []:
            click.echo(f"  {click.style('[WARN]', fg='yellow')} Issue : {issue}")
        for rec in result.get("recommendations") or []:
            click.echo(f"  {click.style('->', fg='cyan')} Recommend : {rec}")
        mp = result.get("maintenance_proposal")
        if mp:
            click.echo(
                f"  {click.style('!', fg='magenta')} Maintenance proposal available: "
                f"{mp.get('proposal_id', '')[:8]}  ({mp.get('recommended_action', '')})"
            )
    else:
        result = _api(ctx, "/evolution/health")
        if as_json:
            _print_json(result)
            return
        total = result.get("total_skills", 0)
        healthy = result.get("healthy_count", 0)
        degraded = result.get("degraded_count", 0)
        critical = result.get("critical_count", 0)
        stale = result.get("stale_count", 0)
        ratio = result.get("health_ratio", 1.0)
        color = "green" if ratio >= 0.9 else "yellow" if ratio >= 0.7 else "red"
        click.echo(f"System health  : {click.style(f'{ratio * 100:.1f}%', fg=color)}")
        click.echo(f"  Total     : {total}")
        click.echo(f"  Healthy   : {click.style(str(healthy), fg='green')}")
        click.echo(f"  Degraded  : {click.style(str(degraded), fg='yellow')}")
        click.echo(f"  Critical  : {click.style(str(critical), fg='red')}")
        click.echo(f"  Stale     : {stale}")
        for sr in result.get("skill_reports") or []:
            if sr.get("status") in ("degraded", "critical"):
                s = sr.get("status")
                c = "yellow" if s == "degraded" else "red"
                click.echo(f"  {click.style('!', fg='yellow')} {sr.get('skill_id')}  ({s})")


# ---------------------------------------------------------------------------
# proposal
# ---------------------------------------------------------------------------


@cli.group()
def proposal():
    """Manage maintenance proposals."""


@proposal.command("list")
@click.option("--status", default=None, help="Filter by status: pending | accepted | rejected")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def proposal_list(ctx: click.Context, status: Optional[str], as_json: bool) -> None:
    """List maintenance proposals.

    \b
    Example:
      skillwiki proposal list
      skillwiki proposal list --status pending
    """
    result = _api(ctx, "/evolution/proposals")
    proposals = result.get("proposals") or []
    if status:
        proposals = [p for p in proposals if p.get("status") == status]
    if as_json:
        _print_json(proposals)
        return
    if not proposals:
        click.echo("No proposals found.")
        return
    click.echo(f"{'ID':<10} {'Skill':<32} {'Action':<10} {'Status':<12} {'Confidence'}")
    click.echo("-" * 80)
    for p in proposals:
        pid = (p.get("proposal_id") or "")[:8]
        sid = (p.get("skill_id") or "")[:32]
        action = (p.get("recommended_action") or "")[:10]
        pstat = (p.get("status") or "")[:12]
        conf = p.get("confidence", 0.0)
        color = "green" if pstat == "accepted" else "red" if pstat == "rejected" else "yellow"
        click.echo(
            f"{pid:<10} {sid:<32} {action:<10} {click.style(pstat, fg=color):<12} {conf:.0%}"
        )
        rc = p.get("root_cause") or ""
        if rc:
            click.echo(f"  root_cause: {rc}")


@proposal.command("accept")
@click.argument("proposal_id")
@click.pass_context
def proposal_accept(ctx: click.Context, proposal_id: str) -> None:
    """Accept a maintenance proposal.

    \b
    Example:
      skillwiki proposal accept abc12345
    """
    result = _api(ctx, f"/evolution/proposals/{proposal_id}/accept", method="POST")
    click.echo(click.style(f"[OK] Proposal {proposal_id[:8]} accepted", fg="green"))
    click.echo(f"  Status: {result.get('status')}")


@proposal.command("reject")
@click.argument("proposal_id")
@click.pass_context
def proposal_reject(ctx: click.Context, proposal_id: str) -> None:
    """Reject a maintenance proposal.

    \b
    Example:
      skillwiki proposal reject abc12345
    """
    _api(ctx, f"/evolution/proposals/{proposal_id}/reject", method="POST")
    click.echo(click.style(f"[OK] Proposal {proposal_id[:8]} rejected", fg="yellow"))


# ---------------------------------------------------------------------------
# repair / evolve
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("skill_id")
@click.pass_context
def repair(ctx: click.Context, skill_id: str) -> None:
    """Generate a maintenance candidate for a degraded skill.

    Triggers a governed repair: inspects recent failures, creates a
    MaintenanceProposal, and queues it for human review.

    \b
    Example:
      skillwiki repair abc123
    """
    result = _api(ctx, f"/evolution/repair/{skill_id}", method="POST")
    pid = (result.get("proposal_id") or result.get("id") or "")[:8]
    action = result.get("recommended_action") or ""
    click.echo(click.style(f"[OK] Maintenance candidate generated for {skill_id}", fg="green"))
    if pid:
        click.echo(f"  Proposal : {pid}  action={action}")
    for key in ("root_cause", "patch_hint"):
        val = result.get(key) or ""
        if val:
            click.echo(f"  {key.replace('_', ' ').title()} : {val}")


@cli.command()
@click.option("--json", "as_json", is_flag=True, default=False, help="Output raw JSON")
@click.pass_context
def evolve(ctx: click.Context, as_json: bool) -> None:
    """Run one full evolution cycle across all skills.

    Scans the skill base, detects degraded/stale skills, generates
    MaintenanceProposals, and reports a summary.

    \b
    Example:
      skillwiki evolve
      skillwiki evolve --json
    """
    click.echo("Running evolution cycle...")
    result = _api(ctx, "/evolution/cycle", method="POST")
    if as_json:
        _print_json(result)
        return
    repaired = result.get("repaired") or []
    deprecated = result.get("deprecated") or []
    merged = result.get("merged") or []
    split_ = result.get("split") or []
    errors = result.get("errors") or []
    proposals = result.get("maintenance_proposals") or []
    click.echo(click.style("[OK] Evolution cycle complete", fg="green"))
    click.echo(f"  Repair candidates : {len(repaired)}")
    click.echo(f"  Deprecated        : {len(deprecated)}")
    click.echo(f"  Merged            : {len(merged)}")
    click.echo(f"  Split             : {len(split_)}")
    click.echo(f"  Proposals queued  : {len(proposals)}")
    if errors:
        click.echo(click.style(f"  Errors : {len(errors)}", fg="red"))
        for e in errors:
            click.echo(f"    - {e}")
    for p in proposals:
        pid = (p.get("proposal_id") or "")[:8]
        sid = p.get("skill_id") or ""
        action = p.get("recommended_action") or ""
        click.echo(f"  -> {sid}  [{pid}]  {action}")


# ---------------------------------------------------------------------------
# graph group
# ---------------------------------------------------------------------------


@cli.group()
def graph():
    """Inspect the skill knowledge graph."""


@graph.command("neighbors")
@click.argument("skill_id")
@click.option("--depth", default=1, show_default=True, help="Neighborhood depth (1-5)")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def graph_neighbors(ctx: click.Context, skill_id: str, depth: int, as_json: bool) -> None:
    """Show nodes directly connected to SKILL_ID.

    \b
    Example:
      skillwiki graph neighbors abc123
      skillwiki graph neighbors abc123 --depth 2
    """
    result = _api(ctx, f"/graph/view?skill_id={skill_id}&depth={depth}&view=skill_only")
    if as_json:
        _print_json(result)
        return
    nodes = result.get("nodes") or []
    edges = result.get("edges") or []
    center_ids = {skill_id}
    # include partial id matching
    center_nodes = [n for n in nodes if n.get("id") == skill_id or skill_id in (n.get("id") or "")]
    if center_nodes:
        cn = center_nodes[0]
        click.echo(f"Center: {cn.get('label') or cn.get('id')}  [{cn.get('type','')}]")
    else:
        click.echo(f"Center: {skill_id}")
    click.echo(f"Neighbors (depth={depth}):")
    connected_ids = set()
    for e in edges:
        src, tgt = e.get("source") or e.get("source_id"), e.get("target") or e.get("target_id")
        if src == skill_id:
            connected_ids.add(tgt)
        elif tgt == skill_id:
            connected_ids.add(src)
    node_map = {n.get("id"): n for n in nodes}
    for nid in connected_ids:
        n = node_map.get(nid) or {}
        label = n.get("label") or n.get("name") or nid
        ntype = n.get("type") or n.get("skill_type") or ""
        rel_edges = [e for e in edges if e.get("source") == nid or e.get("target") == nid]
        etypes = list({e.get("type") or e.get("edge_type") or "" for e in rel_edges})
        click.echo(f"  {label:<36} [{ntype}]  via {', '.join(filter(None, etypes))}")
    click.echo(f"\n  {len(connected_ids)} neighbor(s), {len(edges)} edge(s) in subgraph")


@graph.command("show")
@click.argument("skill_id")
@click.option(
    "--view",
    default="provenance",
    type=click.Choice(["skill_only", "provenance", "version_impact"], case_sensitive=False),
    show_default=True,
    help="Graph view to render",
)
@click.option("--depth", default=2, show_default=True)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def graph_show(ctx: click.Context, skill_id: str, view: str, depth: int, as_json: bool) -> None:
    """Show provenance/version graph around SKILL_ID.

    \b
    Example:
      skillwiki graph show abc123
      skillwiki graph show abc123 --view version_impact
    """
    result = _api(ctx, f"/graph/view?skill_id={skill_id}&depth={depth}&view={view}")
    if as_json:
        _print_json(result)
        return
    nodes = result.get("nodes") or []
    edges = result.get("edges") or []
    node_map = {n.get("id"): n for n in nodes}
    click.echo(f"Graph view: {view}  (depth={depth}  nodes={len(nodes)}  edges={len(edges)})")
    click.echo(f"{'Node':<36} {'Type':<20} {'Label'}")
    click.echo("-" * 80)
    for n in nodes:
        nid = (n.get("id") or "")[:36]
        ntype = (n.get("type") or n.get("skill_type") or "")[:20]
        label = n.get("label") or n.get("name") or ""
        marker = click.style("*", fg="cyan") if skill_id in nid else " "
        click.echo(f"{marker} {nid:<35} {ntype:<20} {label}")
    if edges:
        click.echo(f"\nEdges:")
        for e in edges[:20]:
            src = (e.get("source") or e.get("source_id") or "")[:20]
            tgt = (e.get("target") or e.get("target_id") or "")[:20]
            et = e.get("type") or e.get("edge_type") or ""
            click.echo(f"  {src}  --[{et}]-->  {tgt}")
        if len(edges) > 20:
            click.echo(f"  ... and {len(edges) - 20} more edges")


@graph.command("deps")
@click.argument("skill_id")
@click.option("--json", "as_json", is_flag=True, default=False)
@click.pass_context
def graph_deps(ctx: click.Context, skill_id: str, as_json: bool) -> None:
    """Show dependency chain for SKILL_ID.

    \b
    Example:
      skillwiki graph deps abc123
    """
    result = _api(ctx, f"/graph/{skill_id}/dependencies")
    if as_json:
        _print_json(result)
        return
    if not result:
        click.echo(f"No dependencies found for {skill_id}.")
        return
    click.echo(f"Dependency chain for {skill_id}:")
    for dep in result:
        sid = dep.get("skill_id") or ""
        name = dep.get("name") or sid
        ver = dep.get("version") or ""
        click.echo(f"  -> {name:<36} {sid}  v{ver}")


@graph.command("export")
@click.argument("skill_id")
@click.option("--output", "-o", default=None, help="Output file path (.json)")
@click.option(
    "--view",
    default="skill_only",
    type=click.Choice(["skill_only", "provenance", "version_impact"], case_sensitive=False),
    show_default=True,
)
@click.option("--depth", default=2, show_default=True)
@click.pass_context
def graph_export(
    ctx: click.Context, skill_id: str, output: Optional[str], view: str, depth: int
) -> None:
    """Export the subgraph around SKILL_ID as JSON.

    \b
    Example:
      skillwiki graph export abc123 -o subgraph.json
    """
    result = _api(ctx, f"/graph/view?skill_id={skill_id}&depth={depth}&view={view}")
    out_path = output or f"graph_{skill_id[:8]}_{view}.json"
    Path(out_path).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    nodes = len(result.get("nodes") or [])
    edges = len(result.get("edges") or [])
    click.echo(click.style(f"[OK] Exported {nodes} nodes, {edges} edges -> {out_path}", fg="green"))


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
def init(
    ctx: click.Context,
    api_key: str,
    api_url: str,
    model: str,
    temperature: Optional[float],
    max_tokens: Optional[int],
) -> None:
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
        click.echo(click.style("[OK] Config loaded", fg="green"))
        click.echo(f"  API URL : {llm.api_url}")
        click.echo(f"  Model   : {llm.model}")
        click.echo(f"  Agents  : {len(mgr.list_agent_types())}")
    except Exception as e:
        click.echo(click.style(f"[FAIL] {e}", fg="red"), err=True)
        if ctx.obj.get("debug"):
            raise
        sys.exit(1)


@cli.command("ping")
@click.option("--api-key", required=True, help="LLM API key")
@click.option("--api-url", default=None)
@click.option("--model", default=None)
@click.pass_context
def ping(ctx: click.Context, api_key: str, api_url: Optional[str], model: Optional[str]) -> None:
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
            click.echo(click.style(f"[OK] {msg}", fg="green"))
        else:
            click.echo(click.style(f"[FAIL] {msg}", fg="red"), err=True)
            sys.exit(1)
    except Exception as e:
        click.echo(click.style(f"[FAIL] {e}", fg="red"), err=True)
        sys.exit(1)


@cli.group()
def lifecycle():
    """Version, snapshot, evidence-bound release and recovery (native API)."""


def _file_action(group, name, endpoint, help_text):
    @group.command(name, help=help_text)
    @click.option(
        "--file",
        "payload_file",
        required=True,
        type=click.Path(exists=True),
        help="Native API request JSON object",
    )
    @click.pass_context
    def action(ctx, payload_file, skill_id=None):
        result = _api(
            ctx,
            endpoint.format(skill_id=skill_id),
            method="POST",
            json_body=_read_json(payload_file),
        )
        _print_json(result)
        if (
            result.get("success") is False
            or result.get("passed") is False
            or result.get("decision") in {"reject", "defer"}
        ):
            ctx.exit(1)

    if "{skill_id}" in endpoint:
        click.argument("skill_id")(action)
    return action


_file_action(
    ingest,
    "audit-candidate",
    "/ingest/audit-candidate",
    "Audit an exact edited candidate JSON payload; no persistence.",
)
_file_action(
    ingest,
    "create-candidate",
    "/ingest/create-candidate",
    "Persist a reviewed candidate; creation is not release qualification.",
)
_file_action(
    lifecycle,
    "new-version",
    "/lifecycle/{skill_id}/new-version",
    "Create a new draft identity; old verification does not transfer.",
)
_file_action(
    lifecycle,
    "release",
    "/lifecycle/{skill_id}/release-with-evidence",
    "Submit identity-bound evidence for a release decision.",
)
_file_action(
    lifecycle,
    "rollback",
    "/lifecycle/{skill_id}/rollback",
    "Create a separate recovery draft; verify and release it afresh.",
)
_file_action(
    lifecycle, "snapshot", "/lifecycle/{skill_id}/snapshot", "Commit a native governance snapshot."
)


@lifecycle.command("history")
@click.argument("skill_id")
@click.pass_context
def lifecycle_history(ctx, skill_id):
    """Read Git governance snapshot history."""
    _print_json(_api(ctx, f"/lifecycle/{skill_id}/snapshot/history"))


@lifecycle.command("diff")
@click.argument("skill_id")
@click.option("--from-ref", required=True)
@click.option("--to-ref", default="HEAD", show_default=True)
@click.pass_context
def lifecycle_diff(ctx, skill_id, from_ref, to_ref):
    """Inspect native snapshot differences between Git refs."""
    _print_json(
        _api(
            ctx,
            f"/lifecycle/{skill_id}/snapshot/diff?"
            + urlencode(dict(from_ref=from_ref, to_ref=to_ref)),
        )
    )


@skill.command("versions")
@click.argument("skill_id")
@click.pass_context
def skill_versions(ctx, skill_id):
    """List persisted versions."""
    _print_json(_api(ctx, f"/skills/{skill_id}/versions"))


@skill.command("search")
@click.argument("query")
@click.option(
    "--mode", type=click.Choice(["lexical", "rule", "hybrid", "semantic"]), default="lexical"
)
@click.option("--limit", type=click.IntRange(1, 100), default=20)
@click.pass_context
def skill_search(ctx, query, mode, limit):
    """Search skills using the configured repository capability."""
    _print_json(
        _api(
            ctx,
            "/skills/search",
            method="POST",
            json_body=dict(query=query, mode=mode, limit=limit),
        )
    )


@cli.group()
def execution():
    """Read execution receipts, retrieval traces and retained experience."""


@execution.command("history")
@click.pass_context
def execution_history(ctx):
    _print_json(_api(ctx, "/execution/history"))


@execution.command("get")
@click.argument("execution_id")
@click.pass_context
def execution_get(ctx, execution_id):
    _print_json(_api(ctx, f"/execution/history/{execution_id}"))


@execution.command("trace")
@click.argument("execution_id")
@click.pass_context
def execution_trace(ctx, execution_id):
    _print_json(_api(ctx, f"/execution/trace/{execution_id}"))


@execution.command("experience")
@click.argument("execution_id")
@click.pass_context
def execution_experience(ctx, execution_id):
    _print_json(_api(ctx, f"/execution/history/{execution_id}/experience"))


if __name__ == "__main__":
    cli()
