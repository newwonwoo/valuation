from pathlib import Path
from types import SimpleNamespace

import valuation_engine.live_runtime as live_runtime

from valuation_engine.collection_plan import CollectorCapability
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.doctrine_runtime import load_default_unit_contract_registry
from valuation_engine.live_primary_adapters import CompanyResolutionRequest
from valuation_engine.live_runtime import (
    LiveCollectorProvider,
    LivePrimaryRuntimeConfig,
    build_live_primary_adapters,
)
from valuation_engine.orchestrator import load_stage_sequence
from valuation_engine.orchestrator import (
    ControlledRunResult,
    StageExecutionResult,
)
from valuation_engine.scenario_binding import ScenarioBindingSpec


ROOT = Path(__file__).resolve().parents[1]


class FakeRuntimeConfig:
    def __init__(self, tmp_path):
        collector = LiveCollectorProvider(
            CollectorCapability(
                collector_id="fixture",
                source_id="KR_OPENDART",
                supported_metrics=("financials",),
                jurisdictions=("KR",),
                implementation_ref="tests.fixture",
            ),
            lambda request: None,
        )
        noop = lambda *args, **kwargs: None
        self.providers = SimpleNamespace(
            company_resolver=noop,
            industry_snapshot_loader=noop,
            freshness_loader=noop,
            segment_decomposer=noop,
            industry_dna_router=noop,
            collectors=(collector,),
            scanner_runners={},
            intelligence_officer=noop,
            red_team_officer=noop,
            bridge_analyst=noop,
            evaluator_registry_loader=noop,
            valuation_plan_inputs_loader=noop,
            funding_scanner=None,
            research_recovery_adapter=None,
            beta_loader=None,
            wacc_loader=None,
            dcf_fingerprint_loader=None,
            per_loader=None,
            calibration_loader=None,
            street_loader=None,
            market_loader=None,
        )
        self.capability_registry = None
        self.state_root = tmp_path / "state"
        self.company_request = CompanyResolutionRequest("000000", "KR")
        self.method_choices = ()
        self.market_currency = None
        self.archetype_registry_path = (
            ROOT / "config" / "archetype_module_registry.yaml"
        )
        self.archetype_control_requirements_path = (
            ROOT / "config" / "archetype_control_requirements.yaml"
        )
        self.industry_source_registry_path = (
            ROOT / "config" / "industry_source_registry.yaml"
        )
        self.unit_contract_registry_path = (
            ROOT / "config" / "unit_contract_registry.yaml"
        )
        self.impact_config = None
        self.run_id = "REGISTRY-IDENTITY"
        self.stage_registry_path = (
            ROOT / "config" / "control_plane_stage_registry.yaml"
        )
        self.scenario_binding_spec = ScenarioBindingSpec(
            scenario_ids=("base",),
            required_keys=("revenue",),
        )
        self.initial_data = {}

    def validate(self):
        return None


def test_live_runtime_assembler_covers_every_canonical_stage_except_builtin_freeze(
    tmp_path,
):
    config = FakeRuntimeConfig(tmp_path)
    adapters = build_live_primary_adapters(config)
    sequence = load_stage_sequence(
        ROOT / "config" / "control_plane_stage_registry.yaml"
    )
    assert set(adapters) == set(sequence) - {"INTRINSIC_VALUE_FREEZE"}
    assert len(sequence) == 33
    assert len(adapters) == 32


def test_live_runtime_default_registry_paths_are_repo_anchored():
    fields = LivePrimaryRuntimeConfig.__dataclass_fields__
    expected = {
        "stage_registry_path": ROOT / "config" / "control_plane_stage_registry.yaml",
        "archetype_registry_path": ROOT / "config" / "archetype_module_registry.yaml",
        "archetype_control_requirements_path": (
            ROOT / "config" / "archetype_control_requirements.yaml"
        ),
        "industry_source_registry_path": (
            ROOT / "config" / "industry_source_registry.yaml"
        ),
        "unit_contract_registry_path": ROOT / "config" / "unit_contract_registry.yaml",
    }
    for name, path in expected.items():
        default = Path(fields[name].default)
        assert default.is_absolute()
        assert default == path


def test_run_prism_uses_one_configured_unit_registry_for_audit_and_freeze(
    tmp_path,
    monkeypatch,
):
    registry = load_default_unit_contract_registry()
    captured = {}

    def audit_adapter(*, impact_config, unit_contract_registry):
        captured["audit_registry"] = unit_contract_registry
        return lambda _: StageExecutionResult(
            StageStatus.PASS,
            "fixture audit",
        )

    def controlled_workflow(**kwargs):
        captured["workflow_registry"] = kwargs["unit_contract_registry"]
        return ControlledRunResult(
            run_id=kwargs["run_id"],
            execution_mode=ExecutionMode.LIVE_PRIMARY,
            stage_traces=(),
            data={},
            blocked_reasons=(),
            freeze_token=None,
        )

    monkeypatch.setattr(
        live_runtime,
        "load_unit_contract_registry",
        lambda _: registry,
    )
    monkeypatch.setattr(live_runtime, "generic_audit_adapter", audit_adapter)
    monkeypatch.setattr(
        live_runtime,
        "run_controlled_workflow",
        controlled_workflow,
    )

    live_runtime.run_prism(FakeRuntimeConfig(tmp_path))

    assert captured["audit_registry"] is registry
    assert captured["workflow_registry"] is registry
