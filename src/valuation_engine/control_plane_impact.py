from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .decision_impact import ImpactPolicy
from .impact_history import ModuleImpactHistoryLedger
from .impact_orchestrator import (
    AdaptiveLoadoutPlan,
    LoadoutDisposition,
    ModuleExperimentSpec,
)


class DeploymentStatus(str, Enum):
    DEPLOYED = "deployed"
    DEFERRED_CONDITION = "deferred_condition"
    DEFERRED_SAMPLE = "deferred_sample"
    RETIRE_REVIEW = "retire_review"
    SKIPPED_NOT_APPLICABLE = "skipped_not_applicable"


@dataclass(frozen=True)
class UnitDeploymentOrder:
    module_id: str
    status: DeploymentStatus
    doctrine_coverage_status: str
    deploy: bool
    user_decision_required: bool
    rationale: str


@dataclass(frozen=True)
class ControlPlaneImpactLoadout:
    orders: tuple[UnitDeploymentOrder, ...]

    @property
    def active_modules(self) -> tuple[str, ...]:
        return tuple(order.module_id for order in self.orders if order.deploy)

    @property
    def user_review_modules(self) -> tuple[str, ...]:
        return tuple(order.module_id for order in self.orders if order.user_decision_required)

    @property
    def skipped_not_applicable(self) -> tuple[str, ...]:
        return tuple(
            order.module_id
            for order in self.orders
            if order.status is DeploymentStatus.SKIPPED_NOT_APPLICABLE
        )


def build_control_plane_impact_loadout(
    specs: tuple[ModuleExperimentSpec, ...],
    history: ModuleImpactHistoryLedger,
    *,
    mission_required_modules: tuple[str, ...] = (),
    policy: ImpactPolicy | None = None,
) -> ControlPlaneImpactLoadout:
    """Convert learned impact history into an executable next-run Control Plane order.

    Mission-required units override sampling/conditional deferral but never override
    non-applicability. A retire candidate remains a user-review item and is not deleted from
    the canonical system.
    """
    spec_map = {spec.module_id: spec for spec in specs}
    if len(spec_map) != len(specs):
        raise ValueError("specs must have unique module_id values")
    unknown_required = tuple(sorted(set(mission_required_modules) - set(spec_map)))
    if unknown_required:
        raise ValueError(f"unknown mission-required modules: {', '.join(unknown_required)}")
    invalid_required = tuple(
        sorted(module_id for module_id in mission_required_modules if not spec_map[module_id].applicable)
    )
    if invalid_required:
        raise ValueError(
            "mission-required module cannot be non-applicable: " + ", ".join(invalid_required)
        )

    plan: AdaptiveLoadoutPlan = history.adaptive_loadout(specs, policy=policy)
    required = set(mission_required_modules)
    orders: list[UnitDeploymentOrder] = []

    for row in plan.recommendations:
        if row.disposition is LoadoutDisposition.SKIP_NOT_APPLICABLE:
            status = DeploymentStatus.SKIPPED_NOT_APPLICABLE
            coverage = "skipped_not_applicable"
            deploy = False
            user_required = False
        elif row.disposition is LoadoutDisposition.RETIRE_REVIEW:
            status = DeploymentStatus.RETIRE_REVIEW
            coverage = "awaiting_user_decision"
            deploy = row.module_id in required
            user_required = True
        elif row.deploy_by_default or row.module_id in required:
            status = DeploymentStatus.DEPLOYED
            coverage = "pass"
            deploy = True
            user_required = False
        elif row.disposition is LoadoutDisposition.DEPLOY_CONDITIONAL:
            status = DeploymentStatus.DEFERRED_CONDITION
            coverage = "warning"
            deploy = False
            user_required = False
        else:
            status = DeploymentStatus.DEFERRED_SAMPLE
            coverage = "warning"
            deploy = False
            user_required = False

        orders.append(
            UnitDeploymentOrder(
                module_id=row.module_id,
                status=status,
                doctrine_coverage_status=coverage,
                deploy=deploy,
                user_decision_required=user_required,
                rationale=(
                    row.rationale
                    + ("; mission requirement overrides deferral" if row.module_id in required and deploy else "")
                ),
            )
        )

    mandatory_guardrails = {
        spec.module_id for spec in specs if spec.mandatory_guardrail and spec.applicable
    }
    active = {order.module_id for order in orders if order.deploy}
    missing_guardrails = tuple(sorted(mandatory_guardrails - active))
    if missing_guardrails:
        raise ValueError(
            "Control Plane loadout omitted mandatory guardrails: " + ", ".join(missing_guardrails)
        )

    return ControlPlaneImpactLoadout(tuple(orders))
