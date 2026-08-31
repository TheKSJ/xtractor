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
CONFIG = ROOT / "config" / "chola_transfer_assignment.yaml"
REFERENCE = ROOT / "tests" / "fixtures" / "chola_transfer_assignment_reference.json"
BAJAJ_CONFIG = ROOT / "config" / "bajaj_transfer_assignment.yaml"
BAJAJ_REFERENCE = ROOT / "tests" / "fixtures" / "bajaj_transfer_assignment_reference.json"


class CholaExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(CONFIG)
        cls.reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
        cls.result = extract_document(cls.config)

    def test_pdf_references_and_target_boundaries_are_source_checked(self) -> None:
        self.assertEqual(self.config["source"]["pdf_page_number"], 232)
        self.assertEqual(self.config["source"]["pdf_page_index"], 231)
        self.assertEqual(self.config["source"]["printed_page_number"], 232)

        document = pymupdf.open(self.config["_source_path"])
        try:
            context_text = document[230].get_text("text")
            target_text = document[231].get_text("text")
        finally:
            document.close()
        self.assertIn("RBI Disclosures", context_text)
        self.assertIn("Note : 50", context_text)
        self.assertIn(self.config["target"]["table_title"], target_text)
        self.assertIn("(II) DISCLOSURE RELATING TO SECURITIZATION. (Contd.)", target_text)
        self.assertIn("IV) DETAILS OF STRESSED LOANS TRANSFERRED", target_text)

    def test_reference_fixture_matches_all_14_records(self) -> None:
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

        for record in self.result["records"]:
            for field, expected in self.reference["provenance"].items():
                self.assertEqual(record[field], expected, field)

    def test_structure_years_and_section_boundaries(self) -> None:
        validation = self.result["validation_summary"]
        self.assertTrue(validation["target_table_found"])
        self.assertTrue(validation["excluded_tables_avoided"])
        self.assertEqual(validation["actual_row_count"], 7)
        self.assertEqual(validation["actual_value_cell_count"], 14)
        self.assertEqual(validation["years_detected"], [2026, 2025])
        self.assertEqual({record["year"] for record in self.result["records"]}, {2026, 2025})
        labels = " ".join(record["original_row_label"] for record in self.result["records"])
        self.assertNotIn("securitization", labels.lower())
        self.assertNotIn("stressed loans", labels.lower())
        self.assertTrue(all(record["note"] == "50" for record in self.result["records"]))

    def test_counts_percentages_and_printed_na_values(self) -> None:
        counts = [
            record
            for record in self.result["records"]
            if record["canonical_metric"] == "loan_account_count_assigned"
        ]
        self.assertEqual([(record["normalized_value"], record["unit"]) for record in counts], [(11486, "count"), (4514, "count")])
        self.assertTrue(all(isinstance(record["normalized_value"], int) for record in counts))

        percentages = [
            record
            for record in self.result["records"]
            if record["canonical_metric"] == "retention_beneficial_economic_interest"
        ]
        self.assertEqual({record["raw_value"] for record in percentages}, {"10%"})
        self.assertEqual({record["normalized_value"] for record in percentages}, {10})
        self.assertEqual({record["unit"] for record in percentages}, {"percent"})

        na_cells = [record for record in self.result["records"] if record["raw_value"] == "NA"]
        self.assertEqual(len(na_cells), 4)
        self.assertTrue(all(record["normalized_value"] is None for record in na_cells))
        self.assertTrue(all(record["unit"] is None for record in na_cells))
        self.assertNotIn("Unrated", {record["normalized_value"] for record in na_cells})
        self.assertNotIn(0, {record["normalized_value"] for record in na_cells})

    def test_maturity_metrics_are_distinct_and_warning_is_clear(self) -> None:
        self.assertEqual(
            {
                record["canonical_metric"]
                for record in self.result["records"]
                if "maturity" in record["canonical_metric"]
            },
            {"weighted_average_maturity"},
        )
        self.assertTrue(
            any(
                "distinct from Bajaj's weighted average residual maturity" in warning
                and "unresolved" in warning
                for warning in self.result["warnings"]
            )
        )

        bajaj = extract_document(load_config(BAJAJ_CONFIG))
        self.assertIn(
            "weighted_average_residual_maturity",
            {record["canonical_metric"] for record in bajaj["records"]},
        )
        self.assertNotIn(
            "weighted_average_maturity",
            {record["canonical_metric"] for record in bajaj["records"]},
        )

    def test_normalization_helpers_keep_missing_value_separate(self) -> None:
        self.assertEqual(normalize_value("11,486", "count"), (11486, "count"))
        self.assertEqual(normalize_value("10%", "percent"), (10, "percent"))
        self.assertEqual(normalize_value("NA"), (None, None))

    def test_missing_target_table_fails_clearly(self) -> None:
        broken = copy.deepcopy(self.config)
        broken["target"]["table_title"] = "not a real Chola table"
        with self.assertRaisesRegex(ExtractionError, "target table title"):
            extract_document(broken)


class BajajRegressionTests(unittest.TestCase):
    def test_original_bajaj_fields_and_values_are_unchanged(self) -> None:
        config = load_config(BAJAJ_CONFIG)
        result = extract_document(config)
        reference = json.loads(BAJAJ_REFERENCE.read_text(encoding="utf-8"))
        fields = (
            "original_row_label",
            "year",
            "source_column_position",
            "raw_value",
            "normalized_value",
            "unit",
        )
        actual = [{field: record[field] for field in fields} for record in result["records"]]
        self.assertEqual(len(actual), 24)
        self.assertEqual(actual, reference)

        for record in result["records"]:
            self.assertEqual(
                record["canonical_metric"],
                config["target"]["canonical_metric_map"][record["original_row_label"]],
            )


if __name__ == "__main__":
    unittest.main()
