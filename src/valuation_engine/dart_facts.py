from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Mapping, Sequence

from .evidence_collection import EvidenceCollectionBatch, EvidenceCollectionRequest, EvidenceCollector
from .records import EvidenceRecord, EvidenceSourceLayer


_REPORT_END_DATES = {
    "11013": (3, 31),   # Q1
    "11012": (6, 30),   # half-year
    "11014": (9, 30),   # Q3
    "11011": (12, 31),  # annual
}


@dataclass(frozen=True)
class DartFactMetricSpec:
    metric: str
    account_ids: tuple[str, ...]
    statement_divisions: tuple[str, ...]
    unit: str = "KRW"
    critical: bool = False

    def validate(self) -> None:
        if not self.metric or not self.account_ids or not self.statement_divisions or not self.unit:
            raise ValueError("DART metric spec requires metric, account_ids, statement divisions and unit")
        if len(self.account_ids) != len(set(self.account_ids)):
            raise ValueError(f"duplicate account_ids in DART metric spec {self.metric}")


DEFAULT_CORE_FACT_SPECS: tuple[DartFactMetricSpec, ...] = (
    DartFactMetricSpec(
        "revenue",
        ("ifrs-full_Revenue", "ifrs_Revenue"),
        ("IS", "CIS"),
        critical=True,
    ),
    DartFactMetricSpec(
        "operating_income",
        ("dart_OperatingIncomeLoss",),
        ("IS", "CIS"),
        critical=True,
    ),
    DartFactMetricSpec(
        "net_income",
        ("ifrs-full_ProfitLoss", "ifrs_ProfitLoss"),
        ("IS", "CIS"),
    ),
    DartFactMetricSpec(
        "total_assets",
        ("ifrs-full_Assets", "ifrs_Assets"),
        ("BS",),
    ),
    DartFactMetricSpec(
        "total_liabilities",
        ("ifrs-full_Liabilities", "ifrs_Liabilities"),
        ("BS",),
    ),
    DartFactMetricSpec(
        "total_equity",
        ("ifrs-full_Equity", "ifrs_Equity"),
        ("BS",),
    ),
    DartFactMetricSpec(
        "cash_and_cash_equivalents",
        ("ifrs-full_CashAndCashEquivalents", "ifrs_CashAndCashEquivalents"),
        ("BS",),
    ),
)


def _decimal_amount(value: object) -> Decimal:
    text = str(value or "").strip().replace(",", "")
    if not text or text == "-":
        raise ValueError("DART amount is blank")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"invalid DART amount: {value!r}") from exc
    return -amount if negative else amount


def _json_safe_amount(amount: Decimal) -> int | str:
    """Preserve exact DART precision while keeping Evidence snapshots JSON-serializable."""
    integral = amount.to_integral_value()
    return int(integral) if amount == integral else format(amount, "f")


def _effective_date(business_year: str, report_code: str) -> str:
    try:
        year = int(business_year)
        month, day = _REPORT_END_DATES[report_code]
    except (ValueError, KeyError) as exc:
        raise ValueError(f"unsupported DART business year/report code: {business_year}/{report_code}") from exc
    return date(year, month, day).isoformat()


def _select_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    spec: DartFactMetricSpec,
    fs_div: str,
) -> tuple[Mapping[str, object], ...]:
    spec.validate()
    accepted_accounts = set(spec.account_ids)
    accepted_statements = set(spec.statement_divisions)
    return tuple(
        row
        for row in rows
        if str(row.get("fs_div") or "") == fs_div
        and str(row.get("sj_div") or "") in accepted_statements
        and str(row.get("account_id") or "") in accepted_accounts
        and row.get("thstrm_amount") not in (None, "", "-")
    )


def parse_opendart_financial_facts(
    rows: Sequence[Mapping[str, object]],
    *,
    target_id: str,
    published_date: str,
    source_ref: str,
    specs: tuple[DartFactMetricSpec, ...] = DEFAULT_CORE_FACT_SPECS,
    fs_div: str = "CFS",
    segment: str = "company",
) -> tuple[EvidenceRecord, ...]:
    """Normalize OpenDART financial-statement rows into primary EvidenceRecords.

    Consolidated statements (CFS) are the default. No account-name fuzzy matching is used.
    Company-specific metrics such as contract liabilities require an explicit MetricSpec.
    Multiple materially different rows for one metric fail closed rather than being summed.
    """
    if not target_id or not source_ref:
        raise ValueError("target_id and source_ref are required")
    date.fromisoformat(published_date[:10])
    if fs_div not in {"CFS", "OFS"}:
        raise ValueError("fs_div must be CFS or OFS")

    records: list[EvidenceRecord] = []
    seen_metrics: set[str] = set()
    for spec in specs:
        if spec.metric in seen_metrics:
            raise ValueError(f"duplicate DART metric spec: {spec.metric}")
        seen_metrics.add(spec.metric)
        matches = _select_rows(rows, spec=spec, fs_div=fs_div)
        if not matches:
            continue

        candidates: list[tuple[Mapping[str, object], Decimal]] = []
        for row in matches:
            candidates.append((row, _decimal_amount(row.get("thstrm_amount"))))
        unique_amounts = {amount for _, amount in candidates}
        if len(unique_amounts) != 1:
            ids = ", ".join(str(row.get("account_id")) for row, _ in candidates)
            raise ValueError(
                f"ambiguous DART fact for {spec.metric}: multiple current-period values across {ids}"
            )
        row, amount = candidates[0]
        business_year = str(row.get("bsns_year") or "")
        report_code = str(row.get("reprt_code") or "")
        effective = _effective_date(business_year, report_code)
        receipt = str(row.get("rcept_no") or "").strip()
        account_id = str(row.get("account_id") or "").strip()
        evidence_id = "DART_" + sha256(
            f"{target_id}|{spec.metric}|{effective}|{fs_div}|{account_id}|{receipt}".encode("utf-8")
        ).hexdigest()[:20]
        records.append(
            EvidenceRecord(
                id=evidence_id,
                target=target_id,
                metric=spec.metric,
                value=_json_safe_amount(amount),
                unit=spec.unit,
                source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
                effective_date=effective,
                observed_date=published_date[:10],
                source_name="OpenDART financial statements",
                source_ref=(
                    f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}"
                    if receipt
                    else source_ref
                ),
                source_grade="A",
                confidence=1.0,
                segment=segment,
                notes=f"fs_div={fs_div}; sj_div={row.get('sj_div')}; account_id={account_id}",
                critical=spec.critical,
            )
        )
    return tuple(records)


def opendart_fact_collector(
    *,
    source_id: str,
    checked_at: str,
    rows: Sequence[Mapping[str, object]],
    published_date: str,
    source_ref: str,
    specs: tuple[DartFactMetricSpec, ...] = DEFAULT_CORE_FACT_SPECS,
    fs_div: str = "CFS",
    segment: str = "company",
) -> EvidenceCollector:
    """Create a typed EvidenceCollector from already-fetched OpenDART rows.

    Network/credential handling stays in transport adapters. This function is deterministic and
    testable, and it can be fed by the live OpenDART transport or fixtures.
    """
    if not source_id or not checked_at:
        raise ValueError("source_id and checked_at are required")

    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        records = parse_opendart_financial_facts(
            rows,
            target_id=request.target_id,
            published_date=published_date,
            source_ref=source_ref,
            specs=specs,
            fs_div=fs_div,
            segment=segment,
        )
        payload = json.dumps(list(rows), ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
        document_ids = tuple(sorted({str(row.get("rcept_no")) for row in rows if row.get("rcept_no")}))
        return EvidenceCollectionBatch(
            source_id=source_id,
            checked_at=checked_at,
            records=records,
            source_fingerprint=sha256(payload.encode("utf-8")).hexdigest(),
            document_ids=document_ids,
        )

    return collect
