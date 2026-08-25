from decimal import Decimal

import pytest

from valuation_engine.canonical_company_kpis import (
    CANONICAL_COMPANY_KPI_REGISTRY,
    ExactDocumentMetricCandidate,
    compile_exact_document_observation,
    profile_for,
    validate_canonical_company_kpi_registry,
)


def test_registry_covers_all_four_canonical_real_companies_with_source_specific_breadth():
    validate_canonical_company_kpi_registry()
    assert tuple(item.company_id for item in CANONICAL_COMPANY_KPI_REGISTRY) == (
        "OCI_HOLDINGS",
        "ORACLE",
        "BLOOM_ENERGY",
        "GE_VERNOVA",
    )
    assert profile_for("ORACLE").sec_cik == "0001341439"
    assert profile_for("BLOOM_ENERGY").sec_cik == "0001664703"
    assert profile_for("GE_VERNOVA").sec_cik == "0001996810"
    assert profile_for("OCI_HOLDINGS").opendart_resolver_required
    assert all(profile.document_specs for profile in CANONICAL_COMPANY_KPI_REGISTRY)


def test_company_specific_kpi_registry_uses_exact_facts_and_no_fuzzy_aliases():
    oracle = profile_for("ORACLE")
    assert oracle.sec_fact_specs[0].taxonomy == "us-gaap"
    assert oracle.sec_fact_specs[0].concept == "Revenues"
    oci = profile_for("OCI_HOLDINGS")
    assert oci.dart_fact_specs[0].account_ids == (
        "ifrs-full_Revenue",
        "ifrs_Revenue",
    )
    assert all("similar" not in spec.locator.casefold() for profile in CANONICAL_COMPANY_KPI_REGISTRY for spec in profile.document_specs)


def test_exact_document_candidate_requires_registered_host_locator_and_unit():
    candidate = ExactDocumentMetricCandidate(
        company_id="ORACLE",
        metric="remaining_performance_obligations",
        source_ref="https://www.sec.gov/Archives/edgar/data/1341439/example.htm",
        locator="Remaining Performance Obligations from Contracts with Customers",
        value=Decimal("638000000000"),
        unit="USD",
        effective_date="2026-05-31",
    )
    observation = compile_exact_document_observation(candidate)
    assert observation.metric == "remaining_performance_obligations"
    assert observation.value == Decimal("638000000000")
    assert observation.critical

    with pytest.raises(ValueError, match="source host"):
        compile_exact_document_observation(
            ExactDocumentMetricCandidate(
                **{**candidate.__dict__, "source_ref": "https://example.com/oracle.htm"}
            )
        )
    with pytest.raises(ValueError, match="exact locator"):
        compile_exact_document_observation(
            ExactDocumentMetricCandidate(
                **{**candidate.__dict__, "locator": "Remaining performance obligation"}
            )
        )
    with pytest.raises(ValueError, match="unit mismatch"):
        compile_exact_document_observation(
            ExactDocumentMetricCandidate(
                **{**candidate.__dict__, "unit": "KRW"}
            )
        )


def test_oci_ir_capacity_specs_require_official_host_and_registered_labels():
    observation = compile_exact_document_observation(
        ExactDocumentMetricCandidate(
            company_id="OCI_HOLDINGS",
            metric="solar_grade_polysilicon_capacity",
            source_ref="https://web-static.oci-holdings.co.kr/ir/example.pdf",
            locator="Solar-grade polysilicon capacity expansion (2027)",
            value="56.6",
            unit="kMT",
            effective_date="2027-12-31",
        )
    )
    assert observation.segment == "renewable_energy"
    assert observation.unit == "kMT"


def test_bloom_and_ge_kpi_specs_are_company_scoped_not_generic_market_inputs():
    bloom_metrics = {item.metric for item in profile_for("BLOOM_ENERGY").document_specs}
    ge_metrics = {item.metric for item in profile_for("GE_VERNOVA").document_specs}
    assert {"product_revenue", "installation_revenue", "service_revenue"}.issubset(bloom_metrics)
    assert {
        "remaining_performance_obligations",
        "gas_power_equipment_backlog_and_slot_reservations",
        "orders",
    }.issubset(ge_metrics)
    forbidden = ("target_price", "market_price", "consensus")
    for profile in CANONICAL_COMPANY_KPI_REGISTRY:
        for spec in profile.document_specs:
            assert not any(token in spec.metric.casefold() for token in forbidden)


def test_document_candidate_must_be_numeric_and_company_metric_must_exist():
    with pytest.raises(ValueError, match="numeric"):
        compile_exact_document_observation(
            ExactDocumentMetricCandidate(
                company_id="BLOOM_ENERGY",
                metric="product_revenue",
                source_ref="https://www.sec.gov/Archives/edgar/data/1664703/be.htm",
                locator="Product Revenue",
                value="not-a-number",
                unit="USD",
                effective_date="2026-06-30",
            )
        )
    with pytest.raises(ValueError, match="exactly one document KPI spec"):
        compile_exact_document_observation(
            ExactDocumentMetricCandidate(
                company_id="GE_VERNOVA",
                metric="unknown_metric",
                source_ref="https://www.sec.gov/Archives/edgar/data/1996810/gev.htm",
                locator="Unknown",
                value=1,
                unit="USD",
                effective_date="2026-06-30",
            )
        )
