from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
import yaml

from valuation_engine.source_index import (
    parse_iea_data_product_metadata,
    parse_kiet_release_listing,
    parse_kisdi_report_metadata,
)
from valuation_engine.source_watch import EndpointObservation, EndpointRole, reconcile_endpoint_observations

ROOT = Path(__file__).resolve().parents[1]
probe_doc = yaml.safe_load((ROOT / "data/source_probe_fixtures.yaml").read_text(encoding="utf-8"))
baseline_doc = yaml.safe_load((ROOT / "data/source_watch_baseline.yaml").read_text(encoding="utf-8"))
watch_doc = yaml.safe_load((ROOT / "config/source_watch_registry.yaml").read_text(encoding="utf-8"))

baseline = {x["series_id"]: x for x in baseline_doc["series"]}
rules = {x["series_id"]: x for x in watch_doc["sources"]}
probes_by_series: dict[str, list[dict]] = defaultdict(list)
for probe in probe_doc["probes"]:
    probes_by_series[probe["series_id"]].append(probe)

rows = []
for series_id, rule in sorted(rules.items()):
    base = baseline.get(series_id)
    probes = probes_by_series.get(series_id, [])
    row = {
        "series_id": series_id,
        "source_id": rule["source_id"],
        "cadence": rule["cadence"],
        "check_frequency": rule["check_frequency"],
        "baseline_latest_published_at": base.get("latest_published_at") if base else None,
        "probe_state": "baseline_only",
        "operational_warning": None,
        "freshest_probe_published_at": None,
        "impact_nodes": rule.get("impact_nodes", []),
    }
    if probes:
        row["probe_state"] = "fixture_verified"
        if series_id == "KIET_PSI":
            records = []
            for p in probes:
                records.extend(parse_kiet_release_listing(p["observed_text"]))
            freshest = max((r.published_at for r in records if r.published_at), default=None)
            row["freshest_probe_published_at"] = freshest.isoformat() if freshest else None
        elif series_id == "KISDI_ICT_MEDIUM_TERM":
            records = [parse_kisdi_report_metadata(p["observed_text"], url="https://www.kisdi.re.kr/") for p in probes]
            freshest = max((r.published_at for r in records if r.published_at), default=None)
            row["freshest_probe_published_at"] = freshest.isoformat() if freshest else None
        elif series_id == "IEA_MONTHLY_ELECTRICITY":
            obs = []
            for p in probes:
                meta = parse_iea_data_product_metadata(p["observed_text"])
                freshest = meta.latest_file_updated or meta.last_updated
                role = EndpointRole.DATA_EXPLORER if p["endpoint_id"].endswith("tool") else EndpointRole.PRIMARY_INDEX
                obs.append(EndpointObservation(p["endpoint_id"], role, True, freshest, p["endpoint_id"]))
            rec = reconcile_endpoint_observations(tuple(obs))
            row["freshest_probe_published_at"] = rec.resolved_latest_published_at.isoformat() if rec.resolved_latest_published_at else None
            if rec.divergent:
                row["operational_warning"] = rec.warning
        base_date = base.get("latest_published_at") if base else None
        probe_date = row["freshest_probe_published_at"]
        if base_date and probe_date:
            if probe_date > base_date:
                row["freshness_result"] = "newer_than_baseline_requires_review"
            elif probe_date == base_date:
                row["freshness_result"] = "matches_baseline"
            else:
                row["freshness_result"] = "probe_older_than_baseline_do_not_downgrade"
    if series_id == "KAMA_PRODUCTION_STATS_MEMBER":
        row["access_posture"] = "metadata_only_public_probe; raw statistics require licensed/member access"
    rows.append(row)

summary = {
    "total_series": len(rows),
    "fixture_verified_series": sum(r["probe_state"] == "fixture_verified" for r in rows),
    "baseline_only_series": sum(r["probe_state"] == "baseline_only" for r in rows),
    "endpoint_divergence_warnings": sum(bool(r["operational_warning"]) for r in rows),
    "note": "This is an offline fixture/baseline QA report, not a production live-fetch freshness attestation.",
}
out = {
    "snapshot_version": "2026-08-21.1",
    "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    "summary": summary,
    "series": rows,
}
(ROOT / "data/source_watch_status_snapshot.yaml").write_text(
    yaml.safe_dump(out, allow_unicode=True, sort_keys=False), encoding="utf-8"
)
print(f"WROTE watch_status total={summary['total_series']} fixture_verified={summary['fixture_verified_series']} warnings={summary['endpoint_divergence_warnings']}")
