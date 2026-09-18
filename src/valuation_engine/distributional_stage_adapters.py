from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from .control_plane import StageStatus
from .distribution_route_policy import DistributionIntegrationRoute
from .distributional_runtime import (
    DistributionalAPVExecutionSpec,
    DistributionalPrimaryValuationResult,
    execute_distributional_apv,
)
from .dynamic_driver_distribution import (
    TargetDriverPanel,
    fit_dynamic_driver_posterior,
    simulate_driver_paths,
)
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .risk_adapters import LiveBetaStageResult, LiveWACCStageResult
from .valuation_method_intent import PrimaryAggregatorIntent, ValuationMethodIntent


_DISTRIBUTIONAL_BINDING = "capacity_yield_levered/driver_distributional_apv"


def _decimal_rate(value: object, label: str) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite() or not Decimal("0") <= result < Decimal("1"):
        raise ValueError(f"{label} must be a finite rate in [0,1)")
    return result


def _validate_full_company_scope(
    spec: DistributionalAPVExecutionSpec,
    intent: ValuationMethodIntent,
) -> None:
    expected = {item.segment_id for item in intent.segments}
    if not expected:
        raise ValueError("distributional primary route has no planned company segments")
    for path in spec.paths:
        actual = {
            path.core_segment_id,
            *(item.segment_id for item in path.supplemental_segments),
        }
        if actual != expected:
            missing = sorted(expected - actual)
            extra = sorted(actual - expected)
            raise ValueError(
                "distributional primary route must value every planned company segment "
                f"in every path; missing={missing}, extra={extra}"
            )


def _validate_calibrated_driver_simulation(
    spec: DistributionalAPVExecutionSpec,
    context: OrchestratorContext,
) -> None:
    if spec.route is not DistributionIntegrationRoute.PATHWISE_VALUE_DISTRIBUTION:
        return
    panel = context.data.get("distributional_driver_panel")
    if not isinstance(panel, TargetDriverPanel):
        raise ValueError(
            "pathwise distribution requires target realized quarterly driver history"
        )
    if panel.target_id != spec.target_id:
        raise ValueError("driver-history target does not match valuation target")
    required_driver_ids = spec.metric_mapping.required_driver_ids()
    posterior = fit_dynamic_driver_posterior(
        panel,
        driver_ids=required_driver_ids,
    )
    if not posterior.calibration_diagnostics.valuation_distribution_authorized:
        raise ValueError("pathwise driver distribution failed rolling-origin OOS authorization")
    if not spec.seed_set or len(spec.paths) % len(spec.seed_set) != 0:
        raise ValueError("valuation path count must be an integer number of draws per seed")
    first_map = spec.paths[0].driver_path.as_map()
    horizon_lengths = {len(first_map[key]) for key in required_driver_ids}
    if len(horizon_lengths) != 1:
        raise ValueError("valuation driver paths do not share one horizon")
    horizon = next(iter(horizon_lengths))
    draws_per_seed = len(spec.paths) // len(spec.seed_set)
    simulation = simulate_driver_paths(
        posterior,
        horizon_periods=horizon,
        draws_per_seed=draws_per_seed,
        seed_set=spec.seed_set,
        require_authorized=True,
    )
    supplied_paths = tuple(item.driver_path for item in spec.paths)
    if supplied_paths != simulation.paths:
        raise ValueError(
            "valuation paths do not replay from the target-history calibrated driver model"
        )
    if spec.driver_distribution_authorization_hash != simulation.simulation_hash:
        raise ValueError(
            "driver distribution authorization hash is not the replayed simulation receipt"
        )
    if not spec.driver_distribution_authorized:
        raise ValueError("authorized simulation cannot be consumed with distribution authority disabled")


def bind_same_run_risk_receipts(
    spec: DistributionalAPVExecutionSpec,
    context: OrchestratorContext,
) -> DistributionalAPVExecutionSpec:
    """Bind same-run risk receipts and use their rates in APV arithmetic."""

    receipts = list(spec.route_evidence_path_ids)
    paths = spec.paths
    requires_beta = bool(context.data.get("risk_chain_requires_beta", False))
    requires_wacc = bool(context.data.get("risk_chain_requires_wacc", False))

    if requires_beta:
        beta_result = context.data.get("live_beta_result")
        beta_hash = context.data.get("beta_snapshot_hash")
        if (
            not isinstance(beta_result, LiveBetaStageResult)
            or not isinstance(beta_hash, str)
            or beta_hash != beta_result.snapshot_hash
        ):
            raise ValueError("distributional primary route requires same-run Beta result/receipt")
        receipts.append(f"beta:{beta_hash}")

    if requires_beta or requires_wacc:
        wacc_result = context.data.get("live_wacc_result")
        wacc_hash = context.data.get("wacc_snapshot_hash")
        if (
            not isinstance(wacc_result, LiveWACCStageResult)
            or not isinstance(wacc_hash, str)
            or wacc_hash != wacc_result.snapshot_hash
        ):
            raise ValueError(
                "distributional APV risk chain requires same-run WACC result so discount rates are not caller-supplied"
            )
        if requires_beta and wacc_result.beta_result.snapshot_hash != context.data.get("beta_snapshot_hash"):
            raise ValueError("distributional Beta/WACC snapshots are not one same-run risk chain")
        receipts.append(f"wacc:{wacc_hash}")
        asset_rate = _decimal_rate(wacc_result.wacc_result.wacc, "APV asset discount rate")
        shield_rate = _decimal_rate(
            wacc_result.wacc_result.after_tax_cost_of_debt,
            "APV tax-shield discount rate",
        )
        equity_rate = _decimal_rate(
            wacc_result.wacc_result.cost_of_equity,
            "APV equity required return",
        )
        paths = tuple(
            replace(
                item,
                asset_required_return=asset_rate,
                tax_shield_discount_rate=shield_rate,
                equity_required_return=equity_rate,
            )
            for item in spec.paths
        )

    return replace(
        spec,
        paths=paths,
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
            _validate_full_company_scope(bound_spec, intent)
            _validate_calibrated_driver_simulation(bound_spec, context)
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
            "company-level distributional APV completed for every planned segment with same-run risk rates and target-history calibrated path receipts bound",
            outputs,
        )

    return run


__all__ = [
    "bind_same_run_risk_receipts",
    "canonical_primary_valuation_dispatch_adapter",
]
