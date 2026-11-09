"""Import immutable pre-existing project charges into the shared ledger once."""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skillwiki.utils.cumulative_budget import CumulativeBudgetGuard


def reconcile(ledger: Path, legacy_paths: list[Path]) -> dict:
    guard = CumulativeBudgetGuard(ledger.resolve(), total_limit_cny=10)
    imported = []
    for path in legacy_paths:
        raw = path.read_bytes()
        rows = [json.loads(line) for line in raw.decode("utf-8-sig").splitlines() if line.strip()]
        reservations, settled = {}, {}
        for index, row in enumerate(rows):
            if row.get("event") == "reserve":
                request = row["request_id"]
                if request in reservations:
                    raise ValueError("duplicate legacy reservation")
                reservations[request] = row
            elif row.get("event") == "settle":
                if row["request_id"] in settled:
                    raise ValueError("duplicate legacy settlement")
                settled[row["request_id"]] = row
            elif "reserved_cny" in row and "event" not in row:
                reservations[f"embedding-{index}"] = row
            else:
                raise ValueError("unrecognized historical cost record")
        if set(settled) - set(reservations):
            raise ValueError("legacy settlement has no reservation")
        for request, reservation in reservations.items():
            settlement = settled.get(request)
            amount = Decimal(str((settlement or reservation).get("charged_cny", reservation["reserved_cny"])))
            evidence = json.dumps([reservation, settlement], sort_keys=True, separators=(",", ":"))
            receipt = guard.import_historical_charge("embedding" if request.startswith("embedding-") else "model",
                amount, source_id=f"{path.name}:{request}",
                evidence_sha256=hashlib.sha256(evidence.encode()).hexdigest(),
                unknown=not settlement or settlement.get("status") != "usage_settled",
                metadata={"legacy_file_sha256": hashlib.sha256(raw).hexdigest(), "legacy_request_id": request})
            imported.append(receipt)
    return {"legacy_files": [str(p) for p in legacy_paths], "imports": imported, "snapshot": guard.snapshot()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--legacy", action="append", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = reconcile(args.ledger, args.legacy)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["snapshot"]))
