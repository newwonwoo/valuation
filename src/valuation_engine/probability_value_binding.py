from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json

from .probability_engine_v3 import ProbabilityEngineV3Result


@dataclass(frozen=True)
class ScenarioIntrinsicValue:
    scenario_id: str
    intrinsic_value: Decimal

    def validate(self) -> None:
        if not self.scenario_id:
            raise ValueError("scenario intrinsic value requires scenario_id")
        if not self.intrinsic_value.is_finite():
            raise ValueError("scenario intrinsic value must be finite")


@dataclass(frozen=True)
class ProbabilityWeightedIntrinsicValue:
    scenario_contributions: tuple[tuple[str, Decimal, Decimal, Decimal], ...]
    intrinsic_value: Decimal
    probability_snapshot_hash: str
    valuation_input_hash: str
    binding_hash: str


def bind_frozen_probabilities_to_intrinsic_values(
    probability_result: ProbabilityEngineV3Result,
    scenario_values: tuple[ScenarioIntrinsicValue, ...],
) -> ProbabilityWeightedIntrinsicValue:
    """Combine a frozen probability result with intrinsic values after estimation.

    Market price, target price, return target, and entry price are intentionally not
    accepted by this API. Scenario intrinsic values are consumed only after the
    probability result and its snapshot hash already exist.
    """

    if not probability_result.numeric_weighting_allowed:
        raise PermissionError("probability result must be estimated before valuation binding")
    if not scenario_values:
        raise ValueError("scenario intrinsic values are required")
    for item in scenario_values:
        item.validate()
    value_map = {item.scenario_id: item.intrinsic_value for item in scenario_values}
    if len(value_map) != len(scenario_values):
        raise ValueError("scenario intrinsic values contain duplicate scenario IDs")
    probability_map = dict(probability_result.scenario_probabilities)
    if set(value_map) != set(probability_map):
        raise ValueError("scenario intrinsic values must exactly match frozen probability scenarios")

    contributions = tuple(
        (
            scenario_id,
            probability,
            value_map[scenario_id],
            probability * value_map[scenario_id],
        )
        for scenario_id, probability in probability_result.scenario_probabilities
    )
    intrinsic_value = sum((item[3] for item in contributions), Decimal("0"))
    valuation_input_hash = sha256(
        json.dumps(
            [(item.scenario_id, str(item.intrinsic_value)) for item in scenario_values],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    binding_hash = sha256(
        json.dumps(
            {
                "contract": "post_freeze_probability_value_binding/v1",
                "probability_snapshot_hash": probability_result.snapshot_hash,
                "valuation_input_hash": valuation_input_hash,
                "intrinsic_value": str(intrinsic_value),
                "contributions": [
                    (scenario_id, str(probability), str(value), str(contribution))
                    for scenario_id, probability, value, contribution in contributions
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return ProbabilityWeightedIntrinsicValue(
        scenario_contributions=contributions,
        intrinsic_value=intrinsic_value,
        probability_snapshot_hash=probability_result.snapshot_hash,
        valuation_input_hash=valuation_input_hash,
        binding_hash=binding_hash,
    )
