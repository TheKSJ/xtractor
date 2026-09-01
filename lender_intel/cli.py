from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from .analysis import analyze_records
from .comparability import load_overrides, load_registry
from .ecl import extract_ecl_document, load_ecl_config, write_ecl_result
from .errors import LenderIntelError
from .reporting import load_records, write_analysis_bundle


def _synthetic_records() -> list[dict[str, Any]]:
    common = {"company": "Synthetic Lender", "reporting_period": "Year ended 31 March 2026", "statement_scope": "standalone", "source_document_id": "synthetic_demo", "source_file": "synthetic_demo.pdf", "pdf_page_number": 1, "printed_page_number": 1, "note": "demo", "source_footnotes": [], "population_scope": "demo_loans"}
    records = []
    for year, opening, closing in [(2025, 100, 110), (2026, 110, 125)]:
        for stage, value in [("Stage 1", opening), ("Stage 2", 0), ("Stage 3", 0), ("unresolved", opening)]:
            records.append({**common, "disclosure_family": "ecl_stage_movement", "record_id": f"synthetic:{year}:opening:{stage}", "table_title": "Synthetic ECL movement", "original_row_label": f"Opening {year}", "row_role": "opening", "canonical_movement_category": "opening_balance", "original_stage_label": stage, "canonical_stage": stage, "year": year, "source_column_position": stage.lower().replace(" ", "_"), "raw_value": str(value), "normalized_value": value, "unit": "INR_crore", "value_status": "reported_value", "mapping_confidence": "high", "mapping_explanation": "Synthetic demo record", "source_footnotes": [], "extraction_warnings": []})
            stage_closing = closing if stage in {"Stage 1", "unresolved"} else 0
            stage_movement = stage_closing - opening
            for category, amount in [("new_assets_originated_or_purchased", stage_movement), ("closing_balance", stage_closing)]:
                role = "closing" if category == "closing_balance" else "movement"
                records.append({**common, "disclosure_family": "ecl_stage_movement", "record_id": f"synthetic:{year}:{category}:{stage}", "table_title": "Synthetic ECL movement", "original_row_label": category, "row_role": role, "canonical_movement_category": category, "original_stage_label": stage, "canonical_stage": stage, "year": year, "source_column_position": stage.lower().replace(" ", "_"), "raw_value": str(amount), "normalized_value": amount, "unit": "INR_crore", "value_status": "reported_value", "mapping_confidence": "high", "mapping_explanation": "Synthetic demo record", "source_footnotes": [], "extraction_warnings": []})
    return records


def _extract(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    try:
        raw_config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise LenderIntelError(f"Invalid YAML configuration: {exc}") from exc
    if not isinstance(raw_config, dict):
        raise LenderIntelError("Configuration must contain a YAML mapping")
    family = raw_config.get("disclosure_family")
    if family == "ecl_stage_movement":
        config = load_ecl_config(config_path)
        result = extract_ecl_document(config)
        output = Path(args.output) if args.output else config["_output_path"]
        write_ecl_result(result, output)
    else:
        from extract_transfer_assignment import extract_document, load_config, write_result
        config = load_config(config_path)
        result = extract_document(config)
        output = Path(args.output) if args.output else config["_output_path"]
        write_result(result, output)
    print(f"Wrote {output}")
    return 0


def _benchmark(args: argparse.Namespace) -> int:
    suite = args.suite
    if suite in {"transfer", "all"}:
        import benchmark_transfer_assignment
        status = benchmark_transfer_assignment.main([])
        if status != 0:
            return status
    if suite in {"ecl", "all"}:
        import benchmark_ecl
        status = benchmark_ecl.main()
        if status != 0:
            return status
    return 0


def _analyze(args: argparse.Namespace) -> int:
    records = load_records(args.input)
    if not records:
        raise LenderIntelError(f"No extraction result JSON records found under {args.input}")
    overrides = load_overrides(args.override_file)
    analysis = analyze_records(records, load_registry(args.registry), overrides)
    metadata = {"registry": args.registry, "override_file": args.override_file}
    manifest = Path(args.input) / "demo_manifest.json"
    if manifest.is_file():
        try:
            manifest_data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest_data = {}
        if isinstance(manifest_data, dict) and manifest_data.get("synthetic") is True:
            metadata["demo"] = True
            metadata["synthetic"] = True
    output = write_analysis_bundle(records, analysis, args.output, metadata=metadata)
    print(f"Wrote analyst bundle to {output}")
    return 0


def _demo(args: argparse.Namespace) -> int:
    records = _synthetic_records()
    analysis = analyze_records(records, load_registry(args.registry), {})
    output = write_analysis_bundle(records, analysis, args.output, metadata={"demo": True, "deterministic": True})
    (Path(args.output) / "demo_manifest.json").write_text(json.dumps({"synthetic": True, "record_count": len(records)}, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote deterministic demo to {output}")
    return 0


def _holdout(args: argparse.Namespace) -> int:
    from .holdout import evaluate_holdout, extract_holdout, load_holdout_config
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    config = load_holdout_config(args.config)
    result = extract_holdout(config)
    config["_output_path"].parent.mkdir(parents=True, exist_ok=True)
    config["_output_path"].write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "final_extraction.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "evaluation.json").write_text(json.dumps(evaluate_holdout(args.config), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote holdout evaluation to {output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lender-intel", description="Extract and compare Indian lender annual-report disclosures.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("extract"); p.add_argument("--config", required=True); p.add_argument("--output"); p.set_defaults(func=_extract)
    p = sub.add_parser("benchmark"); p.add_argument("--suite", choices=["transfer", "ecl", "all"], default="all"); p.set_defaults(func=_benchmark)
    p = sub.add_parser("analyze"); p.add_argument("--input", required=True); p.add_argument("--output", required=True); p.add_argument("--registry", default="config/comparability_registry.yaml"); p.add_argument("--override-file"); p.set_defaults(func=_analyze)
    p = sub.add_parser("demo"); p.add_argument("--output", default="demo-output"); p.add_argument("--registry", default="config/comparability_registry.yaml"); p.set_defaults(func=_demo)
    p = sub.add_parser("holdout"); p.add_argument("--config", required=True); p.add_argument("--output", required=True); p.set_defaults(func=_holdout)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return int(args.func(args))
    except (LenderIntelError, FileNotFoundError, ValueError) as exc:
        print(f"lender-intel error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
