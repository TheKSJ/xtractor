"""Deterministic analyst-facing report writers."""

from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any, Iterable


def load_records(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    candidates = [source] if source.is_file() else sorted(source.rglob("*.json"))
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates: list[str] = []
    for candidate in candidates:
        if any(part.lower() in {"baseline", "benchmark_transfer_assignment.json"} for part in candidate.parts):
            continue
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("records"), list):
            for record in data["records"]:
                if not isinstance(record, dict):
                    continue
                identity = str(record.get("record_id") or "|".join(str(record.get(k, "")) for k in ("source_document_id", "canonical_metric", "canonical_movement_category", "canonical_stage", "original_row_label", "year", "source_column_position")))
                if identity not in seen:
                    seen.add(identity)
                    records.append(record)
                else:
                    duplicates.append(identity)
    if duplicates:
        raise ValueError(f"Duplicate record identities found under {source}: {sorted(set(duplicates))}")
    return records


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    materialized = list(rows)
    keys = sorted({key for row in materialized for key in row})
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(materialized)


def _brief_markdown(records: list[dict[str, Any]], analysis: dict[str, Any]) -> str:
    summary = analysis.get("summary", {})
    lines = [
        "# Indian Lender Disclosure Intelligence Brief",
        "",
        "## Executive summary",
        "",
        f"The bundle contains {len(records)} validated source records, {summary.get('comparison_count', 0)} semantic comparison decisions, and {summary.get('reconciliation_count', 0)} ECL reconciliation checks.",
        "",
        "Interpretations are cautious and do not constitute investment advice or stock-price prediction.",
        "",
        "## Coverage",
        "",
    ]
    families = {}
    companies = {}
    for r in records:
        families[str(r.get("disclosure_family", "transfer_assignment"))] = families.get(str(r.get("disclosure_family", "transfer_assignment")), 0) + 1
        companies[str(r.get("company", ""))] = companies.get(str(r.get("company", "")), 0) + 1
    lines.extend([f"- {name}: {count} records" for name, count in sorted(families.items())])
    lines.extend([f"- {name}: {count} records" for name, count in sorted(companies.items())])
    lines.extend(["", "## Important changes", ""])
    changes = [c for c in analysis.get("calculations", []) if c.get("calculation") == "period_change"]
    if not changes:
        lines.append("No permitted period-over-period calculation was available.")
    else:
        for change in changes[:50]:
            if change.get("status") == "calculated":
                lines.append(f"- [mechanical_calculation] {change.get('from_year')} to {change.get('to_year')}: absolute change {change.get('absolute_change')}, percentage change {change.get('percentage_change')}%.")
            else:
                lines.append(f"- [unresolved_question] Calculation unavailable: {change.get('reason')}")
    lines.extend(["", "## Observed facts", ""])
    if records:
        for record in records[:25]:
            value = record.get("raw_value")
            lines.append(f"- [observed_fact] `{record.get('company')}` `{record.get('original_row_label')}` year {record.get('year')}: source value `{value}` {record.get('unit') or ''}, PDF page {record.get('pdf_page_number')}.")
    else:
        lines.append("No source records were available.")
    lines.extend(["", "## Financial interpretation (cautious)", "", "- [financial_interpretation] A mechanical change in transfer activity or ECL allowance may warrant review of exposure mix, stage migration, assumptions and portfolio scope; this report does not establish causality or an investment conclusion."])
    lines.extend(["", "## ECL movements", ""])
    for item in analysis.get("calculations", []):
        if item.get("calculation") == "net_transfer_effect_on_allowance":
            lines.append(f"- [mechanical_calculation] {item['company']} {item['year']} {item['canonical_stage']}: net transfer effect {item['value']} in source units.")
    lines.extend(["", "## Reconciliation results", ""])
    for item in analysis.get("reconciliations", []):
        lines.append(f"- {item['company']} {item['year']} {item['canonical_stage']}: {item.get('status')} (residual {item.get('residual', 'n/a')}).")
    lines.extend(["", "## Cross-lender comparison", ""])
    cross_lender = [d for d in analysis.get("comparability_matrix", []) if d.get("relation") == "cross_lender"]
    if not cross_lender:
        lines.append("No cross-lender comparison pairs were available.")
    else:
        for decision in cross_lender[:100]:
            lines.append(f"- `{decision['left_record_id']}` vs `{decision['right_record_id']}`: `{decision['status']}` — {decision['reason']}")
    lines.extend(["", "## Comparability and non-comparable metrics", ""])
    blocked = [d for d in analysis.get("comparability_matrix", []) if d.get("status") != "comparable"]
    for decision in blocked[:100]:
        lines.append(f"- `{decision['comparison_id']}` `{decision['status']}`: {decision['reason']}")
    if not blocked:
        lines.append("No blocked comparisons.")
    lines.extend(["", "## Uncertainty and unresolved questions", ""])
    unresolved = [d for d in analysis.get("comparability_matrix", []) if d.get("status") in {"label_only", "unresolved", "not_comparable", "comparable_after_scope_review"}]
    residuals = [r for r in analysis.get("reconciliations", []) if r.get("status") != "passed"]
    if not unresolved and not residuals:
        lines.append("No unresolved comparisons or reconciliation residuals.")
    else:
        for decision in unresolved[:100]:
            lines.append(f"- [unresolved_question] `{decision['comparison_id']}` `{decision['status']}`: {decision['reason']}")
        for residual in residuals[:100]:
            lines.append(f"- [unresolved_question] Reconciliation for {residual.get('company')} {residual.get('year')} {residual.get('canonical_stage')}: {residual.get('reason')} (residual {residual.get('residual', 'n/a')}).")
    lines.extend(["", "## Transfer-assignment observations", "", "Source labels, units, unidentified subcolumns, exclusions, and missing values remain visible in records.json. Unit conversion is not evidence of economic comparability.", "", "## Data-quality warnings", ""])
    if analysis.get("warnings"):
        for warning in analysis["warnings"][:100]:
            lines.append(f"- `{warning.get('comparison_id', '')}`: {warning.get('warning') or warning.get('reason')}")
    else:
        lines.append("No additional structured warnings.")
    lines.extend(["", "## Limitations", "", "The configured reports are not a claim about unseen-report accuracy. Financial sign-off requires human visual review of each cited annual-report page. The dataset does not support stock-price prediction, causal claims, or composite risk scores.", "", "## Direct source references", ""])
    seen = set()
    for record in records:
        key = (record.get("source_document_id"), record.get("source_file"), record.get("pdf_page_number"))
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"- `{key[0]}` — `{key[1]}`, PDF page {key[2]}, printed page {record.get('printed_page_number')}, note {record.get('note')}")
    return "\n".join(lines) + "\n"


def write_analysis_bundle(records: list[dict[str, Any]], analysis: dict[str, Any], output: str | Path, *, metadata: dict[str, Any] | None = None) -> Path:
    target = Path(output)
    target.mkdir(parents=True, exist_ok=True)
    _write_json(target / "records.json", {"metadata": metadata or {}, "records": records})
    _write_csv(target / "records.csv", records)
    _write_json(target / "comparability_matrix.json", analysis.get("comparability_matrix", []))
    _write_csv(target / "comparability_matrix.csv", analysis.get("comparability_matrix", []))
    _write_json(target / "calculations.json", analysis.get("calculations", []))
    _write_json(target / "validation_report.json", {"summary": analysis.get("summary", {}), "reconciliations": analysis.get("reconciliations", [])})
    _write_json(target / "warnings.json", analysis.get("warnings", []))
    _write_json(target / "overrides.json", analysis.get("overrides", []))
    _write_json(target / "error_taxonomy.json", {
        "scope_mismatch": sum(1 for w in analysis.get("warnings", []) if w.get("status") == "comparable_after_scope_review"),
        "comparison_blocked": sum(1 for w in analysis.get("warnings", []) if w.get("status") in {"label_only", "unresolved", "not_comparable"}),
        "reconciliation_residual": sum(1 for r in analysis.get("reconciliations", []) if r.get("status") == "residual"),
    })
    markdown = _brief_markdown(records, analysis)
    if metadata and metadata.get("demo"):
        markdown = "# SYNTHETIC DEMO — NOT A REAL BENCHMARK\n\nThis bundle contains deterministic synthetic records only; do not combine it with known-report benchmark results.\n\n" + markdown
    (target / "analyst_brief.md").write_text(markdown, encoding="utf-8")
    body = html.escape(markdown).replace("\n", "<br>\n")
    (target / "analyst_brief.html").write_text("<!doctype html><html><head><meta charset='utf-8'><title>Lender brief</title><style>body{font:16px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem;line-height:1.5;color:#202124}br{display:block;margin:.25rem}</style></head><body>" + body + "</body></html>\n", encoding="utf-8")
    return target
