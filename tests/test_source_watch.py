from datetime import date

from valuation_engine.source_watch import (
    SourceSnapshot, WatchRule, WatchStatus, detect_source_update, requires_revalidation
)


def snap(doc="d1", pub=date(2026,7,20), dh="doc1", fh="fact1", de="def1", sc="schema1", ok=True, rev=None):
    return SourceSnapshot("series", date(2026,8,21), doc, pub, dh, fh, de, sc, ok, rev)


def rule(next_release=None, grace=5):
    return WatchRule("series", grace, ("power.grid",), next_release)


def test_new_release_dirties_impacted_nodes():
    f = detect_source_update(snap(), snap(doc="d2", pub=date(2026,8,17), dh="doc2", fh="fact2"), rule())
    assert f.status is WatchStatus.NEW_RELEASE
    assert f.dirty_nodes == ("power.grid",)
    assert requires_revalidation(f)


def test_definition_change_blocks_automatic_promotion():
    f = detect_source_update(snap(), snap(de="def2"), rule())
    assert f.status is WatchStatus.DEFINITION_CHANGE
    assert f.blocks_automatic_promotion


def test_schema_change_is_separate_from_fact_revision():
    f = detect_source_update(snap(), snap(sc="schema2", fh="fact2"), rule())
    assert f.status is WatchStatus.SCHEMA_CHANGE


def test_fact_change_same_document_is_revision():
    f = detect_source_update(snap(), snap(fh="fact2"), rule())
    assert f.status is WatchStatus.REVISION


def test_expected_release_missed_only_after_grace_window():
    previous = snap()
    current = snap()
    f = detect_source_update(previous, current, rule(date(2026,8,17), grace=2), today=date(2026,8,21))
    assert f.status is WatchStatus.EXPECTED_RELEASE_MISSED
    assert not requires_revalidation(f)


def test_source_failure_is_explicit():
    f = detect_source_update(snap(), snap(ok=False), rule())
    assert f.status is WatchStatus.SOURCE_FAILURE
    assert f.blocks_automatic_promotion


def test_endpoint_reconciliation_prevents_false_missed_release():
    from valuation_engine.source_watch import (
        EndpointObservation, EndpointRole, reconcile_endpoint_observations, missed_release_after_reconciliation
    )
    observations = (
        EndpointObservation("product_page", EndpointRole.PRIMARY_INDEX, True, date(2026,7,20), "jul"),
        EndpointObservation("data_explorer", EndpointRole.DATA_EXPLORER, True, date(2026,8,17), "aug"),
    )
    r = reconcile_endpoint_observations(observations)
    assert r.divergent
    assert r.resolved_latest_published_at == date(2026,8,17)
    assert not missed_release_after_reconciliation(r, rule(date(2026,8,17), grace=2), today=date(2026,8,21))
