from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from math import isfinite
from statistics import median

from .assumption_compiler import CompiledAssumptionSet
from .official_market_data import DartEPS
from .orchestrator import OrchestratorContext
from .per import PER_LEVEL_ORDER, PERLevelName
from .per_adapters import (
    LiveExpansionPERConfig,
    LivePERAssumptionKeys,
    LivePERInputs,
    LivePERLevelObservation,
    LivePeerPERObservation,
    PERApplicability,
    PERInputsLoader,
)


class AuthorizedPERProviderError(ValueError):
    pass


class EPSNormalizationMethod(str, Enum):
    LATEST_ANNUAL_ADJUSTED = "latest_annual_adjusted"
    THREE_YEAR_MEDIAN_ADJUSTED = "three_year_median_adjusted"


@dataclass(frozen=True)
class FilingEPSObservation:
    filing: DartEPS
    evidence_id: str

    def validate(self) -> None:
        if not self.evidence_id:
            raise AuthorizedPERProviderError("filing EPS observation requires Evidence ID")
        if self.filing.report_code != "11011":
            raise AuthorizedPERProviderError(
                "normalized EPS base accepts annual OpenDART filing EPS only"
            )
        if not self.filing.source_ref or not self.filing.receipt_no:
            raise AuthorizedPERProviderError("filing EPS source provenance is incomplete")
        if not self.filing.eps.is_finite():
            raise AuthorizedPERProviderError("filing EPS must be finite")
        try:
            int(self.filing.business_year)
        except ValueError as exc:
            raise AuthorizedPERProviderError("business_year must be numeric") from exc


@dataclass(frozen=True)
class EPSNormalizationAdjustment:
    label: str
    business_year: str
    per_share_amount: Decimal
    evidence_ids: tuple[str, ...]
    source_ref: str

    def validate(self) -> None:
        if not self.label or not self.business_year or not self.source_ref:
            raise AuthorizedPERProviderError(
                "EPS normalization adjustment requires label, year and source"
            )
        if not self.evidence_ids:
            raise AuthorizedPERProviderError(
                "EPS normalization adjustment requires Evidence IDs"
            )
        if not self.per_share_amount.is_finite():
            raise AuthorizedPERProviderError("EPS adjustment must be finite")
        try:
            int(self.business_year)
        except ValueError as exc:
            raise AuthorizedPERProviderError(
                "EPS adjustment business_year must be numeric"
            ) from exc


@dataclass(frozen=True)
class NormalizedForwardEPSCandidate:
    corp_code: str
    fs_div: str
    base_business_year: str
    forward_business_year: str
    normalization_method: EPSNormalizationMethod
    normalized_base_eps: Decimal
    forward_growth_rate: Decimal
    normalized_forward_eps: Decimal
    evidence_ids: tuple[str, ...]
    source_refs: tuple[str, ...]
    methodology: str

    def validate(self) -> None:
        if not all(
            (
                self.corp_code,
                self.fs_div,
                self.base_business_year,
                self.forward_business_year,
                self.evidence_ids,
                self.source_refs,
                self.methodology,
            )
        ):
            raise AuthorizedPERProviderError(
                "normalized forward EPS candidate is incomplete"
            )
        if not self.normalized_base_eps.is_finite() or self.normalized_base_eps <= 0:
            raise AuthorizedPERProviderError(
                "normalized base EPS must be finite and positive"
            )
        if not self.forward_growth_rate.is_finite() or self.forward_growth_rate <= Decimal("-1"):
            raise AuthorizedPERProviderError(
                "forward EPS growth rate must be finite and greater than -100%"
            )
        if (
            not self.normalized_forward_eps.is_finite()
            or self.normalized_forward_eps <= 0
        ):
            raise AuthorizedPERProviderError(
                "normalized forward EPS must be finite and positive"
            )
        if int(self.forward_business_year) != int(self.base_business_year) + 1:
            raise AuthorizedPERProviderError(
                "normalized forward EPS candidate supports exactly the next business year"
            )


def build_normalized_forward_eps_candidate(
    filings: tuple[FilingEPSObservation, ...],
    *,
    normalization_method: EPSNormalizationMethod,
    adjustments: tuple[EPSNormalizationAdjustment, ...] = (),
    forward_growth_rate: Decimal,
    forward_growth_evidence_ids: tuple[str, ...],
    forward_growth_source_ref: str,
) -> NormalizedForwardEPSCandidate:
    if not filings:
        raise AuthorizedPERProviderError("annual filing EPS history is required")
    if not forward_growth_evidence_ids or not forward_growth_source_ref:
        raise AuthorizedPERProviderError(
            "forward EPS growth requires explicit non-Street Evidence and source"
        )
    if not forward_growth_rate.is_finite() or forward_growth_rate <= Decimal("-1"):
        raise AuthorizedPERProviderError(
            "forward EPS growth rate must be finite and greater than -100%"
        )
    for item in filings:
        item.validate()
    for item in adjustments:
        item.validate()

    corp_codes = {item.filing.corp_code for item in filings}
    fs_divs = {item.filing.fs_div for item in filings}
    if len(corp_codes) != 1 or len(fs_divs) != 1:
        raise AuthorizedPERProviderError(
            "normalized EPS history must use one issuer and one statement scope"
        )
    by_year: dict[str, FilingEPSObservation] = {}
    for item in filings:
        year = item.filing.business_year
        if year in by_year:
            raise AuthorizedPERProviderError(
                f"duplicate annual EPS filing for business year {year}"
            )
        by_year[year] = item

    adjustment_by_year: dict[str, Decimal] = {}
    adjustment_evidence: list[str] = []
    adjustment_sources: set[str] = set()
    for adjustment in adjustments:
        if adjustment.business_year not in by_year:
            raise AuthorizedPERProviderError(
                f"EPS adjustment year {adjustment.business_year} has no filing EPS"
            )
        adjustment_by_year[adjustment.business_year] = (
            adjustment_by_year.get(adjustment.business_year, Decimal("0"))
            + adjustment.per_share_amount
        )
        adjustment_evidence.extend(adjustment.evidence_ids)
        adjustment_sources.add(adjustment.source_ref)

    years = sorted(by_year, key=int)
    adjusted: dict[str, Decimal] = {
        year: by_year[year].filing.eps + adjustment_by_year.get(year, Decimal("0"))
        for year in years
    }
    latest_year = years[-1]
    if normalization_method is EPSNormalizationMethod.LATEST_ANNUAL_ADJUSTED:
        base = adjusted[latest_year]
        used_years = (latest_year,)
    elif normalization_method is EPSNormalizationMethod.THREE_YEAR_MEDIAN_ADJUSTED:
        if len(years) < 3:
            raise AuthorizedPERProviderError(
                "three-year median EPS normalization requires at least three annual filings"
            )
        used_years = tuple(years[-3:])
        base = Decimal(str(median([adjusted[year] for year in used_years])))
    else:
        raise AuthorizedPERProviderError(
            f"unsupported EPS normalization method: {normalization_method}"
        )
    if not base.is_finite() or base <= 0:
        raise AuthorizedPERProviderError(
            "normalized filing EPS base must be positive before forward projection"
        )
    forward = base * (Decimal("1") + forward_growth_rate)
    evidence_ids = tuple(
        dict.fromkeys(
            [by_year[year].evidence_id for year in used_years]
            + adjustment_evidence
            + list(forward_growth_evidence_ids)
        )
    )
    source_refs = tuple(
        sorted(
            {by_year[year].filing.source_ref for year in used_years}
            | adjustment_sources
            | {forward_growth_source_ref}
        )
    )
    result = NormalizedForwardEPSCandidate(
        corp_code=next(iter(corp_codes)),
        fs_div=next(iter(fs_divs)),
        base_business_year=latest_year,
        forward_business_year=str(int(latest_year) + 1),
        normalization_method=normalization_method,
        normalized_base_eps=base,
        forward_growth_rate=forward_growth_rate,
        normalized_forward_eps=forward,
        evidence_ids=evidence_ids,
        source_refs=source_refs,
        methodology=(
            f"{normalization_method.value}; annual OpenDART EPS adjusted only by explicit "
            "Evidence-backed per-share items, then projected one year by an explicit "
            "non-Street growth assumption"
        ),
    )
    result.validate()
    return result


@dataclass(frozen=True)
class AuthorizedPeerPERSource:
    peer_id: str
    market_price: float
    normalized_forward_eps: float
    fundamental_forward_per: float
    as_of: str
    market_source_ref: str
    eps_source_ref: str
    fundamental_model_ref: str
    methodology: str

    def validate(self) -> None:
        if not all(
            (
                self.peer_id,
                self.as_of,
                self.market_source_ref,
                self.eps_source_ref,
                self.fundamental_model_ref,
                self.methodology,
            )
        ):
            raise AuthorizedPERProviderError(
                "authorized peer PER source has missing identity/source fields"
            )
        date.fromisoformat(self.as_of[:10])
        for label, value in (
            ("peer market price", self.market_price),
            ("peer normalized forward EPS", self.normalized_forward_eps),
            ("peer fundamental forward PER", self.fundamental_forward_per),
        ):
            if not isfinite(value) or value <= 0:
                raise AuthorizedPERProviderError(f"{label} must be finite and positive")

    @property
    def market_forward_per(self) -> float:
        self.validate()
        return self.market_price / self.normalized_forward_eps

    def to_live_observation(self) -> LivePeerPERObservation:
        self.validate()
        result = LivePeerPERObservation(
            peer_id=self.peer_id,
            market_forward_per=self.market_forward_per,
            fundamental_forward_per=self.fundamental_forward_per,
            as_of=self.as_of,
            market_source_ref=self.market_source_ref,
            fundamental_model_ref=self.fundamental_model_ref,
            methodology=(
                self.methodology
                + f"; peer EPS source={self.eps_source_ref}; peer-only market reference"
            ),
        )
        result.validate()
        return result


@dataclass(frozen=True)
class AuthorizedPERLevelSource:
    level: PERLevelName
    peers: tuple[AuthorizedPeerPERSource, ...]
    selection_rationale: str
    selection_evidence_ids: tuple[str, ...]
    economic_twin_features: tuple[str, ...]

    def validate(self) -> None:
        if not self.peers or not self.selection_rationale or not self.selection_evidence_ids:
            raise AuthorizedPERProviderError(
                f"{self.level.value} PER provider requires peers, rationale and Evidence IDs"
            )
        for peer in self.peers:
            peer.validate()
        if self.level is PERLevelName.L4_ECONOMIC_TWINS and not self.economic_twin_features:
            raise AuthorizedPERProviderError(
                "L4 PER provider requires explicit economic-twin features"
            )

    def to_live_level(self) -> LivePERLevelObservation:
        self.validate()
        result = LivePERLevelObservation(
            level=self.level,
            peers=tuple(peer.to_live_observation() for peer in self.peers),
            selection_rationale=self.selection_rationale,
            selection_evidence_ids=self.selection_evidence_ids,
            economic_twin_features=self.economic_twin_features,
        )
        result.validate()
        return result


@dataclass(frozen=True)
class AuthorizedPERProviderPack:
    target_id: str
    normalized_forward_eps: NormalizedForwardEPSCandidate
    residual_levels: tuple[AuthorizedPERLevelSource, ...]

    def validate(self) -> None:
        if not self.target_id:
            raise AuthorizedPERProviderError("PER provider target_id is required")
        self.normalized_forward_eps.validate()
        if tuple(item.level for item in self.residual_levels) != PER_LEVEL_ORDER:
            raise AuthorizedPERProviderError(
                "authorized PER residual provider must be exactly L1→L2→L3→L4"
            )
        seen: set[str] = set()
        as_of: set[str] = set()
        for level in self.residual_levels:
            level.validate()
            for peer in level.peers:
                if peer.peer_id == self.target_id:
                    raise AuthorizedPERProviderError(
                        "target company cannot enter authorized PER peer residual pool"
                    )
                if peer.peer_id in seen:
                    raise AuthorizedPERProviderError(
                        f"peer {peer.peer_id} appears in multiple PER provider levels"
                    )
                seen.add(peer.peer_id)
                as_of.add(peer.as_of[:10])
        if len(as_of) != 1:
            raise AuthorizedPERProviderError(
                "authorized PER peer residuals require one normalized as-of date"
            )

    def live_residual_levels(self) -> tuple[LivePERLevelObservation, ...]:
        self.validate()
        return tuple(level.to_live_level() for level in self.residual_levels)

    def loader(
        self,
        *,
        core_assumption_keys: LivePERAssumptionKeys,
        applicability_rationale: str,
        expansion: LiveExpansionPERConfig | None = None,
        require_dcf_consistency: bool = True,
    ) -> PERInputsLoader:
        self.validate()
        core_assumption_keys.validate()

        def load(context: OrchestratorContext) -> LivePERInputs:
            compiled = context.data.get("compiled_assumption_set")
            if not isinstance(compiled, CompiledAssumptionSet):
                raise AuthorizedPERProviderError(
                    "CompiledAssumptionSet is required for authorized PER provider"
                )
            if compiled.target_id != self.target_id:
                raise AuthorizedPERProviderError(
                    "authorized PER provider target must match compiled assumptions"
                )
            assumption = compiled.get(
                core_assumption_keys.normalized_forward_eps_key,
                core_assumption_keys.scenario_id,
            )
            normalized = assumption.measure.convert_to(
                core_assumption_keys.normalized_forward_eps_unit
            ).amount
            if normalized != self.normalized_forward_eps.normalized_forward_eps:
                raise AuthorizedPERProviderError(
                    "compiled normalized forward EPS does not match authorized provider candidate"
                )
            missing_evidence = set(self.normalized_forward_eps.evidence_ids).difference(
                assumption.evidence_ids
            )
            if missing_evidence:
                raise AuthorizedPERProviderError(
                    "compiled normalized forward EPS is missing provider Evidence IDs: "
                    + ", ".join(sorted(missing_evidence))
                )
            refs = tuple(
                sorted(
                    set(self.normalized_forward_eps.source_refs)
                    | {
                        peer.market_source_ref
                        for level in self.residual_levels
                        for peer in level.peers
                    }
                    | {
                        peer.eps_source_ref
                        for level in self.residual_levels
                        for peer in level.peers
                    }
                    | {
                        peer.fundamental_model_ref
                        for level in self.residual_levels
                        for peer in level.peers
                    }
                )
            )
            return LivePERInputs(
                target_id=self.target_id,
                applicability=PERApplicability.APPLICABLE,
                applicability_rationale=applicability_rationale,
                core_assumption_keys=core_assumption_keys,
                expansion=expansion,
                residual_levels=self.live_residual_levels(),
                require_dcf_consistency=require_dcf_consistency,
                source_refs=refs,
            )

        return load
