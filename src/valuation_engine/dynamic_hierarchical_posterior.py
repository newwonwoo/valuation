from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
import math
from statistics import NormalDist


class PosteriorStatus(str, Enum):
    ESTIMATED = "ESTIMATED"
    DATA_BLOCKED = "DATA_BLOCKED"


@dataclass(frozen=True)
class DataIntegrityAssessment:
    first_seen_valid: bool = True
    publication_cutoff_valid: bool = True
    no_duplicate_events: bool = True
    no_outcome_leakage: bool = True
    source_traceable: bool = True
    period_units_consistent: bool = True

    @property
    def violations(self) -> tuple[str, ...]:
        checks = (
            ("FIRST_SEEN_VIOLATION", self.first_seen_valid),
            ("PUBLICATION_CUTOFF_VIOLATION", self.publication_cutoff_valid),
            ("DUPLICATE_EVENT_VIOLATION", self.no_duplicate_events),
            ("OUTCOME_LEAKAGE_VIOLATION", self.no_outcome_leakage),
            ("SOURCE_TRACEABILITY_VIOLATION", self.source_traceable),
            ("PERIOD_UNIT_MISMATCH", self.period_units_consistent),
        )
        return tuple(code for code, passed in checks if not passed)

    @property
    def passed(self) -> bool:
        return not self.violations


@dataclass(frozen=True)
class BetaPosterior:
    alpha: Decimal
    beta: Decimal

    def validate(self) -> None:
        if not self.alpha.is_finite() or not self.beta.is_finite():
            raise ValueError("beta posterior parameters must be finite")
        if self.alpha <= 0 or self.beta <= 0:
            raise ValueError("beta posterior parameters must be positive")

    @classmethod
    def from_mean_strength(cls, mean: Decimal, strength: Decimal) -> "BetaPosterior":
        if not mean.is_finite() or not Decimal("0") < mean < Decimal("1"):
            raise ValueError("prior mean must lie strictly within (0,1)")
        if not strength.is_finite() or strength <= 0:
            raise ValueError("prior strength must be positive")
        result = cls(mean * strength, (Decimal("1") - mean) * strength)
        result.validate()
        return result

    @property
    def strength(self) -> Decimal:
        return self.alpha + self.beta

    @property
    def mean(self) -> Decimal:
        self.validate()
        return self.alpha / self.strength

    @property
    def variance(self) -> Decimal:
        self.validate()
        s = self.strength
        return (self.alpha * self.beta) / (s * s * (s + Decimal("1")))

    def credible_interval(self, level: Decimal = Decimal("0.90")) -> tuple[Decimal, Decimal]:
        self.validate()
        if not Decimal("0") < level < Decimal("1"):
            raise ValueError("credible interval level must lie within (0,1)")
        # Deterministic normal approximation to the beta posterior. The interval is
        # deliberately conservative near the boundaries by clipping to [0,1].
        tail = float((Decimal("1") - level) / Decimal("2"))
        z = Decimal(str(NormalDist().inv_cdf(1.0 - tail)))
        sd = Decimal(str(math.sqrt(float(self.variance))))
        lower = max(Decimal("0"), self.mean - z * sd)
        upper = min(Decimal("1"), self.mean + z * sd)
        return lower, upper


@dataclass(frozen=True)
class HierarchicalEvidenceBlock:
    node_id: str
    success_count: int
    total_count: int
    likelihood_weight: Decimal
    dataset_hash: str
    integrity: DataIntegrityAssessment = DataIntegrityAssessment()

    def validate(self) -> None:
        if not self.node_id or not self.dataset_hash:
            raise ValueError("hierarchical evidence block identity is incomplete")
        if self.total_count < 0 or self.success_count < 0:
            raise ValueError("hierarchical evidence counts cannot be negative")
        if self.success_count > self.total_count:
            raise ValueError("success_count cannot exceed total_count")
        if (
            not self.likelihood_weight.is_finite()
            or not Decimal("0") <= self.likelihood_weight <= Decimal("1")
        ):
            raise ValueError("likelihood_weight must lie within [0,1]")


@dataclass(frozen=True)
class HierarchicalPosteriorNode:
    node_id: str
    prior_alpha: Decimal
    prior_beta: Decimal
    weighted_successes: Decimal
    weighted_failures: Decimal
    posterior: BetaPosterior
    likelihood_weight: Decimal
    dataset_hash: str


@dataclass(frozen=True)
class DynamicPosteriorSnapshot:
    event_class: str
    horizon: str
    root_prior: BetaPosterior
    nodes: tuple[HierarchicalPosteriorNode, ...]
    final_posterior: BetaPosterior | None
    credible_level: Decimal
    status: PosteriorStatus
    integrity_violations: tuple[str, ...]
    dataset_hash: str
    snapshot_hash: str

    @property
    def numeric_weighting_allowed(self) -> bool:
        return self.status is PosteriorStatus.ESTIMATED and self.final_posterior is not None

    @property
    def probability(self) -> Decimal | None:
        return self.final_posterior.mean if self.final_posterior is not None else None

    @property
    def credible_interval(self) -> tuple[Decimal, Decimal] | None:
        if self.final_posterior is None:
            return None
        return self.final_posterior.credible_interval(self.credible_level)

    def certificate(self) -> "PosteriorWeightingCertificate":
        if not self.numeric_weighting_allowed or self.final_posterior is None:
            raise PermissionError("posterior snapshot is blocked by data-integrity violations")
        lower, upper = self.final_posterior.credible_interval(self.credible_level)
        certificate = PosteriorWeightingCertificate(
            cohort_key=f"{self.event_class}|{self.horizon}",
            snapshot_hash=self.snapshot_hash,
            dataset_hash=self.dataset_hash,
            final_probability=self.final_posterior.mean,
            lower_probability=lower,
            upper_probability=upper,
            credible_level=self.credible_level,
            node_hashes=tuple(node.dataset_hash for node in self.nodes),
        )
        certificate.validate_for_weighting()
        return certificate


@dataclass(frozen=True)
class PosteriorWeightingCertificate:
    cohort_key: str
    snapshot_hash: str
    dataset_hash: str
    final_probability: Decimal
    lower_probability: Decimal
    upper_probability: Decimal
    credible_level: Decimal
    node_hashes: tuple[str, ...]

    def validate_for_weighting(self) -> None:
        if not self.cohort_key or not self.snapshot_hash or not self.dataset_hash:
            raise ValueError("posterior weighting certificate is incomplete")
        if not Decimal("0") <= self.lower_probability <= self.final_probability <= self.upper_probability <= Decimal("1"):
            raise ValueError("posterior probability interval is invalid")
        if not Decimal("0") < self.credible_level < Decimal("1"):
            raise ValueError("posterior credible level is invalid")
        if any(not value for value in self.node_hashes):
            raise ValueError("posterior certificate contains empty node lineage")

    @property
    def lineage_hash(self) -> str:
        payload = {
            "contract": "posterior_weighting_certificate/v1",
            "cohort_key": self.cohort_key,
            "snapshot_hash": self.snapshot_hash,
            "dataset_hash": self.dataset_hash,
            "final_probability": str(self.final_probability),
            "lower_probability": str(self.lower_probability),
            "upper_probability": str(self.upper_probability),
            "credible_level": str(self.credible_level),
            "node_hashes": self.node_hashes,
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def build_dynamic_hierarchical_posterior(
    *,
    event_class: str,
    horizon: str,
    root_prior: BetaPosterior,
    evidence_blocks: tuple[HierarchicalEvidenceBlock, ...],
    credible_level: Decimal = Decimal("0.90"),
) -> DynamicPosteriorSnapshot:
    if not event_class or not horizon:
        raise ValueError("dynamic posterior requires event_class and horizon")
    root_prior.validate()
    if not Decimal("0") < credible_level < Decimal("1"):
        raise ValueError("credible_level must lie within (0,1)")

    violations: list[str] = []
    for block in evidence_blocks:
        block.validate()
        violations.extend(f"{block.node_id}:{code}" for code in block.integrity.violations)

    if violations:
        dataset_hash = _dataset_hash(root_prior, evidence_blocks)
        return _snapshot(
            event_class=event_class,
            horizon=horizon,
            root_prior=root_prior,
            nodes=(),
            final_posterior=None,
            credible_level=credible_level,
            status=PosteriorStatus.DATA_BLOCKED,
            violations=tuple(sorted(violations)),
            dataset_hash=dataset_hash,
        )

    prior = root_prior
    nodes: list[HierarchicalPosteriorNode] = []
    for block in evidence_blocks:
        weighted_successes = Decimal(block.success_count) * block.likelihood_weight
        weighted_failures = Decimal(block.total_count - block.success_count) * block.likelihood_weight
        posterior = BetaPosterior(
            alpha=prior.alpha + weighted_successes,
            beta=prior.beta + weighted_failures,
        )
        posterior.validate()
        nodes.append(
            HierarchicalPosteriorNode(
                node_id=block.node_id,
                prior_alpha=prior.alpha,
                prior_beta=prior.beta,
                weighted_successes=weighted_successes,
                weighted_failures=weighted_failures,
                posterior=posterior,
                likelihood_weight=block.likelihood_weight,
                dataset_hash=block.dataset_hash,
            )
        )
        prior = posterior

    final = prior
    dataset_hash = _dataset_hash(root_prior, evidence_blocks)
    return _snapshot(
        event_class=event_class,
        horizon=horizon,
        root_prior=root_prior,
        nodes=tuple(nodes),
        final_posterior=final,
        credible_level=credible_level,
        status=PosteriorStatus.ESTIMATED,
        violations=(),
        dataset_hash=dataset_hash,
    )


def estimate_empirical_bayes_parent_strength(
    *,
    group_successes: tuple[int, ...],
    group_totals: tuple[int, ...],
    minimum_strength: Decimal = Decimal("2"),
    maximum_strength: Decimal = Decimal("500"),
) -> Decimal:
    if len(group_successes) != len(group_totals) or not group_successes:
        raise ValueError("group successes/totals must be non-empty and aligned")
    if minimum_strength <= 0 or maximum_strength < minimum_strength:
        raise ValueError("invalid empirical-bayes strength bounds")
    rates: list[Decimal] = []
    weights: list[Decimal] = []
    for successes, total in zip(group_successes, group_totals):
        if total <= 0 or successes < 0 or successes > total:
            raise ValueError("invalid group success/total pair")
        rates.append(Decimal(successes) / Decimal(total))
        weights.append(Decimal(total))
    weight_sum = sum(weights, Decimal("0"))
    mean = sum(rate * weight for rate, weight in zip(rates, weights)) / weight_sum
    if mean <= Decimal("0") or mean >= Decimal("1"):
        return maximum_strength
    observed_variance = sum(
        weight * (rate - mean) * (rate - mean)
        for rate, weight in zip(rates, weights)
    ) / weight_sum
    sampling_variance = sum(
        weight * (mean * (Decimal("1") - mean) / Decimal(total))
        for weight, total in zip(weights, group_totals)
    ) / weight_sum
    latent_variance = max(Decimal("1e-12"), observed_variance - sampling_variance)
    strength = mean * (Decimal("1") - mean) / latent_variance - Decimal("1")
    return max(minimum_strength, min(maximum_strength, strength))


def _dataset_hash(root_prior: BetaPosterior, blocks: tuple[HierarchicalEvidenceBlock, ...]) -> str:
    payload = {
        "contract": "dynamic_hierarchical_probability_dataset/v1",
        "root_alpha": str(root_prior.alpha),
        "root_beta": str(root_prior.beta),
        "blocks": [
            {
                "node_id": block.node_id,
                "success_count": block.success_count,
                "total_count": block.total_count,
                "likelihood_weight": str(block.likelihood_weight),
                "dataset_hash": block.dataset_hash,
                "violations": block.integrity.violations,
            }
            for block in blocks
        ],
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _snapshot(
    *,
    event_class: str,
    horizon: str,
    root_prior: BetaPosterior,
    nodes: tuple[HierarchicalPosteriorNode, ...],
    final_posterior: BetaPosterior | None,
    credible_level: Decimal,
    status: PosteriorStatus,
    violations: tuple[str, ...],
    dataset_hash: str,
) -> DynamicPosteriorSnapshot:
    payload = {
        "contract": "dynamic_hierarchical_probability_snapshot/v1",
        "event_class": event_class,
        "horizon": horizon,
        "root": (str(root_prior.alpha), str(root_prior.beta)),
        "nodes": [
            (
                node.node_id,
                str(node.posterior.alpha),
                str(node.posterior.beta),
                str(node.likelihood_weight),
                node.dataset_hash,
            )
            for node in nodes
        ],
        "final": (
            (str(final_posterior.alpha), str(final_posterior.beta))
            if final_posterior is not None
            else None
        ),
        "credible_level": str(credible_level),
        "status": status.value,
        "violations": violations,
        "dataset_hash": dataset_hash,
    }
    snapshot_hash = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return DynamicPosteriorSnapshot(
        event_class=event_class,
        horizon=horizon,
        root_prior=root_prior,
        nodes=nodes,
        final_posterior=final_posterior,
        credible_level=credible_level,
        status=status,
        integrity_violations=violations,
        dataset_hash=dataset_hash,
        snapshot_hash=snapshot_hash,
    )
