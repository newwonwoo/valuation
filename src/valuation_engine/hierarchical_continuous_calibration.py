from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json
import math

from .dynamic_hierarchical_posterior import DataIntegrityAssessment


@dataclass(frozen=True)
class NormalInverseGammaPosterior:
    mean: Decimal
    mean_strength: Decimal
    shape: Decimal
    scale: Decimal

    def validate(self) -> None:
        if any(not x.is_finite() for x in (self.mean, self.mean_strength, self.shape, self.scale)):
            raise ValueError("continuous posterior parameters must be finite")
        if self.mean_strength <= 0 or self.shape <= 1 or self.scale <= 0:
            raise ValueError("continuous posterior strength/shape/scale are invalid")

    @property
    def predictive_df(self) -> Decimal:
        self.validate()
        return Decimal("2") * self.shape

    @property
    def predictive_scale(self) -> Decimal:
        self.validate()
        variance_scale = self.scale * (self.mean_strength + Decimal("1")) / (self.shape * self.mean_strength)
        return Decimal(str(math.sqrt(float(variance_scale))))

    @property
    def mean_uncertainty(self) -> Decimal:
        self.validate()
        variance = self.scale / ((self.shape - Decimal("1")) * self.mean_strength)
        return Decimal(str(math.sqrt(float(variance))))


@dataclass(frozen=True)
class ContinuousSummaryEvidence:
    node_id: str
    sample_mean: Decimal
    sample_scale: Decimal
    sample_count: int
    likelihood_weight: Decimal
    dataset_hash: str
    integrity: DataIntegrityAssessment = DataIntegrityAssessment()

    def validate(self) -> None:
        if not self.node_id or not self.dataset_hash:
            raise ValueError("continuous summary evidence identity is incomplete")
        if self.sample_count < 0:
            raise ValueError("continuous summary sample count cannot be negative")
        if not self.sample_mean.is_finite() or not self.sample_scale.is_finite() or self.sample_scale < 0:
            raise ValueError("continuous summary moments are invalid")
        if not self.likelihood_weight.is_finite() or not Decimal("0") <= self.likelihood_weight <= Decimal("1"):
            raise ValueError("continuous summary likelihood weight must lie within [0,1]")


@dataclass(frozen=True)
class ContinuousHierarchyNode:
    node_id: str
    prior: NormalInverseGammaPosterior
    posterior: NormalInverseGammaPosterior
    effective_count: Decimal
    likelihood_weight: Decimal
    dataset_hash: str


@dataclass(frozen=True)
class ContinuousHierarchicalSnapshot:
    driver_id: str
    horizon: str
    root_prior: NormalInverseGammaPosterior
    nodes: tuple[ContinuousHierarchyNode, ...]
    final_posterior: NormalInverseGammaPosterior | None
    integrity_violations: tuple[str, ...]
    dataset_hash: str
    snapshot_hash: str

    @property
    def estimated(self) -> bool:
        return self.final_posterior is not None and not self.integrity_violations


def build_hierarchical_continuous_posterior(
    *,
    driver_id: str,
    horizon: str,
    root_prior: NormalInverseGammaPosterior,
    evidence: tuple[ContinuousSummaryEvidence, ...],
) -> ContinuousHierarchicalSnapshot:
    if not driver_id or not horizon:
        raise ValueError("continuous hierarchical calibration requires driver_id and horizon")
    root_prior.validate()
    violations: list[str] = []
    for item in evidence:
        item.validate()
        violations.extend(f"{item.node_id}:{code}" for code in item.integrity.violations)
    dataset_hash = _dataset_hash(driver_id, horizon, root_prior, evidence)
    if violations:
        return _snapshot(driver_id, horizon, root_prior, (), None, tuple(sorted(violations)), dataset_hash)

    prior = root_prior
    nodes: list[ContinuousHierarchyNode] = []
    for item in evidence:
        posterior, effective_count = _fractional_nig_update(prior, item)
        nodes.append(
            ContinuousHierarchyNode(
                node_id=item.node_id,
                prior=prior,
                posterior=posterior,
                effective_count=effective_count,
                likelihood_weight=item.likelihood_weight,
                dataset_hash=item.dataset_hash,
            )
        )
        prior = posterior
    return _snapshot(driver_id, horizon, root_prior, tuple(nodes), prior, (), dataset_hash)


def _fractional_nig_update(
    prior: NormalInverseGammaPosterior,
    evidence: ContinuousSummaryEvidence,
) -> tuple[NormalInverseGammaPosterior, Decimal]:
    prior.validate()
    evidence.validate()
    if evidence.sample_count == 0 or evidence.likelihood_weight == 0:
        return prior, Decimal("0")

    n_raw = Decimal(evidence.sample_count)
    n_eff = n_raw * evidence.likelihood_weight
    kappa0 = prior.mean_strength
    mu0 = prior.mean
    kappa_n = kappa0 + n_eff
    mu_n = (kappa0 * mu0 + n_eff * evidence.sample_mean) / kappa_n
    within_ss = evidence.likelihood_weight * Decimal(max(evidence.sample_count - 1, 0)) * evidence.sample_scale * evidence.sample_scale
    mean_shift = (kappa0 * n_eff / kappa_n) * (evidence.sample_mean - mu0) * (evidence.sample_mean - mu0)
    alpha_n = prior.shape + n_eff / Decimal("2")
    beta_n = prior.scale + Decimal("0.5") * (within_ss + mean_shift)
    posterior = NormalInverseGammaPosterior(mu_n, kappa_n, alpha_n, beta_n)
    posterior.validate()
    return posterior, n_eff


def _dataset_hash(
    driver_id: str,
    horizon: str,
    root: NormalInverseGammaPosterior,
    evidence: tuple[ContinuousSummaryEvidence, ...],
) -> str:
    payload = {
        "contract": "hierarchical_continuous_calibration_dataset/v1",
        "driver_id": driver_id,
        "horizon": horizon,
        "root": [str(root.mean), str(root.mean_strength), str(root.shape), str(root.scale)],
        "evidence": [
            [
                item.node_id,
                str(item.sample_mean),
                str(item.sample_scale),
                item.sample_count,
                str(item.likelihood_weight),
                item.dataset_hash,
                list(item.integrity.violations),
            ]
            for item in evidence
        ],
    }
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _snapshot(
    driver_id: str,
    horizon: str,
    root_prior: NormalInverseGammaPosterior,
    nodes: tuple[ContinuousHierarchyNode, ...],
    final_posterior: NormalInverseGammaPosterior | None,
    violations: tuple[str, ...],
    dataset_hash: str,
) -> ContinuousHierarchicalSnapshot:
    payload = {
        "contract": "hierarchical_continuous_calibration_snapshot/v1",
        "driver_id": driver_id,
        "horizon": horizon,
        "root": [str(root_prior.mean), str(root_prior.mean_strength), str(root_prior.shape), str(root_prior.scale)],
        "nodes": [
            [node.node_id, str(node.posterior.mean), str(node.posterior.mean_strength), str(node.posterior.shape), str(node.posterior.scale), node.dataset_hash]
            for node in nodes
        ],
        "final": (
            [str(final_posterior.mean), str(final_posterior.mean_strength), str(final_posterior.shape), str(final_posterior.scale)]
            if final_posterior is not None
            else None
        ),
        "violations": list(violations),
        "dataset_hash": dataset_hash,
    }
    snapshot_hash = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return ContinuousHierarchicalSnapshot(
        driver_id=driver_id,
        horizon=horizon,
        root_prior=root_prior,
        nodes=nodes,
        final_posterior=final_posterior,
        integrity_violations=violations,
        dataset_hash=dataset_hash,
        snapshot_hash=snapshot_hash,
    )
