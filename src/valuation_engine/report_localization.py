from __future__ import annotations

import re


_SCENARIO_LABELS = {
    "Down": "하방",
    "Core": "기준",
    "Bull": "상방",
    "Base": "기준",
}

_CURRENCY_LABELS = {
    "KRW": "원",
    "USD": "달러",
    "EUR": "유로",
    "JPY": "엔",
    "CNY": "위안",
}

_CALIBRATION_LABELS = {
    "CALIBRATED": "보정 완료",
    "UNCALIBRATED": "미보정",
    "DESCRIPTIVE_ONLY": "설명 전용",
    "NOT_APPLICABLE": "해당 없음",
}

_VALUATION_SCOPE_LABELS = {
    "FULL_INTRINSIC": "전체 기업 내재가치",
    "PARTIAL_INTRINSIC": "평가 완료 사업부 소계",
}

_GATE_LABELS = {
    "G1_EVIDENCE_ROUTING": "증거 수집·산업 라우팅",
    "G2_INSIGHT_CHALLENGE": "인사이트 도출·반증 검토",
    "G3_ASSUMPTIONS_METHOD_RISK": "가정·평가방법·위험",
    "G4_VALUATION_AUDIT_FREEZE": "가치평가·오류 점검·결과 확정",
    "G5_POST_FREEZE_PERSISTENCE": "증권사·시장 비교·보고서 저장",
}

_STATUS_LABELS = {
    "pass": "통과",
    "warning": "경고",
    "blocked": "차단",
    "skipped_not_applicable": "해당 없음",
    "not_implemented": "미구현",
    "recovered": "복구 완료",
    "recovery_required": "복구 필요",
    "awaiting_user_decision": "사용자 결정 대기",
}

_STAGE_LABELS = {
    "COMPANY_RESOLUTION": "기업 식별",
    "LOAD_COMPANY_STATE": "기존 분석 상태 불러오기",
    "LOAD_INDUSTRY_KNOWLEDGE_SNAPSHOT": "산업 지식 기준일 설정",
    "SOURCE_FRESHNESS_PRECHECK": "출처 최신성 사전점검",
    "SEGMENT_DECOMPOSITION": "사업부 분해",
    "INDUSTRY_DNA_ROUTE": "산업 특성 분류",
    "MODULE_REQUIREMENT_PLAN": "필수 분석 모듈 확정",
    "PRIMARY_EVIDENCE_COLLECTION": "1차 근거 수집",
    "EVIDENCE_LEDGER": "근거 기록 확정",
    "ROCKET_INSIGHT_SCAN": "환경 변화 인사이트 탐색",
    "UPSTREAM_FUNDING_SCAN": "상류 자금흐름 점검",
    "RESEARCHER_A": "주 분석가 가설 도출",
    "BLIND_RED_TEAM_B": "독립 반증 검토",
    "RESEARCH_LOOP": "추가 조사 반복",
    "EVIDENCE_TO_ASSUMPTION_BRIDGE": "근거·가정 연결",
    "SCENARIO_BUILD": "시나리오 구성",
    "VALUATION_METHOD_INTENT": "가치평가 방법 확정",
    "HIERARCHICAL_BETA_ESTIMATION": "계층형 베타 추정",
    "WACC_VALIDATION": "가중평균자본비용 검증",
    "DETERMINISTIC_VALUATION": "결정론적 가치평가",
    "HIERARCHICAL_WARRANTED_PER": "계층형 적정 주가수익비율",
    "DCF_PER_ASSUMPTION_CONSISTENCY_GATE": "현금흐름·주가수익비율 가정 정합성",
    "CROSS_METHOD_DOUBLE_COUNT_AUDIT": "평가방법 간 이중계상 감사",
    "PROBABILITY_DISTRIBUTION_ANALYSIS": "시나리오 확률 보정 점검",
    "AUDIT_GATE": "최종 감사",
    "INTRINSIC_VALUE_FREEZE": "가치평가 결과 확정",
    "STREET_REFERENCE_LOAD": "증권사 자료 불러오기",
    "STREET_GAP_ANALYZER": "증권사 목표가 비교",
    "MARKET_PRICE_LOAD": "현재 시장가격 불러오기",
    "MARKET_COMPARE": "시장가격 비교",
    "THESIS_DELTA": "투자논지 변화 점검",
    "SAVE_STATE": "분석 결과 저장",
    "FINAL_REPORT": "최종보고서 생성",
}

_MODULE_LABELS = {
    "ASSUMPTION_COMPILER": "가정 컴파일러",
    "BLIND_RED_TEAM_B": "독립 반증 검토",
    "BROKER_RESEARCH": "증권사 자료 검증",
    "DETERMINISTIC_VALUATION": "결정론적 가치평가",
    "EVIDENCE_LEDGER": "근거 원장",
    "EVIDENCE_TO_ASSUMPTION_BRIDGE": "근거·가정 연결",
    "HIERARCHICAL_BETA_ENGINE": "계층형 베타",
    "INDUSTRY_DNA_ROUTER": "산업 특성 분류",
    "INDUSTRY_KNOWLEDGE": "산업 지식",
    "KNOWLEDGE_PLACEMENT_GATE": "지식 배치 검증",
    "LLM_STAFF": "인공지능 분석 지원",
    "PRIMARY_EVIDENCE_COLLECTION": "1차 근거 수집",
    "ROCKET_INSIGHT_SCAN": "환경 변화 인사이트 탐색",
    "SCENARIO_ENGINE": "시나리오 엔진",
    "SIGNAL_INTELLIGENCE": "신호 탐색",
    "SOTP_AGGREGATOR": "사업부가치 합산",
    "UPSTREAM_FUNDING_SCAN": "상류 자금흐름 점검",
    "WACC_ENGINE": "가중평균자본비용",
    "WARRANTED_PER_ENGINE": "적정 주가수익비율",
}

_IDENTIFIER_LABELS = {
    "SANIL_SECOND_FACTORY_RAMP": "제2공장 가동 정상화",
    "SANIL_UHV_PROPERTY_ACQUISITION_20260826": "초고압 변압기 생산용 부동산 양수계약",
    "SANIL_UNDERWRITING_20260826": "산일전기 분석가 가치평가 가정",
    "SANIL_RISK_SOURCE_REGISTER_20260825_REGRESSION": "산일전기 위험 입력 출처 등록부",
    "SANIL_2025_ANNUAL_REPORT": "산일전기 2025년 사업보고서",
    "SANIL_2026_Q2_IR": "산일전기 2026년 2분기 기업설명자료",
    "frozen filing fixture": "고정 공시 시험자료",
    "Mirae Asset Securities": "미래에셋증권",
    "Shinhan Securities": "신한투자증권",
    "BrokerA": "증권사 A",
    "BrokerB": "증권사 B",
}

_METRIC_LABELS = {
    "asp": "평균판매가격",
    "backlog": "수주잔고",
    "backlog_conversion": "수주잔고의 매출 전환",
    "benchmark_price": "기준 가격",
    "book_to_bill": "수주·매출 비율",
    "cancellation_rate": "수주 취소율",
    "cancellation_terms": "수주 취소 조건",
    "capacity": "생산능력",
    "cash": "현금",
    "cash_cost": "현금원가",
    "cost_curve_position": "원가곡선상 위치",
    "diluted_shares": "희석주식수",
    "ev_adjustment": "기업가치 조정",
    "expansion_baseline_inclusion": "기존 생산능력 포함 여부",
    "expansion_cancelled": "증설 취소 여부",
    "expansion_capex_committed": "확정 증설 자본적지출",
    "expansion_land_control": "증설 부지 통제",
    "expansion_ramp_date": "증설 가동 시점",
    "inventory": "재고",
    "net_income_h1_2026": "2026년 상반기 순이익",
    "no_active_capacity_expansion": "진행 중 증설 부재 여부",
    "normalized_ebitda": "정상화 상각전영업이익",
    "normalized_ebitda_multiple": "정상화 상각전영업이익 배수",
    "normalized_earnings": "정상화 이익",
    "normalized_multiple": "정상화 배수",
    "orders": "수주",
    "ownership": "지분율",
    "production": "생산량",
    "realized_price": "실현 판매가격",
    "uhv_property_asset_ratio": "초고압 부동산 자산 비율",
    "uhv_property_contract_amount": "초고압 부동산 계약금액",
    "utilization": "설비가동률",
}


def scenario_label_ko(value: object) -> str:
    text = str(value)
    return _SCENARIO_LABELS.get(text, text)


def currency_label_ko(value: object) -> str:
    text = str(value)
    return _CURRENCY_LABELS.get(text, text)


def calibration_label_ko(value: object) -> str:
    text = str(value)
    return _CALIBRATION_LABELS.get(text, text)


def valuation_scope_label_ko(value: object) -> str:
    text = str(value)
    return _VALUATION_SCOPE_LABELS.get(text, text)


def status_label_ko(value: object) -> str:
    text = str(getattr(value, "value", value))
    return _STATUS_LABELS.get(text, text)


def next_action_label_ko(value: object) -> str:
    text = str(value)
    if text == "FINAL_RESULT_REPORT":
        return "최종 결과보고서"
    if text.startswith("RESOLVE_"):
        gate_id = text.removeprefix("RESOLVE_")
        return f"{_GATE_LABELS.get(gate_id, gate_id)} 차단 해소"
    return _GATE_LABELS.get(text, localize_stage_references(text))


def stage_label_ko(value: object) -> str:
    text = str(value)
    return _STAGE_LABELS.get(text, text)


def module_label_ko(value: object) -> str:
    text = str(value)
    return _MODULE_LABELS.get(text, stage_label_ko(text))


def identifier_label_ko(value: object) -> str:
    text = str(value)
    if text.startswith("증권사: "):
        broker = text.removeprefix("증권사: ")
        return f"증권사: {_IDENTIFIER_LABELS.get(broker, broker)}"
    if text.startswith("증권사 자료 탐색 / 증권사: "):
        broker = text.removeprefix("증권사 자료 탐색 / 증권사: ")
        return f"증권사 자료 탐색 / 증권사: {_IDENTIFIER_LABELS.get(broker, broker)}"
    return _IDENTIFIER_LABELS.get(text, text)


def metric_label_ko(value: object) -> str:
    text = str(value)
    if text.startswith("model_"):
        return "가치평가 모형 입력값"
    if text.startswith("beta_selection_"):
        return "베타 비교군 선정 근거"
    return _METRIC_LABELS.get(text, text)


def evidence_label_ko(evidence_id: object) -> str:
    text = str(evidence_id)
    suffix = text.rsplit(":", 1)[-1]
    return metric_label_ko(suffix)


def method_label_ko(value: object) -> str:
    text = str(value)
    if "driver_dcf" in text:
        return "핵심동인 현금흐름할인법"
    if "normalized_multiple" in text:
        return "정상화 이익배수법"
    if "finite_life_npv" in text:
        return "유한수명 순현재가치법"
    if "rnvp" in text.casefold() or "rnpv" in text.casefold():
        return "위험조정 순현재가치법"
    return "등록된 결정론적 가치평가법"


def localize_stage_references(value: object) -> str:
    text = str(value)
    for stage_id in sorted(_STAGE_LABELS, key=len, reverse=True):
        text = text.replace(stage_id, _STAGE_LABELS[stage_id])
    text = re.sub(r"\bpass\b", "통과", text, flags=re.IGNORECASE)
    text = re.sub(r"\bwarning\b", "경고", text, flags=re.IGNORECASE)
    text = re.sub(r"\bblocked\b", "차단", text, flags=re.IGNORECASE)
    return text
