from dataclasses import replace
from decimal import Decimal
import json

import pytest

from valuation_engine.broker_comparison import (
    BrokerClaim, BrokerClaimType, BrokerMetric, BrokerMetricBasis,
    BrokerReference, BrokerReferenceBundle, FrozenOperatingMetric,
    broker_content_hash, build_broker_comparison, canonical_broker_payload,
    load_broker_reference_bundle, parse_broker_reference_bundle,
    parse_frozen_operating_metrics,
)
from valuation_engine.post_freeze import _envelope
from valuation_engine.runtime_authority import llm_proposal_scope
from valuation_engine.sotp import ScenarioEquityAggregation
from valuation_engine.valuation_execution import GenericValuationResult, ScenarioPerShareValue


def basis(**changes):
    values = dict(metric="operating_profit", period_start="2027-01-01",
        period_end="2027-12-31", frequency="ANNUAL", scope="CONSOLIDATED",
        perimeter="GROUP_INCLUDING_SUBSIDIARIES", accounting_basis="IFRS_REPORTED",
        unit="KRW_billion", currency="KRW")
    return BrokerMetricBasis(**(values | changes))


def claim(*, claim_id="C1", value="3.3", low=None, high=None, metric_basis=None):
    metric = BrokerMetric(claim_id + ":metric", metric_basis or basis(),
                          value=value, low=low, high=high, source_locator="PDF p2 forecast table")
    return BrokerClaim(claim_id, BrokerClaimType.BROKER_FORECAST,
                       "2027 analyst forecast, not a realized fact", metric,
                       source_locator="PDF p2", underlying_source_families=("issuer2026H1",))


def reference(**changes):
    values = dict(reference_id="R1", reference_kind="NAMED_REPORT", provider_name="Public broker website",
        source_ref="https://example.com/report.pdf", content_sha256="a" * 64,
        authorization_basis="explicit_permission", observed_at="2026-10-26T05:00:00Z",
        original_published_at="2026-05-14", broker="Test Broker", rating="NR", claims=(claim(),),
        underlying_source_families=("issuer2026H1",))
    return BrokerReference(**(values | changes))


def bundle(*references):
    return BrokerReferenceBundle("broker-reference-bundle/v1", "KR:TEST", "2026-10-26T05:30:00Z",
        references or (reference(),), "A limited reviewed set; not exhaustive coverage.")


def own_metric(*, metric_basis=None, value="-14", low=None, high=None, **changes):
    values = dict(metric=BrokerMetric("OWN:Base:OP2027", metric_basis or basis(), value=value,
                                    low=low, high=high, source_locator="frozen operating model"),
        scenario_id="Base", artifact_ref="operating_cases.json", artifact_sha256="b" * 64,
        json_pointer="/cases/Base/0/operating_profit", freeze_token_hash="FREEZE", valuation_hash="VALUE")
    return FrozenOperatingMetric(**(values | changes))


def valuation():
    scenarios = tuple(ScenarioPerShareValue(name, Decimal(v) * 10, "KRW", Decimal(10),
        Decimal(v), name, (name,)) for name, v in (("Down", "-20"), ("Base", "100"), ("Bull", "150")))
    return GenericValuationResult(scenarios=scenarios,
        equity_aggregation=ScenarioEquityAggregation((), None, False),
        expected_value_per_share=None, reporting_unit="KRW", valuation_hash="VALUE")


def compare(refs=None, metrics=None, **kw):
    return build_broker_comparison(refs or bundle(), frozen_metrics=(own_metric(),) if metrics is None else metrics,
        valuation=valuation(), freeze_token_hash="FREEZE", intrinsic_as_of="2026-10-26", **kw)


def aggregate(**changes):
    values = dict(reference_id="AGG1", reference_kind="AGGREGATE_SNAPSHOT", provider_name="Public aggregate provider",
        source_ref="https://example.com/aggregate", content_sha256="c" * 64,
        authorization_basis="public_access", observed_at="2026-10-26T05:00:00Z",
        original_published_at=None, last_rating_at="2026-08-14", broker=None, rating="SELL",
        target_price="9000", target_currency="KRW", aggregate_report_count=1,
        aggregate_median_target_price="9000", aggregate_min_target_price="9000",
        aggregate_max_target_price="9000")
    return BrokerReference(**(values | changes))


def test_nr_forecast_is_compared_without_target_or_expected_value():
    result = compare()
    assert result.numeric_target_status == "NOT_APPLICABLE_NO_NUMERIC_TARGET"
    assert result.research_comparison_status == "COMPARED"
    row = result.metric_comparisons[0]
    assert row.status == "COMPARABLE"
    assert row.difference == Decimal("-17.3")
    assert row.difference_pct_of_broker is None  # loss/profit signs are not a return
    assert result.target_comparisons[0].envelope is None


def test_nr_status_does_not_imply_absence_of_numeric_target():
    ref = reference(target_price="90", target_currency="KRW", target_share_basis="CURRENT", valuation_method="DCF")
    result = compare(bundle(ref))
    assert ref.rating == "NR"
    assert result.numeric_target_status == "COMPARED"
    assert result.metric_comparisons[0].status == "COMPARABLE"
    assert result.target_comparisons[0].envelope.expected_gap is None


def test_aggregate_unknown_broker_original_date_are_preserved_and_existing_math_reused():
    ref = aggregate()
    frozen = valuation()
    result = compare(bundle(ref), metrics=())
    row = result.target_comparisons[0]
    assert result.references[0].broker is None
    assert result.references[0].original_published_at is None
    assert row.reference_date_basis == "OBSERVED_AT"
    assert row.reference_as_of == "2026-10-26T05:00:00Z"
    assert row.reference_as_of != ref.last_rating_at
    expected = _envelope(frozen, reference_name="AGGREGATE_SNAPSHOT: Public aggregate provider (AGG1)",
        reference_as_of=ref.observed_at, reference_value=Decimal(9000), currency="KRW")
    assert row.envelope == expected
    assert row.envelope.get("Down").intrinsic_value_per_share == -20
    assert row.envelope.get("Down").comparison_equity_value_per_share == 0
    assert frozen.scenarios[0].value_per_share == -20
    assert row.status == "COMPARED_WITH_LIMITATIONS"


def test_explicit_snapshot_date_is_not_original_publication_or_rating_date():
    ref = aggregate(snapshot_as_of="2026-10-23")
    row = compare(bundle(ref), metrics=()).target_comparisons[0]
    assert row.reference_as_of == "2026-10-23"
    assert row.reference_date_basis == "SNAPSHOT_AS_OF"
    assert ref.original_published_at is None


def test_range_forecast_has_no_midpoint_and_difference_is_interval():
    ref = reference(claims=(claim(value=None, low="35", high="50", metric_basis=basis(metric="scope_revenue")),))
    own = own_metric(metric_basis=basis(metric="scope_revenue"), value="17.8")
    row = compare(bundle(ref), metrics=(own,)).metric_comparisons[0]
    assert row.broker_metric.value is None
    assert row.difference is None
    assert (row.difference_low, row.difference_high) == (Decimal("-32.2"), Decimal("-17.2"))
    assert row.difference_pct_of_broker is None


@pytest.mark.parametrize("change", [
    {"accounting_basis": "LEASE_ADJUSTED_WITH_ECONOMIC_SBC"},
    {"frequency": "CUMULATIVE"}, {"scope": "STANDALONE"},
    {"perimeter": "GROUP_EXCLUDING_SUBSIDIARY"}, {"currency": "USD"}, {"unit": "KRW_million"},
])
def test_basis_mismatch_is_visible_without_fake_subtraction(change):
    row = compare(metrics=(own_metric(metric_basis=basis(**change)),)).metric_comparisons[0]
    assert row.status == "BASIS_DIFFERENT"
    assert row.own_metric is not None
    assert row.difference is row.difference_low is row.difference_high is None


def test_op_is_not_substituted_by_fcff_or_nearest_year():
    row = compare(metrics=(own_metric(metric_basis=basis(metric="fcff")),)).metric_comparisons[0]
    assert row.status == "OWN_METRIC_UNAVAILABLE"
    assert row.own_metric is None and row.difference is None
    row = compare(metrics=(own_metric(metric_basis=basis(period_start="2028-01-01", period_end="2028-12-31")),)).metric_comparisons[0]
    assert row.status == "PERIOD_DIFFERENT" and row.difference is None


def test_quarterly_breakeven_is_not_compared_to_annual_profit():
    q4 = basis(period_start="2026-10-01", period_end="2026-12-31", frequency="QUARTERLY")
    row = compare(bundle(reference(claims=(claim(metric_basis=q4),)))).metric_comparisons[0]
    assert row.status == "PERIOD_DIFFERENT" and row.difference is None


def test_unknown_basis_and_unknown_named_report_date_are_retained():
    ref = reference(original_published_at=None)
    row = compare(bundle(ref), metrics=(own_metric(metric_basis=basis(accounting_basis="UNKNOWN")),)).metric_comparisons[0]
    assert row.status == "BASIS_UNKNOWN"
    assert row.difference is None
    assert compare(bundle(ref)).references[0].original_published_at is None


def test_shared_underlying_family_is_not_counted_as_two_independent_sources():
    second = reference(reference_id="R2", broker="Second Broker", claims=(claim(claim_id="C2"),))
    result = compare(bundle(reference(), second))
    assert result.known_underlying_source_family_count == 1
    assert any("not independent" in warning for warning in result.warnings)


@pytest.mark.parametrize("field,value", [
    ("observed_at", "2026-10-26"), ("observed_at", "2026-10-26T05:00:00"),
    ("original_published_at", "2026-10-27"), ("last_rating_at", "2026-10-27"),
])
def test_invalid_or_future_source_dates_rejected(field, value):
    with pytest.raises(ValueError):
        reference(**{field: value})


def test_later_comparison_does_not_retroactively_change_intrinsic_cutoff():
    result = build_broker_comparison(bundle(), frozen_metrics=(own_metric(),), valuation=valuation(),
        freeze_token_hash="FREEZE", intrinsic_as_of="2026-01-01")
    assert any("not retroactive" in warning for warning in result.warnings)
    assert result.intrinsic_as_of == "2026-01-01"
    with pytest.raises(ValueError, match="precedes"):
        build_broker_comparison(bundle(), valuation=valuation(), freeze_token_hash="FREEZE", intrinsic_as_of="2027-01-01")


def test_comparison_observation_cutoff_is_enforced():
    with pytest.raises(ValueError, match="observed after"):
        replace(bundle(), comparison_as_of="2026-10-26T04:00:00Z")


@pytest.mark.parametrize("change", [{"freeze_token_hash": "STALE"}, {"valuation_hash": "STALE"}])
def test_own_metrics_must_bind_current_frozen_value(change):
    with pytest.raises(ValueError, match="different"):
        compare(metrics=(own_metric(**change),))


def test_expired_or_manually_withheld_target_does_not_become_current_consensus():
    ref = reference(target_price="140000", target_currency="KRW", target_horizon_end="2024-06-20")
    assert compare(bundle(ref)).target_comparisons[0].status == "EXPIRED_REFERENCE"
    withheld = replace(ref, target_comparison_eligible=False, target_withheld_reason="Share basis not verified")
    assert compare(bundle(withheld)).target_comparisons[0].status == "WITHHELD"


def test_target_currency_mismatch_is_noncomputed_reference_not_fake_fx():
    ref = reference(target_price="90", target_currency="USD")
    row = compare(bundle(ref)).target_comparisons[0]
    assert row.status == "CURRENCY_DIFFERENT" and row.envelope is None


def test_hash_covers_forecast_basis_argument_date_and_rights():
    original = bundle()
    variants = [
        replace(original, references=(replace(reference(), valuation_base_year="2027"),)),
        replace(original, references=(replace(reference(), claims=(replace(claim(), summary="Changed argument"),)),)),
        replace(original, references=(replace(reference(), claims=(claim(value="8"),)),)),
        replace(original, references=(replace(reference(), access_quality="public extracted facts"),)),
        replace(original, comparison_as_of="2026-10-26T06:00:00Z"),
    ]
    assert all(item.reference_hash != original.reference_hash for item in variants)
    assert len({compare(item).comparison_hash for item in [original, *variants]}) == len(variants) + 1


def test_json_roundtrip_preserves_types_and_unknowns(tmp_path):
    pack = bundle(reference(), aggregate())
    payload = canonical_broker_payload(pack)
    assert parse_broker_reference_bundle(payload) == pack
    own = (own_metric(),)
    assert parse_frozen_operating_metrics(canonical_broker_payload(own)) == own
    path = tmp_path / "broker.json"
    path.write_text(json.dumps(payload))
    assert load_broker_reference_bundle(path) == pack
    path.write_text('{"schema_version":"x","schema_version":"y"}')
    with pytest.raises(ValueError, match="duplicate"):
        load_broker_reference_bundle(path)


def test_unknown_fields_duplicates_nonfinite_and_invalid_aggregate_fail_closed():
    payload = canonical_broker_payload(bundle())
    payload["arbitrary_override"] = True
    with pytest.raises(ValueError, match="unknown fields"):
        parse_broker_reference_bundle(payload)
    with pytest.raises(ValueError, match="globally unique"):
        bundle(reference(), reference())
    with pytest.raises(ValueError, match="finite"):
        claim(value="NaN")
    with pytest.raises(ValueError, match="point OR"):
        claim(value="1", low="0", high="2")
    with pytest.raises(ValueError, match="outside"):
        aggregate(target_price="9001")
    with pytest.raises(ValueError, match="complete"):
        aggregate(aggregate_median_target_price=None)


def test_llm_scope_cannot_commit_comparison_result():
    with llm_proposal_scope(), pytest.raises(PermissionError, match="boundary"):
        compare()


def test_comparison_is_pure_and_full_result_hash_is_reproducible():
    pack, frozen = bundle(), valuation()
    old = broker_content_hash((pack, frozen))
    result = compare(pack)
    assert broker_content_hash((pack, frozen)) == old
    raw = canonical_broker_payload(result)
    calculated = raw.pop("comparison_hash")
    assert broker_content_hash(raw) == calculated


def test_metadata_only_nr_is_not_mislabelled_completed_forecast_comparison():
    result = compare(bundle(reference(claims=())), metrics=())
    assert result.research_comparison_status == "REFERENCE_METADATA_ONLY"
    assert result.numeric_target_status == "NOT_APPLICABLE_NO_NUMERIC_TARGET"


def test_existing_distributional_intrinsic_range_is_reused_without_new_method():
    from tests.test_canonical_distributional_runtime import _ambiguity_spec
    from valuation_engine.distributional_runtime import execute_distributional_apv
    from valuation_engine.distributional_reporting import _intrinsic_range

    frozen = execute_distributional_apv(_ambiguity_spec())
    old = broker_content_hash(frozen)
    kind, low, high = _intrinsic_range(frozen)
    result = build_broker_comparison(bundle(aggregate()), valuation=frozen,
        freeze_token_hash="FREEZE", intrinsic_as_of="2026-10-26")
    row = result.target_comparisons[0]
    assert row.intrinsic_reference_kind == kind
    assert (row.intrinsic_reference_low, row.intrinsic_reference_high) == (low, high)
    assert row.target_gap_to_low == Decimal(9000) - low
    assert row.target_gap_to_high == Decimal(9000) - high
    assert row.envelope is None
    assert result.valuation_hash == frozen.envelope.envelope_hash
    assert broker_content_hash(frozen) == old
