from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from pathlib import Path

import yaml


class HierarchicalNodeState(str, Enum):
    UNCALIBRATED = "UNCALIBRATED"
    INHERITED = "INHERITED"
    SHRUNK = "SHRUNK"
    CALIBRATED_LOCAL = "CALIBRATED_LOCAL"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True)
class ResolvedCalibrationEvent:
    event_key: str
    company_id: str
    issued_quarter: str
    occurred: bool

    def validate(self) -> None:
        if not self.event_key or not self.company_id or not self.issued_quarter:
            raise ValueError("resolved calibration event identity is incomplete")
        if "Q" not in self.issued_quarter:
            raise ValueError("resolved calibration event requires YYYYQn issued_quarter")


@dataclass(frozen=True)
class ParentCalibrationPrior:
    probability: Decimal
    strength: int
    certified: bool
    event_ids: tuple[str, ...]
    snapshot_hash: str
    dataset_hash: str
    oos_brier_skill_windows: tuple[Decimal, ...]
    strength_source: str = "training_oos_only"

    def validate(self) -> None:
        if (
            not self.probability.is_finite()
            or not Decimal("0") <= self.probability <= Decimal("1")
        ):
            raise ValueError("parent calibration probability must be within [0,1]")
        if self.strength < 0:
            raise ValueError("parent calibration strength cannot be negative")
        if self.certified and (not self.snapshot_hash or not self.dataset_hash):
            raise ValueError("certified parent prior requires snapshot and dataset hashes")
        if len(self.event_ids) != len(set(self.event_ids)):
            raise ValueError("parent calibration prior contains duplicate event IDs")


@dataclass(frozen=True)
class NodeCalibrationEvidence:
    node_id: str
    event_class: str
    horizon: str
    resolved_events: tuple[ResolvedCalibrationEvent, ...]
    oos_brier_skill_windows: tuple[Decimal, ...]
    dataset_hash: str

    def validate(self) -> None:
        if not self.node_id or not self.event_class or not self.horizon or not self.dataset_hash:
            raise ValueError("node calibration evidence identity is incomplete")
        event_ids = []
        for event in self.resolved_events:
            event.validate()
            event_ids.append(event.event_key)
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("node calibration evidence contains duplicate event IDs")

    @property
    def event_ids(self) -> tuple[str, ...]:
        return tuple(item.event_key for item in self.resolved_events)

    @property
    def resolved_count(self) -> int:
        return len(self.resolved_events)

    @property
    def success_count(self) -> int:
        return sum(1 for item in self.resolved_events if item.occurred)

    @property
    def company_count(self) -> int:
        return len({item.company_id for item in self.resolved_events})

    @property
    def quarter_count(self) -> int:
        return len({item.issued_quarter for item in self.resolved_events})

    @property
    def empirical_probability(self) -> Decimal | None:
        if not self.resolved_events:
            return None
        return Decimal(self.success_count) / Decimal(self.resolved_count)


@dataclass(frozen=True)
class ChildSpecializationPolicy:
    version: str
    shrinkage_version: str
    min_resolved_events: int
    min_companies: int
    min_quarters: int
    min_effective_sample_size: int
    min_oos_windows: int
    max_oos_brier_skill_delta_vs_parent: Decimal
    max_posterior_shift_without_local_promotion: Decimal
    parent_strength_default: int
    parent_strength_min: int
    parent_strength_max: int
    parent_strength_source: str

    def validate(self) -> None:
        if not self.version or not self.shrinkage_version or not self.parent_strength_source:
            raise ValueError("hierarchical calibration policy identity is incomplete")
        if min(
            self.min_resolved_events,
            self.min_companies,
            self.min_quarters,
            self.min_effective_sample_size,
            self.min_oos_windows,
            self.parent_strength_min,
        ) < 1:
            raise ValueError("hierarchical calibration policy minimums must be positive")
        if not self.parent_strength_min <= self.parent_strength_default <= self.parent_strength_max:
            raise ValueError("default parent strength must lie within configured bounds")
        if not Decimal("0") <= self.max_oos_brier_skill_delta_vs_parent <= Decimal("1"):
            raise ValueError("OOS Brier tolerance must be within [0,1]")
        if not Decimal("0") <= self.max_posterior_shift_without_local_promotion <= Decimal("1"):
            raise ValueError("posterior shift cap must be within [0,1]")


@dataclass(frozen=True)
class HierarchicalNodeCalibration:
    node_id: str
    event_class: str
    horizon: str
    parent_probability: Decimal | None
    local_empirical_probability: Decimal | None
    parent_strength: int
    local_resolved_count: int
    effective_sample_size: int
    posterior_probability: Decimal | None
    posterior_shift: Decimal | None
    oos_delta_vs_parent: Decimal | None
    state: HierarchicalNodeState
    gate_failures: tuple[str, ...]
    node_dataset_hash: str
    parent_snapshot_hash: str
    parent_dataset_hash: str
    snapshot_hash: str
    policy_version: str
    shrinkage_version: str

    @property
    def authorizable(self) -> bool:
        return self.state in {
            HierarchicalNodeState.INHERITED,
            HierarchicalNodeState.SHRUNK,
            HierarchicalNodeState.CALIBRATED_LOCAL,
        } and self.posterior_probability is not None


def load_child_specialization_policy(path: str | Path) -> ChildSpecializationPolicy:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("hierarchical probability policy root must be a mapping")
    child = payload.get("child_specialization_gate")
    shrinkage = payload.get("shrinkage")
    if not isinstance(child, dict) or not isinstance(shrinkage, dict):
        raise ValueError("hierarchical probability policy requires child gate and shrinkage sections")
    method = str(shrinkage.get("method") or "")
    if method != "beta_binomial_empirical_bayes":
        raise ValueError("unsupported hierarchical shrinkage method")
    if str(shrinkage.get("target_price_or_market_price_tuning") or "") != "forbidden":
        raise ValueError("target/market price tuning must remain forbidden")
    policy = ChildSpecializationPolicy(
        version=str(payload.get("version") or ""),
        shrinkage_version=f"{method}/v1",
        min_resolved_events=int(child.get("min_resolved_events", 30)),
        min_companies=int(child.get("min_companies", 5)),
        min_quarters=int(child.get("min_quarters", 4)),
        min_effective_sample_size=int(child.get("min_effective_sample_size", 50)),
        min_oos_windows=int(child.get("min_oos_windows", 2)),
        max_oos_brier_skill_delta_vs_parent=Decimal(
            str(child.get("max_oos_brier_skill_delta_vs_parent", "0.02"))
        ),
        max_posterior_shift_without_local_promotion=Decimal(
            str(child.get("max_posterior_shift_without_local_promotion", "0.10"))
        ),
        parent_strength_default=int(shrinkage.get("parent_strength_default", 40)),
        parent_strength_min=int(shrinkage.get("parent_strength_min", 10)),
        parent_strength_max=int(shrinkage.get("parent_strength_max", 200)),
        parent_strength_source=str(
            shrinkage.get("parent_strength_source") or "training_oos_only"
        ),
    )
    policy.validate()
    return policy


def _mean(values: tuple[Decimal, ...]) -> Decimal:
    return sum(values, Decimal("0")) / Decimal(len(values))


def build_hierarchical_node_calibration(
    *,
    evidence: NodeCalibrationEvidence,
    parent: ParentCalibrationPrior | None,
    policy: ChildSpecializationPolicy,
    prior_state: HierarchicalNodeState = HierarchicalNodeState.UNCALIBRATED,
) -> HierarchicalNodeCalibration:
    evidence.validate()
    policy.validate()
    if parent is not None:
        parent.validate()

    if parent is None or not parent.certified:
        return _result(
            evidence=evidence,
            parent=parent,
            policy=policy,
            parent_strength=0,
            posterior=None,
            oos_delta=None,
            state=(
                HierarchicalNodeState.DEGRADED
                if prior_state is HierarchicalNodeState.CALIBRATED_LOCAL
                else HierarchicalNodeState.UNCALIBRATED
            ),
            failures=("CERTIFIED_PARENT_REQUIRED",),
        )

    if parent.strength_source != policy.parent_strength_source:
        raise ValueError(
            "parent strength source does not match hierarchical calibration policy"
        )

    overlap = sorted(set(parent.event_ids).intersection(evidence.event_ids))
    if overlap:
        raise ValueError(
            "parent prior and child likelihood must be leave-child-out; "
            f"overlapping event IDs: {', '.join(overlap[:5])}"
        )

    strength = max(
        policy.parent_strength_min,
        min(policy.parent_strength_max, parent.strength or policy.parent_strength_default),
    )
    n = evidence.resolved_count
    if n == 0:
        return _result(
            evidence=evidence,
            parent=parent,
            policy=policy,
            parent_strength=strength,
            posterior=parent.probability,
            oos_delta=None,
            state=HierarchicalNodeState.INHERITED,
            failures=(),
        )

    posterior = (
        Decimal(evidence.success_count) + Decimal(strength) * parent.probability
    ) / Decimal(n + strength)
    effective_sample_size = n + strength

    failures: list[str] = []
    if n < policy.min_resolved_events:
        failures.append("MIN_RESOLVED_EVENTS")
    if evidence.company_count < policy.min_companies:
        failures.append("MIN_COMPANIES")
    if evidence.quarter_count < policy.min_quarters:
        failures.append("MIN_QUARTERS")
    if effective_sample_size < policy.min_effective_sample_size:
        failures.append("MIN_EFFECTIVE_SAMPLE_SIZE")

    oos_delta: Decimal | None = None
    if (
        len(evidence.oos_brier_skill_windows) < policy.min_oos_windows
        or len(parent.oos_brier_skill_windows) < policy.min_oos_windows
    ):
        failures.append("OOS_WINDOWS")
    else:
        child_windows = evidence.oos_brier_skill_windows[-policy.min_oos_windows :]
        parent_windows = parent.oos_brier_skill_windows[-policy.min_oos_windows :]
        oos_delta = _mean(child_windows) - _mean(parent_windows)
        if oos_delta < -policy.max_oos_brier_skill_delta_vs_parent:
            failures.append("OOS_DETERIORATION")

    promoted = not failures
    if not promoted:
        cap = policy.max_posterior_shift_without_local_promotion
        delta = posterior - parent.probability
        if abs(delta) > cap:
            posterior = parent.probability + (cap if delta > 0 else -cap)

    state = (
        HierarchicalNodeState.CALIBRATED_LOCAL
        if promoted
        else (
            HierarchicalNodeState.DEGRADED
            if prior_state is HierarchicalNodeState.CALIBRATED_LOCAL
            else HierarchicalNodeState.SHRUNK
        )
    )
    return _result(
        evidence=evidence,
        parent=parent,
        policy=policy,
        parent_strength=strength,
        posterior=posterior,
        oos_delta=oos_delta,
        state=state,
        failures=tuple(failures),
    )


def _result(
    *,
    evidence: NodeCalibrationEvidence,
    parent: ParentCalibrationPrior | None,
    policy: ChildSpecializationPolicy,
    parent_strength: int,
    posterior: Decimal | None,
    oos_delta: Decimal | None,
    state: HierarchicalNodeState,
    failures: tuple[str, ...],
) -> HierarchicalNodeCalibration:
    parent_probability = parent.probability if parent is not None else None
    shift = (
        posterior - parent_probability
        if posterior is not None and parent_probability is not None
        else None
    )
    payload = {
        "contract": "hierarchical_node_calibration/v1",
        "node_id": evidence.node_id,
        "event_class": evidence.event_class,
        "horizon": evidence.horizon,
        "parent_probability": (
            str(parent_probability) if parent_probability is not None else None
        ),
        "local_empirical_probability": (
            str(evidence.empirical_probability)
            if evidence.empirical_probability is not None
            else None
        ),
        "parent_strength": parent_strength,
        "events": [
            {
                "event_key": item.event_key,
                "company_id": item.company_id,
                "issued_quarter": item.issued_quarter,
                "occurred": item.occurred,
            }
            for item in evidence.resolved_events
        ],
        "posterior": str(posterior) if posterior is not None else None,
        "oos_delta": str(oos_delta) if oos_delta is not None else None,
        "state": state.value,
        "failures": failures,
        "node_dataset_hash": evidence.dataset_hash,
        "parent_snapshot_hash": parent.snapshot_hash if parent else "",
        "parent_dataset_hash": parent.dataset_hash if parent else "",
        "policy_version": policy.version,
        "shrinkage_version": policy.shrinkage_version,
    }
    snapshot_hash = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return HierarchicalNodeCalibration(
        node_id=evidence.node_id,
        event_class=evidence.event_class,
        horizon=evidence.horizon,
        parent_probability=parent_probability,
        local_empirical_probability=evidence.empirical_probability,
        parent_strength=parent_strength,
        local_resolved_count=evidence.resolved_count,
        effective_sample_size=evidence.resolved_count + parent_strength,
        posterior_probability=posterior,
        posterior_shift=shift,
        oos_delta_vs_parent=oos_delta,
        state=state,
        gate_failures=failures,
        node_dataset_hash=evidence.dataset_hash,
        parent_snapshot_hash=parent.snapshot_hash if parent else "",
        parent_dataset_hash=parent.dataset_hash if parent else "",
        snapshot_hash=snapshot_hash,
        policy_version=policy.version,
        shrinkage_version=policy.shrinkage_version,
    )
