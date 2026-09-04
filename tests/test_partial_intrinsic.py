from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from valuation_engine.actual_units import Measure
from valuation_engine.assumption_compiler import CompiledAssumption, CompiledAssumptionSet
from valuation_engine.backlog_evaluators import BacklogBurnDCFEvaluator
from valuation_engine.control_plane import (
    DoctrineCoverageEntry,
    ExecutionMode,
    StageStatus,
    issue_freeze_token,
)
from valuation_engine.evaluator_registry import (
    EvaluatorRegistry,
    ModelKey,
    NormalizedMultipleEvaluator,
)
from valuation_engine.generic_reporting import (
    _scenario_assumptions_line,
    render_generic_report,
)
from valuation_engine.impact_adapter import build_generic_decision_outcome
from valuation_engine.method_capabilities import load_default_method_capability_registry
from valuation_engine.module_plan import ModuleRequirementPlan, SegmentModuleRequirementPlan
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.partial_valuation import promote_partial_valuation_plan
from valuation_engine.post_freeze import compare_generic_to_market
from valuation_engine.post_freeze_adapters import market_compare_adapter
from valuation_engine.records import AuditReport, CalibrationStatus, MarketObservation
from valuation_engine.report_localization import evaluator_assumption_groups_ko
from valuation_engine.scenario_binding import BoundScenario, BoundScenarioSet
from valuation_engine.valuation_adapter import deterministic_valuation_adapter
from valuation_engine.valuation_execution import (
    CompanyValuationPlan,
    IntrinsicValuationScope,
    ParentAdjustmentPlan,
    SegmentValuationPlan,
    UnvaluedSegmentStatus,
    execute_company_valuation,
)
from valuation_engine.valuation_plan_compiler import (
    CompanyValuationPlanInputs,
    SegmentEvaluatorContract,
    SegmentMethodChoice,
    SegmentValueBinding,
    ValuationPlanCompilation,
    ValuationPlanStatus,
    compile_company_valuation_plan,
)
from valuation_engine.visual_reporting import (
    _assumptions_card,
    _multiple_assumption_table,
    render_report_visuals,
)


def _assumption(key: str, value: str, unit: str, *, scenario: str = "BASE") -> CompiledAssumption:
    return CompiledAssumption(
        key=key,
        scenario_id=scenario,
        measure=Measure(Decimal(value), unit, "2026-06-30"),
        bridge_id=f"B:{scenario}:{key}",
        evidence_ids=(f"E:{scenario}:{key}",),
        hypothesis_id=f"H:{scenario}:{key}",
        economic_path_id=f"PATH:{scenario}:{key}",
        transform_id="identity_observation",
        input_evidence_hash=f"HASH:{scenario}:{key}",
    )


def _scenario_set(*, include_shares: bool = True, include_unvalued_ownership: bool = False) -> BoundScenarioSet:
    assumptions = [
        _assumption("normalized_ebitda", "100", "KRW_billion"),
        _assumption("normalized_multiple", "8", "multiple"),
        _assumption("core_ownership", "1", "ratio"),
        _assumption("core_net_debt", "-100", "KRW_billion"),
    ]
    if include_shares:
        assumptions.append(_assumption("shares", "10", "shares"))
    if include_unvalued_ownership:
        assumptions.append(_assumption("unvalued_ownership", "1", "ratio"))
    return BoundScenarioSet(
        target_id="T",
        scenarios=(BoundScenario("BASE", tuple(assumptions)),),
        calibration_status=CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=False,
        scenario_set_hash="SCENARIO-HASH",
    )


def test_mixed_multiple_nav_and_dcf_visual_table_preserves_every_method_input():
    scenario = BoundScenario(
        "BASE",
        (
            _assumption("manufacturing_normalized_ebitda", "100", "KRW_billion"),
            _assumption("manufacturing_normalized_multiple", "8", "multiple"),
            _assumption("trading_normalized_ebitda", "20", "KRW_billion"),
            _assumption("trading_normalized_multiple", "4", "multiple"),
            _assumption("recycling_gross_asset_value", "20", "KRW_billion"),
            _assumption("recycling_liabilities", "5", "KRW_billion"),
            _assumption("transport_fcff_year_1", "10", "KRW_billion"),
            _assumption("transport_fcff_year_5", "15", "KRW_billion"),
            _assumption("transport_terminal_growth", "0.02", "ratio"),
            _assumption("transport_terminal_roic", "0.12", "ratio"),
            _assumption("manufacturing_ev_adjustment", "-10", "KRW_billion"),
            _assumption("manufacturing_ownership", "1", "ratio"),
            _assumption("recycling_ownership", "1", "ratio"),
            _assumption("transport_ownership", "1", "ratio"),
            _assumption("diluted_shares", "10", "shares"),
        ),
    )

    table = _multiple_assumption_table((scenario,))

    assert table is not None
    headers, rows = table
    assert headers == (
        "구분",
        "배수평가 부문",
        "NAV 부문",
        "현금흐름/NPV 부문",
        "영구/기타 입력",
        "EV→지분 조정",
    )
    rendered = " | ".join(rows[0])
    assert "제조 1,000억원×8배" in rendered
    assert "수출입 200억원×4배" in rendered
    assert "기타 150억원" in rendered
    assert "운송 100억원→150억원" in rendered
    assert "운송 g 2.0%/ROIC 12.0%" in rendered

    scenario_set = BoundScenarioSet(
        target_id="MIXED",
        scenarios=(scenario,),
        calibration_status=CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=False,
        scenario_set_hash="MIXED-SCENARIO-HASH",
    )
    svg = _assumptions_card(
        {
            "company": "Mixed Method",
            "ticker": "MIXED",
            "bound_scenario_set": scenario_set,
            "selected_methods": (
                "normalized_multiple",
                "asset_yield_nav",
                "driver_dcf",
            ),
            "live_wacc_result": SimpleNamespace(
                wacc_result=SimpleNamespace(
                    wacc=Decimal("0.09"),
                    cost_of_equity=Decimal("0.11"),
                )
            ),
        },
        "mixed.svg",
    ).svg
    assert "DCF 가중평균자본비용" in svg
    assert "9.00%" in svg
    assert "[DCF 가치+부문 EBITDA×배수 합+유형자산 NAV+EV→지분 조정]" in svg
    assert "제조 1,000억원×8배; 수출입 200억원×4배" not in svg
    assert "제조 1,000억원×8배;" in svg
    assert "수출입 200억원×4배" in svg


def test_typed_plan_contract_keeps_backlog_dcf_in_mixed_sotp_reporting():
    backlog = BacklogBurnDCFEvaluator(
        archetype="contracted_backlog",
        method="backlog_burn_dcf",
        version="1",
        forecast_years=2,
        discount_rate=Decimal("0.09"),
        discount_rate_path_id="WACC:TEST",
        assumption_prefix="transport_",
    )
    assumptions = [
        _assumption("manufacturing_normalized_ebitda", "100", "KRW_billion"),
        _assumption("manufacturing_normalized_multiple", "8", "multiple"),
        _assumption("manufacturing_ownership", "1", "ratio"),
        _assumption("manufacturing_ev_adjustment", "-10", "KRW_billion"),
        _assumption("transport_ownership", "1", "ratio"),
        _assumption("transport_ev_adjustment", "-2", "KRW_billion"),
        _assumption("parent_nci", "-3", "KRW_billion"),
        _assumption("diluted_shares", "10", "shares"),
    ]
    for key in backlog.required_assumption_keys:
        unit = (
            "KRW_billion"
            if key.endswith("opening_backlog")
            or key.endswith("opening_revenue")
            or "new_orders_year_" in key
            else "ratio"
        )
        value = "100" if unit == "KRW_billion" else "0.1"
        if "backlog_burn_rate_year_" in key:
            value = "0.5"
        elif "operating_margin_year_" in key:
            value = "0.2"
        elif key.endswith("terminal_growth"):
            value = "0.02"
        elif key.endswith("terminal_roic"):
            value = "0.12"
        assumptions.append(_assumption(key, value, unit))
    scenario = BoundScenario("BASE", tuple(assumptions))
    scenario_set = BoundScenarioSet(
        target_id="MIXED-BACKLOG",
        scenarios=(scenario,),
        calibration_status=CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=False,
        scenario_set_hash="MIXED-BACKLOG-HASH",
    )
    registry = EvaluatorRegistry()
    registry.register(
        NormalizedMultipleEvaluator(
            "commodity_price_taker",
            ebitda_key="manufacturing_normalized_ebitda",
            multiple_key="manufacturing_normalized_multiple",
        )
    )
    registry.register(backlog)
    module_plan = ModuleRequirementPlan(
        segments=(
            _segment(
                "manufacturing",
                "commodity_price_taker",
                ("normalized_multiple",),
            ),
            _segment(
                "transport",
                "contracted_backlog",
                ("backlog_burn_dcf",),
            ),
        ),
        common_core_modules=("evidence_gate",),
        required_evidence=("revenue",),
        required_kpis=("revenue",),
        mandatory_scanners=("TEST_SCANNER",),
        kill_conditions=("test kill",),
        scenario_variables=("revenue",),
        double_count_traps=(),
        forbidden_methods=(),
    )
    module_plan.validate()
    compilation = compile_company_valuation_plan(
        module_plan,
        scenario_set,
        evaluator_registry=registry,
        capability_registry=load_default_method_capability_registry(),
        inputs=CompanyValuationPlanInputs(
            reporting_unit="KRW_billion",
            diluted_shares_key="diluted_shares",
            segment_bindings=(
                SegmentValueBinding(
                    "manufacturing",
                    "manufacturing",
                    "manufacturing_ownership",
                    "manufacturing_ev_adjustment",
                ),
                SegmentValueBinding(
                    "transport",
                    "transport",
                    "transport_ownership",
                    "transport_ev_adjustment",
                ),
            ),
            parent_adjustments=(
                ParentAdjustmentPlan("parent_nci", "parent_nci"),
            ),
        ),
        method_choices=(
            SegmentMethodChoice(
                "manufacturing",
                "commodity_price_taker",
                "normalized_multiple",
                "1",
            ),
            SegmentMethodChoice(
                "transport",
                "contracted_backlog",
                "backlog_burn_dcf",
                "1",
            ),
        ),
    )
    assert compilation.ready
    assert tuple(
        item.execution_family for item in compilation.evaluator_contracts
    ) == ("normalized_multiple", "contracted_backlog_dcf")

    table = _multiple_assumption_table(
        (scenario,),
        evaluator_contracts=compilation.evaluator_contracts,
        valuation_plan=compilation.plan,
    )
    assert table is not None
    rendered_table = " | ".join(table[1][0])
    assert "제조 1,000억원×8배" in rendered_table
    assert "운송 수주잔고 DCF" in rendered_table
    assert "잔고 1,000억원" in rendered_table
    assert "EV -120억\n모 -30억" in rendered_table

    svg = _assumptions_card(
        {
            "company": "Mixed Backlog",
            "ticker": "MIXED-BACKLOG",
            "bound_scenario_set": scenario_set,
            "valuation_plan_compilation": compilation,
            "selected_methods": (
                "commodity_price_taker/normalized_multiple/1",
                "contracted_backlog/backlog_burn_dcf/1",
            ),
            "live_wacc_result": SimpleNamespace(
                wacc_result=SimpleNamespace(wacc=Decimal("0.09"))
            ),
        },
        "mixed-backlog.svg",
    ).svg
    assert "운송 수주잔고 DCF" in svg
    assert "배수평가 부문 귀속 지분가치+DCF 부문 귀속 지분가치" in svg
    assert "모회사 조정" in svg
    assert "모" in svg
    assert "-30억" in svg

    line = _scenario_assumptions_line(
        scenario,
        evaluator_contracts=compilation.evaluator_contracts,
        valuation_plan=compilation.plan,
    )
    assert "운송 수주잔고 DCF" in line
    assert "배수평가 부문 귀속 지분가치+DCF 부문 귀속 지분가치" in line
    assert "모회사 조정 -30억원" in line

    valuation = execute_company_valuation(
        scenario_set,
        plan=compilation.plan,
        registry=registry,
    )
    report = render_generic_report(
        {
            "company": "Mixed Backlog",
            "bound_scenario_set": scenario_set,
            "generic_valuation_result": valuation,
            "generic_audit_report": AuditReport(()),
            "doctrine_coverage": (),
            "valuation_plan_compilation": compilation,
            "selected_methods": (
                "commodity_price_taker/normalized_multiple/1",
                "contracted_backlog/backlog_burn_dcf/1",
            ),
        }
    )
    assert "운송 수주잔고 DCF" in report
    assert "배수평가 부문 귀속 지분가치+DCF 부문 귀속 지분가치" in report
    assert "모회사 조정 -30억원" in report


def test_single_dcf_assumptions_card_shows_parent_adjustment():
    scenario = BoundScenario(
        "Base",
        (
            _assumption("fcff_year_1", "10", "KRW_billion"),
            _assumption("fcff_year_5", "15", "KRW_billion"),
            _assumption("terminal_growth", "0.02", "ratio"),
            _assumption("terminal_roic", "0.12", "ratio"),
            _assumption("ownership", "1", "ratio"),
            _assumption("ev_adjustment", "-2", "KRW_billion"),
            _assumption("parent_nci", "-3", "KRW_billion"),
            _assumption("diluted_shares", "10", "shares"),
        ),
    )
    scenario_set = BoundScenarioSet(
        target_id="SINGLE-DCF",
        scenarios=(scenario,),
        calibration_status=CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=False,
        scenario_set_hash="SINGLE-DCF-HASH",
    )
    model_key = ModelKey("capacity_manufacturing", "driver_dcf", "1")
    plan = CompanyValuationPlan(
        segments=(
            SegmentValuationPlan(
                "core", "core", model_key, "ownership", "ev_adjustment"
            ),
        ),
        reporting_unit="KRW_billion",
        diluted_shares_key="diluted_shares",
        parent_adjustments=(ParentAdjustmentPlan("parent_nci", "parent_nci"),),
    )
    compilation = ValuationPlanCompilation(
        status=ValuationPlanStatus.READY,
        plan=plan,
        scenario_set_hash="SINGLE-DCF-HASH",
        module_plan_hash="MODULE",
        capability_registry_hash="CAPABILITY",
        evaluator_registry_hash="EVALUATOR",
        method_choices_hash="METHOD",
        segment_resolutions=(),
        evaluator_contracts=(
            SegmentEvaluatorContract(
                segment_id="core",
                model_key=model_key,
                execution_family="explicit_fcff_dcf",
                output_kind="enterprise_value",
                required_assumption_keys=(
                    "fcff_year_1",
                    "fcff_year_5",
                    "terminal_growth",
                    "terminal_roic",
                ),
            ),
        ),
        warranted_per_segments=(),
        aggregator_bindings=(),
        missing_assumptions=(),
    )

    svg = _assumptions_card(
        {
            "company": "Single DCF",
            "bound_scenario_set": scenario_set,
            "valuation_plan_compilation": compilation,
            "live_beta_result": SimpleNamespace(target_levered_beta=Decimal("1.1")),
            "live_wacc_result": SimpleNamespace(
                wacc_result=SimpleNamespace(
                    wacc=Decimal("0.09"),
                    cost_of_equity=Decimal("0.11"),
                )
            ),
        },
        "single-dcf.svg",
    ).svg

    assert "모회사 조정" in svg
    assert "-30억원" in svg


def test_typed_non_fcff_inputs_render_in_mixed_report_and_single_family_card():
    scenario = BoundScenario(
        "Base",
        (
            _assumption("manufacturing_normalized_ebitda", "100", "KRW_billion"),
            _assumption("manufacturing_normalized_multiple", "8", "multiple"),
            _assumption("bank_forward_distribution", "100", "KRW_billion"),
            _assumption("bank_terminal_growth", "0.02", "ratio"),
            _assumption("manufacturing_ownership", "1", "ratio"),
            _assumption("bank_ownership", "1", "ratio"),
            _assumption("diluted_shares", "10", "shares"),
        ),
    )
    multiple_contract = SegmentEvaluatorContract(
        segment_id="manufacturing",
        model_key=ModelKey("commodity_price_taker", "normalized_multiple", "1"),
        execution_family="normalized_multiple",
        output_kind="enterprise_value",
        required_assumption_keys=(
            "manufacturing_normalized_ebitda",
            "manufacturing_normalized_multiple",
        ),
    )
    ddm_key = ModelKey("financial_balance_sheet", "ddm", "1")
    ddm_contract = SegmentEvaluatorContract(
        segment_id="bank",
        model_key=ddm_key,
        execution_family="gordon_ddm",
        output_kind="equity_value",
        required_assumption_keys=(
            "bank_forward_distribution",
            "bank_terminal_growth",
        ),
    )

    line = _scenario_assumptions_line(
        scenario,
        evaluator_contracts=(multiple_contract, ddm_contract),
    )
    assert "bank 배당할인" in line
    assert "선행 배당 1,000억원" in line
    assert "영구성장률 2.0%" in line

    single_scenario = BoundScenario(
        "Base",
        tuple(
            item
            for item in scenario.assumptions
            if not item.key.startswith("manufacturing_")
        ),
    )
    scenario_set = BoundScenarioSet(
        target_id="SINGLE-DDM",
        scenarios=(single_scenario,),
        calibration_status=CalibrationStatus.UNCALIBRATED,
        numeric_weighting_allowed=False,
        scenario_set_hash="SINGLE-DDM-HASH",
    )
    plan = CompanyValuationPlan(
        segments=(
            SegmentValuationPlan(
                "bank", "bank", ddm_key, "bank_ownership", None
            ),
        ),
        reporting_unit="KRW_billion",
        diluted_shares_key="diluted_shares",
    )
    compilation = ValuationPlanCompilation(
        status=ValuationPlanStatus.READY,
        plan=plan,
        scenario_set_hash="SINGLE-DDM-HASH",
        module_plan_hash="MODULE",
        capability_registry_hash="CAPABILITY",
        evaluator_registry_hash="EVALUATOR",
        method_choices_hash="METHOD",
        segment_resolutions=(),
        evaluator_contracts=(ddm_contract,),
        warranted_per_segments=(),
        aggregator_bindings=(),
        missing_assumptions=(),
    )
    svg = _assumptions_card(
        {
            "company": "Single DDM",
            "bound_scenario_set": scenario_set,
            "valuation_plan_compilation": compilation,
            "live_beta_result": SimpleNamespace(target_levered_beta=Decimal("1.1")),
            "live_wacc_result": SimpleNamespace(
                wacc_result=SimpleNamespace(
                    wacc=Decimal("0.09"),
                    cost_of_equity=Decimal("0.11"),
                )
            ),
        },
        "single-ddm.svg",
    ).svg
    assert "bank 배당할인" in svg
    assert "선행 배당" in svg
    assert "1,000억원" in svg
    assert "영구성장률" in svg
    assert "2.0%" in svg
    assert "자기자본비용" in svg
    assert "11.00%" in svg
    assert "가중평균자본비용" not in svg
    assert "배당할인 부문 귀속 지분가치" in svg
    assert "핵심 자본적지출" not in svg
    assert "생산능력 반영" not in svg


@pytest.mark.parametrize(
    ("family", "keys", "expected_labels"),
    (
        (
            "contracted_backlog_dcf",
            (
                "core_opening_backlog",
                "core_opening_revenue",
                "core_new_orders_year_1",
                "core_backlog_burn_rate_year_1",
                "core_operating_margin_year_1",
                "core_operating_tax_rate",
                "core_depreciation_rate_of_revenue",
                "core_maintenance_capex_rate_of_revenue",
                "core_incremental_working_capital_rate",
                "core_terminal_growth",
                "core_terminal_roic",
            ),
            {
                "기초 수주잔고",
                "기초 매출",
                "소진률",
                "신규수주",
                "영업이익률",
                "영업세율",
                "감가상각률",
                "유지보수 투자율",
                "증분 운전자본률",
                "영구성장률",
                "영구 ROIC",
            },
        ),
        (
            "finite_life_npv",
            ("mine_cashflow_year_0", "mine_cashflow_year_1"),
            {"현금흐름"},
        ),
        (
            "gordon_ddm",
            ("bank_forward_distribution", "bank_terminal_growth"),
            {"선행 배당", "영구성장률"},
        ),
        (
            "justified_pb_roe",
            (
                "bank_current_book_value",
                "bank_forward_roe",
                "bank_terminal_growth",
            ),
            {"현재 장부가치", "선행 ROE", "영구성장률"},
        ),
        (
            "residual_income",
            (
                "bank_beginning_book_value",
                "bank_roe_year_1",
                "bank_roe_year_2",
                "bank_distribution_year_1",
                "bank_distribution_year_2",
                "bank_terminal_roe",
                "bank_terminal_growth",
            ),
            {"기초 장부가치", "ROE", "배당", "영구 ROE", "영구성장률"},
        ),
        (
            "rate_base_roe",
            (
                "utility_rate_base",
                "utility_equity_ratio",
                "utility_allowed_roe",
                "utility_terminal_growth",
            ),
            {"요금기반 자산", "자기자본비율", "허용 ROE", "영구성장률"},
        ),
        (
            "calibrated_single_event_rnpv",
            (
                "drug_unconditional_cashflow_year_0",
                "drug_unconditional_cashflow_year_1",
                "drug_contingent_cashflow_year_0",
                "drug_contingent_cashflow_year_1",
                "drug_probability_of_success",
            ),
            {"기본 현금흐름", "조건부 현금흐름", "보정 사건확률"},
        ),
    ),
)
def test_typed_reporting_groups_cover_every_compiled_input(
    family: str,
    keys: tuple[str, ...],
    expected_labels: set[str],
):
    primary, secondary = evaluator_assumption_groups_ko(family, keys)
    groups = (*primary, *secondary)
    assert {label for label, _ in groups} == expected_labels
    grouped_keys = tuple(key for _, grouped in groups for key in grouped)
    assert len(grouped_keys) == len(set(grouped_keys))
    assert set(grouped_keys) == set(keys)


def test_pure_multiple_visual_table_has_a_bounded_column_contract():
    scenario = BoundScenario(
        "BASE",
        (
            _assumption("manufacturing_normalized_ebitda", "100", "KRW_billion"),
            _assumption("manufacturing_normalized_multiple", "8", "multiple"),
            _assumption("trading_normalized_ebitda", "20", "KRW_billion"),
            _assumption("trading_normalized_multiple", "4", "multiple"),
            _assumption("transport_normalized_ebitda", "10", "KRW_billion"),
            _assumption("transport_normalized_multiple", "3", "multiple"),
            _assumption("recycling_gross_asset_value", "20", "KRW_billion"),
            _assumption("recycling_liabilities", "5", "KRW_billion"),
            _assumption("manufacturing_ev_adjustment", "-10", "KRW_billion"),
        ),
    )

    table = _multiple_assumption_table((scenario,))

    assert table is not None
    headers, rows = table
    assert len(headers) == len(rows[0]) == 6
    rendered = " | ".join(rows[0])
    assert "제조 1,000억원×8배" in rendered
    assert "수출입 200억원×4배" in rendered
    assert "운송 100억원×3배" in rendered
    assert "기타 150억원" in rendered
    assert "-100억원" in rendered


def _segment(
    segment_id: str,
    archetype: str,
    methods: tuple[str, ...],
) -> SegmentModuleRequirementPlan:
    result = SegmentModuleRequirementPlan(
        segment_id=segment_id,
        sector_adapter="test.adapter",
        archetypes=(archetype,),
        required_evidence=("revenue",),
        required_kpis=("revenue",),
        mandatory_scanners=("TEST_SCANNER",),
        kill_conditions=("test kill",),
        normalization_rules=("test normalization",),
        beta_peer_features=("risk",),
        per_peer_features=("quality",),
        scenario_variables=("revenue",),
        funding_scans=(),
        terminal_policies=("test terminal",),
        double_count_traps=(),
        forbidden_methods=(),
        allowed_valuation_methods=methods,
    )
    result.validate()
    return result


def _module_plan() -> ModuleRequirementPlan:
    valued = _segment(
        "core",
        "commodity_price_taker",
        ("normalized_multiple",),
    )
    unvalued = _segment(
        "future",
        "capacity_manufacturing",
        ("driver_dcf",),
    )
    plan = ModuleRequirementPlan(
        segments=(valued, unvalued),
        common_core_modules=("evidence_gate",),
        required_evidence=("revenue",),
        required_kpis=("revenue",),
        mandatory_scanners=("TEST_SCANNER",),
        kill_conditions=("test kill",),
        scenario_variables=("revenue",),
        double_count_traps=(),
        forbidden_methods=(),
    )
    plan.validate()
    return plan


def _inputs() -> CompanyValuationPlanInputs:
    return CompanyValuationPlanInputs(
        reporting_unit="KRW_billion",
        diluted_shares_key="shares",
        segment_bindings=(
            SegmentValueBinding(
                "core",
                "core-asset",
                "core_ownership",
                "core_net_debt",
            ),
            SegmentValueBinding(
                "future",
                "future-asset",
                "unvalued_ownership",
                "future_net_debt",
            ),
        ),
    )


def _registry() -> EvaluatorRegistry:
    registry = EvaluatorRegistry()
    registry.register(NormalizedMultipleEvaluator("commodity_price_taker"))
    return registry


def _partial_compilation(*, include_shares: bool = True):
    scenarios = _scenario_set(include_shares=include_shares)
    original = compile_company_valuation_plan(
        _module_plan(),
        scenarios,
        evaluator_registry=_registry(),
        capability_registry=load_default_method_capability_registry(),
        inputs=_inputs(),
    )
    assert original.status is ValuationPlanStatus.CAPABILITY_GAP
    promoted = promote_partial_valuation_plan(
        original,
        inputs=_inputs(),
        scenario_set=scenarios,
    )
    return scenarios, original, promoted


def _partial_result():
    scenarios, _, promoted = _partial_compilation()
    assert promoted.plan is not None
    return execute_company_valuation(
        scenarios,
        plan=promoted.plan,
        registry=_registry(),
    )


def _freeze_token(run_id: str):
    coverage = (DoctrineCoverageEntry("PARTIAL", StageStatus.PASS, "ready"),)
    return issue_freeze_token(
        run_id=run_id,
        audit_passed=True,
        coverage_entries=coverage,
        expected_module_ids=("PARTIAL",),
        ledger_snapshot_hash="ledger",
        assumption_set_hash="assumptions",
        valuation_hash="valuation",
        audit_hash="audit",
        industry_snapshot_hash="industry",
        source_snapshot_hash="source",
    )


def test_segment_local_capability_gap_promotes_to_partial_without_zero_filling():
    _, original, promoted = _partial_compilation()
    assert original.plan is None
    assert promoted.status is ValuationPlanStatus.CAPABILITY_GAP
    assert promoted.plan is not None
    assert promoted.plan.scope is IntrinsicValuationScope.PARTIAL_INTRINSIC
    assert tuple(item.segment_id for item in promoted.plan.segments) == ("core",)
    assert tuple(item.segment_id for item in promoted.plan.unvalued_segments) == ("future",)
    unvalued = promoted.plan.unvalued_segments[0]
    assert unvalued.status is UnvaluedSegmentStatus.UNVALUED_NOT_ZERO
    assert unvalued.resolution_status == "CAPABILITY_GAP"


def test_unvalued_segment_ownership_is_not_required_for_partial_subtotal():
    scenarios, _, promoted = _partial_compilation()
    assert promoted.plan is not None
    assert "BASE/unvalued_ownership" not in promoted.missing_assumptions
    result = execute_company_valuation(scenarios, plan=promoted.plan, registry=_registry())
    assert result.scope is IntrinsicValuationScope.PARTIAL_INTRINSIC
    assert result.scenarios[0].equity_value_amount == Decimal("700")
    assert result.scenarios[0].value_per_share == Decimal("70")


def test_missing_company_common_diluted_shares_prevents_partial_promotion():
    _, _, promoted = _partial_compilation(include_shares=False)
    assert promoted.plan is None
    assert "BASE/shares" in promoted.missing_assumptions


def test_partial_valuation_hash_changes_when_unvalued_contract_changes():
    scenarios, _, promoted = _partial_compilation()
    assert promoted.plan is not None
    first = execute_company_valuation(scenarios, plan=promoted.plan, registry=_registry())
    changed_unvalued = replace(
        promoted.plan.unvalued_segments[0],
        rationale="different unresolved reason",
    )
    changed_plan = replace(promoted.plan, unvalued_segments=(changed_unvalued,))
    second = execute_company_valuation(scenarios, plan=changed_plan, registry=_registry())
    assert first.valuation_hash != second.valuation_hash


def test_deterministic_adapter_returns_warning_and_explicit_partial_scope():
    scenarios, _, promoted = _partial_compilation()
    assert promoted.plan is not None
    context = OrchestratorContext(
        "RUN",
        ExecutionMode.LIVE_PRIMARY,
        {"bound_scenario_set": scenarios},
    )
    result = deterministic_valuation_adapter(
        plan=promoted.plan,
        registry=_registry(),
    )(context)
    assert result.status is StageStatus.WARNING
    assert not result.blocking
    assert result.outputs["valuation_scope"] is IntrinsicValuationScope.PARTIAL_INTRINSIC
    assert result.outputs["unvalued_segments"][0].status is UnvaluedSegmentStatus.UNVALUED_NOT_ZERO


def test_partial_subtotal_cannot_be_compared_to_whole_company_market_price():
    valuation = _partial_result()
    observation = MarketObservation(60.0, "2026-08-25", "market")
    with pytest.raises(ValueError, match="whole-company"):
        compare_generic_to_market(valuation, observation, currency="KRW_billion")

    context = OrchestratorContext(
        "RUN",
        ExecutionMode.LIVE_PRIMARY,
        {
            "generic_valuation_result": valuation,
            "market_observation": observation,
            "market_currency": "KRW_billion",
        },
        freeze_token=_freeze_token("RUN"),
    )
    stage = market_compare_adapter()(context)
    assert stage.status is StageStatus.SKIPPED_NOT_APPLICABLE
    assert "withheld" in stage.rationale
    assert "market_comparison" not in stage.outputs


def test_partial_decision_impact_never_exposes_subtotal_as_full_intrinsic():
    valuation = replace(_partial_result(), expected_value_per_share=Decimal("72"))
    compiled = CompiledAssumptionSet("T", (), "ASSUMPTION-HASH")
    context = OrchestratorContext(
        "RUN",
        ExecutionMode.LIVE_PRIMARY,
        {
            "compiled_assumption_set": compiled,
            "generic_valuation_result": valuation,
            "selected_methods": ("commodity_price_taker/normalized_multiple/1",),
            "route_hash": "ROUTE",
        },
    )
    outcome = build_generic_decision_outcome(context)
    assert outcome.status == "PARTIAL_INTRINSIC"
    assert outcome.intrinsic_value_per_share is None
    assert "partial_intrinsic" in outcome.conclusion_tags


def test_partial_report_labels_subtotal_and_unvalued_not_zero():
    valuation = _partial_result()
    report = render_generic_report(
        {
            "company": "Example",
            "generic_valuation_result": valuation,
            "generic_audit_report": AuditReport(()),
            "doctrine_coverage": (),
        }
    )
    assert "부분 내재가치 — 평가 완료 사업부만 포함" in report
    first_screen = report.split("\n### 한 문장 결론", 1)[0]
    assert "**평가 완료 사업부 소계**" in first_screen
    assert "**평가 완료 사업부 범위**" in first_screen
    assert "**기준 내재가치**" not in first_screen
    assert "평가완료 소계" in report
    assert "미평가 사업부 — 0원으로 간주하지 않음" in report
    assert "미평가 사업부는 0원으로 합산하지 않았습니다" in report

    summary = render_report_visuals(
        {
            "company": "Example",
            "ticker": "PARTIAL",
            "generic_valuation_result": valuation,
        }
    )[0].svg
    assert "평가 완료 사업부 소계" in summary
    assert "결정론적 가치평가 결과" not in summary
