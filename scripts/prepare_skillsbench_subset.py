from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


REPO = "benchflow-ai/skillsbench"
BRANCH = "main"
API_ROOT = f"https://api.github.com/repos/{REPO}"
RAW_ROOT = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts" / "skillsbench-runs"
DEFAULT_TASK_IDS = [
    "citation-check",
    "sales-pivot-analysis",
    "software-dependency-audit",
    "court-form-filling",
    "dialogue-parser",
]
ROOT_FILES = ["README.md", "pyproject.toml", "uv.lock", "taxonomy.yaml", "taxonomy.md", "LICENSE"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a lightweight SkillsBench subset without cloning the full repository. "
            "Downloads root metadata plus selected task directories into an isolated output folder."
        )
    )
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--run-id", default=datetime.now().strftime("%Y%m%d-%H%M%S"))
    parser.add_argument("--task", action="append", dest="tasks", help="Task id to include. Repeatable.")
    parser.add_argument("--max-file-bytes", type=int, default=2_000_000)
    parser.add_argument("--max-files-per-task", type=int, default=0, help="Limit downloaded files per task after core files are prioritized.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_root).resolve() / f"skillsbench-subset-{args.run_id}"
    output_dir.mkdir(parents=True, exist_ok=True)
    task_ids = normalize_task_ids(args.tasks or DEFAULT_TASK_IDS)
    repo_head = get_repo_head()
    downloaded: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for root_file in ROOT_FILES:
        entry = fetch_content_entry(root_file)
        if not entry:
            skipped.append({"path": root_file, "reason": "not found"})
            continue
        download_entry(entry, output_dir, args.max_file_bytes, downloaded, skipped, dry_run=args.dry_run)

    for task_id in task_ids:
        selected_entries, skipped_entries = select_task_entries(
            task_id,
            list_tree(f"tasks/{task_id}"),
            max_files_per_task=args.max_files_per_task,
        )
        skipped.extend(skipped_entries)
        for entry in selected_entries:
            download_entry(entry, output_dir, args.max_file_bytes, downloaded, skipped, dry_run=args.dry_run)

    manifest = build_manifest(
        output_dir=output_dir,
        task_ids=task_ids,
        downloaded=downloaded,
        skipped=skipped,
        repo_head=repo_head,
    )
    write_json(output_dir / "skillsbench_subset_manifest.json", manifest)
    (output_dir / "RUN_COMMANDS.md").write_text(render_run_commands(output_dir, task_ids), encoding="utf-8")
    print(json.dumps({
        "output_dir": str(output_dir),
        "repo_head": repo_head,
        "task_ids": task_ids,
        "downloaded_count": len(downloaded),
        "skipped_count": len(skipped),
        "dry_run": args.dry_run,
    }, indent=2, ensure_ascii=False))
    return 0


def normalize_task_ids(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        task = str(value or "").strip().strip("/\\")
        if not task or task in seen:
            continue
        seen.add(task)
        result.append(task)
    return result


def safe_repo_path(value: str) -> Path:
    raw = str(value or "").replace("\\", "/")
    posix = PurePosixPath(raw)
    if posix.is_absolute() or ":" in raw:
        raise ValueError(f"Unsafe repository path: {value!r}")
    parts = posix.parts
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Unsafe repository path: {value!r}")
    return Path(*parts)


def get_repo_head() -> str:
    refs = http_json(f"{API_ROOT}/git/ref/heads/{BRANCH}")
    if isinstance(refs, dict):
        obj = refs.get("object") if isinstance(refs.get("object"), dict) else {}
        sha = obj.get("sha")
        if isinstance(sha, str):
            return sha
    return ""


def fetch_content_entry(path: str) -> dict[str, Any] | None:
    encoded = urllib.parse.quote(path)
    data = http_json(f"{API_ROOT}/contents/{encoded}?ref={BRANCH}")
    return data if isinstance(data, dict) else None


def list_tree(path: str) -> list[dict[str, Any]]:
    entries = http_json(f"{API_ROOT}/contents/{urllib.parse.quote(path)}?ref={BRANCH}")
    if not isinstance(entries, list):
        return []
    result: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "file":
            result.append(entry)
        elif entry.get("type") == "dir":
            result.extend(list_tree(str(entry.get("path") or "")))
    return result


def select_task_entries(
    task_id: str,
    entries: list[dict[str, Any]],
    *,
    max_files_per_task: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ordered = sorted(entries, key=lambda entry: task_entry_priority(task_id, str(entry.get("path") or "")))
    if max_files_per_task <= 0 or len(ordered) <= max_files_per_task:
        return ordered, []
    selected = ordered[:max_files_per_task]
    selected_paths = {str(entry.get("path") or "") for entry in selected}
    skipped = [
        {"path": str(entry.get("path") or ""), "reason": "max files per task reached"}
        for entry in ordered
        if str(entry.get("path") or "") not in selected_paths
    ]
    return selected, skipped


def task_entry_priority(task_id: str, path: str) -> tuple[int, str]:
    name = PurePosixPath(path).name.lower()
    if name in {"task.yaml", "task.yml", "task.json"}:
        return (0, path)
    if name in {"readme.md", "instructions.md", "prompt.md"}:
        return (1, path)
    if "oracle" in name:
        return (2, path)
    if "verifier" in name or "grader" in name or "eval" in name:
        return (3, path)
    if path.startswith(f"tasks/{task_id}/skills/"):
        return (4, path)
    return (9, path)


def download_entry(
    entry: dict[str, Any],
    output_dir: Path,
    max_file_bytes: int,
    downloaded: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    *,
    dry_run: bool,
) -> None:
    path = str(entry.get("path") or "")
    try:
        relative = safe_repo_path(path)
    except ValueError as exc:
        skipped.append({"path": path, "reason": str(exc)})
        return
    size = int(entry.get("size") or 0)
    if size > max_file_bytes:
        skipped.append({"path": path, "size": size, "sha": entry.get("sha"), "reason": "too large"})
        return
    download_url = entry.get("download_url") or f"{RAW_ROOT}/{urllib.parse.quote(path)}"
    target = output_dir / relative
    if not dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        body = http_bytes(str(download_url))
        target.write_bytes(body)
        size = len(body)
    downloaded.append({
        "path": path,
        "size": size,
        "sha": entry.get("sha"),
        "download_url": download_url,
        "local_path": str(target),
        "dry_run": dry_run,
    })


def build_manifest(
    *,
    output_dir: Path,
    task_ids: list[str],
    downloaded: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    repo_head: str,
) -> dict[str, Any]:
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "repo": REPO,
        "branch": BRANCH,
        "repo_head": repo_head,
        "output_dir": str(output_dir),
        "task_ids": task_ids,
        "downloaded_count": len(downloaded),
        "skipped_count": len(skipped),
        "downloaded": downloaded,
        "skipped": skipped,
        "isolation_policy": (
            "This subset is an isolated benchmark workspace. Do not commit run outputs or copy "
            "large task assets into the SkillOS source tree."
        ),
    }


def render_run_commands(output_dir: Path, task_ids: list[str]) -> str:
    lines = [
        "# SkillsBench Subset Run Commands",
        "",
        "Run these from the subset directory after installing `uv` and BenchFlow dependencies.",
        "",
        "```powershell",
        f"cd {output_dir}",
        "uv sync --locked",
        "```",
        "",
        "Oracle sanity checks:",
        "",
        "```powershell",
    ]
    for task_id in task_ids:
        lines.append(f"uv run bench tasks check tasks/{task_id}")
        lines.append(f"uv run bench eval create -t tasks/{task_id} -a oracle")
    lines.extend([
        "```",
        "",
        "SkillOS adapter runs should write their reports outside the raw task directories, under an eval run folder.",
        "",
    ])
    return "\n".join(lines)


def http_json(url: str) -> Any:
    body = http_bytes(url).decode("utf-8", errors="replace")
    return json.loads(body)


def http_bytes(url: str) -> bytes:
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "SkillOS-subset-preparer")
    last_error: Exception | None = None
    for _ in range(2):
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                return resp.read()
        except urllib.error.HTTPError:
            raise
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"Failed to fetch {url}: {last_error}")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
