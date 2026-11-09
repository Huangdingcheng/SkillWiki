"""Explicit, single-attempt local acceptance calls; raw responses stay in evidence."""
import argparse
import json
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts/20261024-five-capability-demo"
BASE = "http://127.0.0.1:8008/api/v1"
MANIFEST = json.loads((OUT / "demo-manifest.json").read_text(encoding="utf-8"))


def post(name, route, body):
    path = OUT / (name + ".json")
    if path.exists():
        raise RuntimeError(f"{name} already attempted; inspect saved evidence before another attempt")
    path.write_text(json.dumps({"status": "dispatching", "route": route, "input": body}, indent=2), encoding="utf-8")
    try:
        response = requests.post(BASE + route, json=body, timeout=400)
    except requests.RequestException as exc:
        path.write_text(json.dumps({"status": "client_error", "route": route, "input": body,
            "error_type": type(exc).__name__, "error": str(exc), "retry": False,
            "server_outcome": "unknown; inspect server trace and retained ledger reservation"},
            ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    data = response.json()
    path.write_text(json.dumps({"http_status": response.status_code, "input": body, "result": data}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"name": name, "http_status": response.status_code,
        "status": data.get("status"), "error": data.get("error", data.get("detail")),
        "run_id": data.get("run_id"), "candidate": {k:v for k,v in data.get("candidate", {}).items()
        if k in {"draft_skill_id", "version", "candidate_hash", "validation_status"}},
        "plan_id": data.get("plan_id"), "verifier_passed": data.get("verifier_passed")}, ensure_ascii=False), flush=True)
    return data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["checks", "gepa", "skillopt", "combined", "refresh"])
    args = parser.parse_args()
    skills = MANIFEST["skills"]
    runtime = skills["demo_dialogue_runtime"]
    baseline = skills["demo_dialogue_instruction_baseline"]
    if args.phase == "checks":
        for component in ("arex", "skillguard"):
            post("runtime-" + component, "/evolution/component-runs", {"component": component, "skill_id": runtime})
    elif args.phase in {"gepa", "skillopt"}:
        post(args.phase + "-real", "/evolution/component-runs", {"component": args.phase, "skill_id": baseline})
    elif args.phase == "combined":
        post("combined-real", "/execution/plan", {"goal": "Use demo_dialogue_runtime to parse this dialogue into the specified directed graph: Guard: Halt!\nTraveler: Hello. Use one skill step and pass the dialogue input exactly.",
            "context": {"dialogue": "Guard: Halt!\nTraveler: Hello."}, "max_skills": 3})
    else:
        post("arex-refresh-real", "/evolution/component-runs", {"component": "arex", "skill_id": runtime, "refresh": True})


if __name__ == "__main__":
    main()
