from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from hashlib import sha256
import json
from typing import Callable, Mapping, Sequence
from urllib.parse import urlencode

from .evidence_collection import EvidenceCollectionBatch, EvidenceCollectionRequest, EvidenceCollector
from .live_indexers import parse_json_response, require_env_credential
from .records import EvidenceRecord, EvidenceSourceLayer


_REPORT_CODES = frozenset({"11013", "11012", "11014", "11011"})
_YTD_FALLBACK_REPORT_CODES = frozenset({"11013", "11011"})  # Q1 and annual


class DartAmountBasis(str, Enum):
    AUTO = "auto"
    CURRENT_PERIOD = "current_period"
    YEAR_TO_DATE = "year_to_date"
    POINT_IN_TIME = "point_in_time"


@dataclass(frozen=True)
class DartFactMetricSpec:
    metric: str
    account_ids: tuple[str, ...]
    statement_divisions: tuple[str, ...]
    unit: str = "KRW"
    critical: bool = False
    amount_basis: DartAmountBasis = DartAmountBasis.AUTO

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
        amount_basis=DartAmountBasis.YEAR_TO_DATE,
    ),
    DartFactMetricSpec(
        "operating_income",
        ("dart_OperatingIncomeLoss",),
        ("IS", "CIS"),
        critical=True,
        amount_basis=DartAmountBasis.YEAR_TO_DATE,
    ),
    DartFactMetricSpec(
        "net_income",
        ("ifrs-full_ProfitLoss", "ifrs_ProfitLoss"),
        ("IS", "CIS"),
        amount_basis=DartAmountBasis.YEAR_TO_DATE,
    ),
    DartFactMetricSpec(
        "total_assets",
        ("ifrs-full_Assets", "ifrs_Assets"),
        ("BS",),
        amount_basis=DartAmountBasis.POINT_IN_TIME,
    ),
    DartFactMetricSpec(
        "total_liabilities",
        ("ifrs-full_Liabilities", "ifrs_Liabilities"),
        ("BS",),
        amount_basis=DartAmountBasis.POINT_IN_TIME,
    ),
    DartFactMetricSpec(
        "total_equity",
        ("ifrs-full_Equity", "ifrs_Equity"),
        ("BS",),
        amount_basis=DartAmountBasis.POINT_IN_TIME,
    ),
    DartFactMetricSpec(
        "cash_and_cash_equivalents",
        ("ifrs-full_CashAndCashEquivalents", "ifrs_CashAndCashEquivalents"),
        ("BS",),
        amount_basis=DartAmountBasis.POINT_IN_TIME,
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


def _validated_fiscal_period_end(value: object) -> str:
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            "fiscal_period_end is required as an ISO date derived from issuer fiscal-calendar "
            "or filing-period metadata"
        ) from exc
    if parsed.isoformat() != text:
        raise ValueError("fiscal_period_end must use canonical YYYY-MM-DD format")
    return text


def _receipt_date(receipt_no: object, fallback: str) -> str:
    text = str(receipt_no or "").strip()
    if len(text) >= 8 and text[:8].isdigit():
        candidate = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
        try:
            date.fromisoformat(candidate)
            return candidate
        except ValueError:
            pass
    date.fromisoformat(fallback[:10])
    return fallback[:10]


def _row_matches_fs_div(row: Mapping[str, object], fs_div: str) -> bool:
    """The official full-statement endpoint receives fs_div as a request parameter.

    Some normalized fixtures retain fs_div on each row, while the documented response fields do
    not require it. If present it must agree; if absent the request-level fs_div is authoritative.
    """
    row_fs_div = str(row.get("fs_div") or "").strip()
    return not row_fs_div or row_fs_div == fs_div


def _validate_filing_identity(
    rows: Sequence[Mapping[str, object]],
    *,
    corp_code: str,
    business_year: str,
    report_code: str,
) -> None:
    if len(corp_code) != 8 or not corp_code.isdigit():
        raise ValueError("OpenDART corp_code must be 8 digits")
    if len(business_year) != 4 or not business_year.isdigit():
        raise ValueError("OpenDART business_year must be 4 digits")
    if report_code not in _REPORT_CODES:
        raise ValueError("unsupported OpenDART report_code")
    for row in rows:
        row_corp = str(row.get("corp_code") or "").strip()
        row_year = str(row.get("bsns_year") or "").strip()
        row_report = str(row.get("reprt_code") or "").strip()
        if (
            (row_corp and row_corp != corp_code)
            or row_year != business_year
            or row_report != report_code
        ):
            raise ValueError(
                "DART row does not match requested filing: "
                f"expected {corp_code}/{business_year}/{report_code}, "
                f"got {row_corp or '<blank>'}/{row_year or '<blank>'}/"
                f"{row_report or '<blank>'}"
            )


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
        if _row_matches_fs_div(row, fs_div)
        and str(row.get("sj_div") or "") in accepted_statements
        and str(row.get("account_id") or "") in accepted_accounts
    )


def _amount_field(spec: DartFactMetricSpec, row: Mapping[str, object]) -> tuple[str, DartAmountBasis]:
    report_code = str(row.get("reprt_code") or "").strip()
    if report_code not in _REPORT_CODES:
        raise ValueError(f"unsupported DART report code: {report_code or '<blank>'}")
    basis = spec.amount_basis
    if basis is DartAmountBasis.AUTO:
        basis = (
            DartAmountBasis.POINT_IN_TIME
            if str(row.get("sj_div") or "") == "BS"
            else DartAmountBasis.YEAR_TO_DATE
        )
    if basis is DartAmountBasis.CURRENT_PERIOD or basis is DartAmountBasis.POINT_IN_TIME:
        return "thstrm_amount", basis
    if basis is DartAmountBasis.YEAR_TO_DATE:
        # OpenDART documents thstrm_amount as the 3-month amount for interim IS/CIS rows and
        # thstrm_add_amount as cumulative. Annual/Q1 records may not need a separate cumulative field.
        if row.get("thstrm_add_amount") not in (None, "", "-"):
            return "thstrm_add_amount", basis
        if report_code in _YTD_FALLBACK_REPORT_CODES:
            return "thstrm_amount", basis
        return "thstrm_add_amount", basis
    raise ValueError(f"unsupported amount basis: {basis}")


def parse_opendart_financial_facts(
    rows: Sequence[Mapping[str, object]],
    *,
    target_id: str,
    published_date: str,
    source_ref: str,
    fiscal_period_end: str | None,
    corp_code: str,
    business_year: str,
    report_code: str,
    specs: tuple[DartFactMetricSpec, ...] = DEFAULT_CORE_FACT_SPECS,
    fs_div: str = "CFS",
    segment: str = "company",
) -> tuple[EvidenceRecord, ...]:
    """Normalize OpenDART full financial-statement rows into primary EvidenceRecords.

    No account-name fuzzy matching is used. Company-specific metrics such as contract liabilities
    require an explicit MetricSpec. Multiple materially different rows for one metric fail closed.
    Half-year and Q3 flow metrics require cumulative thstrm_add_amount. Rows must match the
    explicitly requested corporation, business year and report code. The exact fiscal period end
    must come from issuer fiscal-calendar or filing-period metadata; report codes alone are not
    treated as fixed calendar dates.
    """
    if not target_id or not source_ref:
        raise ValueError("target_id and source_ref are required")
    published = date.fromisoformat(published_date[:10])
    effective = _validated_fiscal_period_end(fiscal_period_end)
    effective_on = date.fromisoformat(effective)
    if effective_on > published:
        raise ValueError("fiscal_period_end cannot be after published_date")
    if fs_div not in {"CFS", "OFS"}:
        raise ValueError("fs_div must be CFS or OFS")
    _validate_filing_identity(
        rows,
        corp_code=corp_code,
        business_year=business_year,
        report_code=report_code,
    )
    requested_year = int(business_year)
    if effective_on.year not in {requested_year, requested_year + 1}:
        raise ValueError("fiscal_period_end is not aligned with requested business_year")

    records: list[EvidenceRecord] = []
    seen_metrics: set[str] = set()
    for spec in specs:
        if spec.metric in seen_metrics:
            raise ValueError(f"duplicate DART metric spec: {spec.metric}")
        seen_metrics.add(spec.metric)
        matches = _select_rows(rows, spec=spec, fs_div=fs_div)
        if not matches:
            continue

        candidates: list[tuple[Mapping[str, object], Decimal, str, DartAmountBasis]] = []
        missing_required_ytd_codes: set[str] = set()
        for row in matches:
            field_name, basis = _amount_field(spec, row)
            if row.get(field_name) in (None, "", "-"):
                row_report_code = str(row.get("reprt_code") or "").strip()
                if (
                    basis is DartAmountBasis.YEAR_TO_DATE
                    and row_report_code not in _YTD_FALLBACK_REPORT_CODES
                ):
                    missing_required_ytd_codes.add(row_report_code)
                continue
            candidates.append((row, _decimal_amount(row.get(field_name)), field_name, basis))
        if not candidates:
            if missing_required_ytd_codes:
                report_codes = ", ".join(sorted(missing_required_ytd_codes))
                raise ValueError(
                    f"DART YEAR_TO_DATE metric {spec.metric} requires thstrm_add_amount "
                    f"for interim report {report_codes}"
                )
            continue
        unique_amounts = {amount for _, amount, _, _ in candidates}
        if len(unique_amounts) != 1:
            ids = ", ".join(str(row.get("account_id")) for row, _, _, _ in candidates)
            raise ValueError(
                f"ambiguous DART fact for {spec.metric}: multiple current-period values across {ids}"
            )
        row, amount, field_name, basis = candidates[0]
        receipt = str(row.get("rcept_no") or "").strip()
        observed = _receipt_date(receipt, published_date)
        if effective_on > date.fromisoformat(observed):
            raise ValueError("fiscal_period_end cannot be after observed receipt date")
        if date.fromisoformat(observed) > published:
            raise ValueError("observed receipt date cannot be after published_date")
        account_id = str(row.get("account_id") or "").strip()
        row_currency = str(row.get("currency") or "").strip()
        if row_currency and row_currency != spec.unit:
            raise ValueError(
                f"DART currency mismatch for {spec.metric}: spec={spec.unit}, row={row_currency}"
            )
        evidence_id = "DART_" + sha256(
            f"{target_id}|{spec.metric}|{effective}|{fs_div}|{account_id}|{receipt}|{basis.value}".encode("utf-8")
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
                observed_date=observed,
                source_name="OpenDART financial statements",
                source_ref=(
                    f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}"
                    if receipt
                    else source_ref
                ),
                source_grade="A",
                confidence=1.0,
                segment=segment,
                notes=(
                    f"fs_div={fs_div}; sj_div={row.get('sj_div')}; account_id={account_id}; "
                    f"amount_field={field_name}; amount_basis={basis.value}"
                ),
                critical=spec.critical,
            )
        )
    return tuple(records)


def build_opendart_full_financials_url(
    *,
    corp_code: str,
    business_year: str,
    report_code: str,
    fs_div: str = "CFS",
    api_key: str | None = None,
) -> str:
    key = api_key or require_env_credential("DART_API_KEY")
    if len(corp_code) != 8 or not corp_code.isdigit():
        raise ValueError("OpenDART corp_code must be 8 digits")
    if len(business_year) != 4 or not business_year.isdigit():
        raise ValueError("OpenDART business_year must be 4 digits")
    if report_code not in _REPORT_CODES:
        raise ValueError("unsupported OpenDART report_code")
    if fs_div not in {"CFS", "OFS"}:
        raise ValueError("fs_div must be CFS or OFS")
    params = {
        "crtfc_key": key,
        "corp_code": corp_code,
        "bsns_year": business_year,
        "reprt_code": report_code,
        "fs_div": fs_div,
    }
    return "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json?" + urlencode(params)


FetchText = Callable[[str], str]


def live_opendart_fact_collector(
    fetch_text: FetchText,
    *,
    source_id: str,
    checked_at: str,
    corp_code: str,
    business_year: str,
    report_code: str,
    fiscal_period_end: str,
    fs_div: str = "CFS",
    api_key: str | None = None,
    specs: tuple[DartFactMetricSpec, ...] = DEFAULT_CORE_FACT_SPECS,
    segment: str = "company",
) -> EvidenceCollector:
    """Live-source collector using the official OpenDART full-financial-statement endpoint.

    HTTP is injected through fetch_text. The function performs no market/street access and emits
    only filing Evidence. Credential lookup occurs when the collector runs.
    """
    if not source_id or not checked_at:
        raise ValueError("source_id and checked_at are required")

    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        url = build_opendart_full_financials_url(
            corp_code=corp_code,
            business_year=business_year,
            report_code=report_code,
            fs_div=fs_div,
            api_key=api_key,
        )
        rows = parse_json_response(fetch_text(url))
        records = parse_opendart_financial_facts(
            rows,
            target_id=request.target_id,
            published_date=checked_at,
            source_ref=url.split("?", 1)[0],
            fiscal_period_end=fiscal_period_end,
            corp_code=corp_code,
            business_year=business_year,
            report_code=report_code,
            specs=specs,
            fs_div=fs_div,
            segment=segment,
        )
        payload = json.dumps(
            {
                "corp_code": corp_code,
                "business_year": business_year,
                "report_code": report_code,
                "fiscal_period_end": fiscal_period_end,
                "rows": rows,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        document_ids = tuple(sorted({str(row.get("rcept_no")) for row in rows if row.get("rcept_no")}))
        return EvidenceCollectionBatch(
            source_id=source_id,
            checked_at=checked_at,
            records=records,
            source_fingerprint=sha256(payload.encode("utf-8")).hexdigest(),
            document_ids=document_ids,
        )

    return collect


def opendart_fact_collector(
    *,
    source_id: str,
    checked_at: str,
    rows: Sequence[Mapping[str, object]],
    published_date: str,
    source_ref: str,
    fiscal_period_end: str,
    corp_code: str,
    business_year: str,
    report_code: str,
    specs: tuple[DartFactMetricSpec, ...] = DEFAULT_CORE_FACT_SPECS,
    fs_div: str = "CFS",
    segment: str = "company",
) -> EvidenceCollector:
    """Deterministic collector from already-fetched OpenDART rows for replay/tests."""
    if not source_id or not checked_at:
        raise ValueError("source_id and checked_at are required")

    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        records = parse_opendart_financial_facts(
            rows,
            target_id=request.target_id,
            published_date=published_date,
            source_ref=source_ref,
            fiscal_period_end=fiscal_period_end,
            corp_code=corp_code,
            business_year=business_year,
            report_code=report_code,
            specs=specs,
            fs_div=fs_div,
            segment=segment,
        )
        payload = json.dumps(
            {
                "corp_code": corp_code,
                "business_year": business_year,
                "report_code": report_code,
                "fiscal_period_end": fiscal_period_end,
                "rows": list(rows),
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        document_ids = tuple(sorted({str(row.get("rcept_no")) for row in rows if row.get("rcept_no")}))
        return EvidenceCollectionBatch(
            source_id=source_id,
            checked_at=checked_at,
            records=records,
            source_fingerprint=sha256(payload.encode("utf-8")).hexdigest(),
            document_ids=document_ids,
        )

    return collect
