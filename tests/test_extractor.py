from __future__ import annotations

import unittest
from pathlib import Path

from extract_transfer_assignment import (
    ExtractionError,
    extract_document,
    load_config,
    validate_page,
    validate_structure,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "bajaj_transfer_assignment.yaml"


class ConfigurationTests(unittest.TestCase):
    def test_loads_config_and_exact_source_path_exists(self) -> None:
        config = load_config(CONFIG)
        expected = (
            ROOT
            / "data"
            / "raw"
            / "AR_29642_BAJFINANCE_2025_2026_A_23098027_07072026220255.pdf"
        ).resolve()
        self.assertEqual(config["_source_path"], expected)
        self.assertTrue(config["_source_path"].is_file())


class PageValidationTests(unittest.TestCase):
    def test_accepts_one_based_page_391_and_zero_based_index_390(self) -> None:
        validate_page(page_count=515, page_number=391, page_index=390)

    def test_rejects_out_of_range_page(self) -> None:
        with self.assertRaisesRegex(ExtractionError, "outside"):
            validate_page(page_count=390, page_number=391, page_index=390)

    def test_rejects_disagreeing_number_and_index(self) -> None:
        with self.assertRaisesRegex(ExtractionError, "disagree"):
            validate_page(page_count=515, page_number=391, page_index=391)


class ExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.config = load_config(CONFIG)
        cls.result = extract_document(cls.config)

    def test_expected_structure(self) -> None:
        validation = self.result["validation_summary"]
        self.assertTrue(validation["target_table_found"])
        self.assertEqual(validation["actual_row_count"], validation["expected_row_count"])
        self.assertEqual(
            validation["actual_value_cell_count"],
            validation["expected_value_cell_count"],
        )
        self.assertEqual(validation["years_detected"], self.config["target"]["years"])
        self.assertEqual(len(self.result["records"]), 24)

    def test_excludes_note_58_i_b(self) -> None:
        self.assertTrue(self.result["validation_summary"]["excluded_tables_avoided"])
        labels = " ".join(record["original_row_label"] for record in self.result["records"])
        self.assertNotIn("acquired through assignment", labels.lower())
        self.assertTrue(all(record["note"] == "58(I)(a)" for record in self.result["records"]))

    def test_missing_record_fails_structure_validation(self) -> None:
        records = self.result["records"][:-1]
        labels = list(dict.fromkeys(record["original_row_label"] for record in records))
        rows = [
            {
                "label": label,
                "values": [record["raw_value"] for record in records if record["original_row_label"] == label],
            }
            for label in labels
        ]
        with self.assertRaises(ExtractionError):
            validate_structure(rows, records, self.config["expected"])


if __name__ == "__main__":
    unittest.main()
