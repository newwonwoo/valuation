from datetime import datetime, timezone

import pytest

from valuation_engine.signal_intelligence import (
    MarketDataRole,
    NegativeEvidenceContext,
    ProjectState,
    SignalTimestamp,
    market_role_allowed,
    validate_project_transition,
)


def test_target_equity_reference_is_post_freeze_only():
    assert not market_role_allowed(MarketDataRole.TARGET_EQUITY_MARKET_REFERENCE, "wacc_validation")
    assert market_role_allowed(MarketDataRole.TARGET_EQUITY_MARKET_REFERENCE, "market_compare")


def test_financing_market_reference_can_support_wacc_without_equity_anchor():
    assert market_role_allowed(MarketDataRole.FINANCING_MARKET_REFERENCE, "wacc_validation")
    assert not market_role_allowed(MarketDataRole.FINANCING_MARKET_REFERENCE, "industry_dna_route")


def test_positioning_does_not_modify_intrinsic_pre_freeze():
    assert not market_role_allowed(MarketDataRole.POSITIONING_MARKET_SIGNAL, "evidence_to_assumption_bridge")
    assert market_role_allowed(MarketDataRole.POSITIONING_MARKET_SIGNAL, "monitoring")


def test_negative_evidence_is_fail_closed():
    incomplete = NegativeEvidenceContext(True, True, False, True, True)
    assert not incomplete.permits_no_event_inference()
    complete = NegativeEvidenceContext(True, True, True, True, True)
    assert complete.permits_no_event_inference()


def test_signal_timestamp_prevents_lookahead():
    pub = datetime(2026, 1, 2, tzinfo=timezone.utc)
    seen = datetime(2026, 1, 3, tzinfo=timezone.utc)
    SignalTimestamp(None, None, pub, seen, expected_reporting_lag_days=1).validate()
    with pytest.raises(ValueError):
        SignalTimestamp(None, None, seen, pub).validate()


def test_project_state_backward_transition_is_blocked():
    validate_project_transition(ProjectState.APPLIED, ProjectState.PERMITTED)
    with pytest.raises(ValueError):
        validate_project_transition(ProjectState.UNDER_CONSTRUCTION, ProjectState.PERMITTED)
