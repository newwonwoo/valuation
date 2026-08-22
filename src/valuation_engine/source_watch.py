from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum


class WatchStatus(str, Enum):
    CLEAN = "clean"
    NEW_RELEASE = "new_release"
    REVISION = "revision"
    DEFINITION_CHANGE = "definition_change"
    SCHEMA_CHANGE = "schema_change"
    EXPECTED_RELEASE_MISSED = "expected_release_missed"
    SOURCE_FAILURE = "source_failure"
    UPDATED_NOT_REVIEWED = "updated_not_reviewed"
    REVALIDATION_REQUIRED = "revalidation_required"
    MATERIAL_CHANGE = "material_change"


@dataclass(frozen=True)
class SourceSnapshot:
    series_id: str
    checked_at: date
    latest_document_id: str | None
    latest_published_at: date | None
    document_hash: str | None
    fact_hash: str | None
    definition_hash: str | None
    schema_hash: str | None
    fetch_ok: bool = True
    revision_of_document_id: str | None = None


@dataclass(frozen=True)
class WatchRule:
    series_id: str
    grace_days: int
    impact_nodes: tuple[str, ...]
    next_expected_release: date | None = None


@dataclass(frozen=True)
class WatchFinding:
    status: WatchStatus
    series_id: str
    reason: str
    dirty_nodes: tuple[str, ...]
    blocks_automatic_promotion: bool


def _changed(old: str | None, new: str | None) -> bool:
    return old is not None and new is not None and old != new


def detect_source_update(
    previous: SourceSnapshot | None,
    current: SourceSnapshot,
    rule: WatchRule,
    *,
    today: date | None = None,
) -> WatchFinding:
    today = today or current.checked_at
    if not current.fetch_ok:
        return WatchFinding(WatchStatus.SOURCE_FAILURE, rule.series_id, "source fetch failed", rule.impact_nodes, True)

    if previous is None:
        return WatchFinding(WatchStatus.UPDATED_NOT_REVIEWED, rule.series_id, "initial snapshot requires review", rule.impact_nodes, True)

    if _changed(previous.schema_hash, current.schema_hash):
        return WatchFinding(WatchStatus.SCHEMA_CHANGE, rule.series_id, "source schema changed", rule.impact_nodes, True)
    if _changed(previous.definition_hash, current.definition_hash):
        return WatchFinding(WatchStatus.DEFINITION_CHANGE, rule.series_id, "metric definition changed", rule.impact_nodes, True)

    new_document = (
        current.latest_document_id is not None
        and current.latest_document_id != previous.latest_document_id
        and current.latest_published_at is not None
        and (previous.latest_published_at is None or current.latest_published_at >= previous.latest_published_at)
    )
    if new_document:
        return WatchFinding(WatchStatus.NEW_RELEASE, rule.series_id, "new publication/vintage detected", rule.impact_nodes, False)

    if current.revision_of_document_id or _changed(previous.fact_hash, current.fact_hash):
        return WatchFinding(WatchStatus.REVISION, rule.series_id, "existing-period facts changed", rule.impact_nodes, True)

    if _changed(previous.document_hash, current.document_hash):
        return WatchFinding(WatchStatus.UPDATED_NOT_REVIEWED, rule.series_id, "document changed without classified fact/definition change", rule.impact_nodes, True)

    if rule.next_expected_release is not None:
        deadline = rule.next_expected_release + timedelta(days=rule.grace_days)
        no_release_after_expected = (
            current.latest_published_at is None or current.latest_published_at < rule.next_expected_release
        )
        if today > deadline and no_release_after_expected:
            return WatchFinding(
                WatchStatus.EXPECTED_RELEASE_MISSED,
                rule.series_id,
                f"expected release {rule.next_expected_release.isoformat()} exceeded grace window",
                rule.impact_nodes,
                False,
            )

    return WatchFinding(WatchStatus.CLEAN, rule.series_id, "no material source change detected", (), False)


def requires_revalidation(finding: WatchFinding) -> bool:
    return finding.status in {
        WatchStatus.NEW_RELEASE,
        WatchStatus.REVISION,
        WatchStatus.DEFINITION_CHANGE,
        WatchStatus.SCHEMA_CHANGE,
        WatchStatus.UPDATED_NOT_REVIEWED,
        WatchStatus.MATERIAL_CHANGE,
    }


class EndpointRole(str, Enum):
    PRIMARY_INDEX = "primary_index"
    DATA_EXPLORER = "data_explorer"
    DOWNLOAD_INDEX = "download_index"
    SCHEDULE = "schedule"
    API = "api"


@dataclass(frozen=True)
class EndpointObservation:
    endpoint_id: str
    role: EndpointRole
    fetch_ok: bool
    latest_published_at: date | None = None
    latest_document_id: str | None = None
    schema_hash: str | None = None


@dataclass(frozen=True)
class EndpointReconciliation:
    resolved_latest_published_at: date | None
    resolved_latest_document_id: str | None
    healthy_endpoints: int
    divergent: bool
    warning: str | None


def reconcile_endpoint_observations(observations: tuple[EndpointObservation, ...]) -> EndpointReconciliation:
    healthy = tuple(x for x in observations if x.fetch_ok)
    if not healthy:
        return EndpointReconciliation(None, None, 0, False, "all endpoints failed")
    dated = tuple(x for x in healthy if x.latest_published_at is not None)
    if not dated:
        return EndpointReconciliation(None, None, len(healthy), False, "no endpoint exposed a publication date")
    winner = max(dated, key=lambda x: x.latest_published_at)
    distinct_dates = {x.latest_published_at for x in dated}
    divergent = len(distinct_dates) > 1
    warning = None
    if divergent:
        warning = "source endpoints disagree on latest vintage; freshest healthy endpoint retained for freshness only"
    return EndpointReconciliation(
        winner.latest_published_at,
        winner.latest_document_id,
        len(healthy),
        divergent,
        warning,
    )


def missed_release_after_reconciliation(
    reconciliation: EndpointReconciliation,
    rule: WatchRule,
    *,
    today: date,
) -> bool:
    if rule.next_expected_release is None:
        return False
    deadline = rule.next_expected_release + timedelta(days=rule.grace_days)
    if today <= deadline:
        return False
    return (
        reconciliation.resolved_latest_published_at is None
        or reconciliation.resolved_latest_published_at < rule.next_expected_release
    )
