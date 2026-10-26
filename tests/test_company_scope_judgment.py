import json
from dataclasses import replace
from datetime import date
from io import BytesIO
from zipfile import ZipFile, ZipInfo

import pytest

from valuation_engine.company_scope_judgment import (
    COMPANY_SCOPE_ROLE, build_company_scope_request, validate_company_scope_response,
)
from valuation_engine.control_plane import ExecutionMode, StageStatus
from valuation_engine.dart_documents import parse_opendart_original_document_archive
from valuation_engine.generic_kr_industry import GenericKRIndustryError, opendart_filing_snapshot_loader
from valuation_engine.host_judgment import HostJudgmentError, HostJudgmentRequired
from valuation_engine.live_primary_adapters import ResolvedCompanyIdentity, live_industry_snapshot_adapter
from valuation_engine.orchestrator import OrchestratorContext
from valuation_engine.source_index import parse_opendart_report_rows
from valuation_engine.staff_transport import StaffTransport

AS_OF = "2026-10-26"
ROW = {"rcept_no": "20260811000657", "report_nm": "반기보고서 (2026.06)",
       "rcept_dt": "20260811", "corp_code": "00999991", "stock_code": "900991"}
IDENTITY = ResolvedCompanyIdentity("KR:DART:00999991", "가상분석기업", "900991", "KR",
                                   (("opendart_corp_code", ROW["corp_code"]),),
                                   ("https://opendart.fss.or.kr/api/corpCode.xml",))
SINGLE = "연결기업은 재화와 용역의 성격을 고려하여 연결기업 전체를 단일 보고부문으로 결정하였습니다."
MULTI = "연결기업의 보고부문은 철강부문과 건설부문입니다."
TABLE = """<table><tr><th>사업부문</th><th>제품과 용역</th></tr>
<tr><td>제품 A</td><td>10</td></tr><tr><td>제품 B</td><td>20</td></tr>
<tr><td>제품 C</td><td>30</td></tr></table>"""


def archive(statement=SINGLE, extra=""):
    buffer = BytesIO()
    with ZipFile(buffer, "w") as zipped:
        zipped.writestr(ZipInfo("report.xml"), f"<body><h2>영업부문 (연결)</h2><p>당반기</p><p>{statement}</p>{TABLE}</body>")
        if extra:
            zipped.writestr(ZipInfo("other.xml"), extra)
    return buffer.getvalue()


def source(statement=SINGLE, extra=""):
    payload = archive(statement, extra)
    filing = parse_opendart_original_document_archive(payload, rcept_no=ROW["rcept_no"],
        checked_at=date.fromisoformat(AS_OF), published_at=date(2026, 8, 11))
    record = parse_opendart_report_rows((ROW,))[0]
    req = build_company_scope_request(identity=IDENTITY, as_of=AS_OF, record=record,
        filing=filing, screened_names=("제품 A", "제품 B", "제품 C"), issuer_row=ROW)
    return req, filing


def answer(req, quote=SINGLE, decision="SINGLE"):
    src = req.source_bindings[0] if hasattr(req, "source_bindings") else req["source_bindings"][0]
    hash_ = req.request_hash if hasattr(req, "request_hash") else req["request_hash"]
    return {"schema_version": "host-judgment-response/v1", "request_hash": hash_,
            "target_id": IDENTITY.target_id, "as_of": AS_OF, "decision": decision,
            "proposal": {"document_id": src["document_id"], "archive_sha256": src["archive_sha256"],
                "period": src["period"], "member_path": "report.xml",
                "member_sha256": next(member["member_sha256"] for member in src["members"] if member["member_path"] == "report.xml"), "scope": "CONSOLIDATED",
                "quote": quote, "current_period_quote": "당반기", "reportable_segments": [] if decision == "SINGLE" else ["철강부문", "건설부문"],
                "reviewed_member_hashes": {member["member_path"]: member["member_sha256"] for member in src["members"]}},
            "rationale": "제품별 매출 공시는 IFRS 보고부문 구분과 다릅니다.",
            "counterevidence": [{"item_id": item["item_id"], "classification": "product_disaggregation",
                                 "rationale": "단일 보고부문 내 제품별 정보와 회계 범위를 구분했습니다."}
                                for item in src["review_items"]],
            "uncertainty": "단일 보고부문이 단일 경제적 매출·비용 동인을 뜻하지는 않습니다."}


class AnswerTransport:
    def __init__(self, quote=SINGLE, decision="SINGLE"):
        self.calls = []
        self.quote = quote
        self.decision = decision

    def complete(self, *, role, prompt):
        self.calls.append((role, prompt))
        req = json.loads(prompt.split("\n", 1)[1])
        return json.dumps(answer(req, self.quote, self.decision), ensure_ascii=False)


def loader(tmp_path, *, payload=None, transport=None, row=ROW):
    body = payload or archive()
    return opendart_filing_snapshot_loader(
        fetch_text=lambda url: json.dumps({"status": "000", "list": [row]}),
        fetch_bytes=lambda url: body, as_of=AS_OF, api_key="TEST",
        transport=transport, company_scope_receipt_path=tmp_path / "declarations/company_scope_receipt.json")


def test_realistic_parser_miss_emits_existing_assisted_file_handoff(tmp_path):
    transport = StaffTransport(tmp_path / "staff", mode="assisted")
    with pytest.raises(HostJudgmentRequired) as captured:
        loader(tmp_path, transport=transport)(IDENTITY)
    req = captured.value.request
    assert req.gap.judgment_kind == "company_scope"
    assert req.source_bindings[0]["screened_names"] == ["제품 A", "제품 B", "제품 C"]
    assert any(SINGLE in item["quote"] for item in req.source_bindings[0]["review_items"])
    assert list((tmp_path / "staff/requests").glob(f"{COMPANY_SCOPE_ROLE}.*.json"))
    assert not (tmp_path / "declarations/company_scope_receipt.json").exists()


def test_valid_single_candidate_requires_reentry_then_receipt_is_revalidated(tmp_path):
    transport = AnswerTransport()
    run = loader(tmp_path, transport=transport)
    context = OrchestratorContext("scope", ExecutionMode.LIVE_PRIMARY,
                                  {"resolved_company_identity": IDENTITY})
    first = live_industry_snapshot_adapter(loader=run)(context)
    assert first.status is StageStatus.RECOVERY_REQUIRED and first.blocking
    assert first.outputs["host_judgment_status"] == "ACCEPTED_REENTRY_REQUIRED"
    assert "industry_knowledge_snapshot" not in first.outputs
    path = tmp_path / "declarations/company_scope_receipt.json"
    assert not path.exists()
    path.parent.mkdir()
    path.write_text(json.dumps(first.outputs["host_judgment_receipt"]))  # coordinator promotion BETWEEN runs
    second = live_industry_snapshot_adapter(loader=run)(context)
    assert second.status is StageStatus.PASS
    snapshot = second.outputs["industry_knowledge_snapshot"]
    assert any("SEGMENT_SCOPE:SINGLE" in item for item in snapshot.evidence_ids)
    assert any("company-scope-v1:" in item.revision_id for item in snapshot.evidence_lineage)
    assert len(transport.calls) == 1
    assert snapshot.snapshot_hash and context.freeze_token is None


@pytest.mark.parametrize("field,value", [("document_id", "DART_20200101000000"),
    ("archive_sha256", "0" * 64), ("member_sha256", "0" * 64),
    ("member_path", "missing.xml"), ("quote", "연결기업은 단일 보고부문이라는 없는 인용입니다."),
    ("period", "사업보고서 (2020.12)"), ("scope", "PARENT"),
    ("reportable_segments", ["가짜 IFRS 제품부문"])])
def test_invalid_source_quote_period_and_scope_are_rejected(field, value):
    req, filing = source()
    proposal = answer(req)
    proposal["proposal"][field] = value
    with pytest.raises(HostJudgmentError):
        validate_company_scope_response(proposal, req, filing)


@pytest.mark.parametrize("field,value", [("request_hash", "0" * 64), ("target_id", "OTHER"),
                                         ("as_of", "2020-01-01"), ("decision", "PASS")])
def test_stale_or_wrong_company_and_pass_do_not_resume(field, value):
    req, filing = source()
    with pytest.raises(HostJudgmentError):
        validate_company_scope_response({**answer(req), field: value}, req, filing)


def test_all_counterevidence_and_source_members_must_be_reviewed():
    req, filing = source(extra="<p>제품별 매출액 정보입니다.</p>")
    response = answer(req)
    response["counterevidence"] = []
    with pytest.raises(HostJudgmentError, match="counterevidence"):
        validate_company_scope_response(response, req, filing)
    response = answer(req)
    response["proposal"]["reviewed_member_hashes"].pop("other.xml")
    with pytest.raises(HostJudgmentError, match="all current source"):
        validate_company_scope_response(response, req, filing)


def test_parent_only_single_cannot_override_consolidated_multi():
    parent = "지배회사는 단일 보고부문을 영위합니다."
    req, filing = source(parent, f"<p>{MULTI}</p>")
    with pytest.raises(HostJudgmentError):
        validate_company_scope_response(answer(req, parent), req, filing)


def test_conflicting_consolidated_notes_cannot_be_hidden_in_other_member():
    req, filing = source(extra=f"<p>{MULTI}</p>")
    with pytest.raises(HostJudgmentError, match="contrary consolidated multi"):
        validate_company_scope_response(answer(req), req, filing)


def test_multi_confirmation_still_requires_real_declared_segment_path(tmp_path):
    transport = AnswerTransport(MULTI, "MULTI")
    run = loader(tmp_path, payload=archive(MULTI), transport=transport)
    with pytest.raises(HostJudgmentRequired) as captured:
        run(IDENTITY)
    receipt = captured.value.accepted_receipt
    assert receipt["response"]["decision"] == "MULTI"
    path = tmp_path / "declarations/company_scope_receipt.json"
    path.parent.mkdir()
    path.write_text(json.dumps(receipt))
    with pytest.raises(GenericKRIndustryError, match="declare the reportable segments"):
        run(IDENTITY)
    assert len(transport.calls) == 1


def test_uncertain_does_not_create_receipt(tmp_path):
    with pytest.raises(HostJudgmentRequired) as captured:
        loader(tmp_path, transport=AnswerTransport(decision="UNCERTAIN"))(IDENTITY)
    assert captured.value.accepted_receipt is None
    assert "uncertain" in str(captured.value)


def test_other_issuer_and_tampered_archive_never_call_model(tmp_path):
    transport = AnswerTransport()
    with pytest.raises(HostJudgmentError, match="issuer"):
        loader(tmp_path, transport=transport, row={**ROW, "corp_code": "11111111"})(IDENTITY)
    assert not transport.calls
    req, filing = source()
    with pytest.raises(ValueError, match="archive"):
        validate_company_scope_response(answer(req), req, replace(filing, archive_bytes=b"forged"))


def test_stale_accepted_receipt_hard_blocks_without_new_model_override(tmp_path):
    transport = AnswerTransport()
    run = loader(tmp_path, transport=transport)
    with pytest.raises(HostJudgmentRequired) as captured:
        run(IDENTITY)
    path = tmp_path / "declarations/company_scope_receipt.json"
    path.parent.mkdir()
    path.write_text(json.dumps(captured.value.accepted_receipt))
    changed = loader(tmp_path, payload=archive(extra="<p>새로운 공시문구</p>"), transport=transport)
    context = OrchestratorContext("stale", ExecutionMode.LIVE_PRIMARY,
                                  {"resolved_company_identity": IDENTITY})
    result = live_industry_snapshot_adapter(loader=changed)(context)
    assert result.status is StageStatus.BLOCKED
    assert result.outputs == {"host_judgment_status": "HARD_BLOCKED"}
    assert len(transport.calls) == 1


@pytest.mark.parametrize("statement,quote", [
    ("연결기업은 단일 보고부문이 아니며 제품별로 영업 결과를 보고합니다.", "연결기업은 단일 보고부문"),
    ("연결기업은 다양한 사업을 영위하며 지배회사는 단일 보고부문입니다.",
     "연결기업은 다양한 사업을 영위하며 지배회사는 단일 보고부문입니다."),
    ("전기에는 연결기업 전체를 단일 보고부문으로 결정하였습니다. 당기에는 사업구조가 변경되었습니다.",
     "연결기업 전체를 단일 보고부문으로 결정하였습니다."),
])
def test_cropped_negation_parent_subject_and_historical_scope_rejected(statement, quote):
    req, filing = source(statement)
    with pytest.raises(HostJudgmentError):
        validate_company_scope_response(answer(req, quote), req, filing)


@pytest.mark.parametrize("total", ["180", "999"])
def test_actual_reportable_segment_table_blocks_single_even_if_unreconciled(total):
    table = f"""<p>영업부문 정보</p><table><tr><th>구분</th><th>철강부문</th>
<th>건설부문</th><th>부문합계</th></tr><tr><td>매출액</td><td>100</td><td>80</td><td>{total}</td></tr>
<tr><td>영업이익</td><td>10</td><td>8</td><td>18</td></tr></table>"""
    req, filing = source(extra=table)
    with pytest.raises(HostJudgmentError, match="reportable-segment table"):
        validate_company_scope_response(answer(req), req, filing)


def test_without_current_period_source_evidence_cannot_certify_scope():
    req, filing = source()
    response = answer(req)
    response["proposal"]["current_period_quote"] = "2026년 당기"  # absent, not a source quote
    with pytest.raises(HostJudgmentError, match="current-period"):
        validate_company_scope_response(response, req, filing)


def test_legacy_single_regex_does_not_hide_contradictory_multi_table(tmp_path):
    table = """<p>영업부문 정보</p><table><tr><th>구분</th><th>철강부문</th><th>건설부문</th>
<th>부문합계</th></tr><tr><td>매출액</td><td>100</td><td>80</td><td>180</td></tr>
<tr><td>영업이익</td><td>10</td><td>8</td><td>18</td></tr></table>"""
    transport = AnswerTransport()
    run = loader(tmp_path, payload=archive(SINGLE.replace("연결기업", "연결회사"), table), transport=transport)
    with pytest.raises(HostJudgmentRequired):
        run(IDENTITY)
    assert transport.calls


@pytest.mark.parametrize("statement", [
    "연결회사는 단일 보고부문이 아닙니다.",
    "연결회사는 다양한 사업을 영위하며 지배회사는 단일 보고부문입니다.",
    "전기에는 연결회사 전체를 단일 보고부문으로 결정하였습니다. 당기에는 구조가 변경되었습니다.",
])
def test_legacy_recognized_single_cannot_bypass_semantic_guard(tmp_path, statement):
    transport = AnswerTransport()
    with pytest.raises(HostJudgmentRequired):
        loader(tmp_path, payload=archive(statement), transport=transport)(IDENTITY)
    assert transport.calls


def test_multi_rejects_host_admitted_single_counterevidence():
    req, filing = source(MULTI)
    response = answer(req, MULTI, "MULTI")
    response["counterevidence"][0]["classification"] = "supports_single"
    with pytest.raises(HostJudgmentError, match="contrary consolidated single"):
        validate_company_scope_response(response, req, filing)


def test_numeric_historical_scope_date_cannot_borrow_current_marker():
    statement = "2025년 12월 31일 현재 연결기업 전체를 단일 보고부문으로 결정하였습니다."
    req, filing = source(statement)
    with pytest.raises(HostJudgmentError, match="scope date"):
        validate_company_scope_response(answer(req, statement), req, filing)


def test_positive_first_sentence_cannot_hide_immediate_opposite_conclusion():
    statement = SINGLE + " 그러나 현재는 그렇지 않습니다."
    req, filing = source(statement)
    with pytest.raises(HostJudgmentError, match="negated|qualified"):
        validate_company_scope_response(answer(req, SINGLE), req, filing)


def test_distant_current_marker_is_not_current_scope_proof():
    req, filing = source(" ".join(["공시본문"] * 250) + SINGLE)
    with pytest.raises(HostJudgmentError, match="local"):
        validate_company_scope_response(answer(req), req, filing)


@pytest.mark.parametrize("prefix", ["2025.12.31 현재 ", "2025-12-31 현재 "])
def test_numeric_date_punctuation_cannot_hide_scope_period(prefix):
    req, filing = source(prefix + SINGLE)
    with pytest.raises(HostJudgmentError, match="scope date"):
        validate_company_scope_response(answer(req), req, filing)


def test_legacy_numeric_historical_scope_is_sent_for_judgment(tmp_path):
    transport = AnswerTransport()
    statement = "2025년 12월 31일 현재 " + SINGLE.replace("연결기업", "연결회사")
    with pytest.raises(HostJudgmentRequired):
        loader(tmp_path, payload=archive(statement), transport=transport)(IDENTITY)
    assert transport.calls


@pytest.mark.parametrize("statement", [SINGLE, SINGLE.replace("연결기업", "연결회사")])
@pytest.mark.parametrize("field,value", [("corp_code", "11111111"), ("stock_code", "999999")])
def test_both_automatic_and_assisted_scope_reject_wrong_issuer_before_model(tmp_path, statement, field, value):
    transport = AnswerTransport()
    with pytest.raises(HostJudgmentError, match="issuer|stock code"):
        loader(tmp_path, payload=archive(statement), transport=transport,
               row={**ROW, field: value})(IDENTITY)
    assert transport.calls == []


@pytest.mark.parametrize("subject", ["지배기업", "종속기업", "모기업"])
def test_parent_and_subsidiary_accounting_synonyms_cannot_certify_group(subject):
    statement = f"연결기업은 다양한 사업을 영위하며 {subject}은 단일 보고부문입니다."
    req, filing = source(statement)
    with pytest.raises(HostJudgmentError, match="parent/subsidiary"):
        validate_company_scope_response(answer(req, statement), req, filing)
