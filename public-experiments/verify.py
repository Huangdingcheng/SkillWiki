"""Offline consistency checks for the public evaluation companion (no model calls)."""
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read_json(name):
    return json.loads((ROOT / name).read_text(encoding="utf-8-sig"))


def read_csv(name):
    with (ROOT / "data" / name).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    manifest = read_json("files.json")
    for name, digest in manifest.items():
        path = (ROOT / name).resolve()
        require(path.is_relative_to(ROOT), "File outside package")
        require(hashlib.sha256(path.read_bytes()).hexdigest() == digest,
                "File digest differs: " + name)
    summary = read_json("data/summary.json")
    require([r["raw_correct"] for r in summary["q1"]] == [84, 112, 120], "Q1 scores")
    require(len(read_json("materials/use-inputs.json")) == 60, "Use input count")
    updates = read_json("materials/update-inputs.json")
    require(len(updates) == 192, "Update input count")
    require(len({r["case_id"] for r in updates}) == 192, "Duplicate update inputs")
    require(len(read_json("materials/governance-states.json")) == 60, "State count")
    core = read_csv("original-12-core-events.csv")
    require(len(core) == 12, "Core event count")
    require(sum(int(r["U_target_raw"]) for r in core) == 176, "Raw target total")
    require(sum(int(r["U_target_final"]) for r in core) == 192, "Final target total")
    require(sum(int(r["U_keep_final"]) for r in core) == 173, "Retention total")
    require(sum(r["event_primary_success"] == "True" for r in core) == 7, "Event successes")
    calls = read_csv("call-index.csv")
    require(len(calls) == summary["model_calls"] == 4590, "Call count")
    require(len({r["request_id_sha256"] for r in calls}) == 4590, "Duplicate requests")
    require(sum(r["kind"] == "task" for r in calls) == 4572, "Task count")
    require(sum(r["kind"] != "task" for r in calls) == 18, "Proposal count")
    tracking = read_csv("all-120-tracking.csv")
    require(len(tracking) == len({(r["state_id"], r["route"]) for r in tracking}) == 120,
            "Governance slots")
    require(sum(r["coverage"] == "original_revision" for r in tracking) == 100,
            "Main governance coverage")
    require(sum(r["coverage"] == "followup_revision" for r in tracking) == 20,
            "Supplement governance coverage")
    recovery = read_csv("recovery-5-events.csv")
    require(sum(int(r["system_final_correct"]) for r in recovery) == 180, "Recovery final")
    require(sum(int(r["model_raw_correct"]) for r in recovery) == 167, "Recovery raw")
    events = read_csv("all-12-events.csv")
    u03 = next(r for r in events if r["event_id"] == "U03")
    require(u03["original_skillwiki_correct"] == "35" and
            u03["subsequent_skillwiki_revision"] == "not_repeated" and
            u03["original_both_released_and_recovered"] == "False", "Unresolved U03")
    for path in ROOT.rglob("*"):
        if path.is_file() and path.suffix in {".json", ".csv", ".md", ".tex"}:
            text = path.read_text(encoding="utf-8-sig")
            require(":\\Users\\" not in text and ":/Users/" not in text,
                    "Private machine path in " + path.name)
    print("PASS: file digests; 60 use inputs; 192 update inputs; 60 states; "
          "4590 unique calls; revision-aware results; no private machine paths.")


if __name__ == "__main__":
    main()
