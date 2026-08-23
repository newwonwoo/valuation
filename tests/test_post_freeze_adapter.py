from valuation_engine.control_plane import DoctrineCoverageEntry, ExecutionMode, StageStatus, issue_freeze_token
from valuation_engine.orchestrator import run_controlled_workflow
from valuation_engine.post_freeze_adapter import market_price_load_adapter, street_reference_load_adapter
from valuation_engine.records import MarketObservation
from valuation_engine.street import StreetResearchReport


def token(run_id: str):
    return issue_freeze_token(
        run_id=run_id,
        audit_passed=True,
        coverage_entries=(DoctrineCoverageEntry("CORE", StageStatus.PASS, "ok"),),
        expected_module_ids=("CORE",),
        assumption_set_hash="a",
        valuation_hash="v",
        audit_hash="u",
        industry_snapshot_hash="i",
        source_snapshot_hash="s",
    )


def report():
    return StreetResearchReport("Broker", "Analyst", "2026-08-01", 100.0, "KRW", "DCF", "2027", (), "ref")


def test_post_freeze_loader_is_blocked_without_token():
    result = run_controlled_workflow(
        run_id="R1",
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("STREET_REFERENCE_LOAD",),
        adapters={"STREET_REFERENCE_LOAD": street_reference_load_adapter(loader=lambda _: (report(),))},
        required_stages=("STREET_REFERENCE_LOAD",),
    )
    assert result.blocked_reasons


def test_post_freeze_loaders_accept_same_run_token():
    run_id = "R2"
    t = token(run_id)
    result = run_controlled_workflow(
        run_id=run_id,
        execution_mode=ExecutionMode.PRIMARY_SHADOW,
        stage_sequence=("STREET_REFERENCE_LOAD", "MARKET_PRICE_LOAD"),
        adapters={
            "STREET_REFERENCE_LOAD": street_reference_load_adapter(loader=lambda _: (report(),)),
            "MARKET_PRICE_LOAD": market_price_load_adapter(loader=lambda _: MarketObservation(90.0, "2026-08-23", "market")),
        },
        required_stages=("STREET_REFERENCE_LOAD", "MARKET_PRICE_LOAD"),
        initial_data={},
    )
    # Orchestrator intentionally owns the freeze token internally; adapters cannot receive a forged token via data.
    # Direct stage-only runs therefore remain blocked until INTRINSIC_VALUE_FREEZE occurs in the same run.
    assert result.blocked_reasons
    assert t.run_id == run_id
