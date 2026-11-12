"""Run the existing SkillWiki API and UI in one Docker Space.

No model request is performed during startup or health checks.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request


def runtime_environment(source: dict[str, str]) -> tuple[dict[str, str], Path]:
    env = dict(source)
    key = env.get("LLM_API_KEY", "").strip()
    if not key or key.lower() in {"your-api-key", "demo-placeholder-key", "replace-with-your-own-key"}:
        raise ValueError("Set LLM_API_KEY as a Space runtime Secret before starting.")
    try:
        budget = float(env.get("SPACE_MODEL_BUDGET_CNY", ""))
    except ValueError as exc:
        raise ValueError("Set SPACE_MODEL_BUDGET_CNY to the approved positive spending ceiling.") from exc
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError("SPACE_MODEL_BUDGET_CNY must be a positive finite number.")
    raw_root = Path(env.get("SPACE_DATA_DIR", "/data")).expanduser()
    if not raw_root.is_absolute():
        raise ValueError("SPACE_DATA_DIR must be an absolute writable local filesystem path.")
    root = raw_root.resolve()
    env.update({
        "LLM_RETRY_COUNT": "0",
        "SKILLOS_GOVERNANCE_REPO": str(root / "governance"),
        "SKILLWIKI_STORAGE_DIR": str(root / "skills"),
        "SKILLWIKI_CUMULATIVE_BUDGET_LEDGER_PATH": str(root / "budget" / "model-usage.jsonl"),
        "SKILLWIKI_CUMULATIVE_BUDGET_TOTAL_LIMIT_CNY": str(budget),
    })
    env.setdefault("LLM_API_URL", "https://api.deepseek.com")
    env.setdefault("LLM_MODEL", "deepseek-flash")
    env.setdefault("LLM_MAX_TOKENS", "16384")
    env.setdefault("LLM_TIMEOUT", "120")
    # This image uses the product's priced native model route. Unknown providers
    # fail closed, rather than silently bypassing its cumulative budget guard.
    from skillwiki.config.llm_config import LLMConfig
    from skillwiki.utils.llm_budget import PersistentLLMBudget
    config = LLMConfig(
        api_key=key, api_url=env["LLM_API_URL"], model=env["LLM_MODEL"],
        max_tokens=int(env["LLM_MAX_TOKENS"]), timeout=int(env["LLM_TIMEOUT"]), retry_count=0,
    )
    PersistentLLMBudget.validate_route(config)
    return env, root


def prepare_storage(root: Path, app_root: Path = Path("/app")) -> None:
    for name in ("skills", "governance", "receipts", "budget", "work"):
        (root / name).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=root) as marker:
        marker.write(b"ok")
    governance = root / "governance"
    if not (governance / ".git").exists():
        subprocess.run(["git", "init", "--quiet", str(governance)], check=True)
    subprocess.run(["git", "-C", str(governance), "config", "user.name", "SkillWiki Demo"], check=True)
    subprocess.run(["git", "-C", str(governance), "config", "user.email", "demo@skillwiki.invalid"], check=True)
    # HarnessWorkspace derives /app/artifacts from the editable package path.
    # Preserve that existing product convention while persisting its receipts.
    link, target = app_root / "artifacts", root / "receipts"
    if link.is_symlink():
        if link.resolve() != target.resolve():
            raise ValueError("Existing artifact link targets a different data store.")
    elif link.exists():
        raise ValueError("Refusing to replace existing /app/artifacts data.")
    else:
        link.symlink_to(target, target_is_directory=True)


def main() -> int:
    children: list[subprocess.Popen] = []
    stopping = False

    def request_stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        env, root = runtime_environment(dict(os.environ))
        prepare_storage(root)
        print("Starting SkillWiki with an empty-by-default shared Git store; data root: " + str(root), flush=True)
        backend = subprocess.Popen(
            [sys.executable, "-m", "skillwiki.api.main", "--host", "127.0.0.1",
             "--port", "8000", "--repository-backend", "git",
             "--skill-storage-dir", str(root / "skills"), "--llm-retry-count", "0", "--no-seed-demo"],
            cwd=root / "work", env=env,
        )
        children.append(backend)
        ready = False
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(60):
            if stopping or backend.poll() is not None:
                break
            try:
                with opener.open("http://127.0.0.1:8000/health", timeout=1) as response:
                    ready = response.status == 200 and json.load(response).get("status") == "ok"
                if ready:
                    break
            except (OSError, ValueError):
                pass
            time.sleep(1)
        if not ready:
            raise RuntimeError("Backend did not become ready; inspect startup logs.")
        subprocess.run(["nginx", "-t", "-c", "/app/space/nginx.conf"], check=True)
        children.append(subprocess.Popen(["nginx", "-c", "/app/space/nginx.conf", "-g", "daemon off;"]))
        while not stopping:
            for child in children:
                if child.poll() is not None:
                    raise RuntimeError("A required server exited; stopping the container.")
            time.sleep(0.5)
        return 0
    except Exception as exc:
        # Do not print provider keys or a complete environment.
        print("SkillWiki Space startup failed: " + str(exc), file=sys.stderr, flush=True)
        return 1
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
