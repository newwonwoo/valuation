from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one replacement, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "config/archetype_module_registry.yaml",
    "    - expansion_capex\n    normalization:\n",
    """    - expansion_capex
    - expansion_land_control
    - expansion_baseline_inclusion
    - expansion_capacity_committed
    - expansion_site_area
    - expansion_capex_committed
    - expansion_ramp_date
    - expansion_equipment_commitment
    - expansion_cancelled
    - no_active_capacity_expansion
    normalization:
""",
)

replace_once(
    "src/valuation_engine/capacity_consumption.py",
    """                {
                    "capacity_bridge_consumption_required": False,
                    "capacity_commitment_assessment_hash": assessment.assessment_hash,
                },
""",
    """                {
                    "capacity_bridge_consumption_required": False,
                },
""",
)

replace_once(
    "src/valuation_engine/live_runtime.py",
    "from .audit_adapter import generic_audit_adapter\n",
    """from .audit_adapter import generic_audit_adapter
from .capacity_commitment import (
    CapacityCommitmentLoader,
    capacity_commitment_gate_adapter,
)
from .capacity_consumption import (
    CapacityBridgeConsumptionLoader,
    capacity_bridge_consumption_gate_adapter,
)
from .capacity_runtime import (
    capacity_audit_adapter,
    capacity_consistency_gate_adapter,
    capacity_per_binding_adapter,
    capacity_scenario_binding_adapter,
    capacity_valuation_binding_adapter,
)
""",
)
replace_once(
    "src/valuation_engine/live_runtime.py",
    """    valuation_plan_inputs_loader: ValuationPlanInputsLoader
    funding_scanner: FundingScanner | None = None
""",
    """    valuation_plan_inputs_loader: ValuationPlanInputsLoader
    capacity_commitment_loader: CapacityCommitmentLoader | None = None
    capacity_bridge_consumption_loader: CapacityBridgeConsumptionLoader | None = None
    funding_scanner: FundingScanner | None = None
""",
)
replace_once(
    "src/valuation_engine/live_runtime.py",
    """    method_choices: tuple[SegmentMethodChoice, ...] = ()
    market_currency: str | None = None
""",
    """    method_choices: tuple[SegmentMethodChoice, ...] = ()
    capacity_core_scenario_id: str | None = None
    market_currency: str | None = None
""",
)
replace_once(
    "src/valuation_engine/live_runtime.py",
    """    state_load = chain_stage_adapters(
        load_company_state_adapter(state_root=state_root),
        load_research_learning_adapter(store=learning_store),
    )

    funding = conditional_funding_adapter(
""",
    """    state_load = chain_stage_adapters(
        load_company_state_adapter(state_root=state_root),
        load_research_learning_adapter(store=learning_store),
    )
    capacity_commitment = capacity_commitment_gate_adapter(
        loader=providers.capacity_commitment_loader
    )

    funding = conditional_funding_adapter(
""",
)
replace_once(
    "src/valuation_engine/live_runtime.py",
    """    scenario_chain: list[StageAdapter] = []
    if providers.calibration_loader is not None:
""",
    """    scenario_chain: list[StageAdapter] = [
        capacity_bridge_consumption_gate_adapter(
            loader=providers.capacity_bridge_consumption_loader
        )
    ]
    if providers.calibration_loader is not None:
""",
)
replace_once(
    "src/valuation_engine/live_runtime.py",
    """    scenario_chain.append(scenario_build_adapter())
    method_intent = valuation_method_intent_adapter(
""",
    """    scenario_chain.append(scenario_build_adapter())
    scenario_chain.append(
        capacity_scenario_binding_adapter(
            core_scenario_id=config.capacity_core_scenario_id
        )
    )
    method_intent = valuation_method_intent_adapter(
""",
)
replace_once(
    "src/valuation_engine/live_runtime.py",
    """        dcf_consistency_fingerprint_adapter(providers.dcf_fingerprint_loader),
    )

    per = conditional_warranted_per_adapter(
        live_hierarchical_warranted_per_adapter(loader=providers.per_loader)
        if providers.per_loader is not None
        else None
    )
""",
    """        dcf_consistency_fingerprint_adapter(providers.dcf_fingerprint_loader),
        capacity_valuation_binding_adapter(),
    )

    per = chain_stage_adapters(
        conditional_warranted_per_adapter(
            live_hierarchical_warranted_per_adapter(loader=providers.per_loader)
            if providers.per_loader is not None
            else None
        ),
        capacity_per_binding_adapter(),
    )
""",
)
replace_once(
    "src/valuation_engine/live_runtime.py",
    """    bridge = recovery_aware_bridge_adapter(
        evidence_to_assumption_bridge_adapter(analyst=providers.bridge_analyst)
    )
""",
    """    bridge = chain_stage_adapters(
        capacity_commitment,
        recovery_aware_bridge_adapter(
            evidence_to_assumption_bridge_adapter(analyst=providers.bridge_analyst)
        ),
    )
""",
)
replace_once(
    "src/valuation_engine/live_runtime.py",
    """        "DCF_PER_ASSUMPTION_CONSISTENCY_GATE": (
            dcf_per_consistency_gate_adapter()
        ),
""",
    """        "DCF_PER_ASSUMPTION_CONSISTENCY_GATE": chain_stage_adapters(
            dcf_per_consistency_gate_adapter(),
            capacity_consistency_gate_adapter(),
        ),
""",
)
replace_once(
    "src/valuation_engine/live_runtime.py",
    """        "AUDIT_GATE": generic_audit_adapter(
            impact_config=config.impact_config,
            unit_contract_registry=effective_unit_contract_registry,
        ),
""",
    """        "AUDIT_GATE": chain_stage_adapters(
            capacity_audit_adapter(),
            generic_audit_adapter(
                impact_config=config.impact_config,
                unit_contract_registry=effective_unit_contract_registry,
            ),
        ),
""",
)

replace_once(
    "src/valuation_engine/generic_audit.py",
    """    beta_result: LiveBetaStageResult | None = None,
    wacc_result: LiveWACCStageResult | None = None,
) -> GenericAuditResult:
""",
    """    beta_result: LiveBetaStageResult | None = None,
    wacc_result: LiveWACCStageResult | None = None,
    external_guardrail_findings: tuple[AuditFinding, ...] = (),
    external_guardrail_hashes: tuple[str, ...] = (),
) -> GenericAuditResult:
""",
)
replace_once(
    "src/valuation_engine/generic_audit.py",
    """    report = AuditReport(tuple(findings))
    payload = "\\n".join(
        [
            run_id,
            ledger_snapshot_hash,
            compiled.assumption_set_hash,
            scenario_set.scenario_set_hash,
            valuation.valuation_hash,
        ]
        + [f"{item.check}|{item.passed}|{item.blocking}|{item.detail}" for item in report.findings]
    )
""",
    """    if not all(isinstance(item, AuditFinding) for item in external_guardrail_findings):
        raise TypeError("external_guardrail_findings must contain AuditFinding")
    if not all(isinstance(item, str) and item for item in external_guardrail_hashes):
        raise TypeError("external_guardrail_hashes must contain non-empty strings")
    findings.extend(external_guardrail_findings)

    report = AuditReport(tuple(findings))
    payload = "\\n".join(
        [
            run_id,
            ledger_snapshot_hash,
            compiled.assumption_set_hash,
            scenario_set.scenario_set_hash,
            valuation.valuation_hash,
            *external_guardrail_hashes,
        ]
        + [f"{item.check}|{item.passed}|{item.blocking}|{item.detail}" for item in report.findings]
    )
""",
)

replace_once(
    "src/valuation_engine/audit_adapter.py",
    "from .control_plane import DoctrineCoverageEntry, StageStatus\n",
    "from .control_plane import DoctrineCoverageEntry, ExecutionMode, StageStatus\n",
)
replace_once(
    "src/valuation_engine/audit_adapter.py",
    "from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult\n",
    """from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .records import AuditReport
""",
)
replace_once(
    "src/valuation_engine/audit_adapter.py",
    """        beta_result = beta_raw if isinstance(beta_raw, LiveBetaStageResult) else None
        wacc_result = wacc_raw if isinstance(wacc_raw, LiveWACCStageResult) else None

        try:
""",
    """        beta_result = beta_raw if isinstance(beta_raw, LiveBetaStageResult) else None
        wacc_result = wacc_raw if isinstance(wacc_raw, LiveWACCStageResult) else None
        capacity_report_raw = context.data.get("capacity_audit_report")
        capacity_hash_raw = context.data.get("capacity_audit_hash")
        if context.execution_mode is ExecutionMode.LIVE_PRIMARY and (
            not isinstance(capacity_report_raw, AuditReport)
            or not isinstance(capacity_hash_raw, str)
            or not capacity_hash_raw
        ):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "LIVE_PRIMARY capacity audit artifact/hash is required before generic audit",
                blocking=True,
            )
        capacity_report = (
            capacity_report_raw
            if isinstance(capacity_report_raw, AuditReport)
            else None
        )
        capacity_hash = (
            capacity_hash_raw
            if isinstance(capacity_hash_raw, str) and capacity_hash_raw
            else None
        )

        try:
""",
)
replace_once(
    "src/valuation_engine/audit_adapter.py",
    """            beta_result=beta_result,
            wacc_result=wacc_result,
        )
""",
    """            beta_result=beta_result,
            wacc_result=wacc_result,
            external_guardrail_findings=(
                capacity_report.findings if capacity_report is not None else ()
            ),
            external_guardrail_hashes=(
                (capacity_hash,) if capacity_hash is not None else ()
            ),
        )
""",
)

replace_once(
    "tests/test_live_runtime_assembly.py",
    """            valuation_plan_inputs_loader=noop,
            funding_scanner=None,
""",
    """            valuation_plan_inputs_loader=noop,
            capacity_commitment_loader=None,
            capacity_bridge_consumption_loader=None,
            funding_scanner=None,
""",
)

print("runtime orchestration codegen complete")
