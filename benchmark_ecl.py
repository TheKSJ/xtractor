"""Independent structural and reconciliation benchmark for configured ECL tables."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from lender_intel.analysis import reconcile_ecl
from lender_intel.ecl import extract_ecl_document, load_ecl_config
from lender_intel.errors import LenderIntelError


ROOT = Path(__file__).resolve().parent


def _load_spot_checks() -> list[dict]:
    path = ROOT / "tests" / "fixtures" / "ecl_benchmark_expectations.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("checks"), list):
        raise ValueError(f"Invalid ECL benchmark fixture: {path}")
    return [dict(item) for item in data["checks"]]


def _evaluate_spot_checks(config_path: Path, records: list[dict], checks: list[dict]) -> list[dict]:
    result = []
    for check in checks:
        if Path(str(check["config"])).as_posix() != config_path.relative_to(ROOT).as_posix():
            continue
        matches = [
            record for record in records
            if all(record.get(field) == check.get(field) for field in (
                "source_document_id", "year", "canonical_stage", "canonical_movement_category", "source_column_position"
            ))
        ]
        actual = matches[0].get("normalized_value") if len(matches) == 1 else None
        expected = check.get("normalized_value")
        passed = len(matches) == 1 and float(actual) == float(expected)
        result.append({"check": check, "actual": actual, "status": "passed" if passed else "failed"})
    return result


def _expected_from_raw_config(config_path: Path) -> int:
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("tables"), list):
        return 0
    return sum(len(table.get("rows", [])) * len(table.get("stage_columns", [])) for table in data["tables"] if isinstance(table, dict))


def build_report() -> dict:
    cases = []
    spot_checks = _load_spot_checks()
    for config_path in sorted((ROOT / "config").glob("*_ecl_stage_movement.yaml")):
        checks_for_case = [check for check in spot_checks if Path(str(check["config"])).as_posix() == config_path.relative_to(ROOT).as_posix()]
        try:
            result = extract_ecl_document(load_ecl_config(config_path))
            records = result["records"]
            recon = reconcile_ecl(records)
            checks = _evaluate_spot_checks(config_path, records, spot_checks)
            cases.append({"name": config_path.stem, "expected": result["validation_summary"]["expected_record_count"], "extracted": len(records), "reconciliations": len(recon), "reconciliation_failures": sum(1 for item in recon if item.get("status") != "passed"), "spot_checks": checks, "status": "passed" if len(records) == result["validation_summary"]["expected_record_count"] and all(item.get("status") == "passed" for item in recon) and all(item.get("status") == "passed" for item in checks) else "failed"})
        except (LenderIntelError, OSError, KeyError, ValueError, yaml.YAMLError) as exc:
            cases.append({"name": config_path.stem, "expected": _expected_from_raw_config(config_path), "extracted": 0, "reconciliations": 0, "reconciliation_failures": 0, "spot_checks": [{"check": check, "actual": None, "status": "failed"} for check in checks_for_case], "error": str(exc), "status": "failed"})
    report = {"suite": "ecl_stage_movement", "cases": cases, "overall": {"expected": sum(c["expected"] for c in cases), "extracted": sum(c["extracted"] for c in cases), "reconciliation_failures": sum(c["reconciliation_failures"] for c in cases), "spot_checks": sum(len(c["spot_checks"]) for c in cases), "spot_check_failures": sum(sum(1 for item in c["spot_checks"] if item.get("status") != "passed") for c in cases), "status": "passed" if all(c["status"] == "passed" for c in cases) else "failed"}}
    return report


def main() -> int:
    report = build_report()
    output = ROOT / "outputs" / "benchmark_ecl.json"
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["overall"]["status"] == "passed" else 3


if __name__ == "__main__":
    raise SystemExit(main())
