"""Extract one configured assignment-transfer table from one configured PDF page."""

from __future__ import annotations

import argparse
import hashlib
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


def load_source_manifest(manifest_path: str | Path) -> dict[str, Any]:
    path = Path(manifest_path).resolve()
    if not path.is_file():
        raise ExtractionError(f"Source manifest does not exist: {path}")
    try:
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ExtractionError(f"Invalid YAML in source manifest {path}: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("documents"), dict):
        raise ExtractionError("Source manifest must contain a documents mapping")
    for document_id, entry in manifest["documents"].items():
        if not isinstance(entry, dict):
            raise ExtractionError(f"Manifest entry {document_id!r} must be a mapping")
        for key in (
            "company",
            "reporting_period",
            "statement_scope",
            "source_filename",
            "repository_path",
            "official_source_url",
            "sha256",
            "retrieved_at",
        ):
            _required(entry, key)
        if not re.fullmatch(r"[0-9a-fA-F]{64}", str(entry["sha256"])):
            raise ExtractionError(
                f"Manifest entry {document_id!r} has an invalid SHA-256 hash"
            )
        if not str(entry["official_source_url"]).strip():
            raise ExtractionError(
                f"Manifest entry {document_id!r} has an empty official source URL"
            )
    return manifest


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        "source.document_id",
        "source.manifest_file",
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
        "target.canonical_metric_map",
        "expected.row_count",
        "expected.value_columns",
        "expected.value_cell_count",
        "output_file",
    )
    for key in required_keys:
        _required(config, key)

    project_root = path.parent.parent
    manifest_setting = Path(str(config["source"]["manifest_file"]))
    source_setting = Path(str(config["source"]["source_file"]))
    output_setting = Path(str(config["output_file"]))
    config["_config_path"] = path
    config["_source_path"] = (
        source_setting if source_setting.is_absolute() else project_root / source_setting
    ).resolve()
    config["_manifest_path"] = (
        manifest_setting
        if manifest_setting.is_absolute()
        else project_root / manifest_setting
    ).resolve()
    config["_output_path"] = (
        output_setting if output_setting.is_absolute() else project_root / output_setting
    ).resolve()
    manifest = load_source_manifest(config["_manifest_path"])
    document_id = str(config["source"]["document_id"])
    manifest_entry = manifest["documents"].get(document_id)
    if not isinstance(manifest_entry, dict):
        raise ExtractionError(
            f"Source document ID {document_id!r} is not present in the manifest"
        )
    manifest_source_path = (
        project_root / Path(str(manifest_entry["repository_path"]))
    ).resolve()
    if manifest_source_path != config["_source_path"]:
        raise ExtractionError(
            f"Manifest path and configured source path disagree for {document_id!r}"
        )
    for config_value, manifest_key in (
        (config["document"]["company"], "company"),
        (config["document"]["reporting_period"], "reporting_period"),
        (config["document"]["statement_scope"], "statement_scope"),
    ):
        if str(config_value) != str(manifest_entry[manifest_key]):
            raise ExtractionError(
                f"Config and manifest disagree for {document_id!r}: {manifest_key}"
            )
    if config["_source_path"].name != str(manifest_entry["source_filename"]):
        raise ExtractionError(
            f"Manifest filename and configured source filename disagree for {document_id!r}"
        )
    config["_source_manifest"] = manifest
    config["_source_manifest_entry"] = manifest_entry
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


def normalize_value(
    raw_value: str, expected_unit: str | None = None
) -> tuple[int | float | str | None, str | None]:
    """Normalize a source cell, optionally using the configured row meaning."""

    if raw_value == "NA":
        return None, None

    numeric = r"([0-9,]+(?:\.[0-9]+)?)"
    if expected_unit == "count":
        match = re.fullmatch(numeric, raw_value)
        if not match:
            raise ExtractionError(
                f"Expected a numeric count but found {raw_value!r}"
            )
        value = _number(match.group(1))
        if not isinstance(value, int):
            raise ExtractionError(f"Expected an integer count but found {raw_value!r}")
        return value, "count"
    if expected_unit == "INR_crore":
        match = re.fullmatch(rf"₹\s*{numeric}\s+crore", raw_value)
        if not match:
            match = re.fullmatch(numeric, raw_value)
        if not match:
            raise ExtractionError(
                f"Expected an INR-crore value but found {raw_value!r}"
            )
        return _number(match.group(1)), "INR_crore"
    if expected_unit == "INR_lakh":
        match = re.fullmatch(rf"₹\s*{numeric}\s+lakh", raw_value)
        if not match:
            match = re.fullmatch(numeric, raw_value)
        if not match:
            raise ExtractionError(f"Expected an INR-lakh value but found {raw_value!r}")
        return _number(match.group(1)), "INR_lakh"
    if expected_unit == "percent":
        match = re.fullmatch(rf"{numeric}%?", raw_value)
        if not match:
            raise ExtractionError(f"Expected a percentage value but found {raw_value!r}")
        return _number(match.group(1)), "percent"
    if expected_unit == "months":
        match = re.fullmatch(rf"{numeric}(?:\s+months)?", raw_value)
        if not match:
            raise ExtractionError(f"Expected a months value but found {raw_value!r}")
        return _number(match.group(1)), "months"
    if expected_unit == "years":
        match = re.fullmatch(rf"{numeric}(?:\s+years)?", raw_value)
        if not match:
            raise ExtractionError(f"Expected a years value but found {raw_value!r}")
        return _number(match.group(1)), "years"
    if expected_unit == "text":
        return raw_value, "text"
    if expected_unit is not None:
        raise ExtractionError(f"Unsupported configured unit: {expected_unit!r}")

    crore = re.fullmatch(rf"₹\s*{numeric}\s+crore", raw_value)
    if crore:
        return _number(crore.group(1)), "INR_crore"
    percent = re.fullmatch(rf"{numeric}%", raw_value)
    if percent:
        return _number(percent.group(1)), "percent"
    months = re.fullmatch(rf"{numeric}\s+months", raw_value)
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
    manifest_entry = config["_source_manifest_entry"]
    actual_sha256 = sha256_file(source_path)
    expected_sha256 = str(manifest_entry["sha256"]).lower()
    if actual_sha256.lower() != expected_sha256:
        raise ExtractionError(
            f"SHA-256 mismatch for {source_path.name}: expected {expected_sha256}, "
            f"found {actual_sha256}"
        )

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

        preceding_excluded_titles = [
            str(value) for value in target.get("preceding_excluded_table_titles", [])
        ]
        preceding_excluded_rects = [
            _unique_rect(page, value, "preceding excluded table title")
            for value in preceding_excluded_titles
        ]
        if any(rect.y1 >= title_rect.y0 for rect in preceding_excluded_rects):
            raise ExtractionError(
                "A configured preceding excluded-table boundary is not before the target table"
            )

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
        year_header_tolerance_y = float(target.get("year_header_tolerance_y", 2.0))
        year_tokens: list[dict[str, Any]] = []
        for year in expected_years:
            matches = [
                token
                for token in tokens
                if token["text"] == str(year)
                and abs(token["y0"] - header["y0"]) <= year_header_tolerance_y
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

        expected_columns = int(config["expected"]["value_columns"])
        column_edge_strategy = str(target.get("column_edge_strategy", "currency_and_unit"))
        if column_edge_strategy == "currency_and_unit":
            repaired_currency = sorted(
                [token for token in tokens if token["repaired_font"]],
                key=lambda token: token["x0"],
            )
            if len(repaired_currency) != expected_columns:
                raise ExtractionError(
                    f"Expected {expected_columns} currency glyphs in the target table; "
                    f"found {len(repaired_currency)}"
                )
            currency_positions = [
                (token["x0"] + token["x1"]) / 2 for token in repaired_currency
            ]
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
        elif column_edge_strategy == "year_headers":
            if len(year_tokens) != expected_columns:
                raise ExtractionError(
                    f"Expected {expected_columns} year headers to establish value columns; "
                    f"found {len(year_tokens)}"
                )
            column_right_edges = [token["x1"] + 1.0 for token in year_tokens]
            if "value_start_x" not in target:
                raise ExtractionError(
                    "The year_headers column strategy requires target.value_start_x"
                )
            first_value_x = float(target["value_start_x"])
        elif column_edge_strategy == "year_header_midpoints":
            if len(year_tokens) != expected_columns:
                raise ExtractionError(
                    f"Expected {expected_columns} year headers to establish value columns; "
                    f"found {len(year_tokens)}"
                )
            centers = [(token["x0"] + token["x1"]) / 2 for token in year_tokens]
            column_right_edges = [
                (centers[index] + centers[index + 1]) / 2
                for index in range(len(centers) - 1)
            ]
            column_right_edges.append(
                float(target.get("last_column_right_edge", page.rect.width))
            )
            if "value_start_x" not in target:
                raise ExtractionError(
                    "The year_header_midpoints column strategy requires target.value_start_x"
                )
            first_value_x = float(target["value_start_x"])
        else:
            raise ExtractionError(f"Unsupported column edge strategy: {column_edge_strategy!r}")

        column_left_edges = [first_value_x, *column_right_edges[:-1]]
        if any(
            right_edge <= left_edge
            for left_edge, right_edge in zip(column_left_edges, column_right_edges)
        ):
            raise ExtractionError("Configured value-column boundaries are not strictly increasing")

        def build_row(
            row_tokens: list[dict[str, Any]],
            excluded_token_ids: set[int] | None = None,
        ) -> dict[str, Any] | None:
            excluded_token_ids = excluded_token_ids or set()
            row_tokens = [token for token in row_tokens if id(token) not in excluded_token_ids]
            label_tokens = [token for token in row_tokens if token["x0"] < first_value_x]
            value_tokens = [token for token in row_tokens if token["x0"] >= first_value_x]
            if not label_tokens or not value_tokens:
                return None
            columns: list[list[dict[str, Any]]] = [[] for _ in column_right_edges]
            for token in value_tokens:
                matching_columns = [
                    index
                    for index, (left_edge, right_edge) in enumerate(
                        zip(column_left_edges, column_right_edges)
                    )
                    if token["x0"] >= left_edge - 1.0
                    and token["x1"] <= right_edge + 1.0
                ]
                if len(matching_columns) != 1:
                    raise ExtractionError(
                        f"Token {token['text']!r} does not fit exactly one value column "
                        f"using both left and right boundaries"
                    )
                column_index = matching_columns[0]
                columns[column_index].append(token)
            values = [_join(column) for column in columns]
            if not all(values):
                return None
            return {
                "label": _join(label_tokens),
                "values": values,
                "top": min(token["y0"] for token in row_tokens),
                "bottom": max(token["y1"] for token in row_tokens),
            }

        rows: list[dict[str, Any]] = []
        row_extraction_strategy = str(target.get("row_extraction_strategy", "blocks"))
        if row_extraction_strategy == "blocks":
            grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for token in tokens:
                if token["y0"] > header["y1"]:
                    grouped[int(token["block"])].append(token)
            for block_tokens in grouped.values():
                row = build_row(block_tokens)
                if row:
                    rows.append(row)
        elif row_extraction_strategy == "leading_numbered_rows":
            expected_row_count = int(config["expected"]["row_count"])
            row_anchor_values = {str(value) for value in target.get("row_anchor_values", [])}
            if not row_anchor_values:
                raise ExtractionError(
                    "The leading_numbered_rows strategy requires target.row_anchor_values"
                )
            row_anchor_x_max = float(target.get("row_anchor_x_max", first_value_x))
            anchors = sorted(
                [
                    token
                    for token in tokens
                    if token["text"] in row_anchor_values
                    and token["x1"] <= row_anchor_x_max
                    and token["y0"] > header["y1"]
                ],
                key=lambda token: token["y0"],
            )
            if len(anchors) != expected_row_count:
                raise ExtractionError(
                    f"Expected {expected_row_count} numbered row anchors; found {len(anchors)}"
                )
            if [token["text"] for token in anchors] != [str(index) for index in range(1, expected_row_count + 1)]:
                raise ExtractionError(
                    "Numbered row anchors are missing or out of order"
                )
            if "row_data_bottom_y" not in target:
                raise ExtractionError(
                    "The leading_numbered_rows strategy requires target.row_data_bottom_y"
                )
            row_data_bottom_y = float(target["row_data_bottom_y"])
            if row_data_bottom_y <= anchors[-1]["y1"] or row_data_bottom_y >= lower_boundary:
                raise ExtractionError(
                    "Configured row_data_bottom_y does not bound the target rows"
                )
            for index, anchor in enumerate(anchors):
                end_y = anchors[index + 1]["y0"] if index + 1 < len(anchors) else row_data_bottom_y
                band_tokens = [
                    token
                    for token in tokens
                    if anchor["y0"] <= token["y0"] < end_y
                ]
                row = build_row(band_tokens, {id(anchor)})
                if row:
                    rows.append(row)
        else:
            raise ExtractionError(
                f"Unsupported row extraction strategy: {row_extraction_strategy!r}"
            )
        rows.sort(key=lambda row: row["top"])

        source_columns = [str(value) for value in target["source_columns"]]
        source_column_layout = str(target.get("source_column_layout", "columns_per_year"))
        if source_column_layout == "columns_per_year":
            if len(expected_years) * len(source_columns) != expected_columns:
                raise ExtractionError(
                    "Configured years and source columns do not multiply to expected.value_columns"
                )
        elif source_column_layout == "one_per_year":
            if len(expected_years) != len(source_columns) or len(source_columns) != expected_columns:
                raise ExtractionError(
                    "The one_per_year layout requires one configured source column per year"
                )
        else:
            raise ExtractionError(f"Unsupported source column layout: {source_column_layout!r}")

        canonical_metric_map = target["canonical_metric_map"]
        if not isinstance(canonical_metric_map, dict):
            raise ExtractionError("target.canonical_metric_map must be a mapping")
        metric_units = target.get("metric_units", {})
        if not isinstance(metric_units, dict):
            raise ExtractionError("target.metric_units must be a mapping")

        metadata = {
            "company": config["document"]["company"],
            "reporting_period": config["document"]["reporting_period"],
            "statement_scope": config["document"]["statement_scope"],
            "source_document_id": str(config["source"]["document_id"]),
            "source_manifest_file": str(config["source"]["manifest_file"]),
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
            if row["label"] not in canonical_metric_map:
                raise ExtractionError(
                    f"No canonical metric mapping configured for row {row['label']!r}"
                )
            canonical_metric = str(canonical_metric_map[row["label"]])
            if not canonical_metric:
                raise ExtractionError(
                    f"Canonical metric mapping is empty for row {row['label']!r}"
                )
            for position, raw_value in enumerate(row["values"]):
                if source_column_layout == "columns_per_year":
                    year = expected_years[position // len(source_columns)]
                    source_column = source_columns[position % len(source_columns)]
                else:
                    year = expected_years[position]
                    source_column = source_columns[position]
                normalized_value, unit = normalize_value(
                    raw_value, metric_units.get(canonical_metric)
                )
                records.append(
                    {
                        **{key: value for key, value in metadata.items() if key != "extraction_library"},
                        "original_row_label": row["label"],
                        "canonical_metric": canonical_metric,
                        "year": year,
                        "source_column_position": source_column,
                        "raw_value": raw_value,
                        "normalized_value": normalized_value,
                        "unit": unit,
                    }
                )

        validate_structure(rows, records, config["expected"])
        excluded_tables_avoided = (
            all(row["bottom"] < lower_boundary for row in rows)
            and all(rect.y1 < title_rect.y0 for rect in preceding_excluded_rects)
            and not any("acquired through assignment" in row["label"].lower() for row in rows)
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
        warnings = [str(value) for value in config.get("known_warnings", [])]
        if visible_subheaders:
            raise ExtractionError(
                f"Unexpected visible subcolumn headings require review: {visible_subheaders}"
            )
        if bool(target.get("warn_if_no_visible_subheaders", True)):
            warnings.append(
                "Subcolumn headings are not visible; positions are preserved only as "
                "column_1 and column_2 under each year, and their meaning is not inferred."
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
    records = result["records"]
    positions = list(
        dict.fromkeys(
            f"{record['year']} {record['source_column_position']}"
            for record in records
        )
    )
    print(f"Extracted rows ({', '.join(positions)}):")
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
        f"printed annual-report page {result['metadata']['printed_page_number']}; "
        "a successful run does not establish financial accuracy."
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
