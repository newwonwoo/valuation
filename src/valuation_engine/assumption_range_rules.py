from __future__ import annotations

from dataclasses import dataclass, replace
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
    rule_id: str
    assumption_key: str
    scenario_id: str
    min_value: Decimal
    max_value: Decimal
    anchor_evidence_ids: tuple[str, ...]
    anchor_values: tuple[Decimal, ...]
    canonical_unit: str
    registry_hash: str
    review_ref: str


@dataclass(frozen=True)
class RangeApplicationResult:
    specs: tuple[AssumptionSpec, ...]
    receipts: tuple[AssumptionRangeReceipt, ...]
    ignored_llm_bounds: tuple[tuple[str, str, str | None, str | None], ...]


def load_assumption_range_rule_registry(
    path: str | Path = DEFAULT_RANGE_RULE_REGISTRY_PATH,
) -> AssumptionRangeRuleRegistry:
    raw = Path(path).read_text(encoding="utf-8")
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


def apply_reviewed_assumption_ranges(
    specs: tuple[AssumptionSpec, ...],
    *,
    ledger: EvidenceLedger,
    registry: AssumptionRangeRuleRegistry,
    llm_bounds: tuple[tuple[str, str, str | None, str | None], ...] = (),
) -> RangeApplicationResult:
    """Attach only reviewed, evidence-derived bounds to compiler specs.

    Any LLM-authored min/max values are audit-only inputs and never gain compiler
    authority. A production rule is executable only from same-run filing evidence;
    missing/ambiguous history blocks rather than silently falling back to the LLM.
    """
    registry.validate()
    active = tuple(ledger.active())
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
        candidates = tuple(
            item
            for item in active
            if item.metric == rule.anchor_metric
            and item.source_layer in rule.source_layers
        )
        by_date: dict[str, list] = {}
        for item in candidates:
            by_date.setdefault(item.effective_date, []).append(item)
        observations: list[tuple[str, Decimal, str]] = []
        for effective_date in sorted(by_date, reverse=True):
            rows = by_date[effective_date]
            amounts: dict[Decimal, list[str]] = {}
            for item in rows:
                measure = measure_from_raw(
                    item.value, item.unit, item.effective_date
                ).convert_to(rule.canonical_unit)
                amounts.setdefault(measure.amount, []).append(item.id)
            if len(amounts) != 1:
                raise AssumptionRangeRuleError(
                    f"range rule {rule.rule_id} has ambiguous {rule.anchor_metric} "
                    f"filing values for {effective_date}"
                )
            amount, ids = next(iter(amounts.items()))
            observations.append((effective_date, amount, sorted(ids)[0]))
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
        resolved.append(replace(spec, min_value=lower, max_value=upper))
        receipts.append(
            AssumptionRangeReceipt(
                rule_id=rule.rule_id,
                assumption_key=spec.key,
                scenario_id=spec.scenario_id,
                min_value=lower,
                max_value=upper,
                anchor_evidence_ids=tuple(item[2] for item in observations),
                anchor_values=values,
                canonical_unit=rule.canonical_unit,
                registry_hash=registry.registry_hash,
                review_ref=rule.review_ref,
            )
        )
    return RangeApplicationResult(
        specs=tuple(resolved),
        receipts=tuple(receipts),
        ignored_llm_bounds=llm_bounds,
    )


def range_receipts_hash(receipts: tuple[AssumptionRangeReceipt, ...]) -> str:
    payload = [
        {
            "rule_id": item.rule_id,
            "assumption_key": item.assumption_key,
            "scenario_id": item.scenario_id,
            "min_value": str(item.min_value),
            "max_value": str(item.max_value),
            "anchor_evidence_ids": list(item.anchor_evidence_ids),
            "anchor_values": [str(value) for value in item.anchor_values],
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
