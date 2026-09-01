from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import benchmark_transfer_assignment as benchmark
import extract_bajaj_transfer_assignment as legacy_entry_point

from extract_transfer_assignment import (
    ExtractionError,
    extract_document,
    load_config,
    normalize_value,
)


ROOT = Path(__file__).resolve().parents[1]
BAJAJ_CONFIG = ROOT / "config" / "bajaj_transfer_assignment.yaml"


@unittest.skipUnless((ROOT / "data" / "raw" / "AR_29642_BAJFINANCE_2025_2026_A_23098027_07072026220255.pdf").is_file(), "real annual-report PDFs not present")
class ProvenanceAndNegativeInputTests(unittest.TestCase):
    def test_value_column_requires_both_left_and_right_boundaries(self) -> None:
        self.assertTrue(
            benchmark._token_fits_column({"x0": 101.0, "x1": 199.0}, 100.0, 200.0)
        )
        self.assertFalse(
            benchmark._token_fits_column({"x0": 98.9, "x1": 150.0}, 100.0, 200.0)
        )
        self.assertFalse(
            benchmark._token_fits_column({"x0": 150.0, "x1": 201.1}, 100.0, 200.0)
        )

    def test_number_matching_rejects_partial_digit_matches(self) -> None:
        self.assertTrue(benchmark._complete_number_in_text("1,537.22", "1,537.22 crore"))
        self.assertFalse(benchmark._complete_number_in_text("1,537", "1,537.22 crore"))
        self.assertTrue(benchmark._complete_number_token("1,537.22", "1,537.22"))
        self.assertFalse(benchmark._complete_number_token("1,537", "1,537.22"))

    def test_source_hash_mismatch_is_rejected(self) -> None:
        config = load_config(BAJAJ_CONFIG)
        broken = copy.deepcopy(config)
        broken["_source_manifest_entry"] = dict(config["_source_manifest_entry"])
        broken["_source_manifest_entry"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ExtractionError, "SHA-256 mismatch"):
            extract_document(broken)

    def test_malformed_count_and_currency_are_rejected(self) -> None:
        with self.assertRaisesRegex(ExtractionError, "numeric count"):
            normalize_value("not-a-count", "count")
        with self.assertRaisesRegex(ExtractionError, "integer count"):
            normalize_value("12.5", "count")
        with self.assertRaisesRegex(ExtractionError, "INR-crore"):
            normalize_value("not-a-currency-value", "INR_crore")

    def test_missing_records_stay_in_benchmark_denominator(self) -> None:
        config = load_config(BAJAJ_CONFIG)
        expected = {
            "original_row_label": "Amount of loans transferred through assignment",
            "canonical_metric": "loan_amount_assigned",
            "year": 2026,
            "source_column_position": "column_1",
            "raw_value": "â‚¹ 1,537.22 crore",
            "normalized_value": 1537.22,
            "unit": "INR_crore",
        }
        comparison = benchmark.compare_records(config, [expected], [])
        self.assertEqual(comparison["missing_records"], [[
            "bajaj_finance_fy2025_26_standalone",
            "Amount of loans transferred through assignment",
            2026,
            "column_1",
        ]])
        self.assertEqual(comparison["accuracy"]["raw_value"]["correct"], 0)
        self.assertEqual(comparison["accuracy"]["raw_value"]["denominator"], 1)
        self.assertEqual(comparison["accuracy"]["complete_record"]["denominator"], 1)

    def test_duplicate_and_unexpected_records_are_reported(self) -> None:
        config = load_config(BAJAJ_CONFIG)
        fixture = json.loads(
            (ROOT / "tests" / "fixtures" / "bajaj_transfer_assignment_reference.json").read_text(
                encoding="utf-8"
            )
        )
        expected = benchmark._expected_records(config, [fixture[0]])
        actual = [dict(expected[0]), dict(expected[0]), {
            **expected[0],
            "year": 2025,
        }]
        comparison = benchmark.compare_records(config, expected, actual)
        self.assertEqual(len(comparison["duplicate_records"]), 1)
        self.assertEqual(len(comparison["unexpected_records"]), 1)
        self.assertEqual(comparison["correct_record_count"], 0)

    def test_failed_extraction_keeps_expected_records_in_denominator(self) -> None:
        fixture_path = ROOT / "tests" / "fixtures" / "bajaj_transfer_assignment_reference.json"
        with patch.object(
            benchmark,
            "extract_document",
            side_effect=ExtractionError("deliberate extraction failure"),
        ):
            case = benchmark.run_case("bajaj", BAJAJ_CONFIG, fixture_path)
        self.assertEqual(case["status"], "failed")
        self.assertEqual(case["comparison"]["expected_record_count"], 24)
        self.assertEqual(case["comparison"]["extracted_record_count"], 0)
        self.assertEqual(len(case["comparison"]["missing_records"]), 24)
        self.assertEqual(case["comparison"]["accuracy_denominator"], 24)

    def test_old_bajaj_entry_point_remains_compatible(self) -> None:
        from extract_transfer_assignment import extract_document as engine_extract_document

        self.assertIs(legacy_entry_point.extract_document, engine_extract_document)


if __name__ == "__main__":
    unittest.main()
