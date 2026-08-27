from pathlib import Path

from valuation_engine.capacity_commitment import CapacityCommitmentAssessment
from valuation_engine.control_plane import (
    ExecutionMode,
    IntrinsicFreezeToken,
    StageStatus,
)
from valuation_engine.ledger import EvidenceLedger
from valuation_engine.orchestrator import (
    ControlledRunResult,
    StageTrace,
    load_reporting_contract,
    load_stage_sequence,
    summarize_major_gates,
)
from valuation_engine.report_form import (
    attest_controlled_run,
    render_controlled_run_report,
    render_report_form_template,
)
from valuation_engine.records import EvidenceRecord, EvidenceSourceLayer


ROOT = Path(__file__).resolve().parents[1]
STAGES = ROOT / "config" / "control_plane_stage_registry.yaml"
SOURCE_URL = "https://example.com/verified-filing"


def completed_result() -> ControlledRunResult:
    sequence = load_stage_sequence(STAGES)
    contract = load_reporting_contract(STAGES)
    token = IntrinsicFreezeToken(
        run_id="REPORT-RUN",
        ledger_snapshot_hash="LEDGER",
        assumption_set_hash="ASSUMPTIONS",
        valuation_hash="VALUATION",
        audit_hash="AUDIT",
        industry_snapshot_hash="INDUSTRY",
        source_snapshot_hash="SOURCE",
        token_hash="FREEZE",
    )
    data = {
        "ledger_snapshot_hash": "LEDGER",
        "assumption_set_hash": "ASSUMPTIONS",
        "scenario_set_hash": "SCENARIOS",
        "valuation_hash": "VALUATION",
        "audit_hash": "AUDIT",
        "audit_passed": True,
        "selected_methods": ("commodity_price_taker/normalized_multiple/1",),
        "capacity_commitment_assessment": CapacityCommitmentAssessment(
            (),
            "CAPACITY-ASSESSMENT",
        ),
        "capacity_commitment_assessment_hash": "CAPACITY-ASSESSMENT",
        "capacity_audit_hash": "CAPACITY-AUDIT",
        "capacity_audit_passed": True,
        "evidence_ledger": EvidenceLedger(
            (
                EvidenceRecord(
                    id="E-REPORT",
                    target="REPORT-RUN",
                    metric="normalized_earnings",
                    value=1,
                    unit="KRW",
                    source_layer=EvidenceSourceLayer.REALIZED_OR_FILING,
                    effective_date="2026-06-30",
                    observed_date="2026-08-01",
                    source_name="Verified filing",
                    source_ref=SOURCE_URL,
                    source_grade="A",
                    confidence=1.0,
                    segment="core",
                ),
            )
        ),
        "final_report": (
            "# 영구 저장된 시험 보고서\n\n"
            "## 투자 요약\n"
            "| 핵심 판단 항목 | 내용 |\n"
            "| --- | --- |\n"
            "| **투자판단** | 판단 유보 |\n"
            "| **현재가** | 미확보 |\n"
            "| **기준 내재가치** | 70,000원 |\n"
            "| **가치평가 범위** | 60,000~80,000원 |\n"
            "| **시나리오 가능성** | 미산출 |\n"
            "\n### 한 문장 결론\n시험 보고서입니다.\n"
            "\n### 투자포인트\n- 시험 가치동인입니다.\n"
            "\n### 판단 변경 조건\n- 시험 조건입니다.\n"
            "\n## 가치평가\n- 내재가치: 주당 70,000원\n"
            "\n## 핵심 가정과 위험\n- 가정: 시험 가정입니다.\n"
            "\n## 증권사·시장 비교\n- 비교: 시험 비교입니다.\n"
            "\n## 인공지능 인사이트 — 환경 변화 × 기업 강점\n"
            "- 적용범위: 연결 가설만 제시하며 가치평가 계산에는 관여하지 않습니다.\n"
            "\n## 정보 출처 — 원문 바로 확인\n"
            f"- 출처: [원문]({SOURCE_URL})\n"
        ),
    }
    traces = tuple(
        StageTrace(stage, StageStatus.PASS, "verified", False)
        for stage in sequence
    )
    return ControlledRunResult(
        run_id="REPORT-RUN",
        execution_mode=ExecutionMode.LIVE_PRIMARY,
        stage_traces=traces,
        data=data,
        blocked_reasons=(),
        freeze_token=token,
        major_gate_summaries=summarize_major_gates(traces, contract),
    )


def test_completed_controlled_run_is_verified_and_renders_trace():
    result = completed_result()
    attestation = attest_controlled_run(result, stage_registry_path=STAGES)
    report = render_controlled_run_report(result, stage_registry_path=STAGES)

    assert attestation.passed
    assert report.startswith("# 영구 저장된 시험 보고서")
    assert "검증 상태" not in report
    assert "### 33단계 진행 상태" in report
    assert "## 주요 작업 단계" in report
    assert "증권사·시장 비교·보고서 저장" in report
    assert "작성 근거와 계산 과정 보기" in report
    assert "| Gate |" not in report
    assert "CAPACITY-AUDIT" in report
    assert SOURCE_URL in report
    assert "# 영구 저장된 시험 보고서" in report


def test_manual_or_partial_result_cannot_be_labelled_verified():
    result = completed_result()
    broken = ControlledRunResult(
        run_id=result.run_id,
        execution_mode=result.execution_mode,
        stage_traces=result.stage_traces[:-1],
        data=result.data,
        blocked_reasons=(),
        freeze_token=result.freeze_token,
    )
    attestation = attest_controlled_run(broken, stage_registry_path=STAGES)
    report = render_controlled_run_report(broken, stage_registry_path=STAGES)

    assert not attestation.passed
    assert "검증 상태" not in report
    assert "> **확인 필요:**" in report


def test_report_form_template_contains_required_execution_identities():
    template = render_report_form_template()

    assert "capacity_audit_hash" in template
    assert "beta_snapshot_hash" in template
    assert "wacc_snapshot_hash" in template
    assert "freeze_token_hash" in template
    assert "투자보고서" in template
    assert "## 투자 요약" in template
    assert "### 한 문장 결론" in template
    assert "### 투자포인트" in template
    assert "### 판단 변경 조건" in template
    assert "**시나리오 가능성**" in template
    assert "probability_reporting_and_history_contract" in template
    assert "작성 근거와 계산 과정 보기" in template
    assert "검증 상태" not in template
    assert "major_gate_reporting_contract" in template
    assert "direct_source_links" in template
    assert "정보 출처 — 원문 바로 확인" in template
    assert "회사 강점·투자 결론·가치평가" in template
    assert "가치평가 가정·위험·출처" in template


def test_report_template_exposes_broker_research_audit_identity():
    template = render_report_form_template()

    assert "broker_research_primary_verification_chain" in template
    assert "broker_research_snapshot_hash" in template
    assert "broker_research_audit_hash" in template
