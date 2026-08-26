from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, content: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    content = read(path)
    count = content.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement, found {count}: {old[:80]!r}")
    write(path, content.replace(old, new, 1))


def append_inline_yaml_list(path: str, section: str, key: str, values: tuple[str, ...]) -> None:
    lines = read(path).splitlines()
    section_line = f"  {section}:"
    try:
        start = lines.index(section_line)
    except ValueError as exc:
        raise RuntimeError(f"{path}: missing section {section}") from exc
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("  ") and not line.startswith("    ") and line.endswith(":"):
            end = index
            break
    target_index = None
    for index in range(start + 1, end):
        if lines[index].startswith(f"    {key}:"):
            target_index = index
            break
    if target_index is None:
        raise RuntimeError(f"{path}: missing {section}.{key}")
    line = lines[target_index]
    match = re.fullmatch(rf"(    {re.escape(key)}:\s*\[)(.*)(\])", line)
    if not match:
        raise RuntimeError(f"{path}: {section}.{key} must be an inline YAML list")
    existing = [item.strip() for item in match.group(2).split(",") if item.strip()]
    for value in values:
        if value not in existing:
            existing.append(value)
    lines[target_index] = match.group(1) + ", ".join(existing) + match.group(3)
    write(path, "\n".join(lines) + "\n")


def patch_live_runtime() -> None:
    replace_once(
        "src/valuation_engine/live_runtime.py",
        "from .collection_plan import (\n",
        "from .capacity_commitment import (\n"
        "    CapacityCommitmentLoader,\n"
        "    capacity_commitment_gate_adapter,\n"
        ")\n"
        "from .capacity_consumption import (\n"
        "    CapacityBridgeConsumptionLoader,\n"
        "    capacity_bridge_consumption_gate_adapter,\n"
        ")\n"
        "from .collection_plan import (\n",
    )
    replace_once(
        "src/valuation_engine/live_runtime.py",
        "    valuation_plan_inputs_loader: ValuationPlanInputsLoader\n"
        "    funding_scanner: FundingScanner | None = None\n",
        "    valuation_plan_inputs_loader: ValuationPlanInputsLoader\n"
        "    capacity_commitment_loader: CapacityCommitmentLoader | None = None\n"
        "    capacity_bridge_consumption_loader: (\n"
        "        CapacityBridgeConsumptionLoader | None\n"
        "    ) = None\n"
        "    funding_scanner: FundingScanner | None = None\n",
    )
    replace_once(
        "src/valuation_engine/live_runtime.py",
        "        if not isinstance(self.scanner_runners, Mapping):\n"
        "            raise TypeError(\"scanner_runners must be a mapping\")\n",
        "        if not isinstance(self.scanner_runners, Mapping):\n"
        "            raise TypeError(\"scanner_runners must be a mapping\")\n"
        "        optional_callables = (\n"
        "            self.capacity_commitment_loader,\n"
        "            self.capacity_bridge_consumption_loader,\n"
        "            self.funding_scanner,\n"
        "            self.research_recovery_adapter,\n"
        "            self.beta_loader,\n"
        "            self.wacc_loader,\n"
        "            self.dcf_fingerprint_loader,\n"
        "            self.per_loader,\n"
        "            self.calibration_loader,\n"
        "            self.street_loader,\n"
        "            self.market_loader,\n"
        "        )\n"
        "        if any(item is not None and not callable(item) for item in optional_callables):\n"
        "            raise TypeError(\"LIVE_PRIMARY optional providers must be callable when supplied\")\n",
    )
    replace_once(
        "src/valuation_engine/live_runtime.py",
        "    scenario_chain: list[StageAdapter] = []\n",
        "    scenario_chain: list[StageAdapter] = [\n"
        "        capacity_bridge_consumption_gate_adapter(\n"
        "            loader=providers.capacity_bridge_consumption_loader\n"
        "        )\n"
        "    ]\n",
    )
    replace_once(
        "src/valuation_engine/live_runtime.py",
        "    bridge = recovery_aware_bridge_adapter(\n"
        "        evidence_to_assumption_bridge_adapter(analyst=providers.bridge_analyst)\n"
        "    )\n",
        "    bridge = chain_stage_adapters(\n"
        "        capacity_commitment_gate_adapter(\n"
        "            loader=providers.capacity_commitment_loader\n"
        "        ),\n"
        "        recovery_aware_bridge_adapter(\n"
        "            evidence_to_assumption_bridge_adapter(\n"
        "                analyst=providers.bridge_analyst\n"
        "            )\n"
        "        ),\n"
        "    )\n",
    )


def patch_llm_contract() -> None:
    replace_once(
        "src/valuation_engine/llm_adapters.py",
        "from .llm_staff import (\n",
        "from .llm_staff import (\n",
    )
    content = read("src/valuation_engine/llm_adapters.py")
    if "from .module_plan import ModuleRequirementPlan\n" not in content:
        content = content.replace(
            "from .llm_staff import (\n",
            "from .llm_staff import (\n",
            1,
        )
        marker = "from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult\n"
        if marker not in content:
            raise RuntimeError("llm_adapters.py: orchestrator import marker missing")
        content = content.replace(
            marker,
            "from .module_plan import ModuleRequirementPlan\n" + marker,
            1,
        )
        write("src/valuation_engine/llm_adapters.py", content)
    replace_once(
        "src/valuation_engine/llm_adapters.py",
        "    capacity = context.data.get(\"capacity_commitment_assessment\")\n"
        "    if capacity is not None and not isinstance(\n"
        "        capacity, CapacityCommitmentAssessment\n"
        "    ):\n"
        "        raise ValueError(\n"
        "            \"capacity_commitment_assessment must be typed when present\"\n"
        "        )\n",
        "    capacity = context.data.get(\"capacity_commitment_assessment\")\n"
        "    if capacity is not None and not isinstance(\n"
        "        capacity, CapacityCommitmentAssessment\n"
        "    ):\n"
        "        raise ValueError(\n"
        "            \"capacity_commitment_assessment must be typed when present\"\n"
        "        )\n"
        "    module_plan = context.data.get(\"module_requirement_plan\")\n"
        "    capacity_route = (\n"
        "        isinstance(module_plan, ModuleRequirementPlan)\n"
        "        and any(\n"
        "            \"capacity_manufacturing\" in segment.archetypes\n"
        "            for segment in module_plan.segments\n"
        "        )\n"
        "    )\n"
        "    if capacity_route and not isinstance(\n"
        "        capacity, CapacityCommitmentAssessment\n"
        "    ):\n"
        "        raise ValueError(\n"
        "            \"capacity_manufacturing LLM context requires a frozen \"\n"
        "            \"CapacityCommitmentAssessment\"\n"
        "        )\n",
    )


def patch_collection_contract() -> None:
    metrics = (
        "expansion_land_control",
        "expansion_baseline_inclusion",
        "expansion_capacity_committed",
        "expansion_site_area",
        "expansion_capex_committed",
        "expansion_ramp_date",
        "expansion_equipment_commitment",
        "expansion_cancelled",
        "no_active_capacity_expansion",
    )
    append_inline_yaml_list(
        "config/archetype_control_requirements.yaml",
        "capacity_manufacturing",
        "required_kpis",
        metrics,
    )
    append_inline_yaml_list(
        "config/archetype_module_registry.yaml",
        "capacity_manufacturing",
        "required_evidence",
        metrics,
    )


def patch_existing_test_fixture() -> None:
    replace_once(
        "tests/test_live_runtime_assembly.py",
        "            valuation_plan_inputs_loader=noop,\n"
        "            funding_scanner=None,\n",
        "            valuation_plan_inputs_loader=noop,\n"
        "            capacity_commitment_loader=None,\n"
        "            capacity_bridge_consumption_loader=None,\n"
        "            funding_scanner=None,\n",
    )


def create_runtime_integration_tests() -> None:
    write(
        "tests/test_capacity_live_runtime_integration.py",
        '''from pathlib import Path\nfrom types import SimpleNamespace\n\nfrom valuation_engine.capacity_commitment import (\n    BaselineInclusionStatus,\n    CapacityCommitmentAssessment,\n    CapacityProjectAssessment,\n    CapacityProjectDisposition,\n    CapacityQuantificationStatus,\n    CapacitySegmentAssessment,\n)\nfrom valuation_engine.collection_plan import CollectorCapability\nfrom valuation_engine.control_plane import ExecutionMode, StageStatus\nfrom valuation_engine.evidence_collection import EvidenceCollectionBatch\nfrom valuation_engine.ledger import EvidenceLedger\nfrom valuation_engine.live_primary_adapters import CompanyResolutionRequest\nfrom valuation_engine.live_runtime import (\n    LiveCollectorProvider,\n    build_live_primary_adapters,\n)\nfrom valuation_engine.module_plan import (\n    ModuleRequirementPlan,\n    SegmentModuleRequirementPlan,\n)\nfrom valuation_engine.orchestrator import OrchestratorContext\nfrom valuation_engine.scenario_binding import ScenarioBindingSpec\nfrom valuation_engine.signal_intelligence import ProjectGate\n\n\nROOT = Path(__file__).resolve().parents[1]\n\n\nclass RuntimeConfig:\n    def __init__(self, tmp_path):\n        noop = lambda *args, **kwargs: None\n        collector = LiveCollectorProvider(\n            CollectorCapability(\n                collector_id="fixture",\n                source_id="KR_OPENDART",\n                supported_metrics=("financials",),\n                jurisdictions=("KR",),\n                implementation_ref="tests.fixture",\n            ),\n            lambda request: EvidenceCollectionBatch(\n                source_id="KR_OPENDART",\n                checked_at="2026-08-26",\n                records=(),\n                source_fingerprint="fixture",\n            ),\n        )\n        self.providers = SimpleNamespace(\n            company_resolver=noop,\n            industry_snapshot_loader=noop,\n            freshness_loader=noop,\n            segment_decomposer=noop,\n            industry_dna_router=noop,\n            collectors=(collector,),\n            scanner_runners={},\n            intelligence_officer=noop,\n            red_team_officer=noop,\n            bridge_analyst=noop,\n            evaluator_registry_loader=noop,\n            valuation_plan_inputs_loader=noop,\n            capacity_commitment_loader=None,\n            capacity_bridge_consumption_loader=None,\n            funding_scanner=None,\n            research_recovery_adapter=None,\n            beta_loader=None,\n            wacc_loader=None,\n            dcf_fingerprint_loader=None,\n            per_loader=None,\n            calibration_loader=None,\n            street_loader=None,\n            market_loader=None,\n        )\n        self.capability_registry = None\n        self.state_root = tmp_path / "state"\n        self.company_request = CompanyResolutionRequest("000000", "KR")\n        self.method_choices = ()\n        self.market_currency = None\n        self.archetype_registry_path = ROOT / "config" / "archetype_module_registry.yaml"\n        self.archetype_control_requirements_path = ROOT / "config" / "archetype_control_requirements.yaml"\n        self.industry_source_registry_path = ROOT / "config" / "industry_source_registry.yaml"\n        self.unit_contract_registry_path = ROOT / "config" / "unit_contract_registry.yaml"\n        self.impact_config = None\n        self.run_id = "CAPACITY-RUNTIME"\n        self.stage_registry_path = ROOT / "config" / "control_plane_stage_registry.yaml"\n        self.scenario_binding_spec = ScenarioBindingSpec(\n            scenario_ids=("core",),\n            required_keys=("revenue",),\n        )\n        self.initial_data = {}\n\n    def validate(self):\n        return None\n\n\ndef capacity_plan():\n    segment = SegmentModuleRequirementPlan(\n        segment_id="core",\n        sector_adapter="power.transformer_switchgear",\n        archetypes=("capacity_manufacturing",),\n        required_evidence=("expansion_land_control",),\n        required_kpis=("expansion_land_control",),\n        mandatory_scanners=("CAPACITY_RAMP",),\n        kill_conditions=("capacity ramp fails",),\n        normalization_rules=("capacity_definition",),\n        beta_peer_features=("capacity_intensity",),\n        per_peer_features=("growth_duration",),\n        scenario_variables=("revenue",),\n        funding_scans=(),\n        terminal_policies=("normalize capacity",),\n        double_count_traps=("capacity_without_capex",),\n        forbidden_methods=(),\n        allowed_valuation_methods=("driver_dcf",),\n    )\n    return ModuleRequirementPlan(\n        segments=(segment,),\n        common_core_modules=("evidence_gate",),\n        required_evidence=segment.required_evidence,\n        required_kpis=segment.required_kpis,\n        mandatory_scanners=segment.mandatory_scanners,\n        kill_conditions=segment.kill_conditions,\n        scenario_variables=segment.scenario_variables,\n        double_count_traps=segment.double_count_traps,\n        forbidden_methods=(),\n    )\n\n\ndef assessment():\n    project = CapacityProjectAssessment(\n        project_id="P1",\n        segment_id="core",\n        verified_gates=(ProjectGate.ANNOUNCEMENT, ProjectGate.LAND_CONTROL),\n        land_control_verified=True,\n        baseline_inclusion=BaselineInclusionStatus.NOT_IN_BASELINE,\n        disposition=CapacityProjectDisposition.ACTIVE,\n        core_inclusion_required=True,\n        quantification_status=CapacityQuantificationStatus.BOUNDED_INPUTS_AVAILABLE,\n        qualifying_evidence_ids=("E_LAND", "E_SITE", "E_CAPEX", "E_RAMP"),\n        recovery_required=False,\n        rationale="fixture",\n    )\n    return CapacityCommitmentAssessment(\n        segments=(\n            CapacitySegmentAssessment(\n                segment_id="core",\n                projects=(project,),\n                no_active_expansion_verified=False,\n                no_active_expansion_evidence_ids=(),\n                recovery_required=False,\n                rationale="fixture",\n            ),\n        ),\n        assessment_hash="ASSESSMENT-HASH",\n    )\n\n\ndef test_capacity_commitment_gate_is_executed_before_llm_bridge(tmp_path):\n    adapters = build_live_primary_adapters(RuntimeConfig(tmp_path))\n    result = adapters["EVIDENCE_TO_ASSUMPTION_BRIDGE"](\n        OrchestratorContext(\n            "RUN",\n            ExecutionMode.LIVE_PRIMARY,\n            {\n                "module_requirement_plan": capacity_plan(),\n                "evidence_ledger": EvidenceLedger(()),\n            },\n        )\n    )\n    assert result.status is StageStatus.NOT_IMPLEMENTED\n    assert result.blocking\n    assert "CapacityCommitmentLoader" in result.rationale\n\n\ndef test_capacity_consumption_gate_is_executed_before_scenario_build(tmp_path):\n    adapters = build_live_primary_adapters(RuntimeConfig(tmp_path))\n    result = adapters["SCENARIO_BUILD"](\n        OrchestratorContext(\n            "RUN",\n            ExecutionMode.LIVE_PRIMARY,\n            {"capacity_commitment_assessment": assessment()},\n        )\n    )\n    assert result.status is StageStatus.NOT_IMPLEMENTED\n    assert result.blocking\n    assert "bridge-consumption loader" in result.rationale\n''',
    )


def create_report_form() -> None:
    write(
        "src/valuation_engine/report_form.py",
        '''from __future__ import annotations\n\nfrom typing import Any\n\nfrom .orchestrator import ControlledRunResult\n\n\nREPORT_SECTIONS = (\n    "1. Investment Conclusion",\n    "2. Industry DNA & Bottleneck",\n    "3. Primary Evidence Ledger",\n    "4. Thesis / Variant Perception",\n    "5. Blind Red Team",\n    "6. Scenario Worldviews",\n    "7. Beta & WACC Audit",\n    "8. Deterministic Valuation",\n    "9. PER Consistency",\n    "10. Capacity Commitment Audit",\n    "11. Intrinsic Freeze",\n    "12. Post-Freeze Market / Street Gap",\n    "13. Catalysts / Kill Conditions",\n    "14. Analyst Synthesis",\n    "15. Data Quality & Limitations",\n)\n\n_HASH_KEYS = (\n    "ledger_snapshot_hash",\n    "capacity_commitment_assessment_hash",\n    "capacity_bridge_consumption_hash",\n    "beta_snapshot_hash",\n    "wacc_snapshot_hash",\n    "scenario_set_hash",\n    "valuation_hash",\n    "capacity_audit_hash",\n)\n\n\ndef _value(data: dict[str, Any], key: str) -> str:\n    value = data.get(key)\n    if value is None and key == "scenario_set_hash":\n        scenario_set = data.get("bound_scenario_set")\n        value = getattr(scenario_set, "scenario_set_hash", None)\n    return str(value) if value not in (None, "") else "—"\n\n\ndef render_report_form_template() -> str:\n    lines = [\n        "# PRISM Verified Equity Research Report",\n        "",\n        "> This file is generated from a ControlledRunResult. A fair value may be published only when the run is unblocked and carries an Intrinsic Freeze token.",\n        "",\n        "## Run Verification",\n        "",\n        "| Field | Value |",\n        "|---|---|",\n        "| Run ID | `{{ run_id }}` |",\n        "| Execution mode | `{{ execution_mode }}` |",\n        "| Run status | `{{ run_status }}` |",\n        "| Freeze token | `{{ freeze_token }}` |",\n        "| Evidence Ledger hash | `{{ ledger_snapshot_hash }}` |",\n        "| Capacity assessment hash | `{{ capacity_commitment_assessment_hash }}` |",\n        "| Capacity consumption hash | `{{ capacity_bridge_consumption_hash }}` |",\n        "| Beta snapshot hash | `{{ beta_snapshot_hash }}` |",\n        "| WACC snapshot hash | `{{ wacc_snapshot_hash }}` |",\n        "| Scenario-set hash | `{{ scenario_set_hash }}` |",\n        "| Valuation hash | `{{ valuation_hash }}` |",\n        "| Capacity audit hash | `{{ capacity_audit_hash }}` |",\n        "",\n        "## Stage Trace",\n        "",\n        "| Stage | Status | Rationale |",\n        "|---|---|---|",\n        "| `{{ stage }}` | `{{ status }}` | {{ rationale }} |",\n    ]\n    for section in REPORT_SECTIONS:\n        lines.extend(("", f"## {section}", "", "{{ content }}"))\n    return "\\n".join(lines) + "\\n"\n\n\ndef render_controlled_run_report(result: ControlledRunResult) -> str:\n    blocked = bool(result.blocked_reasons)\n    frozen = result.freeze_token is not None\n    status = "BLOCKED" if blocked else ("VERIFIED_FROZEN" if frozen else "INCOMPLETE")\n    freeze_value = getattr(result.freeze_token, "token_hash", None) or (\n        str(result.freeze_token) if result.freeze_token is not None else "—"\n    )\n    lines = [\n        "# PRISM Verified Equity Research Report",\n        "",\n        f"- Run ID: `{result.run_id}`",\n        f"- Execution mode: `{result.execution_mode.value}`",\n        f"- Run status: **{status}**",\n        f"- Freeze token: `{freeze_value}`",\n        "",\n        "## Run Verification",\n        "",\n        "| Contract | Hash / State |",\n        "|---|---|",\n    ]\n    for key in _HASH_KEYS:\n        lines.append(f"| `{key}` | `{_value(result.data, key)}` |")\n    lines.extend(("", "## Stage Trace", "", "| Stage | Status | Rationale |", "|---|---|---|"))\n    for trace in result.stage_traces:\n        stage = getattr(trace, "stage", getattr(trace, "stage_name", "UNKNOWN"))\n        stage_status = getattr(trace, "status", "UNKNOWN")\n        stage_status = getattr(stage_status, "value", stage_status)\n        rationale = str(getattr(trace, "rationale", "")).replace("|", "\\|").replace("\\n", " ")\n        lines.append(f"| `{stage}` | `{stage_status}` | {rationale} |")\n    if blocked:\n        lines.extend(("", "## Blocking Reasons", ""))\n        lines.extend(f"- {reason}" for reason in result.blocked_reasons)\n    final_report = result.data.get("final_report")\n    if isinstance(final_report, str) and final_report.strip():\n        lines.extend(("", "## Engine Final Report", "", final_report.strip()))\n    else:\n        for section in REPORT_SECTIONS:\n            lines.extend(("", f"## {section}", "", "—"))\n    return "\\n".join(lines) + "\\n"\n''',
    )
    write(
        "scripts/render_verified_report_form.py",
        '''from pathlib import Path\n\nfrom valuation_engine.report_form import render_report_form_template\n\n\nROOT = Path(__file__).resolve().parents[1]\nOUTPUT = ROOT / "examples" / "report_forms" / "PRISM_VERIFIED_REPORT_FORM.md"\n\n\ndef main() -> int:\n    expected = render_report_form_template()\n    OUTPUT.parent.mkdir(parents=True, exist_ok=True)\n    if OUTPUT.exists() and OUTPUT.read_text(encoding="utf-8") == expected:\n        print(f"report form synchronized: {OUTPUT}")\n        return 0\n    OUTPUT.write_text(expected, encoding="utf-8")\n    print(f"report form generated: {OUTPUT}")\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n''',
    )
    write(
        "examples/report_forms/PRISM_VERIFIED_REPORT_FORM.md",
        "",  # generated below after the package imports successfully
    )
    write(
        "tests/test_report_form.py",
        '''from pathlib import Path\n\nfrom valuation_engine.report_form import REPORT_SECTIONS, render_report_form_template\n\n\nROOT = Path(__file__).resolve().parents[1]\n\n\ndef test_committed_report_form_is_deterministically_generated():\n    path = ROOT / "examples" / "report_forms" / "PRISM_VERIFIED_REPORT_FORM.md"\n    assert path.read_text(encoding="utf-8") == render_report_form_template()\n\n\ndef test_report_form_contains_runtime_proof_and_standard_sections():\n    template = render_report_form_template()\n    assert "Capacity assessment hash" in template\n    assert "Beta snapshot hash" in template\n    assert "Freeze token" in template\n    for section in REPORT_SECTIONS:\n        assert section in template\n''',
    )


def update_skill_contract() -> None:
    for path in ("SKILL.md", ".agents/skills/valuation-analysis/SKILL.md"):
        content = read(path)
        marker = "14. up to three targeted `RESEARCH_LOOP` rounds\n15. `EVIDENCE_TO_ASSUMPTION_BRIDGE`\n16. `SCENARIO_BUILD`\n"
        replacement = (
            "14. up to three targeted `RESEARCH_LOOP` rounds\n"
            "15. `EVIDENCE_TO_ASSUMPTION_BRIDGE`: execute the typed Capacity Commitment Gate first for every `capacity_manufacturing` segment, then expose its frozen assessment to the LLM Bridge Analyst\n"
            "16. `SCENARIO_BUILD`: verify complete capacity/CAPEX/ramp Bridge consumption before compiling assumptions\n"
        )
        if marker not in content:
            raise RuntimeError(f"{path}: workflow marker missing")
        write(path, content.replace(marker, replacement, 1))


def main() -> int:
    patch_live_runtime()
    patch_llm_contract()
    patch_collection_contract()
    patch_existing_test_fixture()
    create_runtime_integration_tests()
    create_report_form()
    update_skill_contract()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
