from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass, replace
from decimal import Decimal
from enum import Enum
from hashlib import sha256
import json
from typing import Callable

from .capacity_yield_operating_paths import (
    CapacityYieldMetricMapping,
    CapacityYieldProfile,
    OperatingPolicy,
    evaluate_capacity_yield_path,
)
from .control_plane import StageStatus
from .distribution_route_policy import (
    DistributionIntegrationRoute,
    DistributionRouteAuthorization,
    DistributionRouteRequest,
    DistributionRouteStatus,
    NO_SCENARIO_ASSIGNMENT,
    authorize_distribution_route,
)
from .distributional_apv import (
    APVPathInput,
    EquityValueDistribution,
    NonOperatingAssetDisposal,
    PathAPVResult,
    SegmentCashFlowPath,
    TaxShieldSchedule,
    aggregate_equity_distribution,
    decimal_quantile,
    evaluate_apv_path,
)
from .dynamic_driver_distribution import DriverPath
from .entry_price import EntryPricePolicy, EntryPriceSensitivity, EntryPriceStatus
from .intrinsic_envelope import (
    DistributionLineage,
    IntrinsicValuationEnvelope,
    PrimaryValuationKind,
    seal_intrinsic_valuation_envelope,
)
from .levered_financing_paths import (
    FinancingPathSpec,
    FinancingPeriodInput,
    evaluate_financing_path,
)
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .payoff_model_ambiguity import (
    DatedShareholderPayoffPath,
    PayoffModelCase,
    RobustEntryPolicy,
    RobustPayoffAmbiguityResult,
    calculate_robust_payoff_ambiguity_entry,
    create_payoff_model_case_from_apv_results,
    dated_payoff_from_apv_result,
)
from .probability_ambiguity import ProbabilityVector, validate_probability_ambiguity_set
from .valuation_method_intent import PrimaryAggregatorIntent, ValuationMethodIntent


ZERO = Decimal("0")
ONE = Decimal("1")
_DISTRIBUTIONAL_BINDING = "capacity_yield_levered/driver_distributional_apv"


class DistributionalRuntimeError(ValueError):
    """Raised when a canonical distributional valuation cannot be replayed safely."""


@dataclass(frozen=True)
class DistributionPathExecutionInput:
    """One economic path under one complete financing-model case.

    The loader supplies only typed economic inputs.  Operating, financing and
    APV valuation arithmetic all run inside the canonical valuation stage.
    """

    model_case_id: str
    outcome_id: str
    driver_path: DriverPath
    financing_spec: FinancingPathSpec
    distress_asset_proceeds_by_period: tuple[Decimal, ...]
    core_segment_id: str
    core_economic_path_id: str
    asset_required_return: Decimal
    terminal_growth: Decimal
    tax_shield_discount_rate: Decimal
    equity_required_return: Decimal
    non_operating_assets_present: Decimal
    non_operating_assets_at_horizon: Decimal
    distributions_to_old_holders: tuple[Decimal, ...]
    initial_shares: Decimal
    supplemental_segments: tuple[SegmentCashFlowPath, ...] = ()
    asset_disposals: tuple[NonOperatingAssetDisposal, ...] = ()

    def validate(self, *, profile: CapacityYieldProfile, mapping: CapacityYieldMetricMapping) -> None:
        if not self.model_case_id or not self.outcome_id:
            raise DistributionalRuntimeError("distribution path requires model-case and outcome identity")
        if not self.core_segment_id or not self.core_economic_path_id:
            raise DistributionalRuntimeError("distribution path requires a core segment/economic path")
        drivers = self.driver_path.as_map()
        required = mapping.required_driver_ids()
        if set(required) - set(drivers):
            raise DistributionalRuntimeError("distribution driver path is missing required operating drivers")
        lengths = {len(drivers[item]) for item in required}
        if len(lengths) != 1 or not lengths or next(iter(lengths)) <= 0:
            raise DistributionalRuntimeError("distribution driver horizon is invalid")
        horizon = next(iter(lengths))
        if len(self.distress_asset_proceeds_by_period) != horizon:
            raise DistributionalRuntimeError("distress asset proceeds horizon mismatch")
        if len(self.distributions_to_old_holders) != horizon:
            raise DistributionalRuntimeError("old-holder distribution horizon mismatch")
        if any(value < ZERO for value in self.distress_asset_proceeds_by_period):
            raise DistributionalRuntimeError("distress asset proceeds cannot be negative")
        if any(value < ZERO for value in self.distributions_to_old_holders):
            raise DistributionalRuntimeError("old-holder distributions cannot be negative")
        if self.initial_shares <= ZERO:
            raise DistributionalRuntimeError("initial shares must be positive")
        if not ZERO <= self.asset_required_return < ONE:
            raise DistributionalRuntimeError("asset required return lies outside [0,1)")
        if not Decimal("-0.99") <= self.terminal_growth < ONE:
            raise DistributionalRuntimeError("terminal growth lies outside its valid range")
        if self.asset_required_return <= self.terminal_growth:
            raise DistributionalRuntimeError("asset required return must exceed terminal growth")
        if not ZERO <= self.tax_shield_discount_rate < ONE:
            raise DistributionalRuntimeError("tax-shield discount rate lies outside [0,1)")
        if not ZERO <= self.equity_required_return < ONE:
            raise DistributionalRuntimeError("equity required return lies outside [0,1)")
        supplemental_ids = tuple(item.segment_id for item in self.supplemental_segments)
        if self.core_segment_id in supplemental_ids or len(supplemental_ids) != len(set(supplemental_ids)):
            raise DistributionalRuntimeError("distribution segment identities overlap")
        economic_ids = (self.core_economic_path_id,) + tuple(
            item.economic_path_id for item in self.supplemental_segments
        )
        if len(economic_ids) != len(set(economic_ids)):
            raise DistributionalRuntimeError("distribution economic paths overlap")
        for item in self.supplemental_segments:
            item.validate(horizon)
        self.financing_spec.validate(horizon)
        profile.validate()
        mapping.validate(profile)


@dataclass(frozen=True)
class DistributionalAPVExecutionSpec:
    target_id: str
    profile: CapacityYieldProfile
    metric_mapping: CapacityYieldMetricMapping
    operating_policy: OperatingPolicy
    paths: tuple[DistributionPathExecutionInput, ...]
    route: DistributionIntegrationRoute
    route_evidence_path_ids: tuple[str, ...]
    entry_policy: EntryPricePolicy | RobustEntryPolicy
    driver_distribution_authorized: bool = False
    driver_distribution_authorization_hash: str = ""
    seed_set: tuple[int, ...] = ()
    probability_vectors: tuple[ProbabilityVector, ...] = ()
    reference_value_hashes: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.target_id or self.profile.target_id != self.target_id:
            raise DistributionalRuntimeError("distribution target/profile identity mismatch")
        self.profile.validate()
        self.metric_mapping.validate(self.profile)
        self.operating_policy.validate()
        if not self.paths or not self.route_evidence_path_ids:
            raise DistributionalRuntimeError("distribution execution requires paths and evidence")
        if len(self.route_evidence_path_ids) != len(set(self.route_evidence_path_ids)):
            raise DistributionalRuntimeError("distribution route repeats an evidence path")
        for item in self.paths:
            item.validate(profile=self.profile, mapping=self.metric_mapping)
        pairs = tuple((item.model_case_id, item.outcome_id) for item in self.paths)
        if len(pairs) != len(set(pairs)):
            raise DistributionalRuntimeError("distribution execution repeats model-case/outcome identity")
        if self.route is DistributionIntegrationRoute.PATHWISE_VALUE_DISTRIBUTION:
            if not isinstance(self.entry_policy, EntryPricePolicy):
                raise DistributionalRuntimeError("pathwise distribution requires EntryPricePolicy")
            self.entry_policy.validate()
            model_cases = {item.model_case_id for item in self.paths}
            if len(model_cases) != 1:
                raise DistributionalRuntimeError("calibrated pathwise distribution requires one payoff model case")
            if not self.driver_distribution_authorized or not self.driver_distribution_authorization_hash:
                raise DistributionalRuntimeError("pathwise distribution requires calibrated driver authorization")
            if not self.seed_set or len(self.seed_set) != len(set(self.seed_set)):
                raise DistributionalRuntimeError("pathwise distribution requires a distinct seed set")
            if self.probability_vectors:
                raise DistributionalRuntimeError("pathwise distribution cannot carry analyst probability vectors")
        elif self.route is DistributionIntegrationRoute.PRIOR_AMBIGUITY_VALUE_RANGE:
            if not isinstance(self.entry_policy, RobustEntryPolicy):
                raise DistributionalRuntimeError("prior ambiguity route requires RobustEntryPolicy")
            self.entry_policy.validate()
            if len(self.probability_vectors) < 2:
                raise DistributionalRuntimeError("prior ambiguity route requires at least two probability vectors")
            by_case = _group_path_inputs(self.paths)
            branch_sets = {tuple(sorted(item.outcome_id for item in paths)) for paths in by_case.values()}
            if len(branch_sets) != 1:
                raise DistributionalRuntimeError("every payoff model case must cover the same outcome set")
            branch_ids = next(iter(branch_sets))
            validate_probability_ambiguity_set(
                probability_vectors=self.probability_vectors,
                outcome_ids=branch_ids,
            )
        else:
            raise DistributionalRuntimeError("legacy replay cannot become a canonical primary valuation")


@dataclass(frozen=True)
class DatedPathwiseEntryResult:
    status: EntryPriceStatus
    entry_price: Decimal | None
    target_success_probability: Decimal
    realized_success_probability: Decimal | None
    discounted_payoffs: tuple[Decimal, ...]
    sensitivities: tuple[EntryPriceSensitivity, ...]
    policy_version: str
    distribution_hash: str
    calculation_hash: str
    authorization_receipt: str | None
    withheld_reason: str | None


@dataclass(frozen=True)
class AmbiguityIntrinsicCombination:
    probability_vector_id: str
    payoff_model_case_id: str
    expected_intrinsic_value: Decimal


@dataclass(frozen=True)
class AmbiguityIntrinsicRange:
    minimum_expected_value: Decimal
    maximum_expected_value: Decimal
    binding_minimum_probability_vector_id: str
    binding_minimum_model_case_id: str
    binding_maximum_probability_vector_id: str
    binding_maximum_model_case_id: str
    combinations: tuple[AmbiguityIntrinsicCombination, ...]
    calculation_hash: str


@dataclass(frozen=True)
class DistributionalPrimaryValuationResult:
    target_id: str
    route: DistributionIntegrationRoute
    reporting_unit: str
    path_results: tuple[PathAPVResult, ...]
    pathwise_distribution: EquityValueDistribution | None
    ambiguity_intrinsic_range: AmbiguityIntrinsicRange | None
    pathwise_entry: DatedPathwiseEntryResult | None
    robust_entry: RobustPayoffAmbiguityResult | None
    route_request: DistributionRouteRequest
    route_authorization: DistributionRouteAuthorization
    distribution_hash: str
    execution_input_hash: str
    envelope: IntrinsicValuationEnvelope

    @property
    def entry_price(self) -> Decimal | None:
        if self.pathwise_entry is not None:
            return self.pathwise_entry.entry_price
        if self.robust_entry is not None:
            return self.robust_entry.robust_entry_price
        return None

    @property
    def entry_policy_version(self) -> str:
        if self.pathwise_entry is not None:
            return self.pathwise_entry.policy_version
        if self.robust_entry is not None:
            return self.robust_entry.calculation_hash and ""  # guarded by executor output
        return ""


DistributionalAPVInputLoader = Callable[[OrchestratorContext], DistributionalAPVExecutionSpec]


def execute_distributional_apv(spec: DistributionalAPVExecutionSpec) -> DistributionalPrimaryValuationResult:
    spec.validate()
    execution_input_hash = _hash_payload({"contract": "distributional_apv_runtime_input/v1", "spec": spec})
    evaluated: list[tuple[DistributionPathExecutionInput, PathAPVResult]] = []
    for path_input in spec.paths:
        operating = evaluate_capacity_yield_path(
            profile=spec.profile,
            metric_mapping=spec.metric_mapping,
            driver_path=path_input.driver_path,
            policy=spec.operating_policy,
        )
        financing_inputs = tuple(
            FinancingPeriodInput(
                period=period.period,
                operating_cash_flow=period.operating_cash_flow,
                mandatory_capex=period.mandatory_capex,
                distress_asset_proceeds=path_input.distress_asset_proceeds_by_period[period.period - 1],
                taxable_income_before_interest=period.taxable_income_before_financing,
                tax_rate=spec.operating_policy.tax_rate,
            )
            for period in operating.periods
        )
        financing = evaluate_financing_path(
            inputs=financing_inputs,
            spec=path_input.financing_spec,
        )
        horizon = len(operating.periods)
        deductible_interest = tuple(
            period.debt_interest + period.lease_interest for period in financing.periods
        ) + (ZERO,) * (horizon - len(financing.periods))
        core_segment = SegmentCashFlowPath(
            segment_id=path_input.core_segment_id,
            economic_path_id=path_input.core_economic_path_id,
            unlevered_fcff=tuple(period.unlevered_fcff for period in operating.periods),
            asset_required_return=path_input.asset_required_return,
            terminal_growth=path_input.terminal_growth,
        )
        apv = evaluate_apv_path(
            APVPathInput(
                path_id=f"{path_input.model_case_id}:{path_input.outcome_id}:{path_input.driver_path.path_id}",
                segments=(core_segment, *path_input.supplemental_segments),
                tax_shield_schedule=TaxShieldSchedule(
                    taxable_income_before_interest=tuple(
                        period.taxable_income_before_financing for period in operating.periods
                    ),
                    deductible_interest=deductible_interest,
                    tax_rate=spec.operating_policy.tax_rate,
                    discount_rate=path_input.tax_shield_discount_rate,
                ),
                financing_result=financing,
                non_operating_assets_present=path_input.non_operating_assets_present,
                non_operating_assets_at_horizon=path_input.non_operating_assets_at_horizon,
                distributions_to_old_holders=path_input.distributions_to_old_holders,
                equity_required_return=path_input.equity_required_return,
                initial_shares=path_input.initial_shares,
                asset_disposals=path_input.asset_disposals,
            )
        )
        evaluated.append((path_input, apv))

    path_results = tuple(result for _, result in evaluated)
    economic_path_ids = tuple(
        dict.fromkeys(
            path_id
            for path_input, _ in evaluated
            for path_id in (
                path_input.core_economic_path_id,
                *(item.economic_path_id for item in path_input.supplemental_segments),
            )
        )
    )
    source_payoff_hash = _hash_payload(
        {
            "contract": "distributional_apv_source_payoffs/v1",
            "execution_input_hash": execution_input_hash,
            "path_hashes": tuple(item.path_calculation_hash for item in path_results),
        }
    )

    if spec.route is DistributionIntegrationRoute.PATHWISE_VALUE_DISTRIBUTION:
        distribution = aggregate_equity_distribution(
            path_results=path_results,
            seed_set=spec.seed_set,
            input_hash=_hash_payload(
                {
                    "execution_input_hash": execution_input_hash,
                    "driver_distribution_authorization_hash": spec.driver_distribution_authorization_hash,
                }
            ),
            valuation_distribution_authorized=spec.driver_distribution_authorized,
        )
        assert isinstance(spec.entry_policy, EntryPricePolicy)
        payoffs = tuple(
            dated_payoff_from_apv_result(branch_id=result.path_id, result=result)
            for result in path_results
        )
        pathwise_entry = _dated_pathwise_entry(
            payoffs=payoffs,
            policy=spec.entry_policy,
            distribution_hash=distribution.distribution_hash,
        )
        request = DistributionRouteRequest(
            route=spec.route,
            economic_archetypes=("capacity_yield_levered",),
            scenario_assignment_method=NO_SCENARIO_ASSIGNMENT,
            evidence_path_ids=spec.route_evidence_path_ids,
            calibrated_driver_distribution=True,
            financing_waterfall_authorized=True,
            future_shareholder_payoffs_authorized=(pathwise_entry.authorization_receipt is not None),
            future_payoff_authorization_receipt=pathwise_entry.authorization_receipt,
            payoff_horizon_years=spec.entry_policy.horizon_years,
            payoff_model_case_count=1,
        )
        authorization = authorize_distribution_route(request)
        lineage = DistributionLineage(
            distribution_hash=distribution.distribution_hash,
            route_authorization_hash=authorization.authorization_hash,
            entry_policy_version=spec.entry_policy.policy_version,
            entry_calculation_hash=pathwise_entry.calculation_hash,
        )
        envelope = seal_intrinsic_valuation_envelope(
            kind=PrimaryValuationKind.DISTRIBUTIONAL_APV,
            reporting_unit=spec.profile.reporting_currency,
            source_value_hash=distribution.distribution_hash,
            economic_path_ids=economic_path_ids,
            distribution_lineage=lineage,
            reference_value_hashes=spec.reference_value_hashes,
        )
        return DistributionalPrimaryValuationResult(
            target_id=spec.target_id,
            route=spec.route,
            reporting_unit=spec.profile.reporting_currency,
            path_results=path_results,
            pathwise_distribution=distribution,
            ambiguity_intrinsic_range=None,
            pathwise_entry=pathwise_entry,
            robust_entry=None,
            route_request=request,
            route_authorization=authorization,
            distribution_hash=distribution.distribution_hash,
            execution_input_hash=execution_input_hash,
            envelope=envelope,
        )

    assert spec.route is DistributionIntegrationRoute.PRIOR_AMBIGUITY_VALUE_RANGE
    assert isinstance(spec.entry_policy, RobustEntryPolicy)
    grouped_results = _group_evaluated(evaluated)
    branch_ids = tuple(sorted({item.outcome_id for item in spec.paths}))
    probability_vectors, ambiguity_set_hash = validate_probability_ambiguity_set(
        probability_vectors=spec.probability_vectors,
        outcome_ids=branch_ids,
    )
    payoff_cases: list[PayoffModelCase] = []
    for model_case_id, rows in sorted(grouped_results.items()):
        payoff_cases.append(
            create_payoff_model_case_from_apv_results(
                model_case_id=model_case_id,
                branch_results=tuple((path_input.outcome_id, result) for path_input, result in rows),
                evidence_path_ids=spec.route_evidence_path_ids,
            )
        )
    robust = calculate_robust_payoff_ambiguity_entry(
        payoff_model_cases=tuple(payoff_cases),
        probability_vectors=probability_vectors,
        policy=spec.entry_policy,
        future_payoffs_authorized=True,
        source_payoff_hash=source_payoff_hash,
    )
    intrinsic_range = _ambiguity_intrinsic_range(
        grouped_results=grouped_results,
        probability_vectors=probability_vectors,
        ambiguity_set_hash=ambiguity_set_hash,
        source_payoff_hash=source_payoff_hash,
    )
    distribution_hash = _hash_payload(
        {
            "contract": "prior_ambiguity_distributional_apv/v1",
            "source_payoff_hash": source_payoff_hash,
            "ambiguity_set_hash": ambiguity_set_hash,
            "intrinsic_range_hash": intrinsic_range.calculation_hash,
            "payoff_model_set_hash": robust.payoff_model_set_hash,
        }
    )
    request = DistributionRouteRequest(
        route=spec.route,
        economic_archetypes=("capacity_yield_levered",),
        scenario_assignment_method=NO_SCENARIO_ASSIGNMENT,
        evidence_path_ids=spec.route_evidence_path_ids,
        signed_values_authorized=True,
        ambiguity_set_validated=True,
        ambiguity_vector_count=len(probability_vectors),
        future_shareholder_payoffs_authorized=(robust.authorization_receipt is not None),
        future_payoff_authorization_receipt=robust.authorization_receipt,
        payoff_horizon_years=spec.entry_policy.horizon_years,
        payoff_model_case_count=len(payoff_cases),
    )
    authorization = authorize_distribution_route(request)
    lineage = DistributionLineage(
        distribution_hash=distribution_hash,
        route_authorization_hash=authorization.authorization_hash,
        entry_policy_version=spec.entry_policy.policy_version,
        ambiguity_set_hash=robust.probability_ambiguity_set_hash,
        payoff_model_set_hash=robust.payoff_model_set_hash,
        entry_calculation_hash=robust.calculation_hash,
    )
    envelope = seal_intrinsic_valuation_envelope(
        kind=PrimaryValuationKind.DISTRIBUTIONAL_APV,
        reporting_unit=spec.profile.reporting_currency,
        source_value_hash=distribution_hash,
        economic_path_ids=economic_path_ids,
        distribution_lineage=lineage,
        reference_value_hashes=spec.reference_value_hashes,
    )
    return DistributionalPrimaryValuationResult(
        target_id=spec.target_id,
        route=spec.route,
        reporting_unit=spec.profile.reporting_currency,
        path_results=path_results,
        pathwise_distribution=None,
        ambiguity_intrinsic_range=intrinsic_range,
        pathwise_entry=None,
        robust_entry=robust,
        route_request=request,
        route_authorization=authorization,
        distribution_hash=distribution_hash,
        execution_input_hash=execution_input_hash,
        envelope=envelope,
    )


def distributional_apv_valuation_adapter(
    *,
    loader: DistributionalAPVInputLoader,
) -> StageAdapter:
    if not callable(loader):
        raise TypeError("distributional APV loader must be callable")

    def run(context: OrchestratorContext) -> StageExecutionResult:
        intent = context.data.get("valuation_method_intent")
        if not isinstance(intent, ValuationMethodIntent) or not intent.ready:
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "ValuationMethodIntent is required before distributional APV",
                blocking=True,
            )
        aggregator = intent.primary_aggregator
        if not isinstance(aggregator, PrimaryAggregatorIntent) or not aggregator.ready:
            return StageExecutionResult(
                StageStatus.SKIPPED_NOT_APPLICABLE,
                "no company-level primary aggregator is selected",
            )
        if aggregator.binding != _DISTRIBUTIONAL_BINDING:
            return StageExecutionResult(
                StageStatus.NOT_IMPLEMENTED,
                f"primary aggregator {aggregator.binding} has no canonical runtime",
                blocking=True,
            )
        try:
            spec = loader(context)
            if not isinstance(spec, DistributionalAPVExecutionSpec):
                raise TypeError("distributional APV loader must return DistributionalAPVExecutionSpec")
            result = execute_distributional_apv(spec)
            result.envelope.validate()
        except (TypeError, ValueError, PermissionError) as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"distributional APV execution failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )

        lineage = result.envelope.distribution_lineage
        assert lineage is not None
        outputs: dict[str, object] = {
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
        status = (
            StageStatus.PASS
            if result.route_authorization.status is DistributionRouteStatus.AUTHORIZED
            else StageStatus.WARNING
        )
        return StageExecutionResult(
            status,
            (
                "canonical distributional APV primary valuation completed and route-authorized"
                if status is StageStatus.PASS
                else "distributional APV intrinsic value completed; unsupported point/success claims remain withheld"
            ),
            outputs,
        )

    return run


def primary_valuation_dispatch_adapter(
    *,
    deterministic_adapter: StageAdapter,
    distributional_loader: DistributionalAPVInputLoader | None,
) -> StageAdapter:
    distributional = (
        distributional_apv_valuation_adapter(loader=distributional_loader)
        if distributional_loader is not None
        else None
    )

    def run(context: OrchestratorContext) -> StageExecutionResult:
        intent = context.data.get("valuation_method_intent")
        if isinstance(intent, ValuationMethodIntent) and intent.primary_aggregator is not None:
            if distributional is None:
                return StageExecutionResult(
                    StageStatus.NOT_IMPLEMENTED,
                    "selected primary aggregator requires a DistributionalAPVInputLoader",
                    blocking=True,
                )
            return distributional(context)
        return deterministic_adapter(context)

    return run


def _dated_pathwise_entry(
    *,
    payoffs: tuple[DatedShareholderPayoffPath, ...],
    policy: EntryPricePolicy,
    distribution_hash: str,
) -> DatedPathwiseEntryResult:
    policy.validate()
    if not payoffs or not distribution_hash:
        raise DistributionalRuntimeError("dated pathwise entry requires payoffs and distribution hash")
    branch_ids = tuple(item.branch_id for item in payoffs)
    if len(branch_ids) != len(set(branch_ids)):
        raise DistributionalRuntimeError("dated pathwise payoffs repeat a path")
    for payoff in payoffs:
        payoff.validate(policy.horizon_years)
    discounted = tuple(payoff.present_value(policy.required_annual_return) for payoff in payoffs)
    entry = decimal_quantile(discounted, policy.success_quantile)
    target_success = ONE - policy.success_quantile
    sensitivities = tuple(
        EntryPriceSensitivity(
            required_annual_return=rate,
            entry_price=decimal_quantile(
                tuple(payoff.present_value(rate) for payoff in payoffs),
                policy.success_quantile,
            ),
        )
        for rate in policy.sensitivity_returns
    )
    reason = None if entry > ZERO else "NON_POSITIVE_DATED_ENTRY_QUANTILE"
    realized = (
        Decimal(sum(value >= entry for value in discounted)) / Decimal(len(discounted))
        if reason is None
        else None
    )
    calculation_hash = _hash_payload(
        {
            "contract": "dated_pathwise_entry/v1",
            "distribution_hash": distribution_hash,
            "policy": policy,
            "payoff_hashes": tuple(item.payoff_calculation_hash for item in payoffs),
            "discounted_payoffs": discounted,
            "entry": entry if reason is None else None,
            "reason": reason,
        }
    )
    receipt = (
        sha256(("future-shareholder-payoff-authorization/v1:" + calculation_hash).encode("utf-8")).hexdigest()
        if reason is None
        else None
    )
    return DatedPathwiseEntryResult(
        status=EntryPriceStatus.AVAILABLE if reason is None else EntryPriceStatus.WITHHELD,
        entry_price=entry if reason is None else None,
        target_success_probability=target_success,
        realized_success_probability=realized,
        discounted_payoffs=discounted,
        sensitivities=sensitivities if reason is None else (),
        policy_version=policy.policy_version,
        distribution_hash=distribution_hash,
        calculation_hash=calculation_hash,
        authorization_receipt=receipt,
        withheld_reason=reason,
    )


def _ambiguity_intrinsic_range(
    *,
    grouped_results: dict[str, tuple[tuple[DistributionPathExecutionInput, PathAPVResult], ...]],
    probability_vectors: tuple[ProbabilityVector, ...],
    ambiguity_set_hash: str,
    source_payoff_hash: str,
) -> AmbiguityIntrinsicRange:
    combinations: list[AmbiguityIntrinsicCombination] = []
    for model_case_id, rows in sorted(grouped_results.items()):
        values = {path_input.outcome_id: result.value_per_initial_share for path_input, result in rows}
        for vector in probability_vectors:
            weights = vector.as_map()
            combinations.append(
                AmbiguityIntrinsicCombination(
                    probability_vector_id=vector.vector_id,
                    payoff_model_case_id=model_case_id,
                    expected_intrinsic_value=sum(
                        (values[outcome_id] * weights[outcome_id] for outcome_id in values),
                        ZERO,
                    ),
                )
            )
    if not combinations:
        raise DistributionalRuntimeError("ambiguity intrinsic range has no combinations")
    minimum = min(
        combinations,
        key=lambda item: (item.expected_intrinsic_value, item.probability_vector_id, item.payoff_model_case_id),
    )
    maximum = max(
        combinations,
        key=lambda item: (item.expected_intrinsic_value, item.probability_vector_id, item.payoff_model_case_id),
    )
    calculation_hash = _hash_payload(
        {
            "contract": "distributional_apv_ambiguity_intrinsic_range/v1",
            "source_payoff_hash": source_payoff_hash,
            "ambiguity_set_hash": ambiguity_set_hash,
            "combinations": tuple(combinations),
        }
    )
    return AmbiguityIntrinsicRange(
        minimum_expected_value=minimum.expected_intrinsic_value,
        maximum_expected_value=maximum.expected_intrinsic_value,
        binding_minimum_probability_vector_id=minimum.probability_vector_id,
        binding_minimum_model_case_id=minimum.payoff_model_case_id,
        binding_maximum_probability_vector_id=maximum.probability_vector_id,
        binding_maximum_model_case_id=maximum.payoff_model_case_id,
        combinations=tuple(combinations),
        calculation_hash=calculation_hash,
    )


def _group_path_inputs(
    paths: tuple[DistributionPathExecutionInput, ...],
) -> dict[str, tuple[DistributionPathExecutionInput, ...]]:
    grouped: dict[str, list[DistributionPathExecutionInput]] = {}
    for item in paths:
        grouped.setdefault(item.model_case_id, []).append(item)
    return {key: tuple(value) for key, value in grouped.items()}


def _group_evaluated(
    rows: list[tuple[DistributionPathExecutionInput, PathAPVResult]],
) -> dict[str, tuple[tuple[DistributionPathExecutionInput, PathAPVResult], ...]]:
    grouped: dict[str, list[tuple[DistributionPathExecutionInput, PathAPVResult]]] = {}
    for row in rows:
        grouped.setdefault(row[0].model_case_id, []).append(row)
    return {key: tuple(value) for key, value in grouped.items()}


def _jsonable(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _hash_payload(payload: object) -> str:
    return sha256(
        json.dumps(
            _jsonable(payload),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "AmbiguityIntrinsicRange",
    "DatedPathwiseEntryResult",
    "DistributionPathExecutionInput",
    "DistributionalAPVExecutionSpec",
    "DistributionalAPVInputLoader",
    "DistributionalPrimaryValuationResult",
    "DistributionalRuntimeError",
    "distributional_apv_valuation_adapter",
    "execute_distributional_apv",
    "primary_valuation_dispatch_adapter",
]
