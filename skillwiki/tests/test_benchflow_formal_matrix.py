from fastapi import FastAPI
from fastapi.testclient import TestClient
from skillwiki.api.routes import harness
from skillwiki.layers.skill_runtime.harness.benchflow_current_registry import formal_benchflow_matrix

BATCH = "m2-m5-recovery-20261024"

def test_six_empty_slots_keep_complete_denominator_and_unknown_reward():
    rows = formal_benchflow_matrix([])
    assert [x["formal_slot"] for x in rows] == ["R1","R2","R3","R4","R5","R6"]
    assert all(x["status"] == "not_run" and x["observed_reward"] is None and not x["verifier_ran"] for x in rows)

def test_historical_and_wrong_condition_never_fill_a_formal_slot():
    records = [
        {"run_id":"historical","condition":"legacy-old-direct","status":"completed"},
        {"run_id":"wrong","formal_slot":"R2","batch_id":BATCH,"condition":"wrong","status":"completed"},
        {"run_id":"other-batch","formal_slot":"R2","batch_id":"other","condition":"legacy-old-direct","status":"completed"},
    ]
    assert all(row["status"] == "not_run" for row in formal_benchflow_matrix(records))

def test_exact_slot_preserves_zero_and_failed_terminal():
    record = {"run_id":"r2-real","formal_slot":"R2","batch_id":BATCH,"condition":"legacy-old-direct",
              "status":"failed","observed_reward":0,"verifier_ran":True,"evidence_integrity":"verified"}
    row = formal_benchflow_matrix([record])[1]
    assert row["status"] == "failed" and row["observed_reward"] == 0 and row["verifier_ran"]
    assert not row["model_result_eligible"] and not row["promotion_allowed"]

def test_latest_native_start_selects_attempt_not_lexical_id(monkeypatch,tmp_path):
    from skillwiki.layers.skill_runtime.harness import benchflow_current_registry as registry
    import json
    records=[]
    for name,stamp,status in [("z-earlier","2026-10-25 09:00:00","blocked"),("a-later","2026-10-25 10:00:00","failed")]:
        path=tmp_path/name;path.mkdir();(path/"config.json").write_text(json.dumps({"started_at":stamp}))
        records.append({"run_id":name,"run_path":str(path),"batch_id":BATCH,"formal_slot":"R1","condition":"no-skill","status":status})
    monkeypatch.setattr(registry,"_ARTIFACT_ROOT",tmp_path)
    row=formal_benchflow_matrix(records)[0]
    assert row["run_id"] == "a-later" and row["attempt_count"] == 2

def test_read_only_list_api_returns_six_slots_even_without_attempts(monkeypatch):
    monkeypatch.setattr(harness,"list_current_benchflow_runs",lambda:[])
    app=FastAPI();app.include_router(harness.router,prefix="/api/v1")
    response=TestClient(app).get("/api/v1/harness/benchflow/current-runs")
    assert response.status_code == 200
    data=response.json()
    assert data["total"] == 0 and data["runs"] == [] and len(data["formal_matrix"]) == 6

def test_completed_receipt_stays_completed_when_native_reader_says_passed(monkeypatch,tmp_path):
    from skillwiki.layers.skill_runtime.harness import benchflow_current_registry as registry
    from types import SimpleNamespace
    import json
    run=tmp_path/"run";run.mkdir();receipt=tmp_path/"receipt.json"
    receipt.write_text(json.dumps({"run_id":"receipt","run_path":str(run),"status":"completed","raw_artifacts":{}}))
    monkeypatch.setattr(registry,"_ARTIFACT_ROOT",tmp_path)
    monkeypatch.setattr(registry,"read_benchflow_evidence",lambda *a,**kw:SimpleNamespace(errors=[],status="passed",score=1.0,sidecar_valid=True,observed={},cost={}))
    assert registry._read_registration(receipt)["status"] == "completed"

def test_actual_verifier_zero_survives_agent_error_without_eligibility(tmp_path):
    from skillwiki.layers.skill_runtime.harness import benchflow_current_registry as registry
    from types import SimpleNamespace
    import json
    verifier=tmp_path/"verifier";verifier.mkdir()
    (verifier/"reward.txt").write_text("0.0")
    (verifier/"ctrf.json").write_text(json.dumps({"results":{"tests":[{"name":"missing output","status":"failed"}]}}))
    observation=SimpleNamespace(score=None,sidecar_valid=True,runtime_attestation_valid=True,verifier_error=None,rewards={"reward":0.0})
    artifacts={name:{"sha256":"test"} for name in ("result.json","verifier/reward.txt","verifier/ctrf.json")}
    assert registry._verified_terminal_reward(tmp_path,observation,artifacts,[]) == 0.0
    assert registry._verified_terminal_reward(tmp_path,observation,artifacts,["identity mismatch"]) is None
    observation.runtime_attestation_valid=False
    assert registry._verified_terminal_reward(tmp_path,observation,artifacts,[]) is None

def test_missing_score_and_conflicting_or_empty_verifier_are_not_zero(tmp_path):
    from skillwiki.layers.skill_runtime.harness import benchflow_current_registry as registry
    from types import SimpleNamespace
    import json
    verifier=tmp_path/"verifier";verifier.mkdir()
    (verifier/"reward.txt").write_text("1")
    (verifier/"ctrf.json").write_text(json.dumps({"results":{"tests":[]}}))
    observation=SimpleNamespace(score=None,sidecar_valid=True,runtime_attestation_valid=True,verifier_error=None,rewards={"reward":0})
    artifacts={name:True for name in ("result.json","verifier/reward.txt","verifier/ctrf.json")}
    assert registry._verified_terminal_reward(tmp_path,observation,artifacts,[]) is None
    (verifier/"reward.txt").write_text("0")
    assert registry._verified_terminal_reward(tmp_path,observation,artifacts,[]) is None
    observation.rewards=None
    assert registry._verified_terminal_reward(tmp_path,observation,artifacts,[]) is None
