"""Validated calculations over extracted records."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from .comparability import _fallback_id, build_comparability_matrix, load_registry


BLOCKED = {"comparable_after_scope_review", "label_only", "unresolved", "not_comparable"}


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _effective_value(record: dict[str, Any]) -> Decimal | None:
    value = _dec(record.get("normalized_value"))
    if value is not None:
        return value
    return None


def _json_decimal(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


def convert_value(value: Any, from_unit: str | None, to_unit: str | None, metric: str = "") -> Decimal | None:
    """Apply only conversions explicitly supported by the comparability registry."""
    amount = _dec(value)
    if amount is None or not from_unit or not to_unit or from_unit == to_unit:
        return amount
    if {from_unit, to_unit} == {"INR_lakh", "INR_crore"}:
        return amount / Decimal("100") if from_unit == "INR_lakh" else amount * Decimal("100")
    if {from_unit, to_unit} == {"years", "months"} and metric == "weighted_average_holding_period":
        return amount * Decimal("12") if from_unit == "years" else amount / Decimal("12")
    return None


def reconcile_ecl(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, int, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        if record.get("disclosure_family") != "ecl_stage_movement":
            continue
        groups[(str(record.get("company")), int(record.get("year")), str(record.get("canonical_stage")), str(record.get("source_column_position", "")))].append(record)
    results: list[dict[str, Any]] = []
    for (company, year, stage, source_column), items in sorted(groups.items()):
        openings = [r for r in items if r.get("row_role") == "opening"]
        closings = [r for r in items if r.get("row_role") == "closing"]
        if len(openings) != 1 or len(closings) != 1:
            results.append({"company": company, "year": year, "canonical_stage": stage, "source_column_position": source_column, "status": "unresolved", "reason": "Opening or closing balance is missing or duplicated."})
            continue
        opening = _effective_value(openings[0])
        closing = _effective_value(closings[0])
        movements = [r for r in items if r.get("row_role", "movement") not in {"opening", "closing", "subtotal"}]
        movement_total = sum((_effective_value(r) or Decimal("0")) for r in movements)
        residual = (opening or Decimal("0")) + movement_total - (closing or Decimal("0"))
        complete = opening is not None and closing is not None and all(_effective_value(r) is not None for r in movements)
        results.append({
            "company": company,
            "year": year,
            "canonical_stage": stage,
            "source_column_position": source_column,
            "opening": _json_decimal(opening),
            "movement_total": _json_decimal(movement_total),
            "closing": _json_decimal(closing),
            "residual": _json_decimal(residual),
            "status": "passed" if complete and residual == 0 else ("residual" if complete else "unresolved"),
            "reason": "" if complete and residual == 0 else "Residual or missing source value remains explicit.",
        })
    return results


def ecl_transfer_effects(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, int, str], Decimal] = defaultdict(Decimal)
    for record in records:
        category = str(record.get("canonical_movement_category", ""))
        stage = str(record.get("canonical_stage", ""))
        if record.get("disclosure_family") == "ecl_stage_movement" and category in {"transfer_to_stage_1", "transfer_to_stage_2", "transfer_to_stage_3"} and stage in {"Stage 2", "Stage 3"}:
            value = _effective_value(record)
            if value is not None:
                groups[(str(record.get("company")), int(record.get("year")), stage)] += value
    return [{"company": c, "year": y, "canonical_stage": s, "calculation": "net_transfer_effect_on_allowance", "value": _json_decimal(v), "unit": "source_unit"} for (c, y, s), v in sorted(groups.items())]


def period_changes(records: list[dict[str, Any]], matrix: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(r.get("record_id") or _fallback_id(r)): r for r in records}
    results: list[dict[str, Any]] = []
    for decision in matrix:
        if decision["relation"] != "same_company_periods":
            continue
        left, right = by_id.get(decision["left_record_id"]), by_id.get(decision["right_record_id"])
        if not left or not right:
            continue
        if decision["status"] in BLOCKED:
            results.append({"calculation": "period_change", "comparison_id": decision["comparison_id"], "status": "unavailable", "reason": decision["reason"]})
            continue
        lv, rv = _effective_value(left), _effective_value(right)
        if lv is None or rv is None:
            results.append({"calculation": "period_change", "comparison_id": decision["comparison_id"], "status": "unavailable", "reason": "A source value is missing or not applicable."})
            continue
        conversion = None
        if decision.get("status") == "comparable_after_unit_conversion":
            converted = convert_value(lv, decision.get("left_unit"), decision.get("right_unit"), decision.get("metric", ""))
            if converted is None:
                results.append({"calculation": "period_change", "comparison_id": decision["comparison_id"], "status": "unavailable", "reason": "No explicit conversion is implemented for these units."})
                continue
            lv = converted
            conversion = decision.get("unit_conversion")
        change = rv - lv
        pct = None if lv == 0 else change / lv * Decimal("100")
        results.append({"calculation": "period_change", "comparison_id": decision["comparison_id"], "from_year": left.get("year"), "to_year": right.get("year"), "absolute_change": _json_decimal(change), "percentage_change": _json_decimal(pct), "unit_conversion": conversion, "status": "calculated"})
    return results


def analyze_records(records: list[dict[str, Any]], registry: dict[str, Any] | None = None, overrides: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    registry = registry or load_registry()
    matrix = build_comparability_matrix(records, registry, overrides)
    calculations = period_changes(records, matrix)
    reconciliations = reconcile_ecl(records)
    calculations.extend(ecl_transfer_effects(records))
    warnings = [d for d in matrix if d.get("status") in BLOCKED or d.get("override_applied")]
    return {
        "comparability_matrix": matrix,
        "calculations": calculations,
        "reconciliations": reconciliations,
        "warnings": warnings,
        "summary": {
            "record_count": len(records),
            "comparison_count": len(matrix),
            "blocked_comparison_count": sum(1 for d in matrix if d.get("status") in BLOCKED),
            "reconciliation_count": len(reconciliations),
            "reconciliation_failures": sum(1 for r in reconciliations if r.get("status") != "passed"),
        },
    }
