from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

import pymupdf

from extract_transfer_assignment import (
    ExtractionError,
    extract_document,
    load_config,
    normalize_value,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "ugro_transfer_assignment.yaml"
REFERENCE = ROOT / "tests" / "fixtures" / "ugro_transfer_assignment_reference.json"
BAJAJ_CONFIG = ROOT / "config" / "bajaj_transfer_assignment.yaml"
CHOLA_CONFIG = ROOT / "config" / "chola_transfer_assignment.yaml"


@unittest.skipUnless((ROOT / "data" / "raw" / "1779168341-UGRO Capital Ltd_Annual Report 2025-26.pdf").is_file(), "real annual-report PDFs not present")
class UgroExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(CONFIG)
        cls.reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
        cls.result = extract_document(cls.config)

    def test_pdf_references_and_scope_are_source_checked(self) -> None:
        self.assertEqual(self.config["source"]["pdf_page_number"], 378)
        self.assertEqual(self.config["source"]["pdf_page_index"], 377)
        self.assertEqual(self.config["source"]["printed_page_number"], 378)

        document = pymupdf.open(self.config["_source_path"])
        try:
            page_text = document[377].get_text("text")
        finally:
            document.close()
        compact_text = " ".join(page_text.split())
        self.assertIn("standalone financial statements", compact_text.lower())
        self.assertIn("64", compact_text)
        self.assertIn(self.config["target"]["table_title"], compact_text)
        self.assertIn("ii)", compact_text)
        self.assertIn("acquired loans not in default", compact_text.lower())

    def test_reference_fixture_matches_every_value_and_provenance(self) -> None:
        fields = (
            "original_row_label",
            "canonical_metric",
            "year",
            "source_column_position",
            "raw_value",
            "normalized_value",
            "unit",
        )
        actual = [{field: record[field] for field in fields} for record in self.result["records"]]
        self.assertEqual(actual, self.reference["records"])
        self.assertEqual(len(actual), 14)

        for record in self.result["records"]:
            for field, expected in self.reference["provenance"].items():
                self.assertEqual(record[field], expected, field)

    def test_structure_keeps_ugro_rows_and_excludes_adjacent_tables(self) -> None:
        validation = self.result["validation_summary"]
        self.assertTrue(validation["target_table_found"])
        self.assertTrue(validation["excluded_tables_avoided"])
        self.assertEqual(validation["actual_row_count"], 7)
        self.assertEqual(validation["actual_value_cell_count"], 14)
        self.assertEqual(validation["years_detected"], [2026, 2025])
        self.assertEqual(
            {record["year"] for record in self.result["records"]}, {2026, 2025}
        )
        labels = " ".join(record["original_row_label"] for record in self.result["records"])
        self.assertNotIn("receivables", labels.lower())
        self.assertNotIn("acquired", labels.lower())
        self.assertNotIn("stressed", labels.lower())

    def test_units_and_text_values_are_preserved_explicitly(self) -> None:
        units = {record["unit"] for record in self.result["records"]}
        self.assertEqual(units, {"INR_lakh", "years", "percent", "text"})

        principal = [
            record
            for record in self.result["records"]
            if record["canonical_metric"]
            == "aggregate_principal_outstanding_loans_transferred_through_assignment"
        ]
        self.assertEqual([record["normalized_value"] for record in principal], [147180.8, 94678.52])
        self.assertTrue(all(record["unit"] == "INR_lakh" for record in principal))

        rating = [
            record
            for record in self.result["records"]
            if record["canonical_metric"] == "rating_wise_distribution_rated_loans"
        ]
        self.assertEqual({record["raw_value"] for record in rating}, {"Non-Rated"})
        self.assertEqual({record["normalized_value"] for record in rating}, {"Non-Rated"})
        self.assertEqual({record["unit"] for record in rating}, {"text"})
        self.assertTrue(all(record["normalized_value"] is not None for record in self.result["records"]))

    def test_common_and_distinct_metric_names_are_deliberate(self) -> None:
        metrics = {record["canonical_metric"] for record in self.result["records"]}
        self.assertIn("weighted_average_maturity", metrics)
        self.assertIn("weighted_average_holding_period", metrics)
        self.assertIn("retention_beneficial_economic_interest", metrics)
        self.assertIn("coverage_tangible_security", metrics)
        self.assertIn("rating_wise_distribution_rated_loans", metrics)
        self.assertIn("aggregate_consideration_received", metrics)
        self.assertIn(
            "aggregate_principal_outstanding_loans_transferred_through_assignment",
            metrics,
        )
        self.assertNotIn("loan_amount_assigned", metrics)

        bajaj_metrics = {
            record["canonical_metric"]
            for record in extract_document(load_config(BAJAJ_CONFIG))["records"]
        }
        chola_metrics = {
            record["canonical_metric"]
            for record in extract_document(load_config(CHOLA_CONFIG))["records"]
        }
        self.assertIn("weighted_average_residual_maturity", bajaj_metrics)
        self.assertNotIn("weighted_average_maturity", bajaj_metrics)
        self.assertIn("weighted_average_maturity", chola_metrics)

    def test_warnings_keep_unresolved_differences_visible(self) -> None:
        warnings = " ".join(self.result["warnings"])
        for phrase in (
            "principal-outstanding amount is kept distinct",
            "maturity and holding period in years",
            "only secured loans",
            "Non-Rated is preserved",
            "excludes loans transferred through co-lending",
        ):
            self.assertIn(phrase, warnings)

    def test_normalization_helpers_support_ugro_units_and_missing_values(self) -> None:
        self.assertEqual(normalize_value("1,47,180.80", "INR_lakh"), (147180.8, "INR_lakh"))
        self.assertEqual(normalize_value("6.27", "years"), (6.27, "years"))
        self.assertEqual(normalize_value("Non-Rated", "text"), ("Non-Rated", "text"))
        self.assertEqual(normalize_value("NA"), (None, None))

    def test_missing_target_table_fails_clearly(self) -> None:
        broken = copy.deepcopy(self.config)
        broken["target"]["table_title"] = "not a real Ugro table"
        with self.assertRaisesRegex(ExtractionError, "target table title"):
            extract_document(broken)


if __name__ == "__main__":
    unittest.main()
