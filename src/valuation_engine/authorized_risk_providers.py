from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from statistics import fmean

from .official_market_data import BetaEstimate, CountryRisk, SeriesObservation
from .risk import BetaLevelName
from .risk_adapters import (
    LiveBetaLevelObservation,
    LiveBetaUniverse,
    LiveCapitalStructureObservation,
    LivePeerBetaObservation,
    LiveWACCInputs,
    RateObservation,
    TargetCapitalStructureMethod,
)


class AuthorizedRiskProviderError(ValueError):
    pass


@dataclass(frozen=True)
class PeerCapitalObservation:
    peer_id: str
    debt: float
    equity_market_value: float
    tax_rate: float
    as_of: str
    source_ref: str

    def validate(self) -> None:
        if not self.peer_id or not self.as_of or not self.source_ref:
            raise AuthorizedRiskProviderError(
                "peer capital observation requires identity, as_of and source_ref"
            )
        _parse_date(self.as_of, "peer capital as_of")
        if not isfinite(self.debt) or self.debt < 0:
            raise AuthorizedRiskProviderError("peer debt must be finite and non-negative")
        if not isfinite(self.equity_market_value) or self.equity_market_value <= 0:
            raise AuthorizedRiskProviderError(
                "peer equity market value must be finite and positive"
            )
        if not isfinite(self.tax_rate) or not 0 <= self.tax_rate < 1:
            raise AuthorizedRiskProviderError("peer tax_rate must be in [0, 1)")


@dataclass(frozen=True)
class AuthorizedPeerBetaSource:
    peer_id: str
    beta: BetaEstimate
    capital: PeerCapitalObservation
    beta_source_ref: str
    beta_standard_error: float | None = None

    def validate(self) -> None:
        if not self.peer_id or not self.beta_source_ref:
            raise AuthorizedRiskProviderError(
                "authorized peer Beta source requires peer_id and source_ref"
            )
        self.capital.validate()
        if self.capital.peer_id != self.peer_id:
            raise AuthorizedRiskProviderError(
                "Beta peer and capital observation peer IDs must match"
            )
        if not isfinite(self.beta.beta):
            raise AuthorizedRiskProviderError("peer Beta must be finite")
        if self.beta.observations < 2:
            raise AuthorizedRiskProviderError("peer Beta requires return observations")
        _parse_date(self.beta.start_date, "Beta start_date")
        _parse_date(self.beta.end_date, "Beta end_date")
        if self.beta_standard_error is not None and (
            not isfinite(self.beta_standard_error) or self.beta_standard_error <= 0
        ):
            raise AuthorizedRiskProviderError(
                "beta_standard_error must be finite and positive"
            )

    def to_live_observation(self) -> LivePeerBetaObservation:
        self.validate()
        months = max(
            1,
            round(
                (
                    _parse_date(self.beta.end_date, "Beta end_date")
                    - _parse_date(self.beta.start_date, "Beta start_date")
                ).days
                / 30.4375
            ),
        )
        return LivePeerBetaObservation(
            peer_id=self.peer_id,
            levered_beta=self.beta.beta,
            debt=self.capital.debt,
            equity=self.capital.equity_market_value,
            tax_rate=self.capital.tax_rate,
            benchmark_id=self.beta.benchmark,
            return_frequency="daily",
            estimation_window_months=months,
            as_of=self.beta.end_date,
            source_ref=self.beta_source_ref,
            beta_standard_error=self.beta_standard_error,
            estimation_method=self.beta.method,
        )


@dataclass(frozen=True)
class AuthorizedBetaLevelSource:
    level: BetaLevelName
    peers: tuple[AuthorizedPeerBetaSource, ...]
    selection_rationale: str
    selection_evidence_ids: tuple[str, ...]
    risk_driver_features: tuple[str, ...]

    def validate(self) -> None:
        if not self.peers or not self.selection_rationale or not self.selection_evidence_ids:
            raise AuthorizedRiskProviderError(
                f"{self.level.value} source requires peers, rationale and Evidence IDs"
            )
        for peer in self.peers:
            peer.validate()
        if self.level is BetaLevelName.L4_ECONOMIC_TWINS and not self.risk_driver_features:
            raise AuthorizedRiskProviderError(
                "L4 Economic Twins require explicit risk-driver features"
            )


@dataclass(frozen=True)
class MarginalDebtBenchmark:
    series: SeriesObservation
    credit_rating: str
    maturity: str
    rating_source_ref: str

    def validate(self) -> None:
        if not self.credit_rating or not self.maturity or not self.rating_source_ref:
            raise AuthorizedRiskProviderError(
                "marginal debt benchmark requires rating, maturity and rating source"
            )
        _series_rate(self.series)


@dataclass(frozen=True)
class AuthorizedKRRiskProviderPack:
    beta_levels: tuple[AuthorizedBetaLevelSource, ...]
    risk_free_rate: SeriesObservation
    country_risk: CountryRisk
    marginal_debt: MarginalDebtBenchmark
    cash_flow_currency: str = "KRW"

    def validate(self) -> None:
        expected = (
            BetaLevelName.L1_BROAD_SECTOR,
            BetaLevelName.L2_INDUSTRY,
            BetaLevelName.L3_RISK_DRIVER_SUBINDUSTRY,
            BetaLevelName.L4_ECONOMIC_TWINS,
        )
        if tuple(level.level for level in self.beta_levels) != expected:
            raise AuthorizedRiskProviderError(
                "authorized Beta provider levels must be exactly L1→L2→L3→L4"
            )
        for level in self.beta_levels:
            level.validate()
        if self.cash_flow_currency != "KRW":
            raise AuthorizedRiskProviderError(
                "KR authorized risk provider pack supports KRW cash flows only"
            )
        _series_rate(self.risk_free_rate)
        self.marginal_debt.validate()
        if not self.country_risk.source_ref or not self.country_risk.as_of:
            raise AuthorizedRiskProviderError("country risk source is incomplete")
        _parse_date(self.country_risk.as_of, "country risk as_of")
        if not 0 <= self.country_risk.corporate_tax_rate < 1:
            raise AuthorizedRiskProviderError(
                "country corporate tax rate must be in [0, 1)"
            )

    def target_capital_structure(self) -> LiveCapitalStructureObservation:
        self.validate()
        unique: dict[str, PeerCapitalObservation] = {}
        for level in self.beta_levels:
            for peer in level.peers:
                prior = unique.get(peer.peer_id)
                if prior is not None and prior != peer.capital:
                    raise AuthorizedRiskProviderError(
                        f"peer {peer.peer_id} has conflicting capital observations"
                    )
                unique[peer.peer_id] = peer.capital
        if len(unique) < 2:
            raise AuthorizedRiskProviderError(
                "peer-normalized target structure requires at least two unique peers"
            )
        debt_weights = []
        for capital in unique.values():
            total = capital.debt + capital.equity_market_value
            if total <= 0:
                raise AuthorizedRiskProviderError(
                    f"peer {capital.peer_id} has non-positive total capitalization"
                )
            debt_weights.append(capital.debt / total)
        debt_weight = fmean(debt_weights)
        as_of = min(capital.as_of for capital in unique.values())
        source_refs = tuple(
            sorted(
                {capital.source_ref for capital in unique.values()}
                | {self.country_risk.source_ref}
            )
        )
        return LiveCapitalStructureObservation(
            equity_weight=1.0 - debt_weight,
            debt_weight=debt_weight,
            tax_rate=self.country_risk.corporate_tax_rate,
            method=TargetCapitalStructureMethod.PEER_NORMALIZED_MARKET_VALUE,
            as_of=as_of,
            source_refs=source_refs,
            rationale=(
                "equal-weighted peer debt/(debt+market-equity) structure; "
                "target current market capitalization is not used"
            ),
        )

    def beta_universe(self) -> LiveBetaUniverse:
        structure = self.target_capital_structure()
        levels = tuple(
            LiveBetaLevelObservation(
                level=level.level,
                peers=tuple(peer.to_live_observation() for peer in level.peers),
                selection_rationale=level.selection_rationale,
                selection_evidence_ids=level.selection_evidence_ids,
                risk_driver_features=level.risk_driver_features,
            )
            for level in self.beta_levels
        )
        source_refs = tuple(
            sorted(
                {peer.beta_source_ref for level in self.beta_levels for peer in level.peers}
                | {peer.capital.source_ref for level in self.beta_levels for peer in level.peers}
                | set(structure.source_refs)
            )
        )
        universe = LiveBetaUniverse(
            levels=levels,
            target_capital_structure=structure,
            universe_rationale=(
                "authorized KRX regression Betas plus Evidence-backed L1→L4 peer routing"
            ),
            source_refs=source_refs,
        )
        universe.validate()
        return universe

    def wacc_inputs(
        self,
        *,
        country_risk_lambda: float = 0.0,
        country_risk_exposure_source_ref: str = "",
        terminal_growth: float | None = None,
        terminal_roic: float | None = None,
    ) -> LiveWACCInputs:
        structure = self.target_capital_structure()
        rf_value, rf_as_of = _series_rate(self.risk_free_rate)
        debt_value, debt_as_of = _series_rate(self.marginal_debt.series)
        risk_free = RateObservation(
            value=rf_value,
            currency="KRW",
            as_of=rf_as_of,
            source_ref=self.risk_free_rate.source_ref,
            methodology=f"BOK ECOS: {self.risk_free_rate.name}",
        )
        erp = RateObservation(
            value=self.country_risk.mature_market_erp,
            currency="KRW",
            as_of=self.country_risk.as_of,
            source_ref=self.country_risk.source_ref,
            methodology="Damodaran mature-market ERP separated from country risk",
        )
        crp = RateObservation(
            value=self.country_risk.country_risk_premium,
            currency="KRW",
            as_of=self.country_risk.as_of,
            source_ref=self.country_risk.source_ref,
            methodology="Damodaran country risk premium",
        )
        debt = RateObservation(
            value=debt_value,
            currency="KRW",
            as_of=debt_as_of,
            source_ref=self.marginal_debt.series.source_ref,
            methodology=(
                f"BOK ECOS market borrowing benchmark matched to "
                f"rating={self.marginal_debt.credit_rating}, maturity={self.marginal_debt.maturity}; "
                f"rating source={self.marginal_debt.rating_source_ref}"
            ),
        )
        result = LiveWACCInputs(
            cash_flow_currency="KRW",
            risk_free_rate=risk_free,
            equity_risk_premium=erp,
            marginal_pre_tax_cost_of_debt=debt,
            target_capital_structure=structure,
            country_risk_premium=crp,
            country_risk_lambda=country_risk_lambda,
            country_risk_source_ref=(
                country_risk_exposure_source_ref
                if country_risk_lambda > 0
                else ""
            ),
            terminal_growth=terminal_growth,
            terminal_roic=terminal_roic,
        )
        result.validate()
        return result


def _parse_date(value: str, label: str) -> date:
    text = str(value or "").strip()
    try:
        if len(text) == 8 and text.isdigit():
            return datetime.strptime(text, "%Y%m%d").date()
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise AuthorizedRiskProviderError(f"{label} must be YYYY-MM-DD or YYYYMMDD") from exc


def _series_rate(observation: SeriesObservation) -> tuple[float, str]:
    if not isfinite(observation.value):
        raise AuthorizedRiskProviderError("rate series value must be finite")
    unit = observation.unit.strip().casefold()
    if "%" in observation.unit or "percent" in unit or "퍼센트" in unit:
        value = observation.value / 100.0
    elif unit in {"ratio", "decimal"}:
        value = observation.value
    else:
        raise AuthorizedRiskProviderError(
            f"unsupported rate unit {observation.unit!r}; explicit percent/ratio unit required"
        )
    if not isfinite(value) or value < -0.20 or value > 1.0:
        raise AuthorizedRiskProviderError("normalized rate is outside a plausible decimal range")
    as_of = _parse_date(observation.time, "rate time").isoformat()
    if not observation.source_ref or not observation.name:
        raise AuthorizedRiskProviderError("rate series requires name and source_ref")
    return value, as_of
