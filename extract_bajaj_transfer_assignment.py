"""Extract one configured assignment-transfer table from one configured PDF page."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import pymupdf
import yaml


class ExtractionError(RuntimeError):
    """Raised when extraction cannot be completed without ambiguity."""


def _required(mapping: dict[str, Any], path: str) -> Any:
    value: Any = mapping
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ExtractionError(f"Missing required configuration key: {path}")
        value = value[part]
    return value


def load_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path).resolve()
    if not path.is_file():
        raise ExtractionError(f"Configuration file does not exist: {path}")

    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ExtractionError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise ExtractionError(f"Configuration must be a non-empty mapping: {path}")

    required_keys = (
        "document.company",
        "document.reporting_period",
        "document.statement_scope",
        "source.source_file",
        "source.pdf_page_number",
        "source.pdf_page_index",
        "source.printed_page_number",
        "target.note",
        "target.note_anchors",
        "target.table_title",
        "target.excluded_table_titles",
        "target.years",
        "target.source_columns",
        "expected.row_count",
        "expected.value_columns",
        "expected.value_cell_count",
        "output_file",
    )
    for key in required_keys:
        _required(config, key)

    project_root = path.parent.parent
    source_setting = Path(str(config["source"]["source_file"]))
    output_setting = Path(str(config["output_file"]))
    config["_config_path"] = path
    config["_source_path"] = (
        source_setting if source_setting.is_absolute() else project_root / source_setting
    ).resolve()
    config["_output_path"] = (
        output_setting if output_setting.is_absolute() else project_root / output_setting
    ).resolve()
    return config


def validate_page(page_count: int, page_number: int, page_index: int) -> None:
    if page_number < 1:
        raise ExtractionError(f"PDF page number must be at least 1; got {page_number}")
    if page_index != page_number - 1:
        raise ExtractionError(
            f"Configured PDF page number/index disagree: {page_number} is one-based, "
            f"so its zero-based index must be {page_number - 1}, not {page_index}"
        )
    if page_index < 0 or page_index >= page_count:
        raise ExtractionError(
            f"Configured PDF page {page_number} (index {page_index}) is outside "
            f"the document's {page_count} pages"
        )


def _unique_rect(page: pymupdf.Page, text: str, label: str) -> pymupdf.Rect:
    matches = page.search_for(text)
    if len(matches) != 1:
        raise ExtractionError(
            f"Expected exactly one {label} match for {text!r}; found {len(matches)}"
        )
    return matches[0]


def _printed_glyphs(
    page: pymupdf.Page,
    clip: pymupdf.Rect,
    repairs: list[dict[str, str]],
) -> list[tuple[pymupdf.Rect, str, str]]:
    glyphs: list[tuple[pymupdf.Rect, str, str]] = []
    raw = page.get_text("rawdict", clip=clip)
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for repair in repairs:
                    if span.get("font") != repair.get("font"):
                        continue
                    for char in span.get("chars", []):
                        if char.get("c") == repair.get("extracted"):
                            glyphs.append(
                                (
                                    pymupdf.Rect(char["bbox"]),
                                    str(repair["printed"]),
                                    str(repair["font"]),
                                )
                            )
    return glyphs


def _tokens(
    page: pymupdf.Page,
    clip: pymupdf.Rect,
    glyphs: list[tuple[pymupdf.Rect, str, str]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for word in page.get_text("words", clip=clip, sort=True):
        x0, y0, x1, y1, text, block, line, position = word
        rect = pymupdf.Rect(x0, y0, x1, y1)
        printed = str(text)
        repaired_font = None
        for glyph_rect, replacement, font in glyphs:
            if rect.intersects(glyph_rect) and rect.contains(glyph_rect):
                printed = replacement
                repaired_font = font
                break
        result.append(
            {
                "x0": x0,
                "y0": y0,
                "x1": x1,
                "y1": y1,
                "text": printed,
                "block": block,
                "line": line,
                "position": position,
                "repaired_font": repaired_font,
            }
        )
    return result


def _join(tokens: list[dict[str, Any]]) -> str:
    ordered = sorted(tokens, key=lambda token: (token["line"], token["position"], token["x0"]))
    return " ".join(token["text"] for token in ordered).strip()


def _number(value: str) -> int | float:
    decimal = Decimal(value.replace(",", ""))
    if decimal == decimal.to_integral_value():
        return int(decimal)
    return float(decimal)


def normalize_value(raw_value: str) -> tuple[int | float | str, str]:
    crore = re.fullmatch(r"₹\s*([0-9,]+(?:\.[0-9]+)?)\s+crore", raw_value)
    if crore:
        return _number(crore.group(1)), "INR_crore"
    percent = re.fullmatch(r"([0-9,]+(?:\.[0-9]+)?)%", raw_value)
    if percent:
        return _number(percent.group(1)), "percent"
    months = re.fullmatch(r"([0-9,]+(?:\.[0-9]+)?)\s+months", raw_value)
    if months:
        return _number(months.group(1)), "months"
    return raw_value, "text"


def validate_structure(
    rows: list[dict[str, Any]], records: list[dict[str, Any]], expected: dict[str, int]
) -> None:
    expected_rows = int(expected["row_count"])
    expected_columns = int(expected["value_columns"])
    expected_cells = int(expected["value_cell_count"])
    if len(rows) != expected_rows:
        raise ExtractionError(f"Expected {expected_rows} rows; extracted {len(rows)}")
    bad_rows = [row["label"] for row in rows if len(row["values"]) != expected_columns]
    if bad_rows:
        raise ExtractionError(
            f"Rows do not contain exactly {expected_columns} values: {bad_rows}"
        )
    if any(not value for row in rows for value in row["values"]):
        raise ExtractionError("At least one expected value cell is empty")
    if len(records) != expected_cells:
        raise ExtractionError(
            f"Expected {expected_cells} value-cell records; extracted {len(records)}"
        )


def extract_document(config: dict[str, Any]) -> dict[str, Any]:
    source_path: Path = config["_source_path"]
    if not source_path.is_file():
        raise ExtractionError(f"Configured source PDF does not exist: {source_path}")

    page_number = int(config["source"]["pdf_page_number"])
    page_index = int(config["source"]["pdf_page_index"])
    try:
        document = pymupdf.open(source_path)
    except Exception as exc:
        raise ExtractionError(f"Could not open PDF {source_path}: {exc}") from exc

    try:
        validate_page(len(document), page_number, page_index)
        page = document[page_index]
        target = config["target"]
        title = str(target["table_title"])
        title_rect = _unique_rect(page, title, "target table title")

        excluded_titles = [str(value) for value in target["excluded_table_titles"]]
        excluded_rects = [
            _unique_rect(page, value, "excluded table title") for value in excluded_titles
        ]
        lower_boundary = min(rect.y0 for rect in excluded_rects)
        if lower_boundary <= title_rect.y1:
            raise ExtractionError("Excluded-table boundary occurs before the target table")

        for anchor in target["note_anchors"]:
            matches = [
                rect
                for rect in page.search_for(str(anchor))
                if rect.y0 <= title_rect.y0 + 1.0
            ]
            if not matches:
                raise ExtractionError(
                    f"Target title was found, but note anchor {anchor!r} was not found before it"
                )

        clip = pymupdf.Rect(0, title_rect.y1, page.rect.width, lower_boundary)
        repairs = list(config.get("text_layer_repairs", []))
        glyphs = _printed_glyphs(page, clip, repairs)
        tokens = _tokens(page, clip, glyphs)

        particulars = [token for token in tokens if token["text"] == "Particulars"]
        if len(particulars) != 1:
            raise ExtractionError(
                f"Expected one Particulars header inside target boundary; found {len(particulars)}"
            )
        header = particulars[0]
        expected_years = [int(year) for year in target["years"]]
        year_tokens: list[dict[str, Any]] = []
        for year in expected_years:
            matches = [
                token
                for token in tokens
                if token["text"] == str(year)
                and abs(token["y0"] - header["y0"]) < 2.0
            ]
            if len(matches) != 1:
                raise ExtractionError(
                    f"Expected one visible {year} year header; found {len(matches)}"
                )
            year_tokens.append(matches[0])
        year_tokens.sort(key=lambda token: token["x0"])
        years_detected = [int(token["text"]) for token in year_tokens]
        if years_detected != expected_years:
            raise ExtractionError(
                f"Year headers are misordered: expected {expected_years}, found {years_detected}"
            )

        repaired_currency = sorted(
            [token for token in tokens if token["repaired_font"]], key=lambda token: token["x0"]
        )
        expected_columns = int(config["expected"]["value_columns"])
        if len(repaired_currency) != expected_columns:
            raise ExtractionError(
                f"Expected {expected_columns} currency glyphs in the target table; "
                f"found {len(repaired_currency)}"
            )
        currency_positions = [(token["x0"] + token["x1"]) / 2 for token in repaired_currency]
        if currency_positions != sorted(currency_positions) or len(set(currency_positions)) != len(currency_positions):
            raise ExtractionError("Could not establish distinct left-to-right value columns")
        unit_tokens = sorted(
            [token for token in tokens if token["text"].lower() == "crore"],
            key=lambda token: token["x1"],
        )
        if len(unit_tokens) != expected_columns:
            raise ExtractionError(
                f"Expected {expected_columns} crore unit labels to establish cell edges; "
                f"found {len(unit_tokens)}"
            )
        column_right_edges = [token["x1"] for token in unit_tokens]
        first_value_x = repaired_currency[0]["x0"]

        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for token in tokens:
            if token["y0"] > header["y1"]:
                grouped[int(token["block"])].append(token)

        rows: list[dict[str, Any]] = []
        for block_tokens in grouped.values():
            label_tokens = [token for token in block_tokens if token["x0"] < first_value_x]
            value_tokens = [token for token in block_tokens if token["x0"] >= first_value_x]
            if not label_tokens or not value_tokens:
                continue
            columns: list[list[dict[str, Any]]] = [[] for _ in column_right_edges]
            for token in value_tokens:
                matching_columns = [
                    index
                    for index, right_edge in enumerate(column_right_edges)
                    if token["x1"] <= right_edge + 1.0
                ]
                if not matching_columns:
                    raise ExtractionError(
                        f"Token {token['text']!r} falls outside the four established value columns"
                    )
                column_index = matching_columns[0]
                columns[column_index].append(token)
            values = [_join(column) for column in columns]
            if all(values):
                rows.append(
                    {
                        "label": _join(label_tokens),
                        "values": values,
                        "top": min(token["y0"] for token in block_tokens),
                        "bottom": max(token["y1"] for token in block_tokens),
                    }
                )
        rows.sort(key=lambda row: row["top"])

        source_columns = [str(value) for value in target["source_columns"]]
        if len(expected_years) * len(source_columns) != expected_columns:
            raise ExtractionError(
                "Configured years and source columns do not multiply to expected.value_columns"
            )

        metadata = {
            "company": config["document"]["company"],
            "reporting_period": config["document"]["reporting_period"],
            "statement_scope": config["document"]["statement_scope"],
            "source_filename": source_path.name,
            "source_file": str(config["source"]["source_file"]),
            "pdf_page_number": page_number,
            "pdf_page_index": page_index,
            "printed_page_number": int(config["source"]["printed_page_number"]),
            "note": target["note"],
            "table_title": title,
            "extraction_library": f"PyMuPDF {pymupdf.__version__}",
        }
        records: list[dict[str, Any]] = []
        for row in rows:
            for position, raw_value in enumerate(row["values"]):
                year = expected_years[position // len(source_columns)]
                source_column = source_columns[position % len(source_columns)]
                normalized_value, unit = normalize_value(raw_value)
                records.append(
                    {
                        **{key: value for key, value in metadata.items() if key != "extraction_library"},
                        "original_row_label": row["label"],
                        "year": year,
                        "source_column_position": source_column,
                        "raw_value": raw_value,
                        "normalized_value": normalized_value,
                        "unit": unit,
                    }
                )

        validate_structure(rows, records, config["expected"])
        excluded_tables_avoided = all(row["bottom"] < lower_boundary for row in rows) and not any(
            "acquired through assignment" in row["label"].lower() for row in rows
        )
        if not excluded_tables_avoided:
            raise ExtractionError("The extracted region overlaps an explicitly excluded table")

        first_row_top = min(row["top"] for row in rows)
        visible_subheaders = [
            token["text"]
            for token in tokens
            if token["y0"] > max(year["y1"] for year in year_tokens)
            and token["y1"] < first_row_top
        ]
        warnings = []
        if not visible_subheaders:
            warnings.append(
                "Subcolumn headings are not visible; positions are preserved only as "
                "column_1 and column_2 under each year, and their meaning is not inferred."
            )
        else:
            raise ExtractionError(
                f"Unexpected visible subcolumn headings require review: {visible_subheaders}"
            )
        if glyphs:
            warnings.append(
                "The PDF text layer encodes the printed rupee sign as C in the ITFRupee font; "
                "raw values restore that font-specific glyph to ₹."
            )

        return {
            "metadata": metadata,
            "records": records,
            "warnings": warnings,
            "validation_summary": {
                "expected_row_count": int(config["expected"]["row_count"]),
                "actual_row_count": len(rows),
                "expected_value_cell_count": int(config["expected"]["value_cell_count"]),
                "actual_value_cell_count": len(records),
                "years_detected": years_detected,
                "target_table_found": True,
                "excluded_tables_avoided": excluded_tables_avoided,
            },
        }
    finally:
        document.close()


def write_result(result: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(output_path)


def print_summary(result: dict[str, Any], output_path: Path) -> None:
    print("Extracted rows (2026 column_1, 2026 column_2, 2025 column_1, 2025 column_2):")
    records = result["records"]
    labels = list(dict.fromkeys(record["original_row_label"] for record in records))
    for label in labels:
        values = [record["raw_value"] for record in records if record["original_row_label"] == label]
        print(f"- {label}: {' | '.join(values)}")
    print("Warnings and ambiguities:")
    for warning in result["warnings"]:
        print(f"- {warning}")
    print(f"Output: {output_path.resolve()}")
    print(
        "Visual verification still required: compare every extracted value against "
        "printed annual-report page 358; a successful run does not establish financial accuracy."
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default="config/bajaj_transfer_assignment.yaml",
        help="Path to the YAML configuration",
    )
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        result = extract_document(config)
        output_path: Path = config["_output_path"]
        write_result(result, output_path)
        print_summary(result, output_path)
        return 0
    except ExtractionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
