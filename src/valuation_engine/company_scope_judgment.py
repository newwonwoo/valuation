"""Source-bound IFRS reporting-scope adjudication, never segment fabrication.

The host distinguishes product disclosures from reportable segments. Acceptance
is deliberately narrower than free-form interpretation: a consolidated reporting
statement, every reviewed source member and all contrary scope signals must be
accounted for. Unsupported wording remains uncertain; raw filings are immutable.
"""
from __future__ import annotations

from pathlib import Path
import re

from .dart_kpi import _visible_text
from .host_judgment import (
    HostJudgmentError, HostJudgmentRequest, HostJudgmentRequired, JudgmentGap,
    judgment_hash, load_judgment_json, parse_host_judgment_response,
)
from .llm_transport import TransportError

COMPANY_SCOPE_ROLE = "company_scope_analyst"
_SCOPE_TOKEN = re.compile(r"(?:보고|영업)\s*부문|(?:reportable|operating)\s+segments?", re.I)
_GROUP = r"(?:연결기업|연결회사|연결실체|연결그룹|consolidated\s+(?:group|entity))"
_SINGLE = re.compile(_GROUP + r"[^.!?。]{0,180}?(?:전체를\s*)?단일\s*(?:보고|영업)\s*부문", re.I)
_EN_SINGLE = re.compile(_GROUP + r"[^.!?。]{0,180}?(?:single|one)\s+(?:reportable|operating)\s+segment", re.I)
_MULTI = re.compile(_GROUP + r"[^.!?。]{0,240}?(?:(?:[2-9]|[1-9]\d+|두|세|네|다섯)\s*개\s*(?:보고|영업)\s*부문|(?:보고|영업)\s*부문(?:은|으로|을|:)\s*[^.!?。]{0,160}?(?:부문과|부문,|부문 및)|(?:two|three|four|multiple)\s+(?:reportable|operating)\s+segments)", re.I)
_PARENT = re.compile(r"(?:(?:지배|모|종속)(?:회사|기업|법인)|자회사|당사|parent(?:\s+company)?|subsidiar\w*)", re.I)
_NEGATION = re.compile(r"(?:아니|아닙|아님|아닌|아닐|않|제외|not\b|no longer|instead)", re.I)
_HISTORICAL = re.compile(r"(?:전기|전년|과거|이전에는|previous|prior\s+(?:year|period)|last\s+year)", re.I)
_CURRENT = re.compile(r"당(?:기|반기|분기)|current\s+(?:period|year|quarter|half)", re.I)
_QUALIFIED = re.compile(r"(?:그러나|하지만|다만|변경|used\s+to|previously)", re.I)
_SENTENCE_END = re.compile(r"(?<!\d)[.!?。](?=\s|$)")


def _normalized(value):
    return " ".join(value.split())


def containing_scope_sentence(text, start, end):
    preceding_stops = list(_SENTENCE_END.finditer(text, 0, start))
    begin = preceding_stops[-1].end() if preceding_stops else 0
    if not text[start:end].endswith("."):
        stop = _SENTENCE_END.search(text, end)
        end = len(text) if stop is None else stop.end()
    # A qualification in the immediately following narrative is part of the
    # same claim, even when the proposal quotes only the positive sentence.
    # Stop at a new product/table section rather than importing its unrelated
    # comparative periods into this reporting-scope claim.
    continuation = text[end:end + 400]
    stop = continuation.find(".")
    continuation = continuation if stop < 0 else continuation[:stop + 1]
    if (re.search(r"^\s*(?:그러나|하지만|다만|현재|당기|당반기|당분기|이러한|이는|but\b|however\b|currently\b)", continuation, re.I)
            or (_SCOPE_TOKEN.search(continuation)
                and any(pattern.search(continuation) for pattern in (_NEGATION, _HISTORICAL, _QUALIFIED)))):
        end += len(continuation)
    return text[begin:end]


def unqualified_scope_sentence(text, start, end):
    sentence = containing_scope_sentence(text, start, end)
    return not any(pattern.search(sentence) for pattern in
                   (_PARENT, _NEGATION, _HISTORICAL, _QUALIFIED)) and not re.search(
                       r"20\d{2}\s*(?:년|[./-])", sentence)


def reportable_segment_table_names(filing):
    """An IFRS segment header is counterevidence even if its values do not reconcile.

    Catching and ignoring a note parser's reconciliation error would be an
    unsafe single-company override. Product-only tables lack this structure.
    """
    from .generic_kr_industry import _SegmentTableParser, _expand_table
    from .segment_note import _segment_columns

    names = set()
    for member in filing.text_members:
        parser = _SegmentTableParser()
        parser.feed(member.text or "")
        for table in parser.tables:
            columns = _segment_columns(_expand_table(table))
            if columns is not None:
                names.update(name for _index, name in columns[1])
    return tuple(sorted(names))


def has_reportable_segment_table(filing):
    return bool(reportable_segment_table_names(filing))


def _review_items(filing, screened_names):
    items = []
    for member in filing.text_members:
        text = _normalized(_visible_text(member))
        # Source excerpts are bounded, but the validator scans the entire member.
        windows = set()
        for match in _SCOPE_TOKEN.finditer(text):
            start = max(0, text.rfind(".", 0, match.start()) + 1, match.start() - 140)
            end = text.find(".", match.end())
            end = min(len(text), match.end() + 280) if end < 0 else min(end + 1, match.end() + 280)
            windows.add((start, end))
        for name in screened_names:
            position = text.find(name)
            if position >= 0:
                windows.add((max(0, position - 100), min(len(text), position + len(name) + 180)))
        for start, end in sorted(windows):
            quote = text[start:end].strip()
            item = {"member_path": member.path, "member_sha256": member.content_hash,
                    "quote": quote}
            item["item_id"] = judgment_hash(item)
            items.append(item)
    return items


def validate_company_scope_source(*, identity, as_of, record, filing, issuer_row):
    """Issuer/integrity binding applies to automatic and assisted scope equally."""
    identity.validate()
    filing.validate()  # Integrity failures must never reach a model.
    from .source_index import parse_opendart_report_rows

    if parse_opendart_report_rows((issuer_row,)) != (record,):
        raise HostJudgmentError("source index does not match the issuer's original filing-list row")
    corp_code = dict(identity.external_ids).get("opendart_corp_code")
    if not corp_code or str(issuer_row.get("corp_code", "")) != corp_code:
        raise HostJudgmentError("filing issuer does not match resolved company")
    if identity.target_id != f"KR:DART:{corp_code}":
        raise HostJudgmentError("resolved target ID does not match its DART issuer")
    if issuer_row.get("stock_code") and identity.ticker and issuer_row["stock_code"] != identity.ticker:
        raise HostJudgmentError("filing stock code does not match resolved company")
    if issuer_row.get("rcept_no") != filing.rcept_no or record.document_id != f"DART_{filing.rcept_no}":
        raise HostJudgmentError("filing receipt does not match indexed document")
    if record.published_at is None or record.published_at.isoformat() > as_of[:10]:
        raise HostJudgmentError("filing was not known at the requested date")
    if filing.published_at != record.published_at:
        raise HostJudgmentError("filing publication date does not match index")
    return corp_code


def build_company_scope_request(*, identity, as_of, record, filing,
                                screened_names, issuer_row):
    corp_code = validate_company_scope_source(
        identity=identity, as_of=as_of, record=record, filing=filing, issuer_row=issuer_row)
    source = {"kind": "primary_consolidated_filing", "source_id": record.source_id,
              "target_id": identity.target_id, "legal_name": identity.legal_name,
              "issuer_corp_code": corp_code, "document_id": record.document_id,
              "published_at": record.published_at.isoformat(), "period": record.period,
              "source_ref": filing.source_ref, "archive_sha256": filing.archive_hash,
              "manifest_sha256": filing.manifest_hash, "index_sha256": record.content_fingerprint,
              "members": [{"member_path": member.path, "member_sha256": member.content_hash}
                          for member in filing.text_members],
              "screened_names": list(screened_names),
              "review_items": _review_items(filing, screened_names)}
    response_schema = {
        "schema_version": "host-judgment-response/v1", "request_hash": "copy request_hash",
        "target_id": identity.target_id, "as_of": as_of,
        "decision": "SINGLE | MULTI | UNCERTAIN",
        "proposal": {"document_id": record.document_id, "archive_sha256": filing.archive_hash,
                     "period": record.period, "member_path": "cited member path",
                     "member_sha256": "cited member hash", "scope": "CONSOLIDATED",
                     "quote": "exact normalized source quote with explicit consolidated reporting scope",
                     "current_period_quote": "exact source quote identifying the current period (e.g. 당반기), in the same member",
                     "reportable_segments": "[] for SINGLE; actual IFRS note names for MULTI",
                     "reviewed_member_hashes": "mapping of EVERY text member path to SHA-256"},
        "rationale": "distinguish accounting scope from product/economic drivers; no valuation conclusion",
        "counterevidence": [{"item_id": "each review_items item exactly once",
                             "classification": "supports_single | supports_multi | product_disaggregation | parent_only | subsidiary_only | not_scope | unresolved",
                             "rationale": "explain this source excerpt, including apparent conflicts"}],
        "uncertainty": "limitations; UNCERTAIN if material scope conflict remains",
    }
    return HostJudgmentRequest.build(
        gap=JudgmentGap("COMPANY_SCOPE_AMBIGUOUS", "company_scope", identity.target_id,
                        as_of, "INDUSTRY_KNOWLEDGE_LOAD",
                        "The over-inclusive business/product screen found multiple operating segments "
                        f"({', '.join(screened_names)}). Adjudicate the consolidated IFRS reporting scope; "
                        "product rows do not by themselves establish reportable segments. If MULTI is "
                        "confirmed, declare the reportable segments (declarations/segments.yaml)."),
        source_bindings=(source,), allowed_actions=("SINGLE", "MULTI", "UNCERTAIN"),
        response_schema=response_schema, input_hash=judgment_hash(source),
        validator_id="company-scope-validator/v1",
        write_targets=("declarations/company_scope_receipt.json",))


def _source_quote(proposal, source, filing):
    if proposal["document_id"] != source["document_id"] or proposal["archive_sha256"] != filing.archive_hash:
        raise HostJudgmentError("scope proposal document/archive hash mismatch")
    if proposal["period"] != source["period"]:
        raise HostJudgmentError("scope proposal reporting period mismatch")
    members = {member.path: member for member in filing.text_members}
    expected_members = {path: member.content_hash for path, member in members.items()}
    if proposal["reviewed_member_hashes"] != expected_members:
        raise HostJudgmentError("all current source members must be reviewed with matching hashes")
    member = members.get(proposal["member_path"])
    if member is None or proposal["member_sha256"] != member.content_hash:
        raise HostJudgmentError("scope proposal member/hash mismatch")
    if proposal["scope"] != "CONSOLIDATED":
        raise HostJudgmentError("parent/subsidiary-only scope cannot certify the consolidated company")
    quote = proposal["quote"]
    if not isinstance(quote, str) or len(quote.strip()) < 12:
        raise HostJudgmentError("scope proposal requires a substantive exact source quote")
    quote = _normalized(quote)
    text = _normalized(_visible_text(member))
    if text.count(quote) != 1:
        raise HostJudgmentError("scope quote absent or ambiguous in the cited source member")
    period_quote = proposal["current_period_quote"]
    if (not isinstance(period_quote, str) or not _CURRENT.search(period_quote)
            or _HISTORICAL.search(period_quote) or _normalized(period_quote) not in text):
        raise HostJudgmentError("scope requires exact current-period evidence in the cited member")
    # Validate the complete source sentence, not a selectively cropped quote.
    # Otherwise `단일 보고부문` could be cut out of `단일 보고부문이 아니다`.
    start = text.index(quote)
    end = start + len(quote)
    local_context = text[max(0, start - 800):min(len(text), end + 800)]
    if _normalized(period_quote) not in local_context:
        raise HostJudgmentError("current-period evidence must be local to the reporting-scope statement")
    sentence = containing_scope_sentence(text, start, end)
    indexed_period = re.search(r"(20\d{2})[./-](\d{1,2})", source["period"] or "")
    if indexed_period is None:
        raise HostJudgmentError("indexed reporting period is not sufficiently specific for scope adjudication")
    period_year, period_month = map(int, indexed_period.groups())
    date_context = text[max(0, start - 120):start] + sentence
    for dated in re.finditer(r"(20\d{2})\s*(?:년|[./-])\s*(\d{1,2})?", date_context):
        year, month = dated.groups()
        if int(year) != period_year or (month is not None and int(month) != period_month):
            raise HostJudgmentError("explicit scope date does not match the current indexed reporting period")
    if _PARENT.search(sentence):
        raise HostJudgmentError("scope statement refers to a parent/subsidiary rather than unqualified consolidated scope")
    if _NEGATION.search(sentence):
        raise HostJudgmentError("negated/qualified source scope sentence cannot certify this decision")
    if _HISTORICAL.search(sentence):
        raise HostJudgmentError("prior-period-only scope statement cannot certify current reporting scope")
    if _QUALIFIED.search(sentence):
        raise HostJudgmentError("qualified or changed scope statement requires further primary evidence")
    return quote


def validate_company_scope_response(raw, request, filing):
    response = parse_host_judgment_response(raw, request)
    filing.validate()
    source = request.source_bindings[0]
    if source["archive_sha256"] != filing.archive_hash or source["manifest_sha256"] != filing.manifest_hash:
        raise HostJudgmentError("scope request is stale for the current filing")
    if response["decision"] == "UNCERTAIN":
        raise HostJudgmentError("scope remains uncertain; additional primary evidence or clarification required")
    proposal = response["proposal"]
    fields = {"document_id", "archive_sha256", "period", "member_path", "member_sha256",
              "scope", "quote", "current_period_quote", "reportable_segments", "reviewed_member_hashes"}
    if set(proposal) != fields:
        raise HostJudgmentError(f"scope proposal fields must be exactly {sorted(fields)}")
    quote = _source_quote(proposal, source, filing)
    review = response["counterevidence"]
    expected_ids = {item["item_id"] for item in source["review_items"]}
    if len(review) != len(expected_ids) or {item.get("item_id") for item in review} != expected_ids:
        raise HostJudgmentError("counterevidence must address every source scope/product review item exactly once")
    classes = {"supports_single", "supports_multi", "product_disaggregation", "parent_only",
               "subsidiary_only", "not_scope", "unresolved"}
    for item in review:
        if set(item) != {"item_id", "classification", "rationale"} or item["classification"] not in classes:
            raise HostJudgmentError("invalid counterevidence review classification")
        if not isinstance(item["rationale"], str) or not item["rationale"].strip():
            raise HostJudgmentError("counterevidence requires a rationale")
        if item["classification"] == "unresolved":
            raise HostJudgmentError("unresolved counterevidence blocks a scope decision")
    single = bool(_SINGLE.search(quote) or _EN_SINGLE.search(quote))
    multi = bool(_MULTI.search(quote))
    if _NEGATION.search(quote):
        raise HostJudgmentError("negated/qualified scope wording requires further adjudication")
    segments = proposal["reportable_segments"]
    if not isinstance(segments, list) or not all(isinstance(name, str) and name.strip() for name in segments):
        raise HostJudgmentError("reportable_segments must be a list of actual note names")
    all_text = [_normalized(_visible_text(member)) for member in filing.text_members]
    if response["decision"] == "SINGLE":
        if not single or multi or segments:
            raise HostJudgmentError("single decision needs explicit consolidated single-reportable wording and no fake segments")
        if any(_MULTI.search(text) for text in all_text) or any(item["classification"] == "supports_multi" for item in review):
            raise HostJudgmentError("contrary consolidated multi-segment evidence blocks a single override")
        if has_reportable_segment_table(filing):
            raise HostJudgmentError("contrary or unreconciled reportable-segment table blocks a single override")
    else:
        if not multi or single or len(segments) < 2 or len(segments) != len(set(segments)):
            raise HostJudgmentError("multi decision needs actual consolidated multi-reportable names")
        if any(name not in quote for name in segments):
            raise HostJudgmentError("every reportable segment name must occur in the cited scope quote")
        if (any(_SINGLE.search(text) or _EN_SINGLE.search(text) for text in all_text)
                or any(item["classification"] == "supports_single" for item in review)):
            raise HostJudgmentError("contrary consolidated single-segment evidence blocks a multi override")
    receipt = {"schema_version": "company-scope-receipt/v1", "request_hash": request.request_hash,
               "request": request.to_dict(), "response": response}
    return {**receipt, "receipt_hash": judgment_hash(receipt)}


def resolve_company_scope(*, request, filing, transport=None, receipt_path=None):
    """Return only a revalidated declaration, or hand a candidate to canonical reentry.

    Never mutate an input declaration while a run is executing. The coordinator
    may promote the returned candidate BETWEEN runs; all subsequent reads recheck
    the entire receipt against the freshly retrieved filing and current request.
    """
    if receipt_path is not None and Path(receipt_path).is_file():
        saved = load_judgment_json(Path(receipt_path).read_text(encoding="utf-8"))
        expected = {"schema_version", "request_hash", "request", "response", "receipt_hash"}
        if set(saved) != expected or saved.get("schema_version") != "company-scope-receipt/v1":
            raise HostJudgmentError("invalid company scope receipt schema")
        body = {key: value for key, value in saved.items() if key != "receipt_hash"}
        if saved["receipt_hash"] != judgment_hash(body):
            raise HostJudgmentError("company scope receipt hash mismatch; no model override permitted")
        validated = validate_company_scope_response(saved["response"], request, filing)
        if saved != validated:
            raise HostJudgmentError("company scope receipt is stale or mismatched; no model override permitted")
        return validated
    if transport is None:
        raise HostJudgmentRequired(request, request.gap.detail)
    try:
        raw = transport.complete(role=COMPANY_SCOPE_ROLE, prompt=request.prompt())
    except TransportError as exc:
        raise HostJudgmentRequired(request, f"{request.gap.detail} {exc}") from exc
    try:
        receipt = validate_company_scope_response(raw, request, filing)
    except HostJudgmentError as exc:
        raise HostJudgmentRequired(request, f"scope proposal rejected: {exc}") from exc
    raise HostJudgmentRequired(request, "validated scope candidate requires canonical reentry before use",
                               accepted_receipt=receipt)
