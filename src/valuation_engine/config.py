from __future__ import annotations

from pathlib import Path
import yaml

from .models import Scenario


def load_company_config(path: str | Path) -> tuple[int, float | None, list[Scenario], dict]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    shares, scenarios = _parse_intrinsic(raw)
    market_price = raw.get("market_comparison", {}).get("price")
    return shares, market_price, scenarios, raw


def load_intrinsic_company_config(path: str | Path) -> tuple[int, list[Scenario], dict]:
    """Load only intrinsic inputs. Market data is intentionally not returned."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    shares, scenarios = _parse_intrinsic(raw)
    intrinsic_raw = {key: value for key, value in raw.items() if key != "market_comparison"}
    return shares, scenarios, intrinsic_raw


def load_market_comparison(path: str | Path) -> dict:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    market = raw.get("market_comparison")
    if not market or market.get("price") is None or market.get("as_of") is None:
        raise ValueError("market comparison price and as_of are required")
    return dict(market)


def _parse_intrinsic(raw: dict) -> tuple[int, list[Scenario]]:
    shares = int(raw["company"]["shares"])
    common = raw["common"]
    scenarios = []
    for item in raw["scenarios"]:
        scenarios.append(Scenario(
            name=item["name"], probability=float(item["probability"]),
            poly_asp_usd_per_kg=float(item["poly_asp_usd_per_kg"]),
            poly_cash_cost_usd_per_kg=float(common["poly_cash_cost_usd_per_kg"]),
            poly_other_cost_usd_per_kg=float(common["poly_other_cost_usd_per_kg"]),
            poly_capacity_kmt=float(common["poly_capacity_kmt"]),
            poly_utilization=float(item["poly_utilization"]), poly_multiple=float(item["poly_multiple"]),
            wafer_capacity_gw=float(common["wafer_capacity_gw"]), wafer_utilization=float(item["wafer_utilization"]),
            wafer_ebitda_usd_per_w=float(item["wafer_ebitda_usd_per_w"]), wafer_multiple=float(item["wafer_multiple"]),
            wafer_economic_share=float(common["wafer_economic_share"]), fx_krw_per_usd=float(common["fx_krw_per_usd"]),
            discount_rate=float(item["discount_rate"]), terminal_years=float(common["terminal_years"]),
            net_debt_trn_krw=float(item["net_debt_trn_krw"]), other_business_pv_trn_krw=float(item["other_business_pv_trn_krw"]),
        ))
    return shares, scenarios
