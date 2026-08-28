from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json

from .calibration_hierarchy import CalibrationHierarchyPath
from .hierarchical_calibration import HierarchicalNodeCalibration, HierarchicalNodeState
from .probability_authorization import ProbabilityWeightingCertificate
from .records import CalibrationStatus


_AUTHORIZABLE_NODE_STATES = {
    HierarchicalNodeState.INHERITED,
    HierarchicalNodeState.SHRUNK,
    HierarchicalNodeState.CALIBRATED_LOCAL,
}


@dataclass(frozen=True)
class HierarchicalCalibrationCertificate:
    cohort_key: str
    event_class: str
    horizon: str
    path_key: str
    policy_version: str
    mapping_version: str
    shrinkage_version: str
    snapshot_hash: str
    dataset_hash: str
    ancestor_snapshot_hashes: tuple[str, ...]
    ancestor_dataset_hashes: tuple[str, ...]
    node_snapshot_hashes: tuple[str, ...]
    node_states: tuple[str, ...]
    final_probability: Decimal
    status: CalibrationStatus

    @property
    def lineage_hash(self) -> str:
        payload = {
            "contract": "hierarchical_calibration_certificate/v1",
            "cohort_key": self.cohort_key,
            "event_class": self.event_class,
            "horizon": self.horizon,
            "path_key": self.path_key,
            "policy_version": self.policy_version,
            "mapping_version": self.mapping_version,
            "shrinkage_version": self.shrinkage_version,
            "snapshot_hash": self.snapshot_hash,
            "dataset_hash": self.dataset_hash,
            "ancestor_snapshot_hashes": self.ancestor_snapshot_hashes,
            "ancestor_dataset_hashes": self.ancestor_dataset_hashes,
            "node_snapshot_hashes": self.node_snapshot_hashes,
            "node_states": self.node_states,
            "final_probability": str(self.final_probability),
            "status": self.status.value,
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def validate_for_weighting(self) -> None:
        if self.status is not CalibrationStatus.CALIBRATED:
            raise PermissionError(
                "hierarchical calibration certificate is not authorized for intrinsic weighting"
            )
        if not all(
            (
                self.cohort_key,
                self.event_class,
                self.horizon,
                self.path_key,
                self.policy_version,
                self.mapping_version,
                self.shrinkage_version,
                self.snapshot_hash,
                self.dataset_hash,
            )
        ):
            raise ValueError("hierarchical calibration certificate is incomplete")
        if (
            not self.final_probability.is_finite()
            or not Decimal("0") <= self.final_probability <= Decimal("1")
        ):
            raise ValueError("hierarchical final probability must be within [0,1]")
        if not self.ancestor_snapshot_hashes or not self.ancestor_dataset_hashes:
            raise ValueError(
                "hierarchical certificate requires certified ancestor lineage"
            )
        if len(self.ancestor_snapshot_hashes) != len(self.ancestor_dataset_hashes):
            raise ValueError("hierarchical ancestor lineage hash lengths differ")
        if len(self.node_snapshot_hashes) != len(self.node_states):
            raise ValueError("hierarchical node lineage hash/state lengths differ")
        if any(
            state not in {item.value for item in _AUTHORIZABLE_NODE_STATES}
            for state in self.node_states
        ):
            raise PermissionError(
                "hierarchical certificate contains an unauthorized node state"
            )


@dataclass(frozen=True)
class HierarchicalCalibrationSnapshot:
    path: CalibrationHierarchyPath
    root_cohort_key: str
    root_probability: Decimal
    root_snapshot_hash: str
    root_dataset_hash: str
    root_certificate_lineage_hash: str
    node_calibrations: tuple[HierarchicalNodeCalibration, ...]
    policy_version: str
    mapping_version: str
    shrinkage_version: str
    snapshot_hash: str
    dataset_hash: str
    status: CalibrationStatus

    @property
    def cohort_key(self) -> str:
        return f"{self.path.event_class}|{self.path.horizon}"

    @property
    def final_probability(self) -> Decimal | None:
        if not self.node_calibrations:
            return self.root_probability
        return self.node_calibrations[-1].posterior_probability

    def certificate(self) -> HierarchicalCalibrationCertificate:
        if self.status is not CalibrationStatus.CALIBRATED:
            raise PermissionError(
                "hierarchical calibration snapshot has not passed authorization gates"
            )
        probability = self.final_probability
        if probability is None:
            raise PermissionError("hierarchical calibration has no final probability")
        certificate = HierarchicalCalibrationCertificate(
            cohort_key=self.cohort_key,
            event_class=self.path.event_class,
            horizon=self.path.horizon,
            path_key=self.path.path_key,
            policy_version=self.policy_version,
            mapping_version=self.mapping_version,
            shrinkage_version=self.shrinkage_version,
            snapshot_hash=self.snapshot_hash,
            dataset_hash=self.dataset_hash,
            ancestor_snapshot_hashes=(self.root_snapshot_hash,),
            ancestor_dataset_hashes=(self.root_dataset_hash,),
            node_snapshot_hashes=tuple(
                item.snapshot_hash for item in self.node_calibrations
            ),
            node_states=tuple(item.state.value for item in self.node_calibrations),
            final_probability=probability,
            status=self.status,
        )
        certificate.validate_for_weighting()
        return certificate


def build_hierarchical_calibration_snapshot(
    *,
    path: CalibrationHierarchyPath,
    root_certificate: ProbabilityWeightingCertificate,
    root_probability: Decimal,
    node_calibrations: tuple[HierarchicalNodeCalibration, ...],
    policy_version: str,
    shrinkage_version: str,
) -> HierarchicalCalibrationSnapshot:
    path.validate()
    root_certificate.validate_for_weighting()
    if not policy_version or not shrinkage_version:
        raise ValueError(
            "hierarchical calibration snapshot requires policy and shrinkage versions"
        )
    if (
        not root_probability.is_finite()
        or not Decimal("0") <= root_probability <= Decimal("1")
    ):
        raise ValueError("root calibrated probability must be within [0,1]")

    expected_node_ids = tuple(item.node_id for item in path.nodes[1:])
    actual_node_ids = tuple(item.node_id for item in node_calibrations)
    if actual_node_ids != expected_node_ids[: len(actual_node_ids)]:
        raise ValueError(
            "hierarchical node calibrations do not follow the declared hierarchy path"
        )
    if len(node_calibrations) != len(expected_node_ids):
        raise ValueError(
            "hierarchical snapshot requires one calibration result per non-root path node"
        )

    prior_snapshot_hash = root_certificate.snapshot_hash
    prior_dataset_hash = root_certificate.dataset_hash
    prior_probability = root_probability
    for item in node_calibrations:
        if item.event_class != path.event_class or item.horizon != path.horizon:
            raise ValueError(
                "hierarchical node event class/horizon differs from declared path"
            )
        if item.parent_snapshot_hash != prior_snapshot_hash:
            raise ValueError(
                f"hierarchical parent snapshot chain mismatch at node {item.node_id}"
            )
        if item.parent_dataset_hash != prior_dataset_hash:
            raise ValueError(
                f"hierarchical parent dataset chain mismatch at node {item.node_id}"
            )
        if item.parent_probability != prior_probability:
            raise ValueError(
                f"hierarchical parent probability chain mismatch at node {item.node_id}"
            )
        prior_snapshot_hash = item.snapshot_hash
        prior_dataset_hash = item.node_dataset_hash
        if item.posterior_probability is not None:
            prior_probability = item.posterior_probability

    if any(item.state is HierarchicalNodeState.DEGRADED for item in node_calibrations):
        status = CalibrationStatus.DEGRADED
    elif any(
        item.state is HierarchicalNodeState.UNCALIBRATED
        or not item.authorizable
        for item in node_calibrations
    ):
        status = CalibrationStatus.UNCALIBRATED
    else:
        status = CalibrationStatus.CALIBRATED

    dataset_payload = {
        "contract": "hierarchical_calibration_dataset_lineage/v1",
        "root_dataset_hash": root_certificate.dataset_hash,
        "node_dataset_hashes": [
            item.node_dataset_hash for item in node_calibrations
        ],
        "path_key": path.path_key,
        "mapping_version": path.mapping_version,
    }
    dataset_hash = sha256(
        json.dumps(
            dataset_payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()

    root_lineage_hash = str(
        getattr(root_certificate, "lineage_hash", root_certificate.snapshot_hash)
    )
    snapshot_payload = {
        "contract": "hierarchical_calibration_snapshot/v1",
        "cohort_key": f"{path.event_class}|{path.horizon}",
        "root_cohort_key": root_certificate.cohort_key,
        "root_probability": str(root_probability),
        "root_snapshot_hash": root_certificate.snapshot_hash,
        "root_dataset_hash": root_certificate.dataset_hash,
        "root_certificate_lineage_hash": root_lineage_hash,
        "path_key": path.path_key,
        "node_snapshot_hashes": [
            item.snapshot_hash for item in node_calibrations
        ],
        "node_states": [item.state.value for item in node_calibrations],
        "final_probability": (
            str(node_calibrations[-1].posterior_probability)
            if node_calibrations
            else str(root_probability)
        ),
        "policy_version": policy_version,
        "mapping_version": path.mapping_version,
        "shrinkage_version": shrinkage_version,
        "dataset_hash": dataset_hash,
        "status": status.value,
    }
    snapshot_hash = sha256(
        json.dumps(
            snapshot_payload, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    return HierarchicalCalibrationSnapshot(
        path=path,
        root_cohort_key=root_certificate.cohort_key,
        root_probability=root_probability,
        root_snapshot_hash=root_certificate.snapshot_hash,
        root_dataset_hash=root_certificate.dataset_hash,
        root_certificate_lineage_hash=root_lineage_hash,
        node_calibrations=node_calibrations,
        policy_version=policy_version,
        mapping_version=path.mapping_version,
        shrinkage_version=shrinkage_version,
        snapshot_hash=snapshot_hash,
        dataset_hash=dataset_hash,
        status=status,
    )
