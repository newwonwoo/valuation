from pathlib import Path

import yaml

from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.industry_dna import EconomicArchetype, IndustryDNAProfile
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.module_plan import (
    COMMON_CORE_MODULES,
    ModuleRequirementPlan,
    SegmentModuleRequirementPlan,
    build_module_requirement_plan,
)
from valuation_engine.module_plan_adapter import module_requirement_plan_adapter
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.scanner_runtime import (
    ScannerFinding,
    ScannerFindingStatus,
    live_rocket_insight_dispatch_adapter,
)


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "archetype_module_registry.yaml"
CONTROLS = ROOT / "config" / "archetype_control_requirements.yaml"


def _profile() -> IndustryDNAProfile:
    return IndustryDNAProfile(
        segment_id="segment",
        sector_adapter="equipment.transformer",
        archetypes=(EconomicArchetype.CONTRACTED_BACKLOG,),
        revenue_recognition="delivery",
        price_formation="negotiated",
        asset_ownership="manufacturer",
        capital_intensity="high",
        regulation_intensity="medium",
        customer_structure="utilities",
        reinvestment_model="capacity",
        cashflow_duration="multi_year",
        evidence_keys=("E1",),
    )


def _plan() -> ModuleRequirementPlan:
    segment = SegmentModuleRequirementPlan(
        segment_id="segment",
        sector_adapter="equipment.transformer",
        archetypes=("contracted_backlog",),
        required_evidence=("backlog",),
        required_kpis=("book_to_bill",),
        mandatory_scanners=("MANDATORY",),
        kill_conditions=("backlog fails",),
        normalization_rules=("normalize",),
        beta_peer_features=("risk",),
        per_peer_features=("growth",),
        scenario_variables=("conversion",),
        funding_scans=(),
        terminal_policies=("terminal",),
        double_count_traps=(),
        forbidden_methods=(),
        allowed_valuation_methods=("normalized_dcf",),
        optional_scanners=("OPTIONAL",),
    )
    plan = ModuleRequirementPlan(
        segments=(segment,),
        common_core_modules=COMMON_CORE_MODULES,
        required_evidence=segment.required_evidence,
        required_kpis=segment.required_kpis,
        mandatory_scanners=segment.mandatory_scanners,
        kill_conditions=segment.kill_conditions,
        scenario_variables=segment.scenario_variables,
        double_count_traps=(),
        forbidden_methods=(),
        optional_scanners=segment.optional_scanners,
    )
    plan.validate()
    return plan


def _finding(scanner_id: str) -> ScannerFinding:
    return ScannerFinding(
        scanner_id=scanner_id,
        status=ScannerFindingStatus.PASS,
        summary=f"{scanner_id} ran",
        context_only=True,
    )


def _scanner_context(**extra) -> OrchestratorContext:
    data = {
        "company": "Company",
        "ticker": "000000",
        "target_id": "T",
        "evidence_ledger": EvidenceLedger(),
        "module_requirement_plan": _plan(),
        "mandatory_scanners": ("MANDATORY",),
    }
    data.update(extra)
    return OrchestratorContext("RUN", ExecutionMode.LIVE_PRIMARY, data)


def test_generic_active_research_unit_is_never_inferred_as_optional_scanner():
    called: list[str] = []

    def runner(context):
        called.append(context.scanner_id)
        return _finding(context.scanner_id)

    result = live_rocket_insight_dispatch_adapter(
        runners={"MANDATORY": runner, "OPTIONAL": runner}
    )(
        _scanner_context(
            active_research_units=("MANDATORY", "OPTIONAL"),
            active_optional_scanners=(),
        )
    )
    assert result.status is StageStatus.PASS
    assert called == ["MANDATORY"]
    assert tuple(item.scanner_id for item in result.outputs["scanner_findings"]) == (
        "MANDATORY",
    )


def test_explicit_declared_optional_scanner_executes_after_mandatory_scanner():
    called: list[str] = []

    def runner(context):
        called.append(context.scanner_id)
        return _finding(context.scanner_id)

    result = live_rocket_insight_dispatch_adapter(
        runners={"MANDATORY": runner, "OPTIONAL": runner}
    )(_scanner_context(active_optional_scanners=("OPTIONAL",)))
    assert result.status is StageStatus.PASS
    assert called == ["MANDATORY", "OPTIONAL"]


def test_undeclared_optional_scanner_activation_fails_closed():
    result = live_rocket_insight_dispatch_adapter(
        runners={"MANDATORY": lambda context: _finding(context.scanner_id)}
    )(_scanner_context(active_optional_scanners=("NOT_IN_PLAN",)))
    assert result.status is StageStatus.BLOCKED
    assert result.blocking
    assert "outside the Module Requirement Plan" in result.rationale


def test_existing_control_registry_defaults_optional_scanners_to_empty():
    plan = build_module_requirement_plan(
        (_profile(),),
        registry_path=REGISTRY,
        control_requirements_path=CONTROLS,
    )
    assert plan.optional_scanners == ()
    assert plan.segments[0].optional_scanners == ()


def test_control_registry_can_declare_typed_optional_scanner(tmp_path):
    payload = yaml.safe_load(CONTROLS.read_text(encoding="utf-8"))
    payload["requirements"]["contracted_backlog"]["optional_scanners"] = [
        "OPTIONAL_BACKLOG_DEPTH"
    ]
    controls = tmp_path / "controls.yaml"
    controls.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    plan = build_module_requirement_plan(
        (_profile(),),
        registry_path=REGISTRY,
        control_requirements_path=controls,
    )
    assert plan.optional_scanners == ("OPTIONAL_BACKLOG_DEPTH",)
    assert plan.segments[0].optional_scanners == ("OPTIONAL_BACKLOG_DEPTH",)


def test_module_plan_adapter_emits_separate_optional_scanner_activation(tmp_path):
    payload = yaml.safe_load(CONTROLS.read_text(encoding="utf-8"))
    payload["requirements"]["contracted_backlog"]["optional_scanners"] = [
        "OPTIONAL_BACKLOG_DEPTH"
    ]
    controls = tmp_path / "controls.yaml"
    controls.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    adapter = module_requirement_plan_adapter(
        registry_path=REGISTRY,
        control_requirements_path=controls,
    )
    context = OrchestratorContext(
        "RUN",
        ExecutionMode.LIVE_PRIMARY,
        {
            "industry_dna_profiles": (_profile(),),
            "optional_research_units": ("OPTIONAL_BACKLOG_DEPTH",),
            "optional_scanner_ids": ("OPTIONAL_BACKLOG_DEPTH",),
        },
    )
    result = adapter(context)
    assert result.status is StageStatus.PASS
    assert result.outputs["active_optional_scanners"] == (
        "OPTIONAL_BACKLOG_DEPTH",
    )
    assert "OPTIONAL_BACKLOG_DEPTH" in result.outputs["active_research_units"]


def test_module_plan_adapter_rejects_optional_scanner_not_declared_by_plan():
    adapter = module_requirement_plan_adapter(
        registry_path=REGISTRY,
        control_requirements_path=CONTROLS,
    )
    result = adapter(
        OrchestratorContext(
            "RUN",
            ExecutionMode.LIVE_PRIMARY,
            {
                "industry_dna_profiles": (_profile(),),
                "optional_scanner_ids": ("UNDECLARED",),
            },
        )
    )
    assert result.status is StageStatus.BLOCKED
    assert "outside the Module Requirement Plan" in result.rationale
