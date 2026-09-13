from dataclasses import asdict, replace
from decimal import Decimal
import json

import pytest

from valuation_engine.new_business_research import (
    BusinessPath, BusinessPeriod, BusinessResearchError, DriverEstimate,
    build_cashflow_proposal, business_path_from_dict,
)
from valuation_engine.valuation_sensitivity import enterprise_value

D = Decimal


def driver(value, unit, name):
    return DriverEstimate(D(value), unit, "expansion", (f"https://example.com/{name}",), (name,))


def period(year):
    units = {
        "backlog_additions": ("0", "USD_million"),
        "revenue_demand": ("1000", "USD_million"),
        "capacity": ("10", "MW"),
        "utilization": ("0.8", "ratio"),
        "price_per_capacity_year": ("10", "USD_million_per_MW_year"),
        "cash_cost_ratio": ("0.25", "ratio"),
        "fixed_cash_costs": ("5", "USD_million"),
        "depreciation": ("10", "USD_million"),
        "tax_rate": ("0.2", "ratio"),
        "capex": ("20", "USD_million"),
        "delta_nwc": ("2", "USD_million"),
    }
    return BusinessPeriod(year, **{name: driver(value, unit, f"{year}.{name}") for name, (value, unit) in units.items()})


def path():
    return BusinessPath(
        "expansion", "USD_million", "MW",
        driver("10000", "USD_million", "opening_backlog"),
        driver("0", "MW", "precommissioning_capacity"),
        driver("0", "years", "commissioning_delay"),
        "no_current_benefit", tuple(period(year) for year in range(2027, 2030)),
    )


def value(proposal):
    return enterprise_value(fcff_path=proposal.fcff_path, discount_rate=D("0.1"), terminal_growth=D("0.02"))


def test_capacity_delay_and_capex_flow_through_existing_dcf():
    base = build_cashflow_proposal(path())
    assert base.fcff_path == (D("24"), D("24"), D("24"))
    expanded = build_cashflow_proposal(replace(path(), periods=tuple(
        replace(p, capacity=replace(p.capacity, value=D("20"))) for p in path().periods
    )))
    assert expanded.periods[0].revenue == D("160")
    assert value(expanded) > value(base)
    delayed = build_cashflow_proposal(replace(path(), commissioning_delay_years=replace(path().commissioning_delay_years, value=D("1"))))
    assert delayed.periods[0].revenue == 0
    assert delayed.periods[0].capex == D("20")
    assert delayed.periods[0].fcff == D("-27")
    assert delayed.periods[1].revenue == base.periods[0].revenue
    assert value(delayed) < value(base)
    first, *rest = path().periods
    more_capex = build_cashflow_proposal(replace(path(), periods=(replace(first, capex=replace(first.capex, value=D("31"))), *rest)))
    assert more_capex.fcff_path[0] == base.fcff_path[0] - D("11")
    assert abs(value(base) - value(more_capex) - D("10")) < D("1e-20")


def test_backlog_is_consumed_once_and_new_bookings_are_available():
    p = path()
    last = replace(p.periods[-1], backlog_additions=replace(p.periods[-1].backlog_additions, value=D("30")))
    proposal = build_cashflow_proposal(replace(p, opening_backlog=replace(p.opening_backlog, value=D("100")), periods=(*p.periods[:-1], last)))
    assert tuple(row.revenue for row in proposal.periods) == (D("80"), D("20"), D("30"))
    assert proposal.periods[-1].closing_backlog == 0
    assert sum(row.revenue for row in proposal.periods) == D("130")
    # Prior demand/capacity affects closing backlog and must remain in lineage.
    assert "2027.capacity" in proposal.periods[-1].assumption_refs


def test_depreciation_tax_shield_and_explicit_loss_treatment():
    p = path()
    base = build_cashflow_proposal(p)
    no_da = build_cashflow_proposal(replace(p, periods=tuple(replace(row, depreciation=replace(row.depreciation, value=D("0"))) for row in p.periods)))
    assert base.fcff_path[0] - no_da.fcff_path[0] == D("2")
    delayed = replace(p, commissioning_delay_years=replace(p.commissioning_delay_years, value=D("1")))
    no_relief = build_cashflow_proposal(delayed)
    relief = build_cashflow_proposal(replace(delayed, loss_tax_treatment="immediate_relief"))
    assert no_relief.periods[0].cash_taxes == 0
    assert relief.periods[0].cash_taxes == D("-3")
    assert relief.fcff_path[0] - no_relief.fcff_path[0] == D("3")


@pytest.mark.parametrize("change", [
    {"unit": "KRW_million"}, {"source_refs": ()}, {"assumption_refs": ()},
    {"economic_path_id": "other_path"}, {"value": D("NaN")}, {"value": D("-1")},
])
def test_driver_dimensions_and_provenance_are_not_silently_repaired(change):
    p = path()
    with pytest.raises(BusinessResearchError):
        build_cashflow_proposal(replace(p, opening_backlog=replace(p.opening_backlog, **change)))


def test_price_utilization_demand_and_existing_capacity_are_operating_drivers():
    p = path()
    base = build_cashflow_proposal(p)
    first, *rest = p.periods
    for field in ("utilization", "price_per_capacity_year"):
        estimate = getattr(first, field)
        lower = build_cashflow_proposal(replace(p, periods=(replace(first, **{field: replace(estimate, value=estimate.value / 2)}), *rest)))
        assert lower.periods[0].revenue == base.periods[0].revenue / 2
        assert value(lower) < value(base)
    limited = build_cashflow_proposal(replace(p, periods=(replace(first, revenue_demand=replace(first.revenue_demand, value=D("15"))), *rest)))
    assert limited.periods[0].revenue == D("15")
    delayed = build_cashflow_proposal(replace(p,
        precommissioning_capacity=replace(p.precommissioning_capacity, value=D("2")),
        commissioning_delay_years=replace(p.commissioning_delay_years, value=D("1")),
    ))
    assert delayed.periods[0].revenue == D("16")


def test_json_ingress_and_output_are_reviewable_proposals():
    payload = json.loads(json.dumps(asdict(path()), default=str))
    proposal = build_cashflow_proposal(business_path_from_dict(payload))
    assert proposal == build_cashflow_proposal(path())
    output = proposal.to_dict()
    assert output["status"] == "RESEARCH_ESTIMATE"
    assert [D(value) for value in output["fcff_path"]] == [D("24")] * 3
    assert "fcff_year_1" in output["underwriting_rows"]
    assert proposal.underwriting_rows("cashflow_")["cashflow_1"]["authority"] == "analyst_declared"
    assert "equity_value" not in json.dumps(output)
    payload["opening_backlog"]["value"] = 1.5
    with pytest.raises(BusinessResearchError):
        business_path_from_dict(payload)


def test_missing_inputs_fractional_delay_and_year_gaps_are_rejected():
    payload = json.loads(json.dumps(asdict(path()), default=str))
    del payload["periods"][0]["capex"]
    with pytest.raises(BusinessResearchError):
        business_path_from_dict(payload)
    with pytest.raises(BusinessResearchError):
        build_cashflow_proposal(replace(path(), commissioning_delay_years=replace(path().commissioning_delay_years, value=D("0.5"))))
    with pytest.raises(BusinessResearchError):
        build_cashflow_proposal(replace(path(), periods=(period(2027), period(2029))))


def test_cashflow_receipt_reproduces_and_binds_snapshot():
    from valuation_engine.new_business_research import business_path_snapshot_hash, validate_cashflow_receipt

    payload = json.loads(json.dumps(asdict(path()), default=str))
    proposal = build_cashflow_proposal(path())
    row = proposal.underwriting_rows()["fcff_year_1"]
    receipt = {
        "schema_version": "business-cashflow-receipt/v1",
        "plan_hash": "a" * 64,
        "path_hash": business_path_snapshot_hash(payload),
        "path": payload,
        "metric_prefix": "fcff_year_",
        "source_refs": row["source_refs"],
        "assumptions": {},
    }
    validate_cashflow_receipt(receipt, "fcff_year_1", row["value"], row["unit"])
    for invalid in (
        {**receipt, "schema_version": "unknown"},
        {**receipt, "plan_hash": "unbound"},
        {**receipt, "path_hash": "b" * 64},
        {**receipt, "source_refs": []},
        {**receipt, "metric_prefix": "different_"},
    ):
        with pytest.raises(BusinessResearchError):
            validate_cashflow_receipt(invalid, "fcff_year_1", row["value"], row["unit"])
    for metric, value_arg, unit in (
        ("fcff_year_1", "999", row["unit"]),
        ("fcff_year_1", row["value"], "KRW_million"),
        ("fcff_year_9", row["value"], row["unit"]),
        ("fcff_year_1", "NaN", row["unit"]),
    ):
        with pytest.raises(BusinessResearchError):
            validate_cashflow_receipt(receipt, metric, value_arg, unit)
    payload["periods"][0]["capex"]["value"] = "21"
    with pytest.raises(BusinessResearchError, match="snapshot hash mismatch"):
        validate_cashflow_receipt(receipt, "fcff_year_1", row["value"], row["unit"])
    receipt["path_hash"] = business_path_snapshot_hash(payload)
    with pytest.raises(BusinessResearchError, match="does not reproduce"):
        validate_cashflow_receipt(receipt, "fcff_year_1", row["value"], row["unit"])
