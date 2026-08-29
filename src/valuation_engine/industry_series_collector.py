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
- **Knowledge-time discipline.** The newest observation at or before the run's
  ``as_of`` is selected; period strings (YYYY / YYYYMM / YYYYMMDD) resolve to
  period-end effective dates, and the observation date is the run cutoff.
- **Credentials never leak.** The fetch URL is rendered from a template with
  an environment credential; the Evidence ``source_ref`` carries the redacted
  form.
"""

from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
import json
import os
from typing import Callable, Mapping

import yaml

from .collection_plan import CollectorCapability
from .evidence_collection import EvidenceCollectionBatch, EvidenceCollectionRequest
from .live_indexers import parse_json_response, parse_kosis_series_values
from .live_runtime import LiveCollectorProvider
from .records import EvidenceRecord, EvidenceSourceLayer
from .source_index import stable_hash


_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SERIES_REGISTRY_PATH = _REPO_ROOT / "config" / "kr_industry_series_registry.yaml"

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
        return self.url_template.replace("{api_key}", "[CREDENTIAL]")


def load_industry_series_registry(
    path: str | Path = DEFAULT_SERIES_REGISTRY_PATH,
) -> tuple[IndustrySeriesSpec, ...]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise IndustrySeriesError("industry series registry must be a mapping")
    rows = payload.get("series")
    if rows is None:
        raise IndustrySeriesError("industry series registry requires a series list")
    specs = []
    for row in rows or ():
        if not isinstance(row, Mapping):
            raise IndustrySeriesError("series row must be a mapping")
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
):
    """EvidenceCollector serving one source's verified industry series.

    Each requested metric resolves to its verified series, fetches, and takes
    the newest observation at or before ``as_of``. A series with no observation
    inside the cutoff is a coverage gap, reported by name downstream; a fetch
    or parse failure raises and blocks, because a broken source is not a gap.
    """
    if not segment_id:
        raise IndustrySeriesError("segment_id is required")
    cutoff = as_of[:10]
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
            rows = parse_json_response(fetch_text(spec.fetch_url()))
            observations = parse_kosis_series_values(rows)
            fingerprints.append(stable_hash([spec.series_id, list(observations)]))
            in_window = [
                (period, value)
                for period, value in observations
                if _period_end(period) <= cutoff
            ]
            if not in_window:
                continue  # nothing knowable at the cutoff: a named gap downstream
            period, value = in_window[-1]
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
                    observed_date=cutoff,
                    source_name=f"{spec.source_id} series {spec.series_id}",
                    source_ref=spec.display_ref,
                    source_grade="A",
                    confidence=0.9,
                    segment=segment_id,
                    notes=(
                        f"definition_id={spec.definition_id}; geography={spec.geography}; "
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
