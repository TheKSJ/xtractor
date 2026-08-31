"""Benchmark the configured assignment-disclosure extractors against source-audited fixtures."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymupdf

from extract_transfer_assignment import ExtractionError, extract_document, load_config


ROOT = Path(__file__).resolve().parent
CASES = (
    (
        "bajaj",
        ROOT / "config" / "bajaj_transfer_assignment.yaml",
        ROOT / "tests" / "fixtures" / "bajaj_transfer_assignment_reference.json",
    ),
    (
        "chola",
        ROOT / "config" / "chola_transfer_assignment.yaml",
        ROOT / "tests" / "fixtures" / "chola_transfer_assignment_reference.json",
    ),
    (
        "ugro",
        ROOT / "config" / "ugro_transfer_assignment.yaml",
        ROOT / "tests" / "fixtures" / "ugro_transfer_assignment_reference.json",
    ),
)

RECORD_FIELDS = (
    "original_row_label",
    "canonical_metric",
    "year",
    "source_column_position",
    "raw_value",
    "normalized_value",
    "unit",
)
PROVENANCE_FIELDS = (
    "company",
    "reporting_period",
    "statement_scope",
    "source_document_id",
    "source_manifest_file",
    "source_filename",
    "source_file",
    "pdf_page_number",
    "pdf_page_index",
    "printed_page_number",
    "note",
    "table_title",
)


def _compact(value: str) -> str:
    normalized = value.lower().replace("\u20b9", "").replace("\u00e2\u201a\u00b9", "")
    return re.sub(r"[^a-z0-9]+", "", normalized)


def _complete_number_in_text(number: str, text: str) -> bool:
    """Find a whole numeric string, never a digit substring inside a larger number."""

    pattern = rf"(?<![0-9,.]){re.escape(number)}(?![0-9,.])"
    return re.search(pattern, text) is not None


def _complete_number_token(token: str, expected_number: str) -> bool:
    """Compare one complete PDF word with one expected number."""

    match = re.fullmatch(r"([0-9][0-9,]*(?:\.[0-9]+)?)(?:%)?", token.strip())
    if not match:
        return False
    try:
        return Decimal(match.group(1).replace(",", "")) == Decimal(
            expected_number.replace(",", "")
        )
    except Exception:
        return False


def _column_bounds(first_value_x: float, right_edges: list[float]) -> list[tuple[float, float]]:
    left_edges = [first_value_x, *right_edges[:-1]]
    if len(left_edges) != len(right_edges) or any(
        right_edge <= left_edge
        for left_edge, right_edge in zip(left_edges, right_edges)
    ):
        raise ExtractionError("Source value-column boundaries are not strictly increasing")
    return list(zip(left_edges, right_edges))


def _token_fits_column(
    token: dict[str, Any], left_edge: float, right_edge: float, tolerance: float = 1.0
) -> bool:
    """Require a token to stay inside both edges, allowing 1 PDF point of layout noise."""

    return (
        token["x0"] >= left_edge - tolerance
        and token["x1"] <= right_edge + tolerance
    )


def _fixture_records(fixture: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if isinstance(fixture, list):
        return {}, [dict(record) for record in fixture]
    if isinstance(fixture, dict) and isinstance(fixture.get("records"), list):
        provenance = fixture.get("provenance", {})
        if not isinstance(provenance, dict):
            raise ExtractionError("Fixture provenance must be a mapping")
        return dict(provenance), [dict(record) for record in fixture["records"]]
    raise ExtractionError("Fixture must be a record list or an object with records")


def _expected_records(config: dict[str, Any], fixture_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    mapping = config["target"]["canonical_metric_map"]
    result = []
    for record in fixture_records:
        enriched = dict(record)
        if "canonical_metric" not in enriched:
            label = str(enriched["original_row_label"])
            enriched["canonical_metric"] = str(mapping[label])
        result.append(enriched)
    return result


def _record_key(record: dict[str, Any], document_id: str) -> tuple[str, str, int, str]:
    return (
        str(record.get("source_document_id", document_id)),
        str(record["original_row_label"]),
        int(record["year"]),
        str(record["source_column_position"]),
    )


def _numeric_equal(left: Any, right: Any) -> bool:
    number_types = (int, float, Decimal)
    if isinstance(left, number_types) and not isinstance(left, bool) and isinstance(right, number_types) and not isinstance(right, bool):
        return Decimal(str(left)) == Decimal(str(right))
    return left == right


def _field_equal(field: str, expected: Any, actual: Any) -> bool:
    if field == "normalized_value":
        return _numeric_equal(expected, actual)
    return expected == actual


def _expected_provenance(config: dict[str, Any]) -> dict[str, Any]:
    source = config["source"]
    document = config["document"]
    entry = config["_source_manifest_entry"]
    return {
        "company": document["company"],
        "reporting_period": document["reporting_period"],
        "statement_scope": document["statement_scope"],
        "source_document_id": source["document_id"],
        "source_manifest_file": source["manifest_file"],
        "source_filename": entry["source_filename"],
        "source_file": str(source["source_file"]),
        "pdf_page_number": int(source["pdf_page_number"]),
        "pdf_page_index": int(source["pdf_page_index"]),
        "printed_page_number": int(source["printed_page_number"]),
        "note": config["target"]["note"],
        "table_title": config["target"]["table_title"],
    }


def _accuracy(correct: int, denominator: int) -> dict[str, Any]:
    return {
        "correct": correct,
        "denominator": denominator,
        "ratio": correct / denominator if denominator else 0.0,
    }


def _source_value_is_present(raw_value: str, page_text: str) -> bool:
    compact_page = _compact(page_text)
    numeric_parts = re.findall(r"[0-9][0-9,]*(?:\.[0-9]+)?", raw_value)
    if numeric_parts and not all(
        _complete_number_in_text(number, page_text) for number in numeric_parts
    ):
        return False
    if not numeric_parts:
        return _compact(raw_value) in compact_page
    if "%" in raw_value and "%" not in page_text:
        return False
    for word in ("crore", "crores", "lakh", "lakhs", "months", "month", "years", "year"):
        if word in raw_value.lower() and word not in page_text.lower():
            return False
    return True


def _source_layout_positions(
    page: pymupdf.Page, config: dict[str, Any], title_rect: pymupdf.Rect, lower_boundary: float
) -> tuple[list[dict[str, Any]], float, list[float]]:
    """Derive source column bands directly from page words, independently of the engine."""

    target = config["target"]
    words = []
    for x0, y0, x1, y1, text, block, line, position in page.get_text("words", sort=True):
        if y0 > title_rect.y1 and y1 < lower_boundary:
            words.append(
                {
                    "x0": x0,
                    "y0": y0,
                    "x1": x1,
                    "y1": y1,
                    "text": str(text),
                    "block": block,
                    "line": line,
                    "position": position,
                }
            )
    particulars = [word for word in words if word["text"] == "Particulars"]
    if len(particulars) != 1:
        raise ExtractionError(
            f"Independent source audit expected one Particulars header; found {len(particulars)}"
        )
    header = particulars[0]
    years = [int(year) for year in target["years"]]
    tolerance = float(target.get("year_header_tolerance_y", 2.0))
    year_words = []
    for year in years:
        matches = [
            word
            for word in words
            if word["text"] == str(year)
            and abs(word["y0"] - header["y0"]) <= tolerance
        ]
        if len(matches) != 1:
            raise ExtractionError(
                f"Independent source audit expected one {year} year header; found {len(matches)}"
            )
        year_words.append(matches[0])
    year_words.sort(key=lambda word: word["x0"])

    strategy = str(target.get("column_edge_strategy", "currency_and_unit"))
    if strategy == "currency_and_unit":
        currency_words = [word for word in words if word["text"] == "C"]
        unit_words = [word for word in words if word["text"].lower() == "crore"]
        expected_columns = int(config["expected"]["value_columns"])
        if len(currency_words) != expected_columns or len(unit_words) != expected_columns:
            raise ExtractionError(
                "Independent source audit could not establish Bajaj currency column bands"
            )
        currency_words.sort(key=lambda word: word["x0"])
        unit_words.sort(key=lambda word: word["x1"])
        first_value_x = currency_words[0]["x0"]
        right_edges = [word["x1"] for word in unit_words]
    elif strategy == "year_headers":
        first_value_x = float(target["value_start_x"])
        right_edges = [word["x1"] + 1.0 for word in year_words]
    elif strategy == "year_header_midpoints":
        first_value_x = float(target["value_start_x"])
        centers = [(word["x0"] + word["x1"]) / 2 for word in year_words]
        right_edges = [
            (centers[index] + centers[index + 1]) / 2
            for index in range(len(centers) - 1)
        ]
        right_edges.append(float(target.get("last_column_right_edge", page.rect.width)))
    else:
        raise ExtractionError(f"Independent source audit does not support layout {strategy!r}")
    return words, first_value_x, right_edges


def _line_groups(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for word in words:
        grouped.setdefault((int(word["block"]), int(word["line"])), []).append(word)
    lines = []
    for (block, line), line_words in grouped.items():
        line_words.sort(key=lambda word: (word["x0"], word["position"]))
        lines.append(
            {
                "block": block,
                "line": line,
                "words": line_words,
                "text": " ".join(word["text"] for word in line_words),
                "y0": min(word["y0"] for word in line_words),
            }
        )
    return sorted(lines, key=lambda item: (item["y0"], item["block"], item["line"]))


def _row_bands(
    words: list[dict[str, Any]], labels: list[str], first_value_x: float, lower_boundary: float
) -> dict[str, tuple[float, float]]:
    lines = _line_groups(words)
    starts: dict[str, float] = {}
    for label in labels:
        label_words = [token for token in re.findall(r"[A-Za-z0-9]+", label) if token]
        prefix = " ".join(label_words[:4])
        matches = [
            line
            for line in lines
            if line["y0"] < lower_boundary
            and any(word["x0"] < first_value_x for word in line["words"])
            and _compact(prefix) in _compact(line["text"])
        ]
        if len(matches) != 1:
            raise ExtractionError(
                f"Independent source audit could not locate one row start for {label!r}; found {len(matches)}"
            )
        starts[label] = float(matches[0]["y0"])
    ordered = sorted(starts.items(), key=lambda item: item[1])
    bands = {}
    for index, (label, start) in enumerate(ordered):
        end = ordered[index + 1][1] if index + 1 < len(ordered) else lower_boundary
        bands[label] = (start - 0.5, end - 0.5)
    return bands


def _direct_value_alignment_checks(
    page: pymupdf.Page,
    config: dict[str, Any],
    fixture_records: list[dict[str, Any]],
    title_rect: pymupdf.Rect,
    lower_boundary: float,
) -> dict[str, Any]:
    data_lower_boundary = min(
        lower_boundary,
        float(config["target"].get("row_data_bottom_y", lower_boundary)),
    )
    words, first_value_x, right_edges = _source_layout_positions(
        page, config, title_rect, data_lower_boundary
    )
    column_bounds = _column_bounds(first_value_x, right_edges)
    labels = list(dict.fromkeys(str(record["original_row_label"]) for record in fixture_records))
    bands = _row_bands(words, labels, first_value_x, data_lower_boundary)
    source_columns = [str(value) for value in config["target"]["source_columns"]]
    expected_years = [int(value) for value in config["target"]["years"]]
    source_column_layout = str(config["target"].get("source_column_layout", "columns_per_year"))
    checks = []
    for record in fixture_records:
        raw_value = str(record["raw_value"])
        compact_raw = _compact(raw_value)
        numeric_parts = re.findall(r"[0-9][0-9,]*(?:\.[0-9]+)?", raw_value)
        if numeric_parts:
            candidates = [
                word
                for word in words
                if any(
                    _complete_number_token(word["text"], number)
                    for number in numeric_parts
                )
            ]
        else:
            candidates = [
                word for word in words if _compact(word["text"]) == compact_raw
            ]
        row_start, row_end = bands[str(record["original_row_label"])]
        candidates = [
            word
            for word in candidates
            if word["y1"] > row_start and word["y0"] < row_end
        ]
        source_column_index = source_columns.index(str(record["source_column_position"]))
        if source_column_layout == "columns_per_year":
            column_index = expected_years.index(int(record["year"])) * len(source_columns) + source_column_index
        elif source_column_layout == "one_per_year":
            column_index = expected_years.index(int(record["year"]))
        else:
            raise ExtractionError(
                f"Independent source audit does not support column layout {source_column_layout!r}"
            )
        left_edge, right_edge = column_bounds[column_index]
        checks.append(
            any(
                _token_fits_column(word, left_edge, right_edge)
                for word in candidates
            )
        )
    return {
        "row_value_alignment_checks_passed": sum(checks),
        "row_value_alignment_checks_total": len(checks),
        "column_boundaries": [
            {"left": round(left, 3), "right": round(right, 3)}
            for left, right in column_bounds
        ],
        "passed": all(checks),
    }


def audit_fixture_against_source(
    config: dict[str, Any], fixture_records: list[dict[str, Any]]
) -> dict[str, Any]:
    """Audit fixture facts against the PDF directly, without calling extract_document."""

    source_path: Path = config["_source_path"]
    page_number = int(config["source"]["pdf_page_number"])
    page_index = int(config["source"]["pdf_page_index"])
    printed_page_number = int(config["source"]["printed_page_number"])
    document = pymupdf.open(source_path)
    try:
        page = document[page_index]
        page_text = page.get_text("text")
        compact_page = _compact(page_text)
        title = str(config["target"]["table_title"])
        title_rects = page.search_for(title)
        excluded_rects = [
            rect
            for value in config["target"]["excluded_table_titles"]
            for rect in page.search_for(str(value))
        ]
        preceding_rects = [
            rect
            for value in config["target"].get("preceding_excluded_table_titles", [])
            for rect in page.search_for(str(value))
        ]
        checks = {
            "file_exists": source_path.is_file(),
            "page_number_index_agree": page_index == page_number - 1,
            "page_index_in_range": 0 <= page_index < len(document),
            "target_title_present": _compact(title) in compact_page,
            "target_title_unique": len(title_rects) == 1,
            "preceding_boundary_present": (
                not config["target"].get("preceding_excluded_table_titles")
                or bool(preceding_rects)
            ),
            "preceding_boundary_before_target": (
                not preceding_rects
                or not title_rects
                or all(rect.y1 <= title_rects[0].y0 for rect in preceding_rects)
            ),
            "excluded_boundary_present": bool(excluded_rects),
            "printed_page_number_present": str(printed_page_number) in page_text,
        }
        for year in config["target"]["years"]:
            checks[f"year_{year}_present"] = str(year) in page_text

        lower_boundary = min((rect.y0 for rect in excluded_rects), default=page.rect.height)
        alignment = _direct_value_alignment_checks(
            page,
            config,
            fixture_records,
            title_rects[0],
            lower_boundary,
        )

        row_labels = {str(record["original_row_label"]) for record in fixture_records}
        row_checks = {
            label: _compact(label) in compact_page for label in sorted(row_labels)
        }
        value_checks = [
            _source_value_is_present(str(record["raw_value"]), page_text)
            for record in fixture_records
        ]
        unit_checks = []
        for record in fixture_records:
            unit = record.get("unit")
            label = str(record["original_row_label"]).lower()
            if unit == "INR_crore":
                unit_checks.append("crore" in page_text.lower() or "crores" in page_text.lower())
            elif unit == "INR_lakh":
                unit_checks.append("lakh" in page_text.lower())
            elif unit == "months":
                unit_checks.append("month" in label or "month" in page_text.lower())
            elif unit == "years":
                unit_checks.append("year" in label or "year" in page_text.lower())
            elif unit == "percent":
                unit_checks.append("%" in label or "%" in page_text)
            elif unit == "count":
                unit_checks.append("count" in label or "accounts" in label)
            elif unit == "text":
                unit_checks.append(_compact(str(record["raw_value"])) in compact_page)
            elif unit is None and str(record["raw_value"]) == "NA":
                unit_checks.append("na" in compact_page)
            else:
                unit_checks.append(False)

        source_audit_passed = (
            all(checks.values())
            and all(row_checks.values())
            and all(value_checks)
            and all(unit_checks)
            and alignment["passed"]
        )
        return {
            "method": "direct PDF text, word-coordinate and page-reference audit; extractor output is not used",
            "human_verified": False,
            "checks": checks,
            "row_label_checks": row_checks,
            "value_presence_checks_passed": sum(value_checks),
            "value_presence_checks_total": len(value_checks),
            "unit_checks_passed": sum(unit_checks),
            "unit_checks_total": len(unit_checks),
            "column_boundary_checks_passed": alignment[
                "row_value_alignment_checks_passed"
            ],
            "column_boundary_checks_total": alignment[
                "row_value_alignment_checks_total"
            ],
            "column_boundaries": alignment["column_boundaries"],
            "row_value_alignment_checks_passed": alignment[
                "row_value_alignment_checks_passed"
            ],
            "row_value_alignment_checks_total": alignment[
                "row_value_alignment_checks_total"
            ],
            "assignment_basis": {
                "years": list(config["target"]["years"]),
                "source_column_layout": config["target"].get("source_column_layout", "columns_per_year"),
                "source_columns": list(config["target"]["source_columns"]),
                "note": "Values are independently matched to source row bands and configured year/column bands using PDF word coordinates; visual review remains required for final human sign-off.",
            },
            "passed": source_audit_passed,
        }
    finally:
        document.close()


def compare_records(
    config: dict[str, Any], expected_records: list[dict[str, Any]], actual_records: list[dict[str, Any]]
) -> dict[str, Any]:
    document_id = str(config["source"]["document_id"])
    expected_by_key = Counter(_record_key(record, document_id) for record in expected_records)
    actual_by_key = Counter(_record_key(record, document_id) for record in actual_records)
    expected_lookup = {_record_key(record, document_id): record for record in expected_records}
    actual_lookup: dict[tuple[str, str, int, str], dict[str, Any]] = {}
    for record in actual_records:
        actual_lookup.setdefault(_record_key(record, document_id), record)

    missing = sorted(key for key in expected_by_key if actual_by_key[key] == 0)
    unexpected = sorted(key for key in actual_by_key if expected_by_key[key] == 0)
    duplicates = sorted(
        {key: count for key, count in actual_by_key.items() if count > 1}.items(),
        key=lambda item: item[0],
    )
    expected_keys = list(expected_by_key)
    field_matches = {field: 0 for field in (*RECORD_FIELDS, *PROVENANCE_FIELDS)}
    incorrect: list[dict[str, Any]] = []
    correct_records = 0
    for key in expected_keys:
        expected = expected_lookup[key]
        actual = actual_lookup.get(key)
        mismatches: dict[str, dict[str, Any]] = {}
        if actual is not None:
            for field in RECORD_FIELDS:
                expected_value = expected.get(field)
                if field == "canonical_metric" and expected_value is None:
                    expected_value = config["target"]["canonical_metric_map"][expected["original_row_label"]]
                actual_value = actual.get(field)
                if _field_equal(field, expected_value, actual_value):
                    field_matches[field] += 1
                else:
                    mismatches[field] = {"expected": expected_value, "actual": actual_value}
            expected_provenance = _expected_provenance(config)
            for field, expected_value in expected_provenance.items():
                actual_value = actual.get(field)
                if actual_value == expected_value:
                    field_matches[field] += 1
                else:
                    mismatches[field] = {"expected": expected_value, "actual": actual_value}
        if actual is not None and actual_by_key[key] == 1 and not mismatches:
            correct_records += 1
        elif actual is not None:
            incorrect.append({"key": key, "mismatches": mismatches})

    denominator = len(expected_records)
    return {
        "expected_record_count": denominator,
        "extracted_record_count": len(actual_records),
        "correct_record_count": correct_records,
        "missing_records": [list(key) for key in missing],
        "unexpected_records": [list(key) for key in unexpected],
        "duplicate_records": [
            {"key": list(key), "count": count} for key, count in duplicates
        ],
        "incorrect_records": incorrect,
        "field_match_counts": field_matches,
        "accuracy_denominator": denominator,
        "accuracy": {
            "complete_record": {
                "correct": correct_records,
                "denominator": denominator,
                "ratio": correct_records / denominator if denominator else 0.0,
            },
            "raw_value": {
                "correct": field_matches["raw_value"],
                "denominator": denominator,
                "ratio": field_matches["raw_value"] / denominator if denominator else 0.0,
            },
            "normalized_value_numeric_or_null": {
                "correct": field_matches["normalized_value"],
                "denominator": denominator,
                "ratio": field_matches["normalized_value"] / denominator if denominator else 0.0,
            },
            "unit": {
                "correct": field_matches["unit"],
                "denominator": denominator,
                "ratio": field_matches["unit"] / denominator if denominator else 0.0,
            },
            "canonical_metric": {
                "correct": field_matches["canonical_metric"],
                "denominator": denominator,
                "ratio": field_matches["canonical_metric"] / denominator if denominator else 0.0,
            },
            "provenance": {
                "correct": sum(field_matches[field] for field in PROVENANCE_FIELDS),
                "correct_fields": sum(field_matches[field] for field in PROVENANCE_FIELDS),
                "denominator": denominator * len(PROVENANCE_FIELDS),
                "ratio": sum(field_matches[field] for field in PROVENANCE_FIELDS)
                / (denominator * len(PROVENANCE_FIELDS))
                if denominator
                else 0.0,
            },
        },
    }


def _empty_comparison(expected_count: int) -> dict[str, Any]:
    """Represent a failed extraction without dropping its expected records."""

    field_matches = {field: 0 for field in (*RECORD_FIELDS, *PROVENANCE_FIELDS)}
    return {
        "expected_record_count": expected_count,
        "extracted_record_count": 0,
        "correct_record_count": 0,
        "missing_records": [],
        "unexpected_records": [],
        "duplicate_records": [],
        "incorrect_records": [],
        "field_match_counts": field_matches,
        "accuracy_denominator": expected_count,
        "accuracy": {
            "complete_record": _accuracy(0, expected_count),
            "raw_value": _accuracy(0, expected_count),
            "normalized_value_numeric_or_null": _accuracy(0, expected_count),
            "unit": _accuracy(0, expected_count),
            "canonical_metric": _accuracy(0, expected_count),
            "provenance": _accuracy(0, expected_count * len(PROVENANCE_FIELDS)),
        },
    }


def run_case(name: str, config_path: Path, fixture_path: Path) -> dict[str, Any]:
    started = time.perf_counter()
    config: dict[str, Any] | None = None
    fixture_provenance: dict[str, Any] = {}
    fixture_records: list[dict[str, Any]] = []
    expected_records: list[dict[str, Any]] = []
    source_audit: dict[str, Any] | None = None
    errors: list[str] = []
    warnings: list[str] = []
    extraction_runtime_seconds: float | None = None

    try:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        fixture_provenance, fixture_records = _fixture_records(fixture)
        provenance_sidecar = fixture_path.with_name(
            f"{fixture_path.stem.replace('_reference', '')}_provenance.json"
        )
        if not fixture_provenance and provenance_sidecar.is_file():
            sidecar = json.loads(provenance_sidecar.read_text(encoding="utf-8"))
            if not isinstance(sidecar, dict):
                raise ExtractionError("Fixture provenance sidecar must be a mapping")
            fixture_provenance = sidecar
    except Exception as exc:
        errors.append(f"fixture: {type(exc).__name__}: {exc}")

    try:
        config = load_config(config_path)
    except Exception as exc:
        errors.append(f"configuration: {type(exc).__name__}: {exc}")

    if config is not None:
        try:
            expected_records = _expected_records(config, fixture_records)
        except Exception as exc:
            errors.append(f"fixture mapping: {type(exc).__name__}: {exc}")
            expected_records = list(fixture_records)

        if not errors or not any(error.startswith("fixture:") for error in errors):
            try:
                source_audit = audit_fixture_against_source(config, expected_records)
            except Exception as exc:
                source_audit = {
                    "method": "direct PDF text, word-coordinate and page-reference audit; extractor output is not used",
                    "human_verified": False,
                    "passed": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                errors.append(f"source audit: {type(exc).__name__}: {exc}")

        actual_records: list[dict[str, Any]] = []
        extraction_started = time.perf_counter()
        try:
            result = extract_document(config)
            actual_records = list(result["records"])
            warnings = list(result["warnings"])
        except Exception as exc:
            errors.append(f"extraction: {type(exc).__name__}: {exc}")
        finally:
            extraction_runtime_seconds = time.perf_counter() - extraction_started

        comparison = compare_records(config, expected_records, actual_records)
        config_provenance = _expected_provenance(config)
        fixture_provenance_check = {
            field: {
                "expected": config_provenance[field],
                "fixture": fixture_provenance.get(field),
                "checked": field in fixture_provenance
                and fixture_provenance[field] == config_provenance[field],
            }
            for field in config_provenance
        }
        if any(not item["checked"] for item in fixture_provenance_check.values()):
            errors.append("fixture provenance: one or more required fields are missing or incorrect")
        passed = (
            not errors
            and source_audit is not None
            and source_audit["passed"]
            and comparison["correct_record_count"] == comparison["expected_record_count"]
            and not comparison["unexpected_records"]
            and not comparison["duplicate_records"]
        )
        return {
            "name": name,
            "status": "passed" if passed else "failed",
            "config_file": str(config_path.relative_to(ROOT)),
            "fixture_file": str(fixture_path.relative_to(ROOT)),
            "fixture_provenance_file": (
                str(provenance_sidecar.relative_to(ROOT))
                if provenance_sidecar.is_file()
                else None
            ),
            "source_document_id": config["source"]["document_id"],
            "source_audit": source_audit,
            "fixture_provenance_check": fixture_provenance_check,
            "comparison": comparison,
            "warnings": warnings,
            "errors": errors,
            "error": "; ".join(errors) if errors else None,
            "extraction_runtime_seconds": extraction_runtime_seconds,
            "total_case_runtime_seconds": time.perf_counter() - started,
        }

    comparison = _empty_comparison(len(fixture_records))
    return {
        "name": name,
        "status": "failed",
        "config_file": str(config_path.relative_to(ROOT)),
        "fixture_file": str(fixture_path.relative_to(ROOT)),
        "fixture_provenance_file": None,
        "comparison": comparison,
        "warnings": warnings,
        "errors": errors,
        "error": "; ".join(errors),
        "extraction_runtime_seconds": extraction_runtime_seconds,
        "total_case_runtime_seconds": time.perf_counter() - started,
    }


def build_report() -> dict[str, Any]:
    started = time.perf_counter()
    cases = [run_case(name, config, fixture) for name, config, fixture in CASES]
    expected_total = sum(
        case.get("comparison", {}).get("expected_record_count", 0) for case in cases
    )
    extracted_total = sum(
        case.get("comparison", {}).get("extracted_record_count", 0) for case in cases
    )
    correct_total = sum(
        case.get("comparison", {}).get("correct_record_count", 0) for case in cases
    )
    field_matches = {
        field: sum(
            case.get("comparison", {}).get("field_match_counts", {}).get(field, 0)
            for case in cases
        )
        for field in (*RECORD_FIELDS, *PROVENANCE_FIELDS)
    }
    provenance_denominator = expected_total * len(PROVENANCE_FIELDS)
    return {
        "benchmark": {
            "name": "configured three-lender assignment-disclosure benchmark",
            "scope": "Known FY2025-26 standalone annual reports only; not an unseen-report accuracy claim.",
            "human_verified": False,
            "matching_rule": "source_document_id + original_row_label + year + source_column_position",
            "missing_denominator_rule": "All expected fixture records remain in every accuracy denominator; missing records count as incorrect.",
            "normalized_number_rule": "Numeric normalized values compare numerically using Decimal(str(value)); raw_value compares exactly as text.",
            "runtime_rule": "Runtime measures config loading and extraction only; no manual-time savings claim is made.",
        },
        "cases": cases,
        "overall": {
            "status": "passed" if all(case["status"] == "passed" for case in cases) else "failed",
            "expected_record_count": expected_total,
            "extracted_record_count": extracted_total,
            "correct_record_count": correct_total,
            "field_match_counts": field_matches,
            "accuracy": {
                "complete_record": _accuracy(correct_total, expected_total),
                "raw_value": _accuracy(field_matches["raw_value"], expected_total),
                "normalized_value_numeric_or_null": _accuracy(
                    field_matches["normalized_value"], expected_total
                ),
                "unit": _accuracy(field_matches["unit"], expected_total),
                "canonical_metric": _accuracy(
                    field_matches["canonical_metric"], expected_total
                ),
                "provenance": _accuracy(
                    sum(field_matches[field] for field in PROVENANCE_FIELDS),
                    provenance_denominator,
                ),
            },
            "missing_record_count": sum(len(case.get("comparison", {}).get("missing_records", [])) for case in cases),
            "unexpected_record_count": sum(len(case.get("comparison", {}).get("unexpected_records", [])) for case in cases),
            "duplicate_record_count": sum(len(case.get("comparison", {}).get("duplicate_records", [])) for case in cases),
            "runtime_seconds": sum(case.get("extraction_runtime_seconds") or 0.0 for case in cases),
            "benchmark_wall_time_seconds": time.perf_counter() - started,
        },
    }


def write_json(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_summary(report: dict[str, Any], path: Path) -> None:
    lines = [
        "# Transfer-assignment benchmark summary",
        "",
        "Scope: known configured FY2025-26 standalone annual reports only. This is not an unseen-report accuracy claim.",
        "",
        f"Overall status: **{report['overall']['status']}**",
        f"Expected records: {report['overall']['expected_record_count']}",
        f"Extracted records: {report['overall']['extracted_record_count']}",
        f"Correct complete records: {report['overall']['correct_record_count']} / {report['overall']['expected_record_count']}",
        f"Overall complete-record accuracy: {report['overall']['accuracy']['complete_record']['correct']} / {report['overall']['accuracy']['complete_record']['denominator']} ({report['overall']['accuracy']['complete_record']['ratio']:.2%})",
        f"Overall raw-value accuracy: {report['overall']['accuracy']['raw_value']['correct']} / {report['overall']['accuracy']['raw_value']['denominator']} ({report['overall']['accuracy']['raw_value']['ratio']:.2%})",
        f"Overall normalized-value accuracy: {report['overall']['accuracy']['normalized_value_numeric_or_null']['correct']} / {report['overall']['accuracy']['normalized_value_numeric_or_null']['denominator']} ({report['overall']['accuracy']['normalized_value_numeric_or_null']['ratio']:.2%})",
        f"Overall unit accuracy: {report['overall']['accuracy']['unit']['correct']} / {report['overall']['accuracy']['unit']['denominator']} ({report['overall']['accuracy']['unit']['ratio']:.2%})",
        f"Overall canonical-metric accuracy: {report['overall']['accuracy']['canonical_metric']['correct']} / {report['overall']['accuracy']['canonical_metric']['denominator']} ({report['overall']['accuracy']['canonical_metric']['ratio']:.2%})",
        f"Overall provenance-field accuracy: {report['overall']['accuracy']['provenance']['correct']} / {report['overall']['accuracy']['provenance']['denominator']} ({report['overall']['accuracy']['provenance']['ratio']:.2%})",
        f"Extraction runtime total: {report['overall']['runtime_seconds']:.6f} seconds",
        "",
        "Accuracy denominators include all expected records; missing records are not removed.",
        "",
        "| Lender | Status | Expected | Extracted | Correct | Missing | Unexpected | Duplicates | Runtime (s) |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for case in report["cases"]:
        comparison = case.get("comparison", {})
        lines.append(
            f"| {case['name']} | {case['status']} | {comparison.get('expected_record_count', 'n/a')} | "
            f"{comparison.get('extracted_record_count', 'n/a')} | {comparison.get('correct_record_count', 'n/a')} | "
            f"{len(comparison.get('missing_records', []))} | {len(comparison.get('unexpected_records', []))} | "
            f"{len(comparison.get('duplicate_records', []))} | {case.get('extraction_runtime_seconds', 0.0) or 0.0:.6f} |"
        )
        accuracy = comparison.get("accuracy", {})
        if accuracy:
            lines.append(
                f"  - Accuracy — complete: {accuracy['complete_record']['correct']}/{accuracy['complete_record']['denominator']} ({accuracy['complete_record']['ratio']:.2%}); "
                f"raw: {accuracy['raw_value']['correct']}/{accuracy['raw_value']['denominator']} ({accuracy['raw_value']['ratio']:.2%}); "
                f"normalized: {accuracy['normalized_value_numeric_or_null']['correct']}/{accuracy['normalized_value_numeric_or_null']['denominator']} ({accuracy['normalized_value_numeric_or_null']['ratio']:.2%}); "
                f"unit: {accuracy['unit']['correct']}/{accuracy['unit']['denominator']} ({accuracy['unit']['ratio']:.2%}); "
                f"canonical: {accuracy['canonical_metric']['correct']}/{accuracy['canonical_metric']['denominator']} ({accuracy['canonical_metric']['ratio']:.2%}); "
                f"provenance fields: {accuracy['provenance']['correct']}/{accuracy['provenance']['denominator']} ({accuracy['provenance']['ratio']:.2%})"
            )
        if case.get("error"):
            lines.extend(["", f"Error: `{case['error']}`"])
        if case.get("warnings"):
            lines.extend(["", "Warnings:"])
            lines.extend(f"- {warning}" for warning in case["warnings"])
    lines.extend(
        [
            "",
            "The source audit checks the configured source page, title, years, row labels, complete numeric tokens, units, page references, and both left/right value-column boundaries directly from the PDF. It does not call the extractor to create expected values.",
            "",
            "Human visual review is still required for the final financial sign-off.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-output", default="outputs/benchmark_transfer_assignment.json")
    parser.add_argument("--summary-output", default="outputs/benchmark_transfer_assignment_summary.md")
    args = parser.parse_args(argv)
    report = build_report()
    json_path = (ROOT / args.json_output).resolve() if not Path(args.json_output).is_absolute() else Path(args.json_output)
    summary_path = (ROOT / args.summary_output).resolve() if not Path(args.summary_output).is_absolute() else Path(args.summary_output)
    write_json(report, json_path)
    write_summary(report, summary_path)
    print(f"Benchmark status: {report['overall']['status']}")
    print(f"Expected records: {report['overall']['expected_record_count']}")
    print(f"Extracted records: {report['overall']['extracted_record_count']}")
    print(f"Correct complete records: {report['overall']['correct_record_count']}")
    print(f"Runtime: {report['overall']['runtime_seconds']:.6f} seconds")
    print(f"JSON report: {json_path}")
    print(f"Summary report: {summary_path}")
    return 0 if report["overall"]["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
