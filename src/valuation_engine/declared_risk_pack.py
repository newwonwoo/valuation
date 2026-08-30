"""The discount rate's front door: an operator-declared risk pack, or no DCF.

Nine of the fourteen execution families require a Beta and a WACC. The typed
providers for both exist (:class:`~.authorized_risk_providers.AuthorizedKRRiskProviderPack`
composes KRX regression Betas, ECOS rates, Damodaran ERP/CRP and a marginal-debt
benchmark into validated ``LiveBetaUniverse``/``LiveWACCInputs``), but until now
nothing company-neutral could *construct* the pack, so a cold start on an unseen
company stopped at ``HIERARCHICAL_BETA_ESTIMATION`` for every discount-rate-bound
method — the boundary ``tests/test_new_archetype_cold_run.py`` pinned.

This module is the entrance, built like the declared-underwriting door:

- a **per-run declared file**, owned by the operator, bound to exactly one
  ``target_id`` — a risk pack written for another company fails closed;
- the four L1→L4 peer levels each carry a **selection rationale of substance**
  and named risk-driver features; every peer carries its regression Beta with
  window and benchmark, its capital observation, and HTTP source references;
- the **target may not appear among its own peers** — the pack's doctrine is a
  peer-normalized structure in which the target's market capitalization is
  never used, and a peer row that smuggles the target back in is refused by
  ticker, by corp code and by target_id;
- each level's peer selection enters the run's Evidence ledger at
  ``ANALYST_UNDERWRITING`` through a collector, and the Beta stage's own
  evidence-ID validation then binds the universe to those records — the
  judgment is auditable, not ambient;
- the file's SHA-256 is the collection batch fingerprint, so the attested hash
  chain binds the exact risk declaration that produced the discount rate.

What this deliberately is NOT: a market-data fetcher. Peer Betas, rates and
premia are authorized observations the operator assembles and cites; fetching
them live is a separate collector's job. The declaration file makes the
judgment reviewable — which peers, which window, which benchmark — which is
exactly the part of a discount rate that is analyst work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Mapping

import yaml

from .authorized_risk_providers import (
    AuthorizedBetaLevelSource,
    AuthorizedKRRiskProviderPack,
    AuthorizedPeerBetaSource,
    MarginalDebtBenchmark,
    PeerCapitalObservation,
)
from .collection_plan import CollectorCapability
from .evidence_collection import EvidenceCollectionBatch, EvidenceCollectionRequest
from .live_primary_adapters import ResolvedCompanyIdentity
from .live_runtime import LiveCollectorProvider
from .official_market_data import BetaEstimate, CountryRisk, SeriesObservation
from .records import EvidenceRecord, EvidenceSourceLayer
from .risk import BETA_LEVEL_ORDER
from .risk_adapters import LiveBetaUniverse, LiveWACCInputs


SOURCE_ID = "OPERATOR_RISK_DECLARATION"
COLLECTOR_ID = "operator-declared-risk-pack"
_MIN_RATIONALE_CHARS = 20

#: One Evidence metric per Beta level; the collection plan requires all four
#: whenever a risk pack is declared, and the Beta adapter's evidence-ID check
#: then proves the ledger really carries the selections the universe cites.
BETA_SELECTION_METRICS = tuple(
    f"beta_selection_{level.value.lower()}" for level in BETA_LEVEL_ORDER
)


class DeclaredRiskPackError(ValueError):
    """Raised when a declared risk-pack file violates its contract."""


def _declared_date(value: str, label: str) -> date:
    text = str(value or "").strip()
    try:
        if len(text) == 8 and text.isdigit():
            return datetime.strptime(text, "%Y%m%d").date()
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise DeclaredRiskPackError(
            f"{label} must be YYYY-MM-DD or YYYYMMDD"
        ) from exc


def _selection_evidence_id(target_id: str, metric: str) -> str:
    return f"RISK:{target_id}:{metric}"


def _require_http(ref: str, label: str) -> str:
    text = str(ref or "").strip()
    if not text.startswith("http"):
        raise DeclaredRiskPackError(
            f"{label} must be an HTTP provenance link, got {text!r}; the "
            "report's source-link contract verifies every reference"
        )
    return text


def _mapping(row: object, label: str) -> Mapping:
    if not isinstance(row, Mapping):
        raise DeclaredRiskPackError(f"{label} must be a mapping")
    return row


def _floatv(row: Mapping, key: str, label: str) -> float:
    value = row.get(key)
    if value is None:
        raise DeclaredRiskPackError(f"{label} requires {key}")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise DeclaredRiskPackError(f"{label}.{key} must be numeric") from exc


def _series(row: object, label: str) -> SeriesObservation:
    data = _mapping(row, label)
    return SeriesObservation(
        time=str(data.get("time") or ""),
        value=_floatv(data, "value", label),
        unit=str(data.get("unit") or ""),
        name=str(data.get("name") or ""),
        source_ref=_require_http(str(data.get("source_ref") or ""), f"{label}.source_ref"),
    )


def _peer(row: object, label: str) -> AuthorizedPeerBetaSource:
    data = _mapping(row, label)
    peer_id = str(data.get("peer_id") or "")
    if not peer_id:
        raise DeclaredRiskPackError(f"{label} requires peer_id")
    beta_row = _mapping(data.get("beta"), f"{label}.beta")
    capital_row = _mapping(data.get("capital"), f"{label}.capital")
    standard_error = data.get("beta_standard_error")
    beta_kwargs = {}
    if beta_row.get("method"):
        beta_kwargs["method"] = str(beta_row["method"])
    return AuthorizedPeerBetaSource(
        peer_id=peer_id,
        beta=BetaEstimate(
            code=str(beta_row.get("code") or peer_id),
            benchmark=str(beta_row.get("benchmark") or ""),
            beta=_floatv(beta_row, "beta", f"{label}.beta"),
            observations=int(beta_row.get("observations") or 0),
            start_date=str(beta_row.get("start_date") or ""),
            end_date=str(beta_row.get("end_date") or ""),
            **beta_kwargs,
        ),
        capital=PeerCapitalObservation(
            peer_id=peer_id,
            debt=_floatv(capital_row, "debt", f"{label}.capital"),
            equity_market_value=_floatv(
                capital_row, "equity_market_value", f"{label}.capital"
            ),
            tax_rate=_floatv(capital_row, "tax_rate", f"{label}.capital"),
            as_of=str(capital_row.get("as_of") or ""),
            source_ref=_require_http(
                str(capital_row.get("source_ref") or ""),
                f"{label}.capital.source_ref",
            ),
        ),
        beta_source_ref=_require_http(
            str(data.get("beta_source_ref") or ""), f"{label}.beta_source_ref"
        ),
        beta_standard_error=(
            float(standard_error) if standard_error is not None else None
        ),
    )


@dataclass(frozen=True)
class DeclaredRiskPack:
    """A loaded, eagerly validated risk declaration bound to one target."""

    target_id: str
    as_of: str
    source_ref: str
    pack: AuthorizedKRRiskProviderPack
    country_risk_lambda: float
    country_risk_exposure_source_ref: str
    file_sha256: str
    #: rationale/features per level, for the Evidence notes.
    level_notes: tuple[tuple[str, str], ...]

    def selection_evidence_ids(self) -> tuple[str, ...]:
        return tuple(
            _selection_evidence_id(self.target_id, metric)
            for metric in BETA_SELECTION_METRICS
        )

    def assert_target(self, target_id: str) -> None:
        if target_id != self.target_id:
            raise DeclaredRiskPackError(
                f"declared risk pack is bound to {self.target_id}, not "
                f"{target_id}; refusing cross-company reuse"
            )

    def assert_knowable_by(self, run_as_of: str) -> None:
        """Reject every dated declaration observation after the run cutoff."""
        cutoff = _declared_date(run_as_of, "run as_of")
        observations = [
            ("risk pack as_of", self.as_of),
            ("risk_free_rate.time", self.pack.risk_free_rate.time),
            ("country_risk.as_of", self.pack.country_risk.as_of),
            (
                "marginal_debt.series.time",
                self.pack.marginal_debt.series.time,
            ),
        ]
        for level in self.pack.beta_levels:
            for peer in level.peers:
                label = f"{level.level.value}.{peer.peer_id}"
                observations.extend(
                    (
                        (f"{label}.beta.start_date", peer.beta.start_date),
                        (f"{label}.beta.end_date", peer.beta.end_date),
                        (f"{label}.capital.as_of", peer.capital.as_of),
                    )
                )
        for label, value in observations:
            observed = _declared_date(value, label)
            if observed > cutoff:
                raise DeclaredRiskPackError(
                    f"declared risk observation {label}={observed.isoformat()} is "
                    f"after run cutoff {cutoff.isoformat()}; future risk input is "
                    "inadmissible"
                )

    def assert_target_not_a_peer(self, identity: ResolvedCompanyIdentity) -> None:
        """The peer-normalized structure must not contain the target itself.

        Re-admitting the target as its own peer would route the target's market
        capitalization back into the capital structure — the exact pre-freeze
        leakage the peer-normalized method exists to exclude.
        """
        def _norm(value: str) -> str:
            return "".join(ch for ch in value.upper() if ch.isalnum())

        markers = {self.target_id}
        if identity.ticker:
            markers.add(identity.ticker)
        markers.update(value for _, value in identity.external_ids if value)
        # Formatting variants ("900881.KS", "A900881", "kr:dart:00888801") are
        # the realistic way the target sneaks back in; compare on normalized
        # alphanumerics, and treat a long marker appearing INSIDE a peer id as
        # the same smuggling — a 6+ character code does not occur by accident.
        normalized_markers = {_norm(marker) for marker in markers if _norm(marker)}
        for level in self.pack.beta_levels:
            for peer in level.peers:
                normalized_peer = _norm(peer.peer_id)
                if peer.peer_id in markers or any(
                    marker == normalized_peer
                    or (len(marker) >= 6 and marker in normalized_peer)
                    for marker in normalized_markers
                ):
                    raise DeclaredRiskPackError(
                        f"declared risk pack lists the target itself as Beta peer "
                        f"{peer.peer_id!r} at {level.level.value}; the target's "
                        "market capitalization may not enter its own capital "
                        "structure"
                    )

    def beta_universe(self) -> LiveBetaUniverse:
        return self.pack.beta_universe()

    def wacc_inputs(self) -> LiveWACCInputs:
        return self.pack.wacc_inputs(
            country_risk_lambda=self.country_risk_lambda,
            country_risk_exposure_source_ref=self.country_risk_exposure_source_ref,
        )


def load_declared_risk_pack(
    path: str | Path, *, run_as_of: str | None = None
) -> DeclaredRiskPack:
    raw = Path(path).read_text(encoding="utf-8")
    payload = _mapping(yaml.safe_load(raw), "declared risk pack")
    target_id = str(payload.get("target_id") or "")
    as_of = str(payload.get("as_of") or "")
    if not target_id or not as_of:
        raise DeclaredRiskPackError("declared risk pack requires target_id and as_of")
    source_ref = _require_http(
        str(payload.get("source_ref") or ""), "declared risk pack source_ref"
    )
    levels_row = _mapping(payload.get("beta_levels"), "beta_levels")
    levels: list[AuthorizedBetaLevelSource] = []
    level_notes: list[tuple[str, str]] = []
    for order, level_name in enumerate(BETA_LEVEL_ORDER):
        row = levels_row.get(level_name.value)
        if row is None:
            raise DeclaredRiskPackError(
                f"beta_levels requires {level_name.value} (all of L1→L4 must be "
                "declared; a missing level is a missing judgment, not a default)"
            )
        data = _mapping(row, f"beta_levels.{level_name.value}")
        rationale = str(data.get("selection_rationale") or "").strip()
        if len(rationale) < _MIN_RATIONALE_CHARS:
            raise DeclaredRiskPackError(
                f"{level_name.value} requires a substantive selection_rationale "
                f"(>= {_MIN_RATIONALE_CHARS} chars); a peer set without a reason "
                "is not admissible"
            )
        peer_rows = data.get("peers")
        if not isinstance(peer_rows, list) or not peer_rows:
            raise DeclaredRiskPackError(f"{level_name.value} requires peers")
        peers = tuple(
            _peer(item, f"beta_levels.{level_name.value}.peers[{index}]")
            for index, item in enumerate(peer_rows)
        )
        features = tuple(
            str(item) for item in (data.get("risk_driver_features") or ())
        )
        metric = BETA_SELECTION_METRICS[order]
        levels.append(
            AuthorizedBetaLevelSource(
                level=level_name,
                peers=peers,
                selection_rationale=rationale,
                selection_evidence_ids=(
                    _selection_evidence_id(target_id, metric),
                ),
                risk_driver_features=features,
            )
        )
        note = (
            f"analyst_declared_peer_selection; rationale={rationale}; "
            f"peers={', '.join(peer.peer_id for peer in peers)}"
        )
        if features:
            note += f"; risk_driver_features={', '.join(features)}"
        level_notes.append((metric, note))

    country_row = _mapping(payload.get("country_risk"), "country_risk")
    country_kwargs = {}
    if country_row.get("source_ref"):
        country_kwargs["source_ref"] = _require_http(
            str(country_row["source_ref"]), "country_risk.source_ref"
        )
    country = CountryRisk(
        country=str(country_row.get("country") or ""),
        as_of=str(country_row.get("as_of") or ""),
        mature_market_erp=_floatv(country_row, "mature_market_erp", "country_risk"),
        country_risk_premium=_floatv(
            country_row, "country_risk_premium", "country_risk"
        ),
        total_equity_risk_premium=_floatv(
            country_row, "total_equity_risk_premium", "country_risk"
        ),
        adjusted_default_spread=_floatv(
            country_row, "adjusted_default_spread", "country_risk"
        ),
        corporate_tax_rate=_floatv(country_row, "corporate_tax_rate", "country_risk"),
        rating=str(country_row.get("rating") or ""),
        **country_kwargs,
    )
    debt_row = _mapping(payload.get("marginal_debt"), "marginal_debt")
    marginal_debt = MarginalDebtBenchmark(
        series=_series(debt_row.get("series"), "marginal_debt.series"),
        credit_rating=str(debt_row.get("credit_rating") or ""),
        maturity=str(debt_row.get("maturity") or ""),
        rating_source_ref=_require_http(
            str(debt_row.get("rating_source_ref") or ""),
            "marginal_debt.rating_source_ref",
        ),
    )
    pack = AuthorizedKRRiskProviderPack(
        beta_levels=tuple(levels),
        risk_free_rate=_series(payload.get("risk_free_rate"), "risk_free_rate"),
        country_risk=country,
        marginal_debt=marginal_debt,
        cash_flow_currency=str(payload.get("cash_flow_currency") or "KRW"),
    )
    country_risk_lambda = float(payload.get("country_risk_lambda") or 0.0)
    exposure_ref = str(payload.get("country_risk_exposure_source_ref") or "")
    if country_risk_lambda > 0:
        exposure_ref = _require_http(
            exposure_ref, "country_risk_exposure_source_ref"
        )
    declared = DeclaredRiskPack(
        target_id=target_id,
        as_of=as_of,
        source_ref=source_ref,
        pack=pack,
        country_risk_lambda=country_risk_lambda,
        country_risk_exposure_source_ref=exposure_ref,
        file_sha256=sha256(raw.encode("utf-8")).hexdigest(),
        level_notes=tuple(level_notes),
    )
    # Fail at load time, not mid-run: a pack that cannot produce a valid
    # universe or valid WACC inputs is a broken declaration file.
    declared.beta_universe()
    declared.wacc_inputs()
    if run_as_of is not None:
        declared.assert_knowable_by(run_as_of)
    return declared


def declared_risk_collector(declared: DeclaredRiskPack, *, segment_id: str):
    """EvidenceCollector serving the pack's four peer-selection judgments."""
    notes_by_metric = dict(declared.level_notes)

    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        declared.assert_target(request.target_id)
        records = []
        for metric in request.required_metrics:
            note = notes_by_metric.get(metric)
            if note is None:
                continue  # not this collector's metric: a named gap downstream
            level = declared.pack.beta_levels[
                BETA_SELECTION_METRICS.index(metric)
            ]
            records.append(
                EvidenceRecord(
                    id=_selection_evidence_id(declared.target_id, metric),
                    target=declared.target_id,
                    metric=metric,
                    value=len(level.peers),
                    unit="dimensionless",
                    source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
                    effective_date=declared.as_of,
                    observed_date=declared.as_of,
                    source_name="operator declared risk pack",
                    source_ref=declared.source_ref,
                    source_grade="B",
                    confidence=0.6,
                    segment=segment_id,
                    notes=note,
                )
            )
        batch = EvidenceCollectionBatch(
            source_id=SOURCE_ID,
            checked_at=declared.as_of,
            records=tuple(records),
            source_fingerprint=declared.file_sha256,
            document_ids=(f"RISK_PACK_{declared.file_sha256[:16]}",),
        )
        batch.validate()
        return batch

    return collect


def declared_risk_provider(
    declared: DeclaredRiskPack, *, segment_id: str
) -> LiveCollectorProvider:
    return LiveCollectorProvider(
        capability=CollectorCapability(
            collector_id=COLLECTOR_ID,
            source_id=SOURCE_ID,
            supported_metrics=BETA_SELECTION_METRICS,
            jurisdictions=("KR",),
            implementation_ref=(
                "valuation_engine.declared_risk_pack.declared_risk_collector"
            ),
        ),
        collector=declared_risk_collector(declared, segment_id=segment_id),
    )


def _resolved_identity(context) -> ResolvedCompanyIdentity:
    identity = context.data.get("resolved_company_identity")
    if not isinstance(identity, ResolvedCompanyIdentity):
        raise DeclaredRiskPackError(
            "resolved company identity is required before the declared risk "
            "pack may serve a Beta universe"
        )
    return identity


def declared_risk_beta_loader(declared: DeclaredRiskPack):
    """BetaUniverseLoader bound to the declaration's target, target-as-peer refused."""

    def load(context) -> LiveBetaUniverse:
        identity = _resolved_identity(context)
        declared.assert_target(identity.target_id)
        declared.assert_target_not_a_peer(identity)
        return declared.beta_universe()

    return load


def declared_risk_wacc_loader(declared: DeclaredRiskPack):
    """WACCInputsLoader bound to the declaration's target."""

    def load(context) -> LiveWACCInputs:
        identity = _resolved_identity(context)
        declared.assert_target(identity.target_id)
        return declared.wacc_inputs()

    return load
