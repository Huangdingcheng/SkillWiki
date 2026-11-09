"""Real filesystem regressions for immutable receipts at Windows path limits."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from skillwiki.api import execution_history as storage


def _directory_at_length(base: Path, length: int) -> Path:
    directory = base / "中文回执"
    while length - len(str(directory)) > 100:
        directory /= "r" * 80
    padding = length - len(str(directory)) - 1
    assert padding > 0
    return directory / ("r" * padding)


def _receipt(output: str = "original") -> dict:
    return {"execution_id": "receipt:路径/../identity",
            "created_at": "2026-10-31T00:00:00+00:00",
            "status": "failed", "output": output}


def _inspect(path: Path) -> Path:
    # Independent inspection also needs the OS extended syntax on Windows.
    return Path("\\\\?\\" + str(path)) if os.name == "nt" else path


@pytest.mark.parametrize("directory_length", [189, 190, 191, 300])
def test_full_filename_boundary_save_reload_and_immutable_duplicates(tmp_path, directory_length):
    # The hashed basename adds 70 characters including the path separator.
    # At 190 the root itself is short, but the target reaches MAX_PATH.
    directory = _directory_at_length(tmp_path, directory_length)
    row = _receipt()
    target = directory / (hashlib.sha256(row["execution_id"].encode()).hexdigest() + ".json")
    assert len(str(target)) == directory_length + 70
    assert storage.save(directory, row) == row
    target = _inspect(target)
    original_bytes = target.read_bytes()
    assert storage.load(directory) == [row]
    assert storage.save(directory, dict(row, created_at="later")) == row
    with pytest.raises(ValueError, match="different immutable evidence"):
        storage.save(directory, _receipt("changed"))
    assert target.read_bytes() == original_bytes
    assert list(_inspect(directory).glob(".pending-*")) == []
    assert len(list(_inspect(directory).glob("*.json"))) == 1


def test_relative_long_path_keeps_store_location(tmp_path, monkeypatch):
    directory = _directory_at_length(tmp_path, 300)
    monkeypatch.chdir(tmp_path)
    relative = directory.relative_to(tmp_path)
    relative = Path("unused") / ".." / relative
    row = _receipt()
    assert storage.load(relative) == []
    assert storage.save(relative, row) == row
    assert storage.load(directory) == storage.load(relative) == [row]


@pytest.mark.skipif(os.name != "nt", reason="Windows extended path syntax")
def test_existing_extended_path_is_supported(tmp_path):
    ordinary = _directory_at_length(tmp_path, 300)
    extended = Path("\\\\?\\" + str(ordinary))
    row = _receipt()
    assert storage.save(extended, row) == row
    assert storage.load(ordinary) == storage.load(extended) == [row]


def test_failed_long_path_publication_removes_pending_file(tmp_path, monkeypatch):
    directory = _directory_at_length(tmp_path, 300)
    def denied(*args):
        raise PermissionError("injected publication failure")
    monkeypatch.setattr(storage.os, "link", denied)
    with pytest.raises(PermissionError):
        storage.save(directory, _receipt())
    assert list(_inspect(directory).iterdir()) == []
    assert storage.load(directory) == []


def test_long_path_load_preserves_order_and_rejects_corrupt_identity(tmp_path):
    directory = _directory_at_length(tmp_path, 300)
    later = dict(_receipt(), execution_id="later", created_at="2026-11-01")
    earlier = dict(_receipt(), execution_id="earlier", created_at="2026-10-31")
    storage.save(directory, later)
    storage.save(directory, earlier)
    assert storage.load(directory) == [earlier, later]
    (_inspect(directory) / "wrong-identity.json").write_text(json.dumps(earlier))
    with pytest.raises(ValueError, match="identity is corrupt"):
        storage.load(directory)


@pytest.mark.parametrize("same_evidence", [False, True])
def test_cross_process_publication_is_atomic_and_never_overwrites(tmp_path, same_evidence):
    directory = _directory_at_length(tmp_path, 300)
    gate = tmp_path / "release"
    source = str(Path(storage.__file__).resolve())
    # Synchronize immediately before the real hard link. Both writers see a
    # missing target, then race the OS primitive; no persistence result is faked.
    script = r'''
import json, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(sys.argv[1]).parents[2]))
from skillwiki.api import execution_history as storage
directory, ready, gate = map(pathlib.Path, sys.argv[2:5])
original_link = storage.os.link
def synchronized_link(source, target):
    ready.write_text("ready")
    deadline = time.monotonic() + 15
    while not gate.exists():
        if time.monotonic() > deadline:
            raise TimeoutError("publication barrier")
        time.sleep(0.01)
    original_link(source, target)
storage.os.link = synchronized_link
try:
    print(json.dumps({"saved": storage.save(directory, json.loads(sys.argv[5]))}))
except ValueError as error:
    print(json.dumps({"refused": str(error)}))
'''
    rows = [_receipt(), _receipt("original" if same_evidence else "contender")]
    rows[1]["created_at"] = "2026-10-31T00:00:01+00:00"
    processes = []
    try:
        for index, row in enumerate(rows):
            processes.append(subprocess.Popen(
                [sys.executable, "-c", script, source, str(directory),
                 str(tmp_path / f"ready-{index}"), str(gate), json.dumps(row)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8",
            ))
        deadline = time.monotonic() + 20
        while not all((tmp_path / f"ready-{i}").exists() for i in range(2)):
            assert time.monotonic() < deadline, "writers did not reach publication"
            assert all(process.poll() is None for process in processes)
            time.sleep(0.01)
        gate.write_text("publish")
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, stderr
            results.append(json.loads(stdout))
        saved = [result["saved"] for result in results if "saved" in result]
        refused = [result["refused"] for result in results if "refused" in result]
        assert len(saved) == (2 if same_evidence else 1)
        assert len(refused) == (0 if same_evidence else 1)
        assert all("different immutable evidence" in error for error in refused)
        assert storage.load(directory) == [saved[0]]
        assert list(_inspect(directory).glob(".pending-*")) == []
        assert len(list(_inspect(directory).glob("*.json"))) == 1
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
