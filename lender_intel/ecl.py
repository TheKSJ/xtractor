"""Configuration-driven extraction of ECL allowance movement tables."""

from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pymupdf
import yaml

from .errors import ConfigurationError, ExtractionError


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required(mapping: dict[str, Any], path: str) -> Any:
    value: Any = mapping
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise ConfigurationError(f"Missing required configuration key: {path}")
        value = value[part]
    return value


def load_ecl_config(path_value: str | Path) -> dict[str, Any]:
    path = Path(path_value).resolve()
    if not path.is_file():
        raise ConfigurationError(f"ECL configuration does not exist: {path}")
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(config, dict) or config.get("disclosure_family") != "ecl_stage_movement":
        raise ConfigurationError("ECL configuration must set disclosure_family=ecl_stage_movement")
    for key in (
        "document.company", "document.reporting_period", "document.statement_scope",
        "source.document_id", "source.manifest_file", "source.source_file",
        "tables", "output_file",
    ):
        _required(config, key)
    if not isinstance(config["tables"], list) or not config["tables"]:
        raise ConfigurationError("ECL configuration tables must be a non-empty list")
    for index, table in enumerate(config["tables"]):
        if not isinstance(table, dict):
            raise ConfigurationError(f"ECL table {index} must be a mapping")
        for key in ("note", "table_title", "pdf_page_number", "printed_page_number", "unit", "population_scope", "stage_columns", "rows"):
            if key not in table or table[key] in (None, ""):
                raise ConfigurationError(f"Missing required configuration key: tables[{index}].{key}")
        if not isinstance(table["stage_columns"], list) or not table["stage_columns"]:
            raise ConfigurationError(f"ECL table {index} stage_columns must be a non-empty list")
        if not isinstance(table["rows"], list) or not table["rows"]:
            raise ConfigurationError(f"ECL table {index} rows must be a non-empty list")
        for column_index, column in enumerate(table["stage_columns"]):
            for key in ("original_stage_label", "source_column_position", "x0", "x1"):
                if key not in column:
                    raise ConfigurationError(f"Missing required configuration key: tables[{index}].stage_columns[{column_index}].{key}")
        for row_index, row in enumerate(table["rows"]):
            for key in ("year", "y", "original_row_label", "canonical_movement_category"):
                if key not in row:
                    raise ConfigurationError(f"Missing required configuration key: tables[{index}].rows[{row_index}].{key}")
    root = path.parent.parent
    source = Path(str(config["source"]["source_file"]))
    manifest = Path(str(config["source"]["manifest_file"]))
    output = Path(str(config["output_file"]))
    config["_config_path"] = path
    config["_source_path"] = (source if source.is_absolute() else root / source).resolve()
    config["_manifest_path"] = (manifest if manifest.is_absolute() else root / manifest).resolve()
    config["_output_path"] = (output if output.is_absolute() else root / output).resolve()
    try:
        manifest_data = yaml.safe_load(config["_manifest_path"].read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"Source manifest does not exist: {config['_manifest_path']}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid source manifest: {exc}") from exc
    entry = (manifest_data or {}).get("documents", {}).get(str(config["source"]["document_id"]))
    if not isinstance(entry, dict):
        raise ConfigurationError(f"Document ID {config['source']['document_id']!r} is absent from source manifest")
    if Path(str(entry.get("repository_path", ""))).as_posix().lower() != Path(str(config["source"]["source_file"])).as_posix().lower():
        raise ConfigurationError("Manifest repository path and ECL source path disagree")
    for field in ("company", "reporting_period", "statement_scope"):
        if str(config["document"][field]) != str(entry.get(field)):
            raise ConfigurationError(f"Config and manifest disagree for {field}")
    config["_source_manifest_entry"] = entry
    return config


def _number(raw: str) -> int | float:
    value = Decimal(raw.replace(",", ""))
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def normalize_ecl_value(raw: str) -> tuple[int | float | None, str]:
    """Return a numeric value while retaining whether a dash was reported."""
    raw = raw.strip()
    if raw in {"-", "—", "–"}:
        return 0, "reported_nil"
    match = re.fullmatch(r"\(([0-9,]+(?:\.[0-9]+)?)\)", raw)
    sign = -1 if match else 1
    text = match.group(1) if match else raw
    if not re.fullmatch(r"[0-9,]+(?:\.[0-9]+)?", text):
        raise ExtractionError(f"Invalid ECL numeric value: {raw!r}")
    return sign * _number(text), "reported_value"


def _line_words(page: pymupdf.Page) -> list[dict[str, Any]]:
    words = []
    for x0, y0, x1, y1, text, block, line, position in page.get_text("words", sort=True):
        words.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": str(text), "block": block, "line": line, "position": position})
    return words


def _joined(tokens: list[dict[str, Any]]) -> str:
    return " ".join(t["text"] for t in sorted(tokens, key=lambda t: (t["x0"], t["position"]))).strip()


def _cell(words: list[dict[str, Any]], y: float, bounds: list[float], tolerance: float) -> str:
    tokens = [w for w in words if abs(w["y0"] - y) <= tolerance and w["x0"] >= bounds[0] and w["x1"] <= bounds[1]]
    if not tokens:
        raise ExtractionError(f"No ECL value found at y={y} in x={bounds}")
    return _joined(tokens)


def _page_for(document: pymupdf.Document, page_number: int, page_index: int) -> pymupdf.Page:
    if page_number < 1 or page_index != page_number - 1 or page_index >= len(document):
        raise ExtractionError(f"Invalid ECL page reference {page_number}/{page_index}")
    return document[page_index]


def extract_ecl_document(config: dict[str, Any]) -> dict[str, Any]:
    source = Path(config["_source_path"])
    if not source.is_file():
        raise ExtractionError(f"Configured ECL source PDF does not exist: {source}")
    expected_hash = str(config["_source_manifest_entry"].get("sha256", "")).lower()
    actual_hash = _sha256(source)
    if expected_hash and actual_hash != expected_hash:
        raise ExtractionError(f"SHA-256 mismatch for {source.name}: expected {expected_hash}, found {actual_hash}")
    try:
        document = pymupdf.open(source)
    except Exception as exc:
        raise ExtractionError(f"Could not open ECL PDF {source}: {exc}") from exc
    records: list[dict[str, Any]] = []
    warnings: list[str] = [str(x) for x in config.get("known_warnings", [])]
    validation_pages: list[dict[str, Any]] = []
    try:
        for table_index, table in enumerate(config["tables"]):
            page_number = int(table["pdf_page_number"])
            page_index = int(table.get("pdf_page_index", page_number - 1))
            page = _page_for(document, page_number, page_index)
            text = page.get_text("text")
            normalized_text = " ".join(text.split()).lower()
            title = str(table["table_title"])
            title_anchor = str(table.get("title_anchor", title))
            if title_anchor.lower() not in normalized_text:
                raise ExtractionError(f"ECL table title not found on PDF page {page_number}: {title_anchor}")
            for anchor in table.get("required_anchors", []):
                if str(anchor).lower() not in normalized_text:
                    raise ExtractionError(f"ECL required heading/anchor missing on page {page_number}: {anchor}")
            for row in table["rows"]:
                for fragment in row.get("label_fragments", [row.get("original_row_label", "")]):
                    if fragment and str(fragment).lower() not in normalized_text:
                        raise ExtractionError(f"ECL row label fragment missing on page {page_number}: {fragment}")
            words = _line_words(page)
            table_records = 0
            for row in table["rows"]:
                y = float(row["y"])
                tolerance = float(row.get("y_tolerance", table.get("y_tolerance", 2.0)))
                for stage in table["stage_columns"]:
                    raw = _cell(words, y, [float(stage["x0"]), float(stage["x1"])], tolerance)
                    normalized, value_status = normalize_ecl_value(raw)
                    canonical_stage = stage.get("canonical_stage", "unresolved")
                    record = {
                        "disclosure_family": "ecl_stage_movement",
                        "record_id": f"{config['source']['document_id']}:{table_index}:{row['year']}:{row['original_row_label']}:{stage['original_stage_label']}",
                        "company": config["document"]["company"],
                        "reporting_period": config["document"]["reporting_period"],
                        "statement_scope": config["document"]["statement_scope"],
                        "source_document_id": str(config["source"]["document_id"]),
                        "source_manifest_file": str(config["source"]["manifest_file"]),
                        "source_filename": source.name,
                        "source_file": str(config["source"]["source_file"]),
                        "pdf_page_number": page_number,
                        "pdf_page_index": page_index,
                        "printed_page_number": int(table["printed_page_number"]),
                        "note": str(table["note"]),
                        "table_title": title,
                        "original_row_label": str(row["original_row_label"]),
                        "row_role": str(row.get("row_role", "movement")),
                        "canonical_movement_category": str(row["canonical_movement_category"]),
                        "original_stage_label": str(stage["original_stage_label"]),
                        "canonical_stage": str(canonical_stage),
                        "year": int(row["year"]),
                        "source_column_position": str(stage["source_column_position"]),
                        "raw_value": raw,
                        "normalized_value": normalized,
                        "unit": str(table["unit"]),
                        "value_status": value_status,
                        "population_scope": str(table["population_scope"]),
                        "mapping_confidence": str(row.get("mapping_confidence", "high" if row["canonical_movement_category"] != "unresolved_movement" else "low")),
                        "mapping_explanation": str(row.get("mapping_explanation", "")),
                        "source_footnotes": list(table.get("source_footnotes", [])),
                        "extraction_warnings": list(row.get("warnings", [])),
                    }
                    records.append(record)
                    table_records += 1
            validation_pages.append({"pdf_page_number": page_number, "table_title": title, "record_count": table_records})
        if not records:
            raise ExtractionError("No ECL records were extracted")
        record_ids = [str(record["record_id"]) for record in records]
        duplicate_ids = sorted({record_id for record_id in record_ids if record_ids.count(record_id) > 1})
        if duplicate_ids:
            raise ExtractionError(f"Duplicate ECL record IDs detected: {duplicate_ids}")
    finally:
        document.close()
    return {
        "metadata": {
            "disclosure_family": "ecl_stage_movement",
            "company": config["document"]["company"],
            "reporting_period": config["document"]["reporting_period"],
            "statement_scope": config["document"]["statement_scope"],
            "source_document_id": str(config["source"]["document_id"]),
            "source_manifest_file": str(config["source"]["manifest_file"]),
            "source_filename": source.name,
            "source_file": str(config["source"]["source_file"]),
            "extraction_library": f"PyMuPDF {pymupdf.__version__}",
        },
        "records": records,
        "warnings": warnings,
        "warning_details": [],
        "validation_summary": {
            "expected_record_count": sum(len(t["rows"]) * len(t["stage_columns"]) for t in config["tables"]),
            "actual_record_count": len(records),
            "duplicate_record_ids": [],
            "tables": validation_pages,
            "reconciliation_ready": True,
        },
    }


def write_ecl_result(result: dict[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
