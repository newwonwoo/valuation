from pathlib import Path
from types import SimpleNamespace

from valuation_engine.collection_plan import CollectorCapability
from valuation_engine.live_primary_adapters import CompanyResolutionRequest
from valuation_engine.live_runtime import (
    LiveCollectorProvider,
    LivePrimaryRuntimeConfig,
    build_live_primary_adapters,
)
from valuation_engine.orchestrator import load_stage_sequence


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
    assert len(sequence) == 32
    assert len(adapters) == 31


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
