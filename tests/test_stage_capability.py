"""A readiness claim must be backed by a symbol that actually imports.

The engine already refuses a number with no receipt. These tests give the same
treatment to the engine's claims about itself: every stage status is derived from
probes, a hand-written status may not exceed what the probes prove, and a
per-company module never counts as a capability.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from valuation_engine.live_readiness import (
    LiveReadinessStatus,
    load_live_primary_readiness,
)
from valuation_engine.orchestrator import load_stage_sequence
from valuation_engine.stage_capability import (
    REQUIRED_PROVIDER_SLOTS,
    AxisOutcome,
    ColdStartOutcome,
    DerivedCapability,
    StageCapabilityDeclaration,
    StageCapabilityError,
    build_stage_capability_report,
    load_stage_capability_declarations,
    probe_cold_start,
    resolve_symbol,
)


ROOT = Path(__file__).resolve().parents[1]
DECLARATIONS = ROOT / "config" / "stage_capability_declarations.yaml"
READINESS = ROOT / "config" / "live_primary_readiness.yaml"
STAGE_REGISTRY = ROOT / "config" / "control_plane_stage_registry.yaml"


@pytest.fixture(scope="module")
def report():
    declarations, company_bound = load_stage_capability_declarations(DECLARATIONS)
    canonical = load_stage_sequence(STAGE_REGISTRY)
    base = build_stage_capability_report(
        declarations=declarations,
        company_bound_modules=company_bound,
        canonical_stages=canonical,
    )
    return build_stage_capability_report(
        declarations=declarations,
        company_bound_modules=company_bound,
        canonical_stages=canonical,
        cold_start=probe_cold_start(base.stages),
    )


# ------------------------------------------------------------------ the receipt


def test_every_declared_symbol_actually_imports(report):
    """This is the whole point: a declaration that names nothing fails here."""
    assert len(report.stages) == 33
    for stage in report.stages:
        assert stage.contract.present, f"{stage.stage} has no resolvable contract"


def test_declared_readiness_never_exceeds_the_probe(report):
    readiness = load_live_primary_readiness(
        readiness_path=READINESS, stage_registry_path=STAGE_REGISTRY
    )
    optimistic = {
        LiveReadinessStatus.LIVE_READY,
        LiveReadinessStatus.RUNTIME_READY,
        LiveReadinessStatus.PARTIAL_LIVE,
    }
    unbacked = {DerivedCapability.PROVIDER_REQUIRED, DerivedCapability.UNDECLARED}
    for row in readiness.stages:
        derived = report.by_stage(row.stage).derived
        if derived in unbacked:
            assert row.status not in optimistic, (
                f"{row.stage} claims {row.status.value} while the probe derives {derived.value}"
            )


def test_provider_required_stages_are_declared_as_such(report):
    readiness = load_live_primary_readiness(
        readiness_path=READINESS, stage_registry_path=STAGE_REGISTRY
    )
    declared = {item.stage: item.status for item in readiness.stages}
    for stage in report.stages:
        if stage.derived is DerivedCapability.PROVIDER_REQUIRED:
            assert declared[stage.stage] is LiveReadinessStatus.PROVIDER_REQUIRED


def test_provider_required_would_count_as_an_unresolved_gap():
    """No stage carries PROVIDER_REQUIRED today; the accounting must still treat it as a gap."""
    from valuation_engine.live_readiness import LivePrimaryReadinessReport, StageReadiness

    report = LivePrimaryReadinessReport(
        (StageReadiness("X", LiveReadinessStatus.PROVIDER_REQUIRED, "r"),)
    )
    assert {item.stage for item in report.unresolved_live_stages} == {"X"}


# --------------------------------------------------------------- symbol probing


def test_a_missing_symbol_is_an_error_not_a_pass():
    with pytest.raises(StageCapabilityError, match="does not exist"):
        resolve_symbol("valuation_engine.llm_staff:NoSuchOfficer")


def test_an_unimportable_module_is_an_error_not_a_pass():
    with pytest.raises(StageCapabilityError, match="unimportable module"):
        resolve_symbol("valuation_engine.no_such_module:thing")


def test_a_malformed_reference_is_rejected():
    with pytest.raises(StageCapabilityError, match="module:symbol"):
        resolve_symbol("valuation_engine.llm_staff.IntelligenceOfficer")


def test_a_company_bound_module_never_satisfies_an_axis():
    """Hand-written per-company code is the thing this probe makes visible."""
    resolution = resolve_symbol(
        "valuation_engine.sanil_live_primary:build_sanil_live_primary_config",
        company_bound_modules=frozenset({"valuation_engine.sanil_live_primary"}),
    )
    assert not resolution.present
    assert "company-bound" in resolution.detail


def test_the_same_symbol_resolves_when_it_is_not_company_bound():
    assert resolve_symbol(
        "valuation_engine.sanil_live_primary:build_sanil_live_primary_config"
    ).present


def test_an_undeclared_symbol_is_absent_rather_than_an_error():
    resolution = resolve_symbol(None)
    assert resolution.outcome is AxisOutcome.ABSENT


# ------------------------------------------------------------- derived ladder


def _declaration(**overrides) -> StageCapabilityDeclaration:
    defaults = dict(
        stage="RESEARCHER_A",
        provider_slot="intelligence_officer",
        contract="valuation_engine.live_runtime:IntelligenceOfficer",
        generic_implementation=None,
        note="probe fixture",
    )
    defaults.update(overrides)
    return StageCapabilityDeclaration(**defaults)


def _single(declaration, cold=ColdStartOutcome()):
    return build_stage_capability_report(
        declarations=(declaration,),
        company_bound_modules=frozenset(),
        canonical_stages=(declaration.stage,),
        cold_start=cold,
    ).stages[0]


def test_contract_without_implementation_is_provider_required():
    assert _single(_declaration()).derived is DerivedCapability.PROVIDER_REQUIRED


def test_no_contract_at_all_is_undeclared():
    stage = _single(_declaration(contract=None, generic_implementation=None))
    assert stage.derived is DerivedCapability.UNDECLARED


def test_contract_plus_implementation_is_implemented_not_cold_proven():
    stage = _single(
        _declaration(
            generic_implementation="valuation_engine.audit_adapter:generic_audit_adapter"
        )
    )
    assert stage.derived is DerivedCapability.IMPLEMENTED
    assert stage.ready
    assert stage.cold_execution is AxisOutcome.NOT_PROBED


def test_only_an_executed_cold_run_yields_cold_proven():
    declaration = _declaration(
        generic_implementation="valuation_engine.audit_adapter:generic_audit_adapter"
    )
    proven = _single(
        declaration,
        cold=ColdStartOutcome(probed=True, reached=("RESEARCHER_A",)),
    )
    assert proven.derived is DerivedCapability.COLD_PROVEN


def test_an_implementation_without_a_contract_is_rejected():
    with pytest.raises(StageCapabilityError, match="implementation without a contract"):
        _declaration(
            contract=None,
            generic_implementation="valuation_engine.audit_adapter:generic_audit_adapter",
        ).validate()


def test_a_declaration_needs_a_note():
    with pytest.raises(StageCapabilityError, match="requires a note"):
        _declaration(note="  ").validate()


# ------------------------------------------------------------------ cold start


def test_the_assembly_probe_finds_no_missing_required_slot(report):
    """Every REQUIRED_PROVIDER_SLOTS seat now has a company-neutral implementation."""
    cold = report.cold_start
    assert not cold.config_blocked_reason
    assert not cold.missing_provider_slots


def test_cold_execution_reflects_the_assembly_probe_only_here(report):
    """This fixture runs only the assembly probe, so nothing may claim PROVEN.

    The executed probe (cold_start_probe.execute_cold_start_probe) is what
    upgrades stages to COLD_PROVEN; scripts/validate_stage_capability.py runs it.
    """
    assert report.cold_proven_count == 0
    for stage in report.stages:
        assert stage.cold_execution is AxisOutcome.NOT_PROBED


def test_the_executed_probe_upgrades_every_stage_to_cold_proven():
    from valuation_engine.cold_start_probe import execute_cold_start_probe

    declarations, company_bound = load_stage_capability_declarations(DECLARATIONS)
    canonical = load_stage_sequence(STAGE_REGISTRY)
    executed = execute_cold_start_probe()
    report = build_stage_capability_report(
        declarations=declarations,
        company_bound_modules=company_bound,
        canonical_stages=canonical,
        cold_start=executed,
    )
    assert executed.blocking_stage is None
    assert report.cold_proven_count == len(executed.reached) == 33
    for stage in ("COMPANY_RESOLUTION", "RESEARCHER_A", "DETERMINISTIC_VALUATION",
                  "INTRINSIC_VALUE_FREEZE", "FINAL_REPORT"):
        assert report.by_stage(stage).derived is DerivedCapability.COLD_PROVEN


def test_cold_start_reports_not_probed_once_every_slot_is_filled():
    """When the gap closes this must demand a real run, not declare success."""
    filled = tuple(
        _single(
            _declaration(
                stage=slot,
                provider_slot=slot,
                generic_implementation="valuation_engine.audit_adapter:generic_audit_adapter",
            )
        )
        for slot in sorted(REQUIRED_PROVIDER_SLOTS)
    )
    outcome = probe_cold_start(filled)
    assert not outcome.probed
    assert not outcome.missing_provider_slots
    assert outcome.outcome_for("anything")[0] is AxisOutcome.NOT_PROBED


# --------------------------------------------------------------- registry sync


def test_declarations_cover_exactly_the_canonical_stages():
    declarations, company_bound = load_stage_capability_declarations(DECLARATIONS)
    canonical = load_stage_sequence(STAGE_REGISTRY)
    assert {item.stage for item in declarations} == set(canonical)
    with pytest.raises(StageCapabilityError, match="capability/stage-registry mismatch"):
        build_stage_capability_report(
            declarations=declarations[:-1],
            company_bound_modules=company_bound,
            canonical_stages=canonical,
        )


def test_the_company_bound_list_names_the_modules_that_exist():
    payload = yaml.safe_load(DECLARATIONS.read_text(encoding="utf-8"))
    modules = payload["company_bound_modules"]
    assert modules
    for name in modules:
        path = ROOT / "src" / Path(*name.split(".")).with_suffix(".py")
        assert path.exists(), f"{name} is listed as company-bound but does not exist"
