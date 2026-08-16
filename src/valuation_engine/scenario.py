from __future__ import annotations

from dataclasses import dataclass

from .models import Scenario
from .records import CalibrationStatus


@dataclass(frozen=True)
class ScenarioSet:
    scenarios: tuple[Scenario, ...]
    calibration_status: CalibrationStatus = CalibrationStatus.UNCALIBRATED

    def validate(self) -> None:
        names = [item.name for item in self.scenarios]
        if len(names) != len(set(names)):
            raise ValueError("scenario names must be unique")
        if not {"Bear", "Base", "Bull"}.issubset(names):
            raise ValueError("Bear, Base and Bull scenarios are required")
        if abs(sum(item.probability for item in self.scenarios) - 1.0) > 1e-9:
            raise ValueError("scenario probabilities must sum to one")
        for item in self.scenarios:
            if not 0 <= item.probability <= 1:
                raise ValueError("scenario probability must be between zero and one")
        by_name = {item.name: item for item in self.scenarios}
        bear, base, bull = by_name["Bear"], by_name["Base"], by_name["Bull"]
        favorable = (
            bear.poly_asp_usd_per_kg <= base.poly_asp_usd_per_kg <= bull.poly_asp_usd_per_kg
            and bear.poly_utilization <= base.poly_utilization <= bull.poly_utilization
            and bear.poly_multiple <= base.poly_multiple <= bull.poly_multiple
            and bear.wafer_utilization <= base.wafer_utilization <= bull.wafer_utilization
            and bear.wafer_multiple <= base.wafer_multiple <= bull.wafer_multiple
            and bear.other_business_pv_trn_krw <= base.other_business_pv_trn_krw <= bull.other_business_pv_trn_krw
        )
        inverse = (
            bear.discount_rate >= base.discount_rate >= bull.discount_rate
            and bear.net_debt_trn_krw >= base.net_debt_trn_krw >= bull.net_debt_trn_krw
        )
        if not favorable or not inverse:
            raise ValueError("OCI scenario worldviews are not economically coherent")
