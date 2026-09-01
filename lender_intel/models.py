from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class DisclosureRecord:
    """Normalized record with source text retained for review."""

    data: dict[str, Any]

    @property
    def record_id(self) -> str:
        return str(self.data.get("record_id", ""))

    @property
    def metric(self) -> str:
        return str(self.data.get("canonical_metric") or self.data.get("canonical_movement_category") or "")

    @property
    def value_decimal(self) -> Decimal | None:
        value = self.data.get("normalized_value")
        if value is None:
            return None
        try:
            return Decimal(str(value))
        except Exception:
            return None


@dataclass
class ExtractionResult:
    metadata: dict[str, Any]
    records: list[dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    warning_details: list[dict[str, Any]] = field(default_factory=list)
    validation_summary: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "metadata": self.metadata,
            "records": self.records,
            "warnings": self.warnings,
            "warning_details": self.warning_details,
            "validation_summary": self.validation_summary,
        }
