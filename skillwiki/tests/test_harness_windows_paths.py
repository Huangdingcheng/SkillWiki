"""Real local harness persistence past the Windows full filename boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from skillwiki.layers.skill_runtime.harness import HarnessTestCase, HarnessWorkspace, LocalSkillWikiHarness
from skillwiki.layers.skill_runtime.harness.workspace import HarnessEvidenceStore
from skillwiki.models.skill_model import Skill, SkillImplementation


def _root(base: Path, length: int) -> Path:
    path = base / "中文验证"
    while length - len(str(path)) > 100:
        path /= "w" * 80
    return path / ("w" * (length - len(str(path)) - 1))


def _inspect(path: Path) -> Path:
    return Path("\\\\?\\" + str(path)) if os.name == "nt" and not str(path).startswith("\\\\?\\") else path


@pytest.mark.parametrize("length", [150, 190, 300])
@pytest.mark.asyncio
async def test_local_harness_writes_all_artifacts_and_store_reads_same_root(tmp_path, length):
    root = _root(tmp_path, length)
    loop_id = "verify_loop_123456789abc"
    workspace = HarnessWorkspace(loop_id, root=root)
    skill = Skill(name="long_path_harness", description="Return an exact value.",
                  implementation=SkillImplementation(code="output['ok'] = True"))
    case = HarnessTestCase(test_id="case-1", name="path check", goal="return ok",
                           verifier_specs=[{"type": "json_equals", "path": "output.ok", "value": True}])
    result = await LocalSkillWikiHarness().run_skill(skill, case, workspace)
    assert result.status == "passed" and result.verifier_passed
    evidence = Path(result.evidence_path)
    for name in ("input.json", "verifier_specs.json", "output.json", "result.json"):
        assert json.loads(_inspect(evidence / name).read_text(encoding="utf-8")) is not None
    assert json.loads(_inspect(evidence / "output.json").read_text(encoding="utf-8"))["output"] == {"ok": True}
    workspace.write_json(root / loop_id / "result.json", {"loop_id": loop_id, "status": "verified"})
    workspace.write_text(root / loop_id / "中文备注.txt", "保留完整路径")
    expected = {"loop_id": loop_id, "status": "verified"}
    assert HarnessEvidenceStore(root).get(loop_id) == expected
    assert HarnessEvidenceStore(root).list_recent() == [expected]
    assert _inspect(root / loop_id / "中文备注.txt").read_text(encoding="utf-8") == "保留完整路径"


def test_long_harness_reader_does_not_misreport_missing_evidence(tmp_path):
    root = _root(tmp_path, 300)
    target = _inspect(root / "loop" / "result.json")
    target.parent.mkdir(parents=True)
    target.write_text('{"status": "failed"}', encoding="utf-8")
    assert HarnessEvidenceStore(root).get("loop") == {"status": "failed"}
    assert HarnessEvidenceStore(root).list_recent() == [{"status": "failed"}]
