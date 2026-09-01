from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import pymupdf

from lender_intel.analysis import analyze_records, convert_value, reconcile_ecl
from lender_intel.comparability import compare_records, load_registry
from lender_intel.cli import main as cli_main
from lender_intel.ecl import extract_ecl_document, load_ecl_config, normalize_ecl_value
from lender_intel.errors import ComparabilityError, ConfigurationError, ExtractionError
from lender_intel.reporting import load_records, write_analysis_bundle


class IntelligenceTests(unittest.TestCase):
    def test_ecl_number_and_nil_semantics(self) -> None:
        self.assertEqual(normalize_ecl_value("(1,234.50)"), (-1234.5, "reported_value"))
        self.assertEqual(normalize_ecl_value("-"), (0, "reported_nil"))

    def test_synthetic_ecl_extraction_is_coordinate_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "synthetic.pdf"
            document = pymupdf.open()
            page = document.new_page(width=600, height=500)
            page.insert_text((40, 30), "Synthetic ECL movement Stage 1 Stage 2 Stage 3 Total Particulars")
            rows = [(80, "Opening balance", ["10", "0", "0", "10"]), (110, "Transfer to Stage 2", ["(2)", "2", "0", "0"]), (140, "Closing balance", ["8", "2", "0", "10"])]
            for y, label, values in rows:
                page.insert_text((40, y), label)
                for x, value in zip((280, 335, 390, 500), values):
                    page.insert_text((x, y), value)
            document.save(pdf)
            document.close()
            sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
            config = {
                "disclosure_family": "ecl_stage_movement",
                "document": {"company": "Synthetic", "reporting_period": "FY2026", "statement_scope": "standalone"},
                "source": {"document_id": "synthetic", "manifest_file": "manifest.yaml", "source_file": str(pdf)},
                "_source_path": pdf,
                "_source_manifest_entry": {"sha256": sha},
                "tables": [{
                    "note": "demo", "table_title": "Synthetic ECL movement", "pdf_page_number": 1, "pdf_page_index": 0, "printed_page_number": 1,
                    "unit": "INR_crore", "population_scope": "demo", "required_anchors": ["Stage 1", "Stage 2", "Stage 3", "Total"],
                    "stage_columns": [
                        {"original_stage_label": "Stage 1", "canonical_stage": "Stage 1", "source_column_position": "stage_1", "x0": 270, "x1": 325},
                        {"original_stage_label": "Stage 2", "canonical_stage": "Stage 2", "source_column_position": "stage_2", "x0": 325, "x1": 380},
                        {"original_stage_label": "Stage 3", "canonical_stage": "Stage 3", "source_column_position": "stage_3", "x0": 380, "x1": 430},
                        {"original_stage_label": "Total", "canonical_stage": "unresolved", "source_column_position": "total", "x0": 490, "x1": 550},
                    ],
                    "rows": [
                        {"year": 2026, "y": 68, "original_row_label": "Opening balance", "canonical_movement_category": "opening_balance", "row_role": "opening"},
                        {"year": 2026, "y": 98, "original_row_label": "Transfer to Stage 2", "canonical_movement_category": "transfer_to_stage_2"},
                        {"year": 2026, "y": 128, "original_row_label": "Closing balance", "canonical_movement_category": "closing_balance", "row_role": "closing"},
                    ],
                }],
            }
            result = extract_ecl_document(config)
            self.assertEqual(len(result["records"]), 12)
            self.assertEqual(result["records"][4]["raw_value"], "(2)")
            self.assertEqual(result["records"][4]["normalized_value"], -2)

            duplicate_config = dict(config)
            duplicate_config["tables"] = [dict(config["tables"][0])]
            duplicate_config["tables"][0]["rows"] = list(config["tables"][0]["rows"]) + [dict(config["tables"][0]["rows"][-1])]
            with self.assertRaisesRegex(ExtractionError, "Duplicate ECL record IDs"):
                extract_ecl_document(duplicate_config)

    def test_reconciliation_preserves_residuals(self) -> None:
        records = []
        base = {"disclosure_family": "ecl_stage_movement", "company": "Demo", "year": 2026, "canonical_stage": "Stage 1", "unit": "INR_crore"}
        records.extend([{**base, "row_role": "opening", "canonical_movement_category": "opening_balance", "normalized_value": 10}, {**base, "row_role": "movement", "canonical_movement_category": "write_offs", "normalized_value": -1}, {**base, "row_role": "closing", "canonical_movement_category": "closing_balance", "normalized_value": 8}])
        result = reconcile_ecl(records)
        self.assertEqual(result[0]["status"], "residual")
        self.assertEqual(result[0]["residual"], "1")

    def test_comparability_blocks_scope_and_override_records_it(self) -> None:
        left = {"record_id": "a", "company": "A", "canonical_metric": "loan_amount_assigned", "unit": "INR_crore", "statement_scope": "standalone", "population_scope": "loans"}
        right = {"record_id": "b", "company": "B", "canonical_metric": "loan_amount_assigned", "unit": "INR_lakh", "statement_scope": "standalone", "population_scope": "other_loans"}
        decision = compare_records(left, right, load_registry(), relation="cross_lender")
        self.assertEqual(decision["status"], "comparable_after_scope_review")
        overridden = compare_records(left, right, load_registry(), relation="cross_lender", overrides={decision["comparison_id"]: {"rationale": "Reviewed population definitions."}})
        self.assertTrue(overridden["override_applied"])
        self.assertEqual(overridden["status"], "comparable")

    def test_analysis_exposes_blocked_comparisons(self) -> None:
        records = [{"record_id": "a", "company": "A", "canonical_metric": "loan_amount_assigned", "year": 2025, "unit": "INR_crore", "statement_scope": "standalone", "population_scope": "loans"}, {"record_id": "b", "company": "A", "canonical_metric": "loan_amount_assigned", "year": 2026, "unit": "INR_crore", "statement_scope": "standalone", "population_scope": "loans"}]
        result = analyze_records(records, load_registry(), {})
        self.assertGreaterEqual(result["summary"]["comparison_count"], 1)

    def test_analysis_builds_cross_lender_pair_for_same_metric_period(self) -> None:
        records = [{"record_id": "a", "company": "A", "canonical_metric": "loan_amount_assigned", "year": 2026, "unit": "INR_crore", "statement_scope": "standalone", "population_scope": "loans"}, {"record_id": "b", "company": "B", "canonical_metric": "loan_amount_assigned", "year": 2026, "unit": "INR_crore", "statement_scope": "standalone", "population_scope": "loans"}]
        result = analyze_records(records, load_registry(), {})
        self.assertEqual([x["relation"] for x in result["comparability_matrix"]], ["cross_lender"])

    def test_analysis_calculates_legacy_records_without_record_ids(self) -> None:
        records = [{"company": "A", "canonical_metric": "loan_amount_assigned", "year": 2025, "unit": "INR_crore", "statement_scope": "standalone", "population_scope": "loans", "normalized_value": 10, "source_document_id": "doc", "source_column_position": "column_1"}, {"company": "A", "canonical_metric": "loan_amount_assigned", "year": 2026, "unit": "INR_crore", "statement_scope": "standalone", "population_scope": "loans", "normalized_value": 12, "source_document_id": "doc", "source_column_position": "column_1"}]
        result = analyze_records(records, load_registry(), {})
        changes = [item for item in result["calculations"] if item.get("calculation") == "period_change"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["absolute_change"], "2")

    def test_period_change_converts_units_before_calculation(self) -> None:
        records = [{"record_id": "prior", "company": "A", "canonical_metric": "loan_amount_assigned", "year": 2025, "unit": "INR_lakh", "statement_scope": "standalone", "population_scope": "loans", "normalized_value": 250, "source_column_position": "column_1"}, {"record_id": "current", "company": "A", "canonical_metric": "loan_amount_assigned", "year": 2026, "unit": "INR_crore", "statement_scope": "standalone", "population_scope": "loans", "normalized_value": 3, "source_column_position": "column_1"}]
        result = analyze_records(records, load_registry(), {})
        changes = [item for item in result["calculations"] if item.get("calculation") == "period_change"]
        self.assertEqual(changes[0]["absolute_change"], "0.5")
        self.assertEqual(changes[0]["unit_conversion"], "INR_lakh / 100 = INR_crore")

    def test_explicit_unit_conversion_is_recorded(self) -> None:
        left = {"record_id": "l", "company": "A", "canonical_metric": "loan_amount_assigned", "unit": "INR_lakh", "statement_scope": "standalone", "population_scope": "loans"}
        right = {"record_id": "r", "company": "B", "canonical_metric": "loan_amount_assigned", "unit": "INR_crore", "statement_scope": "standalone", "population_scope": "loans"}
        decision = compare_records(left, right, load_registry(), relation="same_company_periods")
        self.assertEqual(decision["status"], "comparable_after_unit_conversion")
        self.assertEqual(decision["unit_conversion"], "INR_lakh / 100 = INR_crore")
        self.assertEqual(convert_value("250", "INR_lakh", "INR_crore", "loan_amount_assigned"), 2.5)

    def test_unknown_unit_difference_is_blocked(self) -> None:
        left = {"record_id": "l", "company": "A", "canonical_metric": "loan_amount_assigned", "unit": "INR_crore", "statement_scope": "standalone", "population_scope": "loans"}
        right = {"record_id": "r", "company": "A", "canonical_metric": "loan_amount_assigned", "unit": "percent", "statement_scope": "standalone", "population_scope": "loans"}
        decision = compare_records(left, right, load_registry())
        self.assertEqual(decision["status"], "not_comparable")

    def test_missing_unit_is_unresolved(self) -> None:
        left = {"record_id": "l", "company": "A", "canonical_metric": "loan_amount_assigned", "unit": None, "statement_scope": "standalone", "population_scope": "loans"}
        right = {"record_id": "r", "company": "A", "canonical_metric": "loan_amount_assigned", "unit": "INR_crore", "statement_scope": "standalone", "population_scope": "loans"}
        self.assertEqual(compare_records(left, right, load_registry())["status"], "unresolved")

    def test_unresolved_stage_blocks_ecl_comparison(self) -> None:
        left = {"record_id": "l", "company": "A", "disclosure_family": "ecl_stage_movement", "canonical_movement_category": "closing_balance", "canonical_stage": "unresolved", "unit": "INR_crore", "statement_scope": "standalone", "population_scope": "loans"}
        right = {"record_id": "r", "company": "A", "disclosure_family": "ecl_stage_movement", "canonical_movement_category": "closing_balance", "canonical_stage": "unresolved", "unit": "INR_crore", "statement_scope": "standalone", "population_scope": "loans"}
        self.assertEqual(compare_records(left, right, load_registry())["status"], "unresolved")

    def test_ecl_config_reports_missing_unit_clearly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.yaml"
            path.write_text("""disclosure_family: ecl_stage_movement
document: {company: Demo, reporting_period: FY2026, statement_scope: standalone}
source: {document_id: demo, manifest_file: manifest.yaml, source_file: demo.pdf}
tables: [{note: '1', table_title: Demo, pdf_page_number: 1, printed_page_number: 1, population_scope: loans, stage_columns: [], rows: []}]
output_file: output.json
""", encoding="utf-8")
            with self.assertRaisesRegex(ConfigurationError, "tables\[0\]\.unit"):
                load_ecl_config(path)

    def test_report_loader_does_not_silently_drop_duplicate_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            record = {"record_id": "same", "normalized_value": 1}
            (folder / "one.json").write_text(json.dumps({"records": [record]}), encoding="utf-8")
            (folder / "two.json").write_text(json.dumps({"records": [record]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate record identities"):
                load_records(folder)

    def test_override_is_published_in_analyst_bundle(self) -> None:
        left = {"record_id": "l", "company": "A", "canonical_metric": "loan_amount_assigned", "unit": "INR_crore", "statement_scope": "standalone", "population_scope": "loans", "normalized_value": 1}
        right = {"record_id": "r", "company": "B", "canonical_metric": "loan_amount_assigned", "unit": "INR_crore", "statement_scope": "standalone", "population_scope": "other", "normalized_value": 2}
        decision = compare_records(left, right, load_registry(), relation="cross_lender")
        overrides = {decision["comparison_id"]: {"comparison_id": decision["comparison_id"], "rationale": "Reviewed populations."}}
        analysis = analyze_records([left, right], load_registry(), overrides)
        with tempfile.TemporaryDirectory() as directory:
            write_analysis_bundle([left, right], analysis, directory)
            published = json.loads((Path(directory) / "overrides.json").read_text(encoding="utf-8"))
            self.assertEqual(published[0]["rationale"], "Reviewed populations.")

    def test_unused_override_is_rejected(self) -> None:
        with self.assertRaisesRegex(ComparabilityError, "do not match"):
            analyze_records([], load_registry(), {"cmp_missing": {"comparison_id": "cmp_missing", "rationale": "Typo should fail."}})

    def test_demo_cli_writes_complete_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "demo"
            self.assertEqual(cli_main(["demo", "--output", str(output)]), 0)
            for name in ("records.json", "comparability_matrix.json", "calculations.json", "validation_report.json", "warnings.json", "overrides.json", "analyst_brief.html", "demo_manifest.json"):
                self.assertTrue((output / name).is_file(), name)
            brief = (output / "analyst_brief.md").read_text(encoding="utf-8")
            self.assertIn("Cross-lender comparison", brief)
            for label in ("[observed_fact]", "[mechanical_calculation]", "[financial_interpretation]", "[unresolved_question]"):
                self.assertIn(label, brief)

    def test_analyze_cli_returns_actionable_error_for_empty_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory) / "empty"
            empty.mkdir()
            self.assertEqual(cli_main(["analyze", "--input", str(empty), "--output", str(Path(directory) / "analysis")]), 3)


if __name__ == "__main__":
    unittest.main()
