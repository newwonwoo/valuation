from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import os
import time
from typing import Callable
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from .source_index import (
    DocumentIndexRecord,
    SourceIndexBatch,
    parse_iea_data_product_metadata,
    parse_kiet_release_listing,
    parse_kisdi_report_metadata,
    parse_opendart_report_rows,
    parse_kosis_rows_snapshot,
    schema_hash_from_records,
    stable_hash,
)
from .source_watch import EndpointObservation, EndpointRole, reconcile_endpoint_observations


class SourceFetchError(RuntimeError):
    pass


class MissingCredentialError(RuntimeError):
    pass


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status: int
    text: str
    content_type: str | None


@dataclass(frozen=True)
class BinaryHttpResponse:
    url: str
    status: int
    body: bytes
    content_type: str | None


class HttpTransport:
    """Small index-first HTTP transport.

    It has bounded request time/bytes and no background behavior. Production callers
    should still respect source-specific robots/licence/terms and cadence limits.
    """

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        max_bytes: int = 8_000_000,
        retries: int = 1,
    ):
        if timeout_seconds <= 0 or max_bytes <= 0 or retries < 0:
            raise ValueError("HTTP transport limits must be positive and retries non-negative")
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.retries = retries

    def get_bytes(self, url: str) -> BinaryHttpResponse:
        last: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                req = Request(
                    url,
                    headers={
                        "User-Agent": "RocketSLA-IndustryIndexer/0.5 (+metadata-first)"
                    },
                )
                with urlopen(req, timeout=self.timeout_seconds) as resp:
                    raw = resp.read(self.max_bytes + 1)
                    if len(raw) > self.max_bytes:
                        raise SourceFetchError(
                            f"response exceeds max_bytes={self.max_bytes}"
                        )
                    return BinaryHttpResponse(
                        url=url,
                        status=getattr(resp, "status", 200),
                        body=raw,
                        content_type=resp.headers.get("Content-Type"),
                    )
            except (URLError, HTTPError, TimeoutError, SourceFetchError) as exc:
                last = exc
                if attempt < self.retries:
                    time.sleep(0.25 * (attempt + 1))
        raise SourceFetchError(f"fetch failed for {url}: {last}")

    def get_text(self, url: str) -> HttpResponse:
        response = self.get_bytes(url)
        charset = _content_type_charset(response.content_type) or "utf-8"
        return HttpResponse(
            response.url,
            response.status,
            response.body.decode(charset, errors="replace"),
            response.content_type,
        )


FetchText = Callable[[str], str]


def _content_type_charset(content_type: str | None) -> str | None:
    if not content_type:
        return None
    for part in content_type.split(";")[1:]:
        key, sep, value = part.strip().partition("=")
        if sep and key.lower() == "charset" and value.strip():
            return value.strip().strip("\"'")
    return None


def index_kiet_psi(fetch_text: FetchText, *, checked_at: date) -> SourceIndexBatch:
    url = "https://www.kiet.re.kr/communicate/medataList"
    text = fetch_text(url)
    records = parse_kiet_release_listing(text)
    rows = [
        {
            "document_id": r.document_id,
            "title": r.title,
            "published_at": r.published_at,
        }
        for r in records
    ]
    return SourceIndexBatch(
        "KR_KIET_PSI",
        checked_at,
        records,
        schema_hash_from_records(rows) if rows else stable_hash([]),
        "html_index",
    )


@dataclass(frozen=True)
class IEAMonthlyElectricityIndexResult:
    batch: SourceIndexBatch
    endpoint_warning: str | None
    next_release: date | None
    resolved_latest_published_at: date | None
    schema_transition_note: str | None


def index_iea_monthly_electricity(
    fetch_text: FetchText,
    *,
    checked_at: date,
) -> IEAMonthlyElectricityIndexResult:
    product_url = (
        "https://www.iea.org/data-and-statistics/data-product/"
        "monthly-electricity-statistics"
    )
    tool_url = (
        "https://www.iea.org/data-and-statistics/data-tools/"
        "monthly-electricity-statistics"
    )
    product = parse_iea_data_product_metadata(fetch_text(product_url))
    tool = parse_iea_data_product_metadata(fetch_text(tool_url))

    product_latest = product.latest_file_updated or product.last_updated
    tool_latest = tool.latest_file_updated or tool.last_updated
    obs = (
        EndpointObservation(
            "iea_mes_product",
            EndpointRole.PRIMARY_INDEX,
            True,
            product_latest,
            "IEA_MES_PRODUCT",
        ),
        EndpointObservation(
            "iea_mes_tool",
            EndpointRole.DATA_EXPLORER,
            True,
            tool_latest,
            "IEA_MES_TOOL",
        ),
    )
    recon = reconcile_endpoint_observations(obs)
    resolved = recon.resolved_latest_published_at
    records: tuple[DocumentIndexRecord, ...] = ()
    if resolved is not None:
        record = DocumentIndexRecord(
            source_id="INT_IEA",
            document_id=f"IEA_MES_{resolved.isoformat()}",
            title="Monthly Electricity Statistics",
            published_at=resolved,
            url=tool_url if tool_latest == resolved else product_url,
            document_class="official_dataset_update",
            locator="resolved from IEA product/data-tool endpoints",
            content_fingerprint=stable_hash(
                {
                    "product": product_latest,
                    "tool": tool_latest,
                    "next": product.next_release,
                }
            ),
        )
        records = (record,)
    schema_payload = [
        {
            "product_schema_note": product.schema_transition_note,
            "tool_schema_note": tool.schema_transition_note,
        }
    ]
    batch = SourceIndexBatch(
        "INT_IEA",
        checked_at,
        records,
        schema_hash_from_records(schema_payload),
        "multi_endpoint",
        warning=recon.warning,
    )
    return IEAMonthlyElectricityIndexResult(
        batch=batch,
        endpoint_warning=recon.warning,
        next_release=max(
            x
            for x in (product.next_release, tool.next_release)
            if x is not None
        )
        if any((product.next_release, tool.next_release))
        else None,
        resolved_latest_published_at=resolved,
        schema_transition_note=(
            product.schema_transition_note or tool.schema_transition_note
        ),
    )


def require_env_credential(env_name: str) -> str:
    value = os.getenv(env_name)
    if not value:
        raise MissingCredentialError(
            f"required credential {env_name} is not configured"
        )
    return value


def parse_json_response(text: str) -> list[dict]:
    payload = json.loads(text)
    if isinstance(payload, list):
        return [dict(x) for x in payload]
    if isinstance(payload, dict):
        if payload.get("status") not in (None, "000"):
            raise SourceFetchError(
                f"API returned status={payload.get('status')} "
                f"message={payload.get('message')}"
            )
        if isinstance(payload.get("list"), list):
            return [dict(x) for x in payload["list"]]
        return [payload]
    raise SourceFetchError("JSON root must be list or object")


def index_kisdi_ict(
    fetch_text: FetchText,
    *,
    checked_at: date,
    url: str = (
        "https://www.kisdi.re.kr/report/view.do?arrMasterId=3934580&"
        "artId=1943576&key=m2101113024770&masterId=3934580"
    ),
) -> SourceIndexBatch:
    text = fetch_text(url)
    record = parse_kisdi_report_metadata(text, url=url)
    rows = [
        {
            "document_id": record.document_id,
            "title": record.title,
            "published_at": record.published_at,
        }
    ]
    return SourceIndexBatch(
        "KR_KISDI_ICT",
        checked_at,
        (record,),
        schema_hash_from_records(rows),
        "html_report",
    )


def build_opendart_filing_list_url(
    *,
    corp_code: str,
    begin_date: str,
    end_date: str,
    api_key: str | None = None,
    page_count: int = 100,
) -> str:
    from urllib.parse import urlencode

    key = api_key or require_env_credential("DART_API_KEY")
    params = {
        "crtfc_key": key,
        "corp_code": corp_code,
        "bgn_de": begin_date,
        "end_de": end_date,
        "page_count": page_count,
    }
    return "https://opendart.fss.or.kr/api/list.json?" + urlencode(params)


def index_opendart_filing_list(
    fetch_text: FetchText,
    *,
    checked_at: date,
    corp_code: str,
    begin_date: str,
    end_date: str,
    api_key: str | None = None,
) -> SourceIndexBatch:
    url = build_opendart_filing_list_url(
        corp_code=corp_code,
        begin_date=begin_date,
        end_date=end_date,
        api_key=api_key,
    )
    rows = parse_json_response(fetch_text(url))
    records = parse_opendart_report_rows(rows)
    schema = schema_hash_from_records(rows) if rows else stable_hash([])
    return SourceIndexBatch(
        "KR_OPENDART",
        checked_at,
        records,
        schema,
        "api",
    )


@dataclass(frozen=True)
class KOSISSnapshotResult:
    fact_hash: str
    schema_hash: str
    periods: tuple[str, ...]
    row_count: int


def snapshot_kosis_json(
    fetch_text: FetchText,
    *,
    url: str,
) -> KOSISSnapshotResult:
    rows = parse_json_response(fetch_text(url))
    fact_hash, schema_hash, periods = parse_kosis_rows_snapshot(rows)
    return KOSISSnapshotResult(
        fact_hash,
        schema_hash,
        periods,
        len(rows),
    )
