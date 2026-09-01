"""Conservative, explainable semantic comparability decisions."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml

from .errors import ComparabilityError


STATUSES = {
    "comparable",
    "comparable_after_unit_conversion",
    "comparable_after_scope_review",
    "label_only",
    "unresolved",
    "not_comparable",
}


def load_registry(path: str | Path = "config/comparability_registry.yaml") -> dict[str, Any]:
    source = Path(path)
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("definitions"), dict):
        raise ComparabilityError("Comparability registry must contain definitions")
    return data


def load_overrides(path: str | Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    source = Path(path)
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("overrides"), list):
        raise ComparabilityError("Override file must contain an overrides list")
    result: dict[str, dict[str, Any]] = {}
    for item in data["overrides"]:
        if not isinstance(item, dict) or not item.get("comparison_id") or not str(item.get("rationale", "")).strip():
            raise ComparabilityError("Every comparability override needs an ID and rationale")
        result[str(item["comparison_id"])] = dict(item)
    return result


def _family(record: dict[str, Any]) -> str:
    return str(record.get("disclosure_family") or ("ecl_stage_movement" if "canonical_movement_category" in record else "transfer_assignment"))


def _metric(record: dict[str, Any]) -> str:
    return str(record.get("canonical_metric") or record.get("canonical_movement_category") or "")


def _scope(record: dict[str, Any], registry: dict[str, Any]) -> str:
    return str(record.get("population_scope") or registry.get("company_population", {}).get(str(record.get("company")), "unknown_population"))


def _definition(record: dict[str, Any], registry: dict[str, Any]) -> str:
    key = _metric(record)
    if _family(record) == "ecl_stage_movement":
        key = "ecl_stage_movement"
    return str(registry.get("definitions", {}).get(key, {}).get("definition", ""))


def _decision_id(left: dict[str, Any], right: dict[str, Any], relation: str) -> str:
    ids = sorted([str(left.get("record_id") or _fallback_id(left)), str(right.get("record_id") or _fallback_id(right))])
    raw = "|".join([relation, *ids])
    return "cmp_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _fallback_id(record: dict[str, Any]) -> str:
    fields = [str(record.get(k, "")) for k in ("source_document_id", "canonical_metric", "canonical_movement_category", "canonical_stage", "year", "source_column_position")]
    return hashlib.sha256("|".join(fields).encode("utf-8")).hexdigest()[:16]


def _unit_conversion(left: str | None, right: str | None, metric: str) -> str | None:
    if left == right or not left or not right:
        return None
    if {left, right} == {"INR_lakh", "INR_crore"}:
        return "INR_lakh / 100 = INR_crore"
    if {left, right} == {"years", "months"} and metric == "weighted_average_holding_period":
        return "years * 12 = months"
    return None


def compare_records(
    left: dict[str, Any],
    right: dict[str, Any],
    registry: dict[str, Any] | None = None,
    *,
    relation: str = "same_company_periods",
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    registry = registry or load_registry()
    overrides = overrides or {}
    family_left, family_right = _family(left), _family(right)
    metric_left, metric_right = _metric(left), _metric(right)
    decision_id = _decision_id(left, right, relation)
    conversion = _unit_conversion(left.get("unit"), right.get("unit"), metric_left)
    reason = ""
    status = "comparable"
    if family_left != family_right or metric_left != metric_right:
        status, reason = "not_comparable", "Disclosure family or canonical metric differs."
    elif not metric_left:
        status, reason = "label_only", "No canonical metric or movement mapping is available."
    elif left.get("canonical_stage") != right.get("canonical_stage"):
        status, reason = "not_comparable", "Canonical stage differs; source stage meanings cannot be merged."
    elif left.get("canonical_stage") == "unresolved" or right.get("canonical_stage") == "unresolved":
        status, reason = "unresolved", "At least one source stage is unresolved; no stage-level calculation is permitted."
    elif left.get("statement_scope") != right.get("statement_scope"):
        status, reason = "comparable_after_scope_review", "Statement scope differs."
    elif _scope(left, registry) != _scope(right, registry):
        status, reason = "comparable_after_scope_review", "Population scope differs."
    elif left.get("source_footnotes", []) != right.get("source_footnotes", []):
        status, reason = "comparable_after_scope_review", "Source footnotes or exclusions differ."
    elif conversion:
        status, reason = "comparable_after_unit_conversion", "Units differ but an explicit mechanical conversion is available."
    elif relation == "cross_lender":
        status, reason = "comparable_after_scope_review", "Cross-lender scope and aggregation review is required."
    if metric_left in {"rating_wise_distribution_rated_loans", "coverage_tangible_security"} and (left.get("normalized_value") is None or right.get("normalized_value") is None):
        status, reason = "not_comparable", "A source value is explicitly not reported; it is not zero."
    if status not in STATUSES:
        raise ComparabilityError(f"Invalid comparability status: {status}")
    overridden = decision_id in overrides
    warning = None
    if overridden:
        warning = "Explicit analyst override used; this calculation must be reviewed against the recorded rationale."
        status = "comparable"
        reason = f"Override: {overrides[decision_id].get('rationale')}"
    return {
        "comparison_id": decision_id,
        "relation": relation,
        "left_record_id": str(left.get("record_id") or _fallback_id(left)),
        "right_record_id": str(right.get("record_id") or _fallback_id(right)),
        "family": family_left,
        "metric": metric_left,
        "definition": _definition(left, registry),
        "left_unit": left.get("unit"),
        "right_unit": right.get("unit"),
        "left_scope": _scope(left, registry),
        "right_scope": _scope(right, registry),
        "status": status,
        "reason": reason,
        "unit_conversion": conversion,
        "override_applied": overridden,
        "warning": warning,
    }


def build_comparability_matrix(
    records: list[dict[str, Any]], registry: dict[str, Any] | None = None, overrides: dict[str, dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    registry = registry or load_registry()
    matrix: list[dict[str, Any]] = []
    same_groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    for record in records:
        key = (_family(record), _metric(record), str(record.get("canonical_stage", "")), str(record.get("company", "")), str(record.get("source_column_position", "")))
        same_groups.setdefault(key, []).append(record)
    for group in same_groups.values():
        by_year = {}
        for record in group:
            by_year.setdefault(record.get("year"), record)
        years = sorted(by_year)
        if len(years) >= 2:
            matrix.append(compare_records(by_year[years[-2]], by_year[years[-1]], registry, relation="same_company_periods", overrides=overrides))
    latest: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for record in records:
        key = (_family(record), _metric(record), str(record.get("canonical_stage", "")), str(record.get("year", "")), str(record.get("source_column_position", "")))
        latest.setdefault(key, record)
    for key, left in latest.items():
        peers = [r for k, r in latest.items() if k[:3] == key[:3] and (key[0] == "ecl_stage_movement" or k[4] == key[4]) and r.get("company") != left.get("company")]
        for right in peers:
            if str(left.get("company")) < str(right.get("company")):
                matrix.append(compare_records(left, right, registry, relation="cross_lender", overrides=overrides))
    return matrix
