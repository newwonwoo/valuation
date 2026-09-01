from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Mapping

import yaml

from .actual_units import measure_from_raw
from .ledger import EvidenceLedger
from .records import EvidenceSourceLayer
from .runtime_resources import runtime_registry_path


DEFAULT_RANGE_RULE_REGISTRY_PATH = runtime_registry_path(
    "assumption_range_rule_registry.yaml"
)


class AssumptionRangeRuleError(ValueError):
    """Raised when a reviewed assumption-range rule cannot be applied safely."""


@dataclass(frozen=True)
class AssumptionRangeRule:
    rule_id: str
    assumption_key: str
    anchor_metric: str
    canonical_unit: str
    lookback_observations: int
    min_observations: int
    lower_multiplier: Decimal
    upper_multiplier: Decimal
    review_ref: str
    source_layers: tuple[EvidenceSourceLayer, ...] = (
        EvidenceSourceLayer.REALIZED_OR_FILING,
    )
    floor: Decimal | None = None
    ceiling: Decimal | None = None

    def validate(self) -> None:
        if not all(
            (
                self.rule_id,
                self.assumption_key,
                self.anchor_metric,
                self.canonical_unit,
                self.review_ref,
            )
        ):
            raise AssumptionRangeRuleError(
                "range rule requires id, assumption key, anchor metric, unit and review_ref"
            )
        if self.lookback_observations < 1:
            raise AssumptionRangeRuleError(
                f"range rule {self.rule_id} lookback_observations must be positive"
            )
        if not 1 <= self.min_observations <= self.lookback_observations:
            raise AssumptionRangeRuleError(
                f"range rule {self.rule_id} min_observations must be within lookback"
            )
        if (
            not self.lower_multiplier.is_finite()
            or not self.upper_multiplier.is_finite()
        ):
            raise AssumptionRangeRuleError(
                f"range rule {self.rule_id} multipliers must be finite"
            )
        if self.lower_multiplier < 0 or self.upper_multiplier < 0:
            raise AssumptionRangeRuleError(
                f"range rule {self.rule_id} multipliers must be non-negative"
            )
        if not self.source_layers or any(
            layer is not EvidenceSourceLayer.REALIZED_OR_FILING
            for layer in self.source_layers
        ):
            raise AssumptionRangeRuleError(
                f"range rule {self.rule_id} may use only realized_or_filing evidence; "
                "analyst/market/policy inputs cannot author a judgment envelope"
            )
        if (self.floor is not None and not self.floor.is_finite()) or (
            self.ceiling is not None and not self.ceiling.is_finite()
        ):
            raise AssumptionRangeRuleError(
                f"range rule {self.rule_id} floor/ceiling must be finite"
            )
        if (
            self.floor is not None
            and self.ceiling is not None
            and self.floor > self.ceiling
        ):
            raise AssumptionRangeRuleError(
                f"range rule {self.rule_id} floor cannot exceed ceiling"
            )


@dataclass(frozen=True)
class AssumptionRangeRuleRegistry:
    rules: tuple[AssumptionRangeRule, ...]
    registry_hash: str

    def validate(self) -> None:
        ids = tuple(item.rule_id for item in self.rules)
        keys = tuple(item.assumption_key for item in self.rules)
        if len(ids) != len(set(ids)):
            raise AssumptionRangeRuleError("duplicate assumption range rule_id")
        if len(keys) != len(set(keys)):
            raise AssumptionRangeRuleError(
                "one assumption key may have only one production range rule"
            )
        for item in self.rules:
            item.validate()

    def for_key(self, key: str) -> AssumptionRangeRule | None:
        for item in self.rules:
            if item.assumption_key == key:
                return item
        return None


@dataclass(frozen=True)
class AssumptionRangeReceipt:
    target_id: str
    rule_id: str
    assumption_key: str
    scenario_id: str
    anchor_metric: str
    min_value: Decimal
    max_value: Decimal
    anchor_evidence_ids: tuple[str, ...]
    anchor_values: tuple[Decimal, ...]
    anchor_effective_dates: tuple[str, ...]
    canonical_unit: str
    registry_hash: str
    review_ref: str


def range_provenance_hash_part(
    receipt: AssumptionRangeReceipt,
    *,
    anchor_values: tuple[Decimal, ...] | None = None,
    anchor_effective_dates: tuple[str, ...] | None = None,
) -> str:
    values = receipt.anchor_values if anchor_values is None else anchor_values
    dates = receipt.anchor_effective_dates if anchor_effective_dates is None else anchor_effective_dates
    if len(receipt.anchor_evidence_ids) != len(values) or len(values) != len(dates):
        raise AssumptionRangeRuleError(
            f"range receipt {receipt.rule_id} anchor provenance lengths disagree"
        )
    payload = {
        "target_id": receipt.target_id,
        "rule_id": receipt.rule_id,
        "assumption_key": receipt.assumption_key,
        "scenario_id": receipt.scenario_id,
        "anchor_metric": receipt.anchor_metric,
        "min_value": str(receipt.min_value),
        "max_value": str(receipt.max_value),
        "anchor_evidence_ids": list(receipt.anchor_evidence_ids),
        "anchor_values": [str(value) for value in values],
        "anchor_effective_dates": list(dates),
        "canonical_unit": receipt.canonical_unit,
        "registry_hash": receipt.registry_hash,
        "review_ref": receipt.review_ref,
    }
    return "RANGE|" + json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class RangeApplicationResult:
    specs: tuple[AssumptionSpec, ...]
    receipts: tuple[AssumptionRangeReceipt, ...]
    ignored_llm_bounds: tuple[tuple[str, str, str | None, str | None], ...]


def load_assumption_range_rule_registry(
    path: str | Path | None = None,
) -> AssumptionRangeRuleRegistry:
    resolved_path = DEFAULT_RANGE_RULE_REGISTRY_PATH if path is None else Path(path)
    raw = Path(resolved_path).read_text(encoding="utf-8")
    payload = yaml.safe_load(raw)
    if not isinstance(payload, Mapping):
        raise AssumptionRangeRuleError("assumption range rule registry must be a mapping")
    if payload.get("schema_version") != "assumption_range_rule_registry/v1":
        raise AssumptionRangeRuleError("unsupported assumption range rule registry schema")
    rows = payload.get("rules")
    if not isinstance(rows, list):
        raise AssumptionRangeRuleError("assumption range rule registry requires rules list")
    rules: list[AssumptionRangeRule] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise AssumptionRangeRuleError("assumption range rule row must be a mapping")
        source_layers_raw = row.get("source_layers", ["realized_or_filing"])
        if not isinstance(source_layers_raw, list) or not source_layers_raw:
            raise AssumptionRangeRuleError("range rule source_layers must be a non-empty list")
        try:
            source_layers = tuple(
                EvidenceSourceLayer(str(item)) for item in source_layers_raw
            )
        except ValueError as exc:
            raise AssumptionRangeRuleError(
                f"range rule carries unknown evidence source layer: {exc}"
            ) from exc
        rules.append(
            AssumptionRangeRule(
                rule_id=str(row.get("rule_id") or ""),
                assumption_key=str(row.get("assumption_key") or ""),
                anchor_metric=str(row.get("anchor_metric") or ""),
                canonical_unit=str(row.get("canonical_unit") or ""),
                lookback_observations=int(row.get("lookback_observations", 0)),
                min_observations=int(row.get("min_observations", 0)),
                lower_multiplier=Decimal(str(row.get("lower_multiplier"))),
                upper_multiplier=Decimal(str(row.get("upper_multiplier"))),
                review_ref=str(row.get("review_ref") or ""),
                source_layers=source_layers,
                floor=(
                    Decimal(str(row["floor"])) if row.get("floor") is not None else None
                ),
                ceiling=(
                    Decimal(str(row["ceiling"]))
                    if row.get("ceiling") is not None
                    else None
                ),
            )
        )
    registry = AssumptionRangeRuleRegistry(
        rules=tuple(rules),
        registry_hash=sha256(raw.encode("utf-8")).hexdigest(),
    )
    registry.validate()
    return registry


def derive_reviewed_assumption_range(
    rule: AssumptionRangeRule,
    *,
    ledger: EvidenceLedger,
    target_id: str,
    scenario_id: str,
    registry_hash: str,
) -> AssumptionRangeReceipt:
    """Re-derive one reviewed range from authoritative filing history."""
    rule.validate()
    if not target_id or not scenario_id or not registry_hash:
        raise AssumptionRangeRuleError(
            "range derivation requires target, scenario and registry hash"
        )
    active = tuple(item for item in ledger.active() if item.target == target_id)
    candidates = tuple(
        item
        for item in active
        if item.metric == rule.anchor_metric
        and item.source_layer in rule.source_layers
    )
    by_date: dict[str, list] = {}
    for item in candidates:
        canonical_date = date.fromisoformat(item.effective_date[:10]).isoformat()
        by_date.setdefault(canonical_date, []).append(item)
    observations: list[tuple[str, Decimal, str, str]] = []
    for canonical_date in sorted(by_date, reverse=True):
        rows = by_date[canonical_date]
        amounts: dict[Decimal, list] = {}
        for item in rows:
            measure = measure_from_raw(
                item.value, item.unit, item.effective_date
            ).convert_to(rule.canonical_unit)
            amounts.setdefault(measure.amount, []).append(item)
        if len(amounts) != 1:
            raise AssumptionRangeRuleError(
                f"range rule {rule.rule_id} has ambiguous {rule.anchor_metric} "
                f"filing values for {canonical_date}"
            )
        amount, matching_rows = next(iter(amounts.items()))
        chosen = sorted(matching_rows, key=lambda item: item.id)[0]
        observations.append(
            (canonical_date, amount, chosen.id, chosen.effective_date)
        )
        if len(observations) >= rule.lookback_observations:
            break
    if len(observations) < rule.min_observations:
        raise AssumptionRangeRuleError(
            f"range rule {rule.rule_id} requires {rule.min_observations} filing "
            f"observations of {rule.anchor_metric}; found {len(observations)}"
        )
    values = tuple(item[1] for item in observations)
    lower = min(values) * rule.lower_multiplier
    upper = max(values) * rule.upper_multiplier
    if rule.floor is not None:
        lower = max(lower, rule.floor)
    if rule.ceiling is not None:
        upper = min(upper, rule.ceiling)
    if lower > upper:
        raise AssumptionRangeRuleError(
            f"range rule {rule.rule_id} derived inverted bounds {lower}>{upper}"
        )
    return AssumptionRangeReceipt(
        target_id=target_id,
        rule_id=rule.rule_id,
        assumption_key=rule.assumption_key,
        scenario_id=scenario_id,
        anchor_metric=rule.anchor_metric,
        min_value=lower,
        max_value=upper,
        anchor_evidence_ids=tuple(item[2] for item in observations),
        anchor_values=values,
        anchor_effective_dates=tuple(item[3] for item in observations),
        canonical_unit=rule.canonical_unit,
        registry_hash=registry_hash,
        review_ref=rule.review_ref,
    )


def apply_reviewed_assumption_ranges(
    specs: tuple[AssumptionSpec, ...],
    *,
    ledger: EvidenceLedger,
    target_id: str,
    registry: AssumptionRangeRuleRegistry,
    llm_bounds: tuple[tuple[str, str, str | None, str | None], ...] = (),
) -> RangeApplicationResult:
    """Attach only reviewed, evidence-derived bounds to compiler specs."""
    registry.validate()
    if not target_id:
        raise AssumptionRangeRuleError("range application requires target_id")
    receipts: list[AssumptionRangeReceipt] = []
    resolved: list[AssumptionSpec] = []
    for spec in specs:
        rule = registry.for_key(spec.key)
        if rule is None:
            resolved.append(replace(spec, min_value=None, max_value=None))
            continue
        if spec.canonical_unit != rule.canonical_unit:
            raise AssumptionRangeRuleError(
                f"range rule {rule.rule_id} unit {rule.canonical_unit} does not match "
                f"assumption {spec.key} unit {spec.canonical_unit}"
            )
        receipt = derive_reviewed_assumption_range(
            rule,
            ledger=ledger,
            target_id=target_id,
            scenario_id=spec.scenario_id,
            registry_hash=registry.registry_hash,
        )
        resolved.append(
            replace(spec, min_value=receipt.min_value, max_value=receipt.max_value)
        )
        receipts.append(receipt)
    return RangeApplicationResult(
        specs=tuple(resolved),
        receipts=tuple(receipts),
        ignored_llm_bounds=llm_bounds,
    )


def range_receipts_hash(receipts: tuple[AssumptionRangeReceipt, ...]) -> str:
    payload = [
        {
            "target_id": item.target_id,
            "rule_id": item.rule_id,
            "assumption_key": item.assumption_key,
            "scenario_id": item.scenario_id,
            "anchor_metric": item.anchor_metric,
            "min_value": str(item.min_value),
            "max_value": str(item.max_value),
            "anchor_evidence_ids": list(item.anchor_evidence_ids),
            "anchor_values": [str(value) for value in item.anchor_values],
            "anchor_effective_dates": list(item.anchor_effective_dates),
            "canonical_unit": item.canonical_unit,
            "registry_hash": item.registry_hash,
            "review_ref": item.review_ref,
        }
        for item in receipts
    ]
    return sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
