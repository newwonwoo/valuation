from valuation_engine.strict_live_runtime import _scrub_blocked_live_data


def test_blocked_distributional_run_scrubs_intrinsic_and_entry_outputs():
    data = {
        "target_id": "carrier-x",
        "evidence_ledger": object(),
        "distributional_primary_result": object(),
        "intrinsic_valuation_envelope": object(),
        "distribution_hash": "DIST",
        "distribution_route_authorization_hash": "ROUTE",
        "governed_entry_price": 123,
        "ambiguity_expected_value_range": object(),
        "payoff_model_set_hash": "PAYOFF",
        "valuation_hash": "VALUE",
        "expected_value_per_share": 100,
    }

    scrubbed = _scrub_blocked_live_data(data)

    assert scrubbed["target_id"] == "carrier-x"
    assert "evidence_ledger" in scrubbed
    for key in (
        "distributional_primary_result",
        "intrinsic_valuation_envelope",
        "distribution_hash",
        "distribution_route_authorization_hash",
        "governed_entry_price",
        "ambiguity_expected_value_range",
        "payoff_model_set_hash",
        "valuation_hash",
        "expected_value_per_share",
    ):
        assert key not in scrubbed
