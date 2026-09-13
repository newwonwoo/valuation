"""Recomputable peer-margin assumptions; no source authentication or valuation authority.

Amounts within each peer must share a currency/unit and reporting period. Across
peers only ratios are pooled. EBIT and EBITDA are never silently interchanged.
Adjustments are percentage *points*, while outputs are decimal ratios. Every
target year is explicit: there is no default mature margin or automatic ramp.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timezone
from decimal import Context, Decimal, DecimalException, InvalidOperation, localcontext
import hashlib
import json
from typing import Any, Mapping
from urllib.parse import urlsplit

from .actual_units import Dimension, unit_def


class PeerMarginResearchError(ValueError):
    """Incomplete, incomparable, or inconsistent peer-margin assumptions."""


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PeerMarginResearchError(f"{name}: nonempty text required")
    return value


def _number(value: Any, name: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise PeerMarginResearchError(f"{name}: exact decimal required")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise PeerMarginResearchError(f"{name}: invalid decimal") from exc
    if not result.is_finite():
        raise PeerMarginResearchError(f"{name}: finite decimal required")
    return result


def _range(value: Any, name: str) -> dict[str, Decimal]:
    if not isinstance(value, Mapping) or set(value) != {"low", "base", "high"}:
        raise PeerMarginResearchError(f"{name}: low/base/high required")
    result = {case: _number(value[case], name) for case in ("low", "base", "high")}
    if not result["low"] <= result["base"] <= result["high"]:
        raise PeerMarginResearchError(f"{name}: invalid bounds")
    return result


def _instant(value: Any, name: str, *, cutoff: bool = False) -> datetime:
    raw = _text(value, name)
    try:
        if len(raw) == 10:
            return datetime.combine(date.fromisoformat(raw), time.max if cutoff else time.min, timezone.utc)
        result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("timezone required")
        return result.astimezone(timezone.utc)
    except ValueError as exc:
        raise PeerMarginResearchError(f"{name}: ISO date or timezone-aware timestamp required") from exc


def _refs(value: Any, sources: Mapping[str, Any], name: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or not value:
        raise PeerMarginResearchError(f"{name}: source references required")
    refs = tuple(_text(ref, name) for ref in value)
    if len(set(refs)) != len(refs) or any(ref not in sources for ref in refs):
        raise PeerMarginResearchError(f"{name}: duplicate or unknown source reference")
    return refs


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def build_peer_margin_proposal(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Build a traced assumption from a ``peer-margin-input/v1`` mapping.

    Required top-level keys: target, segment, as_of, economic_path_id, metric
    (EBIT/EBITDA), accounting_basis, sources, peers, years. Sources carry source_id,
    url, published_at, first_seen_at, locator and content_sha256. Each peer has
    peer_id, issuer, segment, metric, accounting_basis, money_unit, period_start,
    period_end, revenue, profit{low,base,high}, weight, weight_rationale,
    comparability_rationale, period_comparability_rationale, source_refs,
    normalization_adjustments. Period comparability must explicitly explain
    reporting-duration/cycle differences; no implicit annualization is applied. Each year
    has year, rationale, adjustments. Every adjustment has adjustment_id,
    percentage_points{low,base,high}, rationale, source_refs. Empty adjustment
    lists are deliberate no-adjustment declarations; year rationale is required.
    """
    try:
        with localcontext(Context(prec=40)):
            return _build(inputs)
    except DecimalException as exc:
        raise PeerMarginResearchError("peer margin arithmetic exceeds decimal limits") from exc


def _build(inputs: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(inputs, Mapping) or inputs.get("schema_version") != "peer-margin-input/v1":
        raise PeerMarginResearchError("peer-margin-input/v1 required")
    for name in ("target", "segment", "economic_path_id", "accounting_basis"):
        _text(inputs.get(name), name)
    metric = inputs.get("metric")
    if metric not in {"EBIT", "EBITDA"}:
        raise PeerMarginResearchError("metric: EBIT or EBITDA required")
    cutoff = _instant(inputs.get("as_of"), "as_of", cutoff=True)
    source_rows = inputs.get("sources")
    if not isinstance(source_rows, (list, tuple)) or not source_rows:
        raise PeerMarginResearchError("sources required")
    sources: dict[str, Any] = {}
    for source in source_rows:
        if not isinstance(source, Mapping):
            raise PeerMarginResearchError("source mapping required")
        key = _text(source.get("source_id"), "source_id")
        if key in sources:
            raise PeerMarginResearchError("duplicate source_id")
        url = urlsplit(_text(source.get("url"), "source url"))
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise PeerMarginResearchError("public HTTP(S) source URL required")
        published = _instant(source.get("published_at"), "published_at")
        first_seen = _instant(source.get("first_seen_at"), "first_seen_at")
        if published > first_seen or first_seen > cutoff:
            raise PeerMarginResearchError("source timestamp exceeds cutoff or reverses publication order")
        _text(source.get("locator"), "locator")
        digest = _text(source.get("content_sha256"), "content_sha256")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise PeerMarginResearchError("content_sha256: lowercase SHA256 required")
        sources[key] = source

    def adjustments(rows: Any, name: str) -> dict[str, Decimal]:
        if not isinstance(rows, (list, tuple)):
            raise PeerMarginResearchError(f"{name}: explicit adjustment list required")
        total = {case: Decimal(0) for case in ("low", "base", "high")}
        seen = set()
        for adjustment in rows:
            if not isinstance(adjustment, Mapping):
                raise PeerMarginResearchError("adjustment mapping required")
            identity = _text(adjustment.get("adjustment_id"), "adjustment_id")
            if identity in seen:
                raise PeerMarginResearchError("duplicate adjustment_id within economic step")
            seen.add(identity)
            _text(adjustment.get("rationale"), "adjustment rationale")
            _refs(adjustment.get("source_refs"), sources, "adjustment")
            values = _range(adjustment.get("percentage_points"), "percentage_points")
            for case in total:
                total[case] += values[case] / Decimal(100)
        return total

    peers = inputs.get("peers")
    if not isinstance(peers, (list, tuple)) or not peers:
        raise PeerMarginResearchError("at least one peer required")
    seen_peers = set()
    seen_observations = set()
    normalized = []
    weight_total = Decimal(0)
    pooled = {case: Decimal(0) for case in ("low", "base", "high")}
    for peer in peers:
        if not isinstance(peer, Mapping):
            raise PeerMarginResearchError("peer mapping required")
        key = _text(peer.get("peer_id"), "peer_id")
        if key in seen_peers:
            raise PeerMarginResearchError("duplicate peer_id")
        seen_peers.add(key)
        for name in ("issuer", "segment", "money_unit", "weight_rationale", "comparability_rationale", "period_comparability_rationale"):
            _text(peer.get(name), name)
        try:
            monetary = unit_def(peer["money_unit"]).dimension == Dimension.MONEY
        except ValueError as exc:
            raise PeerMarginResearchError("unsupported peer money unit") from exc
        if not monetary:
            raise PeerMarginResearchError("peer revenue/profit require a money unit")
        if peer["issuer"].strip().casefold() == inputs["target"].strip().casefold():
            raise PeerMarginResearchError("peer issuer must differ from target")
        if peer.get("metric") != metric or peer.get("accounting_basis") != inputs["accounting_basis"]:
            raise PeerMarginResearchError("peer metric/accounting basis mismatch; normalize explicitly before pooling")
        start = _instant(peer.get("period_start"), "period_start")
        end = _instant(peer.get("period_end"), "period_end")
        observation = (peer["issuer"].strip().casefold(), peer["segment"].strip().casefold(), start, end)
        if observation in seen_observations:
            raise PeerMarginResearchError("duplicate issuer/segment/period observation under different peer IDs")
        seen_observations.add(observation)
        refs = _refs(peer.get("source_refs"), sources, "peer")
        if start > end or end > cutoff or any(end > _instant(sources[ref]["published_at"], "published_at") for ref in refs):
            raise PeerMarginResearchError("peer reporting period unavailable at publication/cutoff")
        revenue = _number(peer.get("revenue"), "revenue")
        weight = _number(peer.get("weight"), "weight")
        if revenue <= 0 or weight <= 0:
            raise PeerMarginResearchError("revenue and weight must be positive")
        profit = _range(peer.get("profit"), "profit")
        offsets = adjustments(peer.get("normalization_adjustments"), "normalization_adjustments")
        margins = {case: profit[case] / revenue + offsets[case] for case in pooled}
        if max(margins.values()) > 1:
            raise PeerMarginResearchError("normalized operating margin exceeds revenue")
        for case in pooled:
            pooled[case] += margins[case] * weight
        weight_total += weight
        normalized.append({"peer_id": key, "margin": _jsonable(margins), "weight": str(weight)})
    pooled = {case: value / weight_total for case, value in pooled.items()}
    years = inputs.get("years")
    if not isinstance(years, (list, tuple)) or not years:
        raise PeerMarginResearchError("explicit target forecast years required")
    output_years = []
    previous_year = None
    for row in years:
        if not isinstance(row, Mapping):
            raise PeerMarginResearchError("year mapping required")
        year = row.get("year")
        if isinstance(year, bool) or not isinstance(year, int) or year <= cutoff.year or (previous_year is not None and year != previous_year + 1):
            raise PeerMarginResearchError("consecutive future forecast years required")
        previous_year = year
        _text(row.get("rationale"), "year rationale")
        offset = adjustments(row.get("adjustments"), "year adjustments")
        margins = {case: pooled[case] + offset[case] for case in pooled}
        if margins["high"] > 1:
            raise PeerMarginResearchError("target operating margin exceeds revenue")
        output_years.append({"year": year, "low": str(margins["low"]), "base": str(margins["base"]), "high": str(margins["high"]), "unit": "ratio"})
    return {"schema_version": "peer-margin-proposal/v1", "kind": "inferred", "metric": metric,
            "target": inputs["target"], "segment": inputs["segment"], "economic_path_id": inputs["economic_path_id"],
            "as_of": inputs["as_of"], "inputs": deepcopy(_jsonable(inputs)), "input_sha256": _hash(inputs),
            "normalized_peers": normalized, "pooled_margin": _jsonable(pooled), "years": output_years,
            "limitations": "Peer comparability and adjustments are analyst assumptions; provenance metadata does not authenticate sources. Bounds are scenarios, not calibrated probabilities."}


def validate_peer_margin_receipt(receipt: Mapping[str, Any], year: int, case: str, value: Any,
                                 unit: str = "ratio", economic_path_id: str | None = None) -> dict[str, Any]:
    """Recompute a receipt and bind a selected annual margin to a consumer value."""
    if not isinstance(receipt, Mapping) or receipt.get("schema_version") != "peer-margin-proposal/v1":
        raise PeerMarginResearchError("peer-margin-proposal/v1 receipt required")
    expected = build_peer_margin_proposal(receipt.get("inputs"))
    if dict(receipt) != expected:
        raise PeerMarginResearchError("peer margin receipt differs from recomputed inputs")
    if case not in {"low", "base", "high"} or unit != "ratio":
        raise PeerMarginResearchError("margin case/unit mismatch")
    if isinstance(year, bool) or not isinstance(year, int):
        raise PeerMarginResearchError("forecast year must be integer")
    if economic_path_id is not None and economic_path_id != expected["economic_path_id"]:
        raise PeerMarginResearchError("economic path mismatch")
    row = next((row for row in expected["years"] if row["year"] == year), None)
    if row is None or _number(value, "value") != Decimal(row[case]):
        raise PeerMarginResearchError("margin value or forecast year mismatch")
    return expected
