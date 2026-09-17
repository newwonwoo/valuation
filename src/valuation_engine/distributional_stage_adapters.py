from __future__ import annotations

from dataclasses import replace

from .control_plane import StageStatus
from .distributional_runtime import (
    DistributionalAPVExecutionSpec,
    DistributionalPrimaryValuationResult,
    execute_distributional_apv,
)
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .valuation_method_intent import PrimaryAggregatorIntent, ValuationMethodIntent


_DISTRIBUTIONAL_BINDING = "capacity_yield_levered/driver_distributional_apv"


def bind_same_run_risk_receipts(
    spec: DistributionalAPVExecutionSpec,
    context: OrchestratorContext,
) -> DistributionalAPVExecutionSpec:
    """Bind risk-stage receipts that do not exist when initial run inputs are created."""

    receipts = list(spec.route_evidence_path_ids)
    if bool(context.data.get("risk_chain_requires_beta", False)):
        beta_hash = context.data.get("beta_snapshot_hash")
        if not isinstance(beta_hash, str) or not beta_hash:
            raise ValueError("distributional primary route requires same-run Beta receipt")
        receipts.append(f"beta:{beta_hash}")
    if bool(context.data.get("risk_chain_requires_wacc", False)):
        wacc_hash = context.data.get("wacc_snapshot_hash")
        if not isinstance(wacc_hash, str) or not wacc_hash:
            raise ValueError("distributional primary route requires same-run WACC receipt")
        receipts.append(f"wacc:{wacc_hash}")
    return replace(
        spec,
        route_evidence_path_ids=tuple(dict.fromkeys(receipts)),
    )


def canonical_primary_valuation_dispatch_adapter(
    *,
    deterministic_adapter: StageAdapter,
    initial_distributional_spec: DistributionalAPVExecutionSpec | None,
) -> StageAdapter:
    """Dispatch the primary valuation only after method intent and risk receipts exist."""

    def run(context: OrchestratorContext) -> StageExecutionResult:
        intent = context.data.get("valuation_method_intent")
        aggregator = (
            intent.primary_aggregator
            if isinstance(intent, ValuationMethodIntent)
            else None
        )
        if aggregator is None:
            return deterministic_adapter(context)
        if not isinstance(aggregator, PrimaryAggregatorIntent) or not aggregator.ready:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "company-level primary aggregator intent is unresolved",
                blocking=True,
            )
        if aggregator.binding != _DISTRIBUTIONAL_BINDING:
            return StageExecutionResult(
                StageStatus.NOT_IMPLEMENTED,
                f"primary aggregator {aggregator.binding} has no canonical runtime",
                blocking=True,
            )
        if initial_distributional_spec is None:
            return StageExecutionResult(
                StageStatus.NOT_IMPLEMENTED,
                "selected distributional primary aggregator requires typed execution inputs",
                blocking=True,
            )

        try:
            bound_spec = bind_same_run_risk_receipts(initial_distributional_spec, context)
            result = execute_distributional_apv(bound_spec)
            if not isinstance(result, DistributionalPrimaryValuationResult):
                raise TypeError("distributional executor returned an invalid result")
            result.envelope.validate()
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"distributional APV execution failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )

        lineage = result.envelope.distribution_lineage
        if lineage is None:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                "distributional primary result lacks immutable distribution lineage",
                blocking=True,
            )
        outputs: dict[str, object] = {
            "bound_distributional_apv_execution_spec": bound_spec,
            "distributional_primary_result": result,
            "intrinsic_valuation_envelope": result.envelope,
            "valuation_hash": result.envelope.envelope_hash,
            "distribution_hash": lineage.distribution_hash,
            "distribution_route_authorization": result.route_authorization,
            "distribution_route_authorization_hash": lineage.route_authorization_hash,
            "entry_policy_version": lineage.entry_policy_version,
            "entry_calculation_hash": lineage.entry_calculation_hash,
            "selected_methods": (_DISTRIBUTIONAL_BINDING,),
            "route_hash": result.route_authorization.authorization_hash,
            "valuation_scope": "FULL_INTRINSIC",
            "unvalued_segments": (),
            "full_company_intrinsic_available": True,
            "governed_entry_price": result.entry_price,
        }
        if result.pathwise_distribution is not None:
            outputs.update(
                {
                    "equity_value_distribution": result.pathwise_distribution,
                    "expected_value_per_share": result.pathwise_distribution.mean,
                    "distress_probability": result.pathwise_distribution.distress_probability,
                    "dilution_probability": result.pathwise_distribution.dilution_probability,
                    "old_shareholder_retention": result.pathwise_distribution.expected_old_share_retention_in_distress,
                }
            )
        if result.ambiguity_intrinsic_range is not None:
            outputs["ambiguity_expected_value_range"] = result.ambiguity_intrinsic_range
        if result.robust_entry is not None:
            outputs.update(
                {
                    "ambiguity_set_hash": result.robust_entry.probability_ambiguity_set_hash,
                    "payoff_model_set_hash": result.robust_entry.payoff_model_set_hash,
                    "payoff_ambiguity_result": result.robust_entry,
                }
            )
        return StageExecutionResult(
            StageStatus.PASS,
            "company-level distributional APV completed inside the canonical valuation stage with same-run risk receipts bound",
            outputs,
        )

    return run


__all__ = [
    "bind_same_run_risk_receipts",
    "canonical_primary_valuation_dispatch_adapter",
]
