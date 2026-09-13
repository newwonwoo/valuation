"""Source-bound facts from saved public DART HTML, never synthetic API rows.

The host identifies cells. This adapter checks source hashes, filing identity,
period/scope, labels and units and reads the amounts itself. It does not attest
that downloaded bytes were independently authenticated by DART.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from hashlib import sha256
from html import unescape
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlparse

from .collection_plan import CollectorCapability
from .evidence_collection import EvidenceCollectionBatch
from .filing_table_cells import _amount, _grids, _table_captions
from .live_runtime import LiveCollectorProvider
from .records import EvidenceRecord, EvidenceSourceLayer

_LABELS = {
    "revenue": {"매출액", "매출", "영업수익", "수익(매출액)"},
    "operating_income": {"영업이익", "영업이익(손실)", "영업손익"},
    "net_income": {"당기순이익", "당기순이익(손실)", "반기순이익", "반기순이익(손실)", "분기순이익", "분기순이익(손실)"},
    "total_assets": {"자산총계"},
    "total_liabilities": {"부채총계"},
    "total_equity": {"자본총계"},
    "cash_and_cash_equivalents": {"현금및현금성자산"},
}
_UNITS = {"원": Decimal(1), "천원": Decimal(1000), "백만원": Decimal(1000000), "억원": Decimal(100000000)}


def _squeeze(value):
    return re.sub(r"\s+", "", str(value))


def _visible(value):
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", value)).split())


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate public filing key: {key}")
        result[key] = value
    return result


def _read_document(root, item, receipt):
    path = (root / item["path"]).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("public filing document must stay within declaration directory")
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != item["sha256"]:
        raise ValueError("public filing document hash mismatch")
    url = urlparse(item["url"])
    query = parse_qs(url.query)
    if (url.scheme != "https" or url.hostname != "dart.fss.or.kr"
            or url.username or url.password
            or receipt not in query.get("rcpNo", []) + query.get("rcpno", [])):
        raise ValueError("public filing original URL must bind the DART receipt")
    return raw.decode(item.get("encoding", "utf-8"))


def load_public_filing_facts(path, *, filing, run_as_of, target_id=None):
    """Reopen source bytes on each call; return records plus immutable receipt."""
    filing.validate()
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique)
    if payload.get("schema_version") != "public-filing-facts/v1":
        raise ValueError("unsupported public filing facts schema")
    target = payload["target_id"]
    if not re.fullmatch(r"KR:DART:\d{8}", target) or (target_id and target != target_id):
        raise ValueError("public filing target mismatch")
    for key in ("business_year", "report_code", "fiscal_period_end", "fs_div"):
        if payload[key] != getattr(filing, key):
            raise ValueError(f"public filing {key} mismatch")
    cutoff = date.fromisoformat(run_as_of[:10])
    checked = date.fromisoformat(payload["as_of"][:10])
    receipt = payload["rcept_no"]
    if not re.fullmatch(r"\d{14}", receipt):
        raise ValueError("invalid public filing receipt")
    published = date.fromisoformat(f"{receipt[:4]}-{receipt[4:6]}-{receipt[6:8]}")
    end = date.fromisoformat(payload["fiscal_period_end"])
    if not end <= published <= checked <= cutoff or checked > date.fromisoformat(filing.checked_at[:10]):
        raise ValueError("public filing cutoff/period mismatch")
    identity = _read_document(path.parent, payload["identity_document"], receipt)
    issuer = payload["issuer_name"]
    if not issuer.strip() or _squeeze(issuer) not in _squeeze(_visible(identity)):
        raise ValueError("public filing issuer missing from identity document")
    # An actual DART main document binds the corporation code to the receipt.
    if target.rsplit(":", 1)[1] not in identity:
        raise ValueError("public filing corp code missing from identity document")
    documents = {}
    for item in payload["documents"]:
        key = item["document_id"]
        if key in documents:
            raise ValueError("duplicate public filing document")
        html = _read_document(path.parent, item, receipt)
        visible = _squeeze(_visible(html))
        for field in ("scope_quote", "period_quote", "unit_quote"):
            if not item[field].strip() or _squeeze(item[field]) not in visible:
                raise ValueError(f"public filing {field} not in source")
        scope = _squeeze(item["scope_quote"])
        if (payload["fs_div"] == "CFS" and "연결" not in scope) or (payload["fs_div"] == "OFS" and ("연결" in scope or "재무" not in scope)):
            raise ValueError("public filing consolidation scope mismatch")
        period_digits = re.sub(r"\D", "", item["period_quote"])
        if end.strftime("%Y%m%d") not in period_digits:
            raise ValueError("public filing period quote does not bind fiscal end")
        unit_match = re.fullmatch(r"\(?단위[:：](원|천원|백만원|억원)\)?", _squeeze(item["unit_quote"]))
        if unit_match is None:
            raise ValueError("public filing unit must be an explicit monetary unit")
        documents[key] = (item, _grids(html), _table_captions(html), _UNITS[unit_match[1]])
    records, seen = [], set()
    for observation in payload["observations"]:
        metric = observation["metric"]
        if metric not in _LABELS or metric not in filing.supported_metrics or metric in seen:
            raise ValueError("unsupported or duplicate public filing metric")
        seen.add(metric)
        item, grids, captions, multiplier = documents[observation["document_id"]]
        ti, ri, ci = (observation[key] for key in ("table_index", "row_index", "column_index"))
        if any(type(n) is not int or n < 0 for n in (ti, ri, ci)):
            raise ValueError("invalid public filing coordinate")
        try:
            grid = grids[ti]
            cell = grid[ri][ci]
        except IndexError as error:
            raise ValueError("public filing coordinate outside table") from error
        label = _squeeze(observation["row_label"])
        account_label = re.sub(r"\(주\d+(?:,\d+)*\)$", "", label)
        if account_label not in _LABELS[metric] or label not in {_squeeze(x) for x in grid[ri][:ci]}:
            raise ValueError("public filing metric row label mismatch")
        column = _squeeze(observation["column_label"])
        headers = {_squeeze(row[ci]) for row in grid[:ri] if len(row) > ci}
        if not column or column not in headers:
            raise ValueError("public filing column label mismatch")
        # Header and explicit period must be linked in the source declaration;
        # '제N기' is allowed only when included in the quoted period text.
        if column not in _squeeze(item["period_quote"]):
            raise ValueError("public filing column not bound to period quote")
        if metric in {"revenue", "operating_income", "net_income"} and payload["report_code"] in {"11012", "11014"}:
            # Interim statements place three-month and YTD cells side by side.
            # A shared '제N기' header alone cannot prove the cumulative amount.
            months = "6개월" if payload["report_code"] == "11012" else "9개월"
            if not any("누적" in header or months in header for header in headers):
                raise ValueError("public filing interim flow requires cumulative column")
        context = _squeeze(captions[ti] + " ".join(" ".join(row) for row in grid))
        if ti:
            context += _squeeze(" ".join(" ".join(row) for row in grids[ti - 1]))
        if _squeeze(item["unit_quote"]) not in context:
            raise ValueError("public filing unit not local to selected table")
        amount = _amount(cell)
        if amount is None or not amount.is_finite():
            raise ValueError("public filing cell is not a finite number")
        value = amount * multiplier
        declared = Decimal(str(observation["value"]))
        if not declared.is_finite() or declared != value or observation["unit"] != "KRW":
            raise ValueError("public filing amount/unit mismatch")
        receipt_data = {"schema_version": "public-filing-fact-receipt/v1", "target_id": target,
                        "rcept_no": receipt, "fs_div": payload["fs_div"], "fiscal_period_end": end.isoformat(),
                        "document": item, "identity_document": payload["identity_document"],
                        "observation": observation, "raw_cell": cell, "canonical_value": str(value)}
        sealed = _canonical(receipt_data)
        records.append(EvidenceRecord(
            id="DART_HTML_" + sha256(sealed.encode()).hexdigest()[:20], target=target,
            metric=metric, value=int(value) if value == value.to_integral_value() else str(value),
            unit="KRW", source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
            effective_date=end.isoformat(), observed_date=published.isoformat(),
            source_name="DART public financial statement HTML", source_ref=item["url"],
            source_grade="A", confidence=1.0, segment=filing.segment_id,
            notes="public_filing_receipt=" + sealed,
            critical=next(spec.critical for spec in filing.specs if spec.metric == metric)))
    if not records:
        raise ValueError("public filing observations are empty")
    return tuple(records), sha256(_canonical(payload).encode()).hexdigest(), receipt, payload["as_of"]


def public_filing_fact_provider(path, *, filing, run_as_of):
    initial, _, _, _ = load_public_filing_facts(path, filing=filing, run_as_of=run_as_of)
    supported = tuple(record.metric for record in initial)

    def collect(request):
        if set(request.required_metrics) - set(supported):
            raise ValueError("public filing collector unsupported requested metrics")
        records, fingerprint, receipt, checked = load_public_filing_facts(
            path, filing=filing, run_as_of=run_as_of, target_id=request.target_id)
        batch = EvidenceCollectionBatch(source_id=filing.source_id, checked_at=checked,
            records=tuple(record for record in records if record.metric in request.required_metrics),
            source_fingerprint=fingerprint, document_ids=(receipt,))
        batch.validate()
        return batch

    return LiveCollectorProvider(
        capability=CollectorCapability(collector_id=filing.collector_id,
            source_id=filing.source_id, supported_metrics=supported, jurisdictions=("KR",),
            supported_segments=(filing.segment_id,),
            implementation_ref="valuation_engine.public_filing_facts.public_filing_fact_provider"),
        collector=collect)
