"""Company-neutral collector for industry data series (KOSIS-style JSON APIs).

The cold-start probe's boundary named the market-side metrics no collector
serves: benchmark prices, industry inventories, spread inputs. Those live in
the official-statistics series the industry source registry researched
(KOSIS, MOTIE, KEEI, …). This collector is the bridge from those series to
Evidence — under the ingestion doctrine's definition gate, enforced
structurally rather than by convention:

- **A series may only serve an industry-observable metric.** Company-realized
  metrics — realized_price, cash_cost, asp, unit_cost, production, orders,
  backlog, utilization, plant_runs, turnaround, product_yield — are refused at
  registry load: an industry average masquerading as a company's own number is
  exactly the conflation the normalization gate forbids (benchmark vs realized
  is never an averaging problem, it is a different claim).
- **Every series carries its definition.** ``definition_id`` and a human
  definition ride into the Evidence notes, so a downstream reader knows which
  economic quantity the number is, not just its metric key.
- **Only operator-verified series collect.** A registry row ships with
  ``verified: false`` until a human has checked the series against the source
  catalog (table identity, unit, cadence). The collector refuses unverified
  rows — a guessed table id is a fabricated source. The default registry
  therefore ships with zero verified production rows and the capability
  reflects only what is verified.
- **Knowledge-time discipline.** A verified registry row points to a persisted
  timestamped snapshot, never the source's current mutable response. Snapshot
  observations must carry publication, first-seen and revision timestamps. The
  newest observation whose period and all three knowledge timestamps are at or
  before the run's ``as_of`` is selected; period strings (YYYY / YYYYMM /
  YYYYMMDD) resolve to period-end effective dates, and the Evidence observation
  date is the first-seen date.
- **Credentials never leak.** Only the operator verifier contacts the mutable
  endpoint with an environment credential. Runtime collection reads the frozen
  snapshot, and Evidence links to a separate credential-free verification URL.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import json
import os
from typing import Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import yaml

from .collection_plan import CollectorCapability
from .evidence_collection import EvidenceCollectionBatch, EvidenceCollectionRequest
from .live_indexers import parse_kosis_series_values
from .live_runtime import LiveCollectorProvider
from .records import EvidenceRecord, EvidenceSourceLayer
from .runtime_resources import runtime_registry_path
from .source_index import stable_hash
from .source_reporting import canonical_verification_url


DEFAULT_SERIES_REGISTRY_PATH = runtime_registry_path(
    "kr_industry_series_registry.yaml"
)

#: Metrics an industry-wide series may legitimately observe. Everything else is
#: a company-realized quantity and must come from the company's own filings,
#: IR, or underwriting — never from an industry average wearing its name.
INDUSTRY_OBSERVABLE_METRICS = frozenset(
    {
        "benchmark_price",
        "input_price",
        "output_price",
        "inventory",
        "trade",
        "trade_value",
        "trade_volume",
        "shipments",
        "demand",
        "fuel_prices",
        "energy_balance",
    }
)

_ALLOWED_LAYERS = {
    "realized_or_filing": EvidenceSourceLayer.REALIZED_OR_FILING,
    "authorized_market_data": EvidenceSourceLayer.AUTHORIZED_MARKET_DATA,
    "policy_primary_source": EvidenceSourceLayer.POLICY_PRIMARY_SOURCE,
}

FetchText = Callable[[str], str]
SnapshotText = Callable[[str], str]

SERIES_SNAPSHOT_SCHEMA = "industry-series-snapshot/v1"


class IndustrySeriesError(ValueError):
    """Raised when the series registry or a fetch violates its contract."""


@dataclass(frozen=True)
class IndustrySeriesSpec:
    series_id: str
    source_id: str
    metric: str
    layer: str
    unit: str
    geography: str
    definition_id: str
    definition: str
    url_template: str
    api_key_env: str
    verified: bool
    published_at_field: str = "PUBLISHED_AT"
    first_seen_at_field: str = "FIRST_SEEN_AT"
    revision_at_field: str = "REVISION_AT"
    snapshot_path: str = ""
    verification_url: str = ""

    def validate(self) -> None:
        required = (
            self.series_id,
            self.source_id,
            self.metric,
            self.layer,
            self.unit,
            self.geography,
            self.definition_id,
            self.definition,
            self.url_template,
            self.published_at_field,
            self.first_seen_at_field,
            self.revision_at_field,
        )
        if not all(required):
            raise IndustrySeriesError(
                f"industry series {self.series_id or '?'} is missing required fields"
            )
        if self.metric not in INDUSTRY_OBSERVABLE_METRICS:
            raise IndustrySeriesError(
                f"series {self.series_id} claims metric {self.metric!r}, which is a "
                "company-realized quantity; an industry series may not serve it "
                "(definition gate)"
            )
        if self.layer not in _ALLOWED_LAYERS:
            raise IndustrySeriesError(
                f"series {self.series_id} declares unsupported layer {self.layer!r}"
            )
        if len(self.definition.strip()) < 20:
            raise IndustrySeriesError(
                f"series {self.series_id} requires a real definition, not a label"
            )
        if "{api_key}" in self.url_template and not self.api_key_env:
            raise IndustrySeriesError(
                f"series {self.series_id} template needs api_key_env for its credential"
            )
        if self.verified and not self.snapshot_path:
            raise IndustrySeriesError(
                f"verified series {self.series_id} requires a persisted snapshot_path"
            )
        if self.verified and canonical_verification_url(self.verification_url) is None:
            raise IndustrySeriesError(
                f"verified series {self.series_id} requires a credential-free HTTP(S) "
                "verification_url"
            )

    @property
    def source_layer(self) -> EvidenceSourceLayer:
        return _ALLOWED_LAYERS[self.layer]

    def fetch_url(self) -> str:
        if "{api_key}" not in self.url_template:
            return self.url_template
        key = os.environ.get(self.api_key_env, "")
        if not key:
            raise IndustrySeriesError(
                f"series {self.series_id} requires credential {self.api_key_env}"
            )
        return self.url_template.replace("{api_key}", key)

    @property
    def display_ref(self) -> str:
        return self.verification_url


def load_industry_series_registry(
    path: str | Path = DEFAULT_SERIES_REGISTRY_PATH,
) -> tuple[IndustrySeriesSpec, ...]:
    registry_path = Path(path)
    payload = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise IndustrySeriesError("industry series registry must be a mapping")
    rows = payload.get("series")
    if rows is None:
        raise IndustrySeriesError("industry series registry requires a series list")
    specs = []
    for row in rows or ():
        if not isinstance(row, Mapping):
            raise IndustrySeriesError("series row must be a mapping")
        snapshot_path = str(row.get("snapshot_path", ""))
        if snapshot_path and not Path(snapshot_path).is_absolute():
            snapshot_path = str((registry_path.parent / snapshot_path).resolve())
        spec = IndustrySeriesSpec(
            series_id=str(row.get("series_id", "")),
            source_id=str(row.get("source_id", "")),
            metric=str(row.get("metric", "")),
            layer=str(row.get("layer", "")),
            unit=str(row.get("unit", "")),
            geography=str(row.get("geography", "")),
            definition_id=str(row.get("definition_id", "")),
            definition=str(row.get("definition", "")),
            url_template=str(row.get("url_template", "")),
            api_key_env=str(row.get("api_key_env", "")),
            verified=bool(row.get("verified", False)),
            published_at_field=str(row.get("published_at_field", "PUBLISHED_AT")),
            first_seen_at_field=str(row.get("first_seen_at_field", "FIRST_SEEN_AT")),
            revision_at_field=str(row.get("revision_at_field", "REVISION_AT")),
            snapshot_path=snapshot_path,
            verification_url=str(row.get("verification_url", "")),
        )
        spec.validate()
        specs.append(spec)
    ids = tuple(item.series_id for item in specs)
    if len(ids) != len(set(ids)):
        raise IndustrySeriesError("industry series registry has duplicate series ids")
    metrics_per_source: dict[tuple[str, str], str] = {}
    for item in specs:
        key = (item.source_id, item.metric)
        if key in metrics_per_source and item.verified:
            raise IndustrySeriesError(
                f"source {item.source_id} maps metric {item.metric} to more than one "
                "verified series; conflicting definitions are a scoped-split decision, "
                "never an average"
            )
        if item.verified:
            metrics_per_source[key] = item.series_id
    return tuple(specs)


def _period_end(period: str) -> str:
    if len(period) == 4:
        return f"{period}-12-31"
    if len(period) == 6:
        year, month = int(period[:4]), int(period[4:6])
        return date(year, month, monthrange(year, month)[1]).isoformat()
    if len(period) == 8:
        return f"{period[:4]}-{period[4:6]}-{period[6:8]}"
    raise IndustrySeriesError(f"unsupported series period: {period!r}")


def _knowledge_timestamp(
    row: Mapping[str, object],
    *,
    field: str,
    label: str,
    series_id: str,
) -> datetime:
    raw = str(row.get(field) or "").strip()
    if not raw:
        raise IndustrySeriesError(
            f"verified series {series_id} row is missing required {label} field {field}"
        )
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IndustrySeriesError(
            f"verified series {series_id} row has invalid {label} timestamp {raw!r}"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IndustrySeriesError(
            f"verified series {series_id} {label} timestamp must include a timezone"
        )
    return parsed


def credential_free_verification_url(url: str) -> str:
    """Remove credential-bearing query parameters from an operator URL.

    The returned URL is a report reference only; collection consumes the frozen
    snapshot. An explicitly supplied public catalog URL remains preferable, but
    this conservative transformation prevents placeholders such as
    ``apiKey={api_key}`` from violating the live report source-link contract.
    """
    parts = urlsplit(url.strip())
    safe_query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if "{api_key}" not in value.casefold()
        and key.casefold()
        not in {
            "api_key",
            "apikey",
            "auth",
            "authorization",
            "access_token",
            "token",
            "password",
            "secret",
        }
    ]
    candidate = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(safe_query), "")
    )
    if canonical_verification_url(candidate) is None:
        raise IndustrySeriesError(
            "industry series verification URL must be credential-free HTTP(S)"
        )
    return candidate


def _load_frozen_series_snapshot(
    spec: IndustrySeriesSpec,
    *,
    read_snapshot: SnapshotText,
) -> tuple[list[dict], Mapping[str, object]]:
    try:
        payload = json.loads(read_snapshot(spec.snapshot_path))
    except (OSError, json.JSONDecodeError) as exc:
        raise IndustrySeriesError(
            f"unable to load frozen snapshot for verified series {spec.series_id}: {exc}"
        ) from exc
    if not isinstance(payload, Mapping):
        raise IndustrySeriesError(
            f"frozen snapshot for {spec.series_id} must be a JSON object"
        )
    if payload.get("schema_version") != SERIES_SNAPSHOT_SCHEMA:
        raise IndustrySeriesError(
            f"frozen snapshot for {spec.series_id} has unsupported schema"
        )
    if (
        payload.get("series_id") != spec.series_id
        or payload.get("source_id") != spec.source_id
    ):
        raise IndustrySeriesError(
            f"frozen snapshot identity does not match verified series {spec.series_id}"
        )
    if payload.get("verification_url") != spec.verification_url:
        raise IndustrySeriesError(
            f"frozen snapshot verification URL does not match series {spec.series_id}"
        )
    rows = payload.get("observations")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise IndustrySeriesError(
            f"frozen snapshot for {spec.series_id} requires observation objects"
        )
    return rows, payload


def _json_safe(amount: Decimal) -> int | str:
    integral = amount.to_integral_value()
    return int(integral) if amount == integral else format(amount, "f")


def request_scoped_industry_series_collector(
    fetch_text: FetchText,
    *,
    source_id: str,
    as_of: str,
    segment_id: str,
    series: tuple[IndustrySeriesSpec, ...],
    snapshot_text: SnapshotText | None = None,
):
    """EvidenceCollector serving one source's verified industry series.

    Each requested metric resolves to its verified frozen snapshot and takes
    the newest observation whose period, publication, first-seen and revision
    timestamps are at or before ``as_of``. A series with no observation inside
    the cutoff is a coverage gap, reported by name downstream; a fetch, parse or
    required knowledge-time metadata failure raises and blocks, because a broken
    source is not a gap. ``fetch_text`` remains in the provider signature for
    compatibility but is deliberately not used for verified series; only the
    operator snapshot workflow may contact the mutable upstream endpoint.
    """
    if not segment_id:
        raise IndustrySeriesError("segment_id is required")
    del fetch_text
    cutoff = as_of[:10]
    read_snapshot = snapshot_text or (
        lambda snapshot_path: Path(snapshot_path).read_text(encoding="utf-8")
    )
    usable = {
        item.metric: item
        for item in series
        if item.source_id == source_id and item.verified
    }
    if not usable:
        raise IndustrySeriesError(
            f"no verified series for source {source_id}; refusing to declare an "
            "empty capability"
        )

    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        unsupported = tuple(sorted(set(request.required_metrics) - set(usable)))
        if unsupported:
            raise IndustrySeriesError(
                f"industry series collector {source_id} received metrics outside "
                "its verified capability: " + ", ".join(unsupported)
            )
        records: list[EvidenceRecord] = []
        fingerprints: list[str] = []
        for metric in request.required_metrics:
            spec = usable[metric]
            spec.validate()
            rows, snapshot_payload = _load_frozen_series_snapshot(
                spec, read_snapshot=read_snapshot
            )
            fingerprints.append(stable_hash(snapshot_payload))
            eligible_by_period: dict[
                str, tuple[str, str, datetime, datetime, datetime]
            ] = {}
            seen_versions: set[tuple[str, datetime, datetime, datetime]] = set()
            for row in rows:
                observations = parse_kosis_series_values([row])
                if not observations:
                    continue
                period, value = observations[0]
                if _period_end(period) > cutoff:
                    continue
                published_at = _knowledge_timestamp(
                    row,
                    field=spec.published_at_field,
                    label="published_at",
                    series_id=spec.series_id,
                )
                first_seen_at = _knowledge_timestamp(
                    row,
                    field=spec.first_seen_at_field,
                    label="first_seen_at",
                    series_id=spec.series_id,
                )
                revision_at = _knowledge_timestamp(
                    row,
                    field=spec.revision_at_field,
                    label="revision_at",
                    series_id=spec.series_id,
                )
                if revision_at < published_at:
                    raise IndustrySeriesError(
                        f"verified series {spec.series_id} revision_at cannot precede "
                        "published_at"
                    )
                if first_seen_at < published_at or first_seen_at < revision_at:
                    raise IndustrySeriesError(
                        f"verified series {spec.series_id} first_seen_at cannot precede "
                        "published_at or revision_at"
                    )
                if any(
                    timestamp.date().isoformat() > cutoff
                    for timestamp in (published_at, first_seen_at, revision_at)
                ):
                    continue
                version_identity = (
                    period, published_at, first_seen_at, revision_at
                )
                if version_identity in seen_versions:
                    raise IndustrySeriesError(
                        f"verified series {spec.series_id} has duplicate eligible "
                        f"revision identity for period {period}"
                    )
                seen_versions.add(version_identity)
                candidate = (
                    period, value, published_at, first_seen_at, revision_at
                )
                current = eligible_by_period.get(period)
                if current is None or (
                    revision_at, first_seen_at, published_at
                ) > (current[4], current[3], current[2]):
                    eligible_by_period[period] = candidate
            if not eligible_by_period:
                continue  # nothing knowable at the cutoff: a named gap downstream
            period, value, published_at, first_seen_at, revision_at = max(
                eligible_by_period.values(), key=lambda item: item[0]
            )
            effective = _period_end(period)
            amount = Decimal(value)
            records.append(
                EvidenceRecord(
                    id=f"INDSER:{spec.series_id}:{period}",
                    target=request.target_id,
                    metric=metric,
                    value=_json_safe(amount),
                    unit=spec.unit,
                    source_layer=spec.source_layer,
                    effective_date=effective,
                    observed_date=first_seen_at.date().isoformat(),
                    source_name=f"{spec.source_id} series {spec.series_id}",
                    source_ref=spec.display_ref,
                    source_grade="A",
                    confidence=0.9,
                    segment=segment_id,
                    notes=(
                        f"definition_id={spec.definition_id}; geography={spec.geography}; "
                        f"published_at={published_at.isoformat()}; "
                        f"first_seen_at={first_seen_at.isoformat()}; "
                        f"revision_at={revision_at.isoformat()}; "
                        f"definition={spec.definition}"
                    ),
                )
            )
        batch = EvidenceCollectionBatch(
            source_id=source_id,
            checked_at=as_of,
            records=tuple(records),
            source_fingerprint=stable_hash(sorted(fingerprints)),
            document_ids=tuple(
                usable[m].series_id for m in request.required_metrics
            ),
        )
        batch.validate()
        return batch

    return collect


def industry_series_collector_providers(
    fetch_text: FetchText,
    *,
    as_of: str,
    segment_id: str,
    registry_path: str | Path = DEFAULT_SERIES_REGISTRY_PATH,
) -> tuple[LiveCollectorProvider, ...]:
    """One provider per source that has at least one verified series.

    An empty verified registry yields an empty tuple, never a provider with an
    invented capability — the cold run's coverage gap then stays truthful.
    """
    specs = load_industry_series_registry(registry_path)
    by_source: dict[str, list[IndustrySeriesSpec]] = {}
    for item in specs:
        if item.verified:
            by_source.setdefault(item.source_id, []).append(item)
    providers = []
    for source_id, rows in sorted(by_source.items()):
        providers.append(
            LiveCollectorProvider(
                capability=CollectorCapability(
                    collector_id=f"industry-series-{source_id.lower().replace('_','-')}",
                    source_id=source_id,
                    supported_metrics=tuple(
                        dict.fromkeys(item.metric for item in rows)
                    ),
                    jurisdictions=("KR",),
                    implementation_ref=(
                        "valuation_engine.industry_series_collector."
                        "request_scoped_industry_series_collector"
                    ),
                ),
                collector=request_scoped_industry_series_collector(
                    fetch_text,
                    source_id=source_id,
                    as_of=as_of,
                    segment_id=segment_id,
                    series=tuple(rows),
                ),
            )
        )
    return tuple(providers)
