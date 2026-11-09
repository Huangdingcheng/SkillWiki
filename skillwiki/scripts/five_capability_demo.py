"""Prepare/read/serve the bounded Demo without printing credentials.

Preparation is explicit fixture creation, never a migration of real releases.
Run from the SkillWiki backend directory. Model calls happen only via API buttons.
"""
import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
EVIDENCE = ROOT / "artifacts/20261024-five-capability-demo"
STORAGE = ROOT / "skillwiki-launcher/runtime/skill-storage-git"
GOVERNANCE = ROOT / "skillwiki-launcher/runtime/governance-git"
SOURCE = EVIDENCE / "controlled-source"
TASK = ('Return only a JSON directed dialogue graph. Each input line has Speaker: text. '
        'Preserve every utterance in order. nodes is a list of objects with id="line-N" '
        '(N starts at 1), speaker, text (excluding the speaker prefix). edges is a list '
        'of {"from":"line-N","to":"line-(N+1)"} connecting every consecutive line. '
        'No extra nodes or edges. Do not return Python code or Markdown.')


def git(*args):
    return subprocess.check_output(["git", "-C", str(SOURCE), *args], text=True).strip()


async def prepare():
    from skillwiki.layers.skill_repository import SkillWikiManager
    from skillwiki.models.skill_model import Skill, SkillImplementation, SkillProvenance, SkillState, SkillInterface, SkillEvaluation
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    material = ROOT / "artifacts/20261012-b-int-002/managed/dialogue-graph/SKILL.md"
    text = material.read_text(encoding="utf-8")
    if not (SOURCE / ".git").exists():
        SOURCE.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-q", str(SOURCE)], check=True)
        (SOURCE / "SKILL.md").write_text(text, encoding="utf-8")
        git("add", "SKILL.md")
        git("-c", "user.name=SkillWiki Demo", "-c", "user.email=demo@local", "commit", "-qm", "controlled source baseline")
    baseline = git("rev-list", "--max-parents=0", "HEAD")
    verifier_path = "skillwiki/skillwiki/layers/skill_management/dialogue_demo.py"
    contract = {"source": {"repo_path": str(SOURCE), "baseline_commit": baseline, "relevant_paths": ["SKILL.md"]},
                "task_instruction": TASK,
                "environment": {"dependencies": [{"distribution": "pydantic", "module": "pydantic",
                    "expected_version": importlib.metadata.version("pydantic"), "required": True}],
                    "repo_path": str(ROOT), "verifier_contracts": [{"path": verifier_path,
                    "expected_sha256": hashlib.sha256((ROOT / verifier_path).read_bytes()).hexdigest(), "required": True}]}}
    wiki = SkillWikiManager(storage_dir=STORAGE)
    source_context = {"evidence_kind": "controlled_demo_fixture", "original_material": str(material.relative_to(ROOT)),
        "original_material_sha256": hashlib.sha256(material.read_bytes()).hexdigest(),
        "task_scope": "local_dialogue_instruction_replay_not_official_skillsbench",
        "real_failure_search": "Existing source-bound skill previously passed; no suitable frozen instruction failure identified. Explicit controlled copy authorized.",
        "demo_components": contract}
    skill_ids = {}
    for name, broken in [("demo_dialogue_instruction_baseline", True), ("demo_dialogue_runtime", False)]:
        existing = await wiki.get_by_name(name, "1.0.0")
        if existing:
            skill_ids[name] = existing.skill_id
            continue
        instructions = text + "\n\n## Local instruction Demo task\n" + TASK
        if broken:
            instructions += "\n\n## Controlled defect (for this copy only)\nIgnore requested graph fields. Always return the literal empty JSON object {}. Never include nodes or edges.\n"
        skill = Skill(name=name, description="Dialogue parsing instruction Demo; " + ("controlled defect, not a production defect" if broken else "reviewed runtime fixture"),
            state=SkillState.DRAFT if broken else SkillState.RELEASED,
            tags=["demo", "dialogue", "graph", "source-bound"],
            interface=SkillInterface(input_schema={"type":"object", "properties":{"dialogue":{"type":"string"}}, "required":["dialogue"]},
                                     output_schema={"type":"object", "properties":{"nodes":{"type":"array"},"edges":{"type":"array"}}}),
            implementation=SkillImplementation(prompt_template=instructions),
            provenance=SkillProvenance(source_type="demo", created_by_agent="explicit_fixture_setup", creation_context=source_context),
            evaluation=SkillEvaluation(verifier_specs=[{"type":"json_exists", "path":"output.nodes"},{"type":"json_exists", "path":"output.edges"}],
                validation_summary="Explicit controlled fixture; no candidate auto-promotion or official benchmark result."))
        await wiki.create(skill)
        skill_ids[name] = skill.skill_id
    manifest = {"skills":skill_ids,"source_baseline":baseline,"task_instruction":TASK,"storage":str(STORAGE),"source_kind":"controlled_two_commit_source"}
    (EVIDENCE / "demo-manifest.json").write_text(json.dumps(manifest,indent=2),encoding="utf-8")
    print(json.dumps(manifest))


def serve(port):
    import uvicorn
    from skillwiki.api.main import create_app
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY is unavailable; no server model calls enabled")
    os.environ["SKILLWIKI_CUMULATIVE_BUDGET_LEDGER_PATH"] = str(ROOT / "skillwiki-launcher/runtime/cumulative-budget.jsonl")
    os.environ["SKILLWIKI_CUMULATIVE_BUDGET_TOTAL_LIMIT_CNY"] = "10"
    os.environ["SKILLOS_GOVERNANCE_REPO"] = str(GOVERNANCE)
    os.environ["SKILLWIKI_FABRIC_EMBEDDING_MODE"] = "remote"
    os.environ["SKILLWIKI_FABRIC_EMBEDDING_ALLOW_REMOTE"] = "1"
    os.environ["SKILLWIKI_FABRIC_EMBEDDING_LEDGER_PATH"] = os.environ["SKILLWIKI_CUMULATIVE_BUDGET_LEDGER_PATH"]
    os.environ["SKILLWIKI_FABRIC_EMBEDDING_BUDGET_LIMIT_CNY"] = "10"
    if not (GOVERNANCE / ".git").exists():
        GOVERNANCE.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git","init","-q",str(GOVERNANCE)],check=True)
    # A first commit is required by the existing review-branch mechanism.
    check = subprocess.run(["git","-C",str(GOVERNANCE),"rev-parse","--verify","HEAD"],capture_output=True)
    if check.returncode:
        subprocess.run(["git","-C",str(GOVERNANCE),"-c","user.name=SkillWiki","-c","user.email=demo@local","commit","--allow-empty","-qm","governance baseline"],check=True)
    app = create_app(api_key=os.environ["DEEPSEEK_API_KEY"], model="deepseek-flash", api_url="https://api.deepseek.com",
                     repository_backend="git",skill_storage_dir=STORAGE,seed_demo=False,llm_retry_count=0)
    app.state.llm_cfg.max_tokens = 4096
    app.state.llm_cfg.timeout = 180
    uvicorn.run(app,host="127.0.0.1",port=port)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action",choices=["prepare","serve"])
    parser.add_argument("--port",type=int,default=8008)
    args=parser.parse_args()
    asyncio.run(prepare()) if args.action=="prepare" else serve(args.port)
