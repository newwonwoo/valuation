from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from .ledger import EvidenceLedger
from .report_localization import identifier_label_ko, metric_label_ko
from .records import MarketObservation
from .street import StreetResearchReport


_SENSITIVE_QUERY_KEYS = {
    "api_key",
    "apikey",
    "crtfc_key",
    "access_token",
    "refresh_token",
    "token",
    "password",
    "secret",
}


@dataclass(frozen=True)
class SourceLink:
    url: str
    labels: tuple[str, ...]
    coverage: tuple[str, ...]


def canonical_verification_url(source_ref: str) -> str | None:
    candidate = source_ref.strip()
    parts = urlsplit(candidate)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.netloc
        or parts.username is not None
        or parts.password is not None
    ):
        return None
    query_keys = {key.casefold() for key, _ in parse_qsl(parts.query)}
    if query_keys.intersection(_SENSITIVE_QUERY_KEYS):
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def build_source_link_index(
    data: dict[str, object],
    *,
    require_all_http: bool,
) -> tuple[SourceLink, ...]:
    grouped: dict[str, dict[str, set[str]]] = {}
    invalid: list[str] = []

    def add(source_ref: object, *, label: str, coverage: str) -> None:
        if not isinstance(source_ref, str) or not source_ref.strip():
            invalid.append(f"{label}:MISSING")
            return
        url = canonical_verification_url(source_ref)
        if url is None:
            invalid.append(f"{label}:NON_HTTP_OR_SENSITIVE")
            return
        bucket = grouped.setdefault(url, {"labels": set(), "coverage": set()})
        bucket["labels"].add(label)
        bucket["coverage"].add(coverage)

    ledger = data.get("evidence_ledger")
    active_evidence = ledger.active() if isinstance(ledger, EvidenceLedger) else ()
    for record in active_evidence:
        add(
            record.source_ref,
            label=record.source_name,
            coverage=(
                f"근거 {record.id}: {record.metric} "
                f"(기준일 {record.effective_date})"
            ),
        )

    identity_refs = data.get("company_resolution_source_refs", ())
    if isinstance(identity_refs, tuple):
        for source_ref in identity_refs:
            add(
                source_ref,
                label="기업 식별정보",
                coverage="기업 식별 확인",
            )

    for key, label, coverage_label in (
        ("beta_source_refs", "베타 입력값", "베타 입력 출처"),
        ("wacc_source_refs", "가중평균자본비용 입력값", "가중평균자본비용 입력 출처"),
        ("per_source_refs", "주가수익비율 입력값", "주가수익비율 입력 출처"),
    ):
        refs = data.get(key, ())
        if isinstance(refs, tuple):
            for source_ref in refs:
                add(source_ref, label=label, coverage=coverage_label)

    broker_result = data.get("broker_research_prefreeze_result")
    broker_refs = getattr(broker_result, "source_refs", ())
    if isinstance(broker_refs, tuple):
        for source_ref in broker_refs:
            add(
                source_ref,
                label="증권사 자료 탐색",
                coverage="내재가치 고정 전 사실 탐색·교차검증 전용",
            )

    street_reports = data.get("street_reports", ())
    if isinstance(street_reports, tuple):
        for report in street_reports:
            if isinstance(report, StreetResearchReport):
                add(
                    report.source_ref,
                    label=f"증권사: {report.broker}",
                    coverage=(
                        f"목표가 발표일 {report.published_date[:10]}"
                    ),
                )

    market = data.get("market_observation")
    if isinstance(market, MarketObservation):
        add(
            market.source_ref,
            label="현재 시장가격",
            coverage=f"시장가격 기준일 {market.as_of}",
        )

    if require_all_http:
        if not active_evidence:
            invalid.append("EvidenceLedger:NO_ACTIVE_EVIDENCE")
        if invalid:
            raise ValueError(
                "direct source-link contract failed: " + ", ".join(sorted(invalid))
            )
        if not grouped:
            raise ValueError("direct source-link contract produced no HTTP(S) links")

    return tuple(
        SourceLink(
            url=url,
            labels=tuple(sorted(bucket["labels"])),
            coverage=tuple(sorted(bucket["coverage"])),
        )
        for url, bucket in sorted(grouped.items())
    )


def render_source_link_section(links: tuple[SourceLink, ...]) -> tuple[str, ...]:
    lines = ["## 정보 출처 — 원문 직접 검증"]
    if not links:
        lines.append(
            "- 검증 가능한 HTTP(S) 원문 링크가 없습니다. 이 실행은 독립 검증용으로 사용할 수 없습니다."
        )
        return tuple(lines)
    for item in links:
        evidence_rows = tuple(
            row for row in item.coverage if row.startswith("근거 ")
        )
        other_rows = tuple(
            row for row in item.coverage if not row.startswith("근거 ")
        )
        if len(evidence_rows) > 6:
            metrics = tuple(dict.fromkeys(
                metric_label_ko(
                    row.split(": ", 1)[1].split(" (기준일 ", 1)[0]
                )
                for row in evidence_rows
            ))
            dates = tuple(
                sorted(
                    {
                        row.rsplit(" (기준일 ", 1)[1].rstrip(")")
                        for row in evidence_rows
                    }
                )
            )
            evidence_summary = (
                f"근거 {len(evidence_rows)}개: {', '.join(metrics[:6])}"
                + (f" 외 {len(metrics) - 6}개 유형" if len(metrics) > 6 else "")
                + f" (기준일 {', '.join(dates)})"
            )
            coverage = (evidence_summary, *other_rows)
        else:
            coverage = tuple(dict.fromkeys(
                (
                    f"{metric_label_ko(row.split(': ', 1)[1].split(' (기준일 ', 1)[0])} "
                    f"(기준일 {row.rsplit(' (기준일 ', 1)[1].rstrip(')')})"
                )
                if row.startswith("근거 ")
                else row
                for row in item.coverage
            ))
        lines.append(
            f"- **{' / '.join(identifier_label_ko(label) for label in item.labels)}** — {'; '.join(coverage)} "
            f"[원문 바로 열기]({item.url})"
        )
    lines.append(
        "- 전체 근거 식별자·지표·기준일 매핑은 동일 실행의 불변 근거 원장에 보존됩니다."
    )
    return tuple(lines)


def linked_evidence_ids(
    data: dict[str, object],
    evidence_ids: tuple[str, ...],
) -> str:
    ledger = data.get("evidence_ledger")
    if not isinstance(ledger, EvidenceLedger):
        return ", ".join(evidence_ids)
    linked: list[str] = []
    for evidence_id in evidence_ids:
        try:
            record = ledger.get(evidence_id)
        except ValueError:
            linked.append(evidence_id)
            continue
        url = canonical_verification_url(record.source_ref)
        linked.append(f"[{evidence_id}]({url})" if url is not None else evidence_id)
    return ", ".join(linked)
