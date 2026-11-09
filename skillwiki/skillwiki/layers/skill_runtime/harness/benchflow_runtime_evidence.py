"""Produce a small, unsigned runtime attestation for a BenchFlow rollout.

The attestation is an execution-bound witness, not a cryptographic signature or
an authenticity guarantee about its author.  The reader verifies its contents
against the rollout bytes before using it.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata as importlib_metadata
import json
import platform
import sys
import subprocess
from pathlib import Path
from typing import Any, Mapping


RUNTIME_ATTESTATION_FILENAME = "runtime-evidence.json"
RUNTIME_ATTESTATION_SCHEMA = "skillwiki.m3.benchflow-runtime-evidence.v1"
RUNTIME_ATTESTATION_KIND = "benchflow_runtime_attestation"
FIXED_WHEEL_FILENAME = "benchflow-0.6.3-py3-none-any.whl"
FIXED_WHEEL_SHA256 = "e032bcb4894dd3f59f98204366a7282e5ae6058289d1bd8c2fce56e2c972efc6"
CURRENT_BENCHFLOW_VERSION = "0.7.9.dev0"
SUPPORTED_BENCHFLOW_VERSIONS = frozenset({"0.6.3", CURRENT_BENCHFLOW_VERSION})
SOURCE_RUNTIME_FILENAME = "runtime-source-evidence.json"
SOURCE_RUNTIME_SCHEMA = "skillwiki.m2.benchflow-source-runtime-evidence.v1"
SOURCE_RUNTIME_KIND = "benchflow_source_runtime_attestation"


def source_manifest_digest(files: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def measure_source_runtime(
    checkout_root: str | Path, package_name: str = "benchflow"
) -> dict[str, Any]:
    """Measure the actually imported editable checkout in this interpreter."""
    root = Path(checkout_root).resolve()
    package = importlib.import_module(package_name)
    imported = Path(package.__file__).resolve()
    expected = root / "src" / package_name
    if not imported.is_relative_to(expected) or not imported.is_file():
        raise ValueError("imported runtime is not the requested source checkout")
    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *args], text=True, encoding="utf-8"
        ).strip()
    if Path(git("rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("runtime checkout must be the Git root")
    commit = git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain", "--untracked-files=all", "--",
                f"src/{package_name}", "pyproject.toml")
    if dirty:
        raise ValueError("runtime source must be committed before measurement")
    paths = sorted([*expected.rglob("*.py"), root / "pyproject.toml"])
    files = []
    for path in paths:
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError("runtime source contains an invalid file")
        files.append({"path": path.relative_to(root).as_posix(),
                      "sha256": _sha256(path), "size": path.stat().st_size})
    files.sort(key=lambda item: item["path"])
    return {
        "runtime_kind": "editable_source_checkout",
        "benchflow_version": str(importlib_metadata.version(package_name)),
        "package_name": package_name,
        "package_location": str(expected.resolve()),
        "import_path": str(imported),
        "checkout_root": str(root),
        "checkout_commit": commit,
        "source_dirty": False,
        "source_files": files,
        "source_tree_sha256": source_manifest_digest(files),
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": platform.python_version(),
    }


def write_source_runtime_attestation(
    run_dir: str | Path, *, checkout_root: str | Path,
    expected_measurement: Mapping[str, Any] | None = None,
    package_name: str = "benchflow",
) -> dict[str, Any]:
    """Bind a measured source runtime to raw files; never invent wheel identity."""
    root = Path(run_dir).resolve()
    measured = measure_source_runtime(checkout_root, package_name)
    if expected_measurement is not None:
        for key, value in measured.items():
            if expected_measurement.get(key) != value:
                raise ValueError(f"runtime changed since admission: {key}")
    if (root / RUNTIME_ATTESTATION_FILENAME).exists():
        raise FileExistsError("run already has a wheel runtime witness")
    payload = {"schema_version": SOURCE_RUNTIME_SCHEMA,
               "kind": SOURCE_RUNTIME_KIND, "run_id": root.name,
               "run_path": str(root), **measured}
    for name in ("result", "config"):
        path = root / f"{name}.json"
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(f"required run file is missing: {path.name}")
        payload[f"{name}_sha256"] = _sha256(path)
        payload[f"{name}_size"] = path.stat().st_size
    with (root / SOURCE_RUNTIME_FILENAME).open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return payload


def write_runtime_attestation(
    run_dir: str | Path,
    *,
    wheel_path: str | Path,
    package_name: str = "benchflow",
) -> dict[str, Any]:
    """Measure the installed runtime and bind it to this run's raw JSON bytes.

    ``wheel_path`` must be the exact wheel used by the runtime.  No expected
    version is accepted: the installed distribution version is observed.
    """

    root = Path(run_dir).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"run directory does not exist: {root}")
    result_path = root / "result.json"
    config_path = root / "config.json"
    for path in (result_path, config_path):
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(f"required run file is missing: {path.name}")

    wheel = Path(wheel_path).resolve()
    if not wheel.is_file() or wheel.is_symlink():
        raise FileNotFoundError(f"wheel file does not exist: {wheel}")
    distribution = importlib_metadata.distribution(package_name)
    version = str(distribution.version).strip()
    if not version:
        raise ValueError("installed distribution has no version")

    payload: dict[str, Any] = {
        "schema_version": RUNTIME_ATTESTATION_SCHEMA,
        "kind": RUNTIME_ATTESTATION_KIND,
        "run_id": root.name,
        "run_path": str(root),
        "benchflow_version": version,
        "package_name": package_name,
        "package_location": str(Path(distribution.locate_file("")).resolve()),
        "wheel_filename": wheel.name,
        "wheel_sha256": _sha256(wheel),
        "python_executable": str(Path(sys.executable).resolve()),
        "python_version": platform.python_version(),
        "result_sha256": _sha256(result_path),
        "config_sha256": _sha256(config_path),
        "result_size": result_path.stat().st_size,
        "config_size": config_path.stat().st_size,
    }
    target = root / RUNTIME_ATTESTATION_FILENAME
    # Exclusive creation prevents a later process from silently replacing the
    # witness for an already-materialized rollout.
    with target.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument("--package", default="benchflow")
    args = parser.parse_args(argv)
    payload = write_runtime_attestation(
        args.run_dir,
        wheel_path=args.wheel,
        package_name=args.package,
    )
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by the real command
    raise SystemExit(_main())
