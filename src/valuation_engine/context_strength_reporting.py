from __future__ import annotations

from typing import Any

from .context_strength_linkage import (
    ContextStrengthLinkage,
    ContextStrengthLinkageDecision,
)
from .source_reporting import linked_evidence_ids


_MISSING_REQUIRED = "MISSING_REQUIRED"


def resolve_context_strength_linkage(
    data: dict[str, Any],
) -> tuple[str, tuple[ContextStrengthLinkage, ...], str]:
    decision = data.get("context_strength_linkage_decision")
    if decision is None:
        proposal = data.get("intelligence_proposal")
        decision = getattr(
            proposal,
            "context_strength_linkage_decision",
            None,
        )
    if decision is None:
        return _MISSING_REQUIRED, (), ""
    if not isinstance(decision, ContextStrengthLinkageDecision):
        raise TypeError(
            "context_strength_linkage_decision must be typed before reporting"
        )
    decision.validate()
    return (
        decision.status.value,
        decision.linkages,
        decision.not_applicable_reason,
    )


def render_context_strength_linkage_section(
    data: dict[str, Any],
) -> tuple[str, ...]:
    status, linkages, not_applicable_reason = resolve_context_strength_linkage(
        data
    )
    lines = [
        "## LLM Insight Layer — Environment × Corporate Strength",
        (
            "- Boundary: 이 영역은 외부 환경 변화와 기업의 기존 강점 사이의 "
            "비자명한 연결을 발견·반증하는 사고 계층이며, 밸류에이션 공식을 "
            "직접 변경하지 않습니다."
        ),
    ]
    if status == _MISSING_REQUIRED:
        lines.append(
            "- Status: MISSING_REQUIRED — canonical valuation report에 필요한 "
            "연결 인사이트 결정이 없습니다."
        )
        return tuple(lines)
    if not linkages:
        lines.extend(
            (
                "- Status: NOT_APPLICABLE",
                f"- Reason: {not_applicable_reason}",
            )
        )
        return tuple(lines)

    lines.append("- Status: APPLICABLE")
    for linkage in linkages:
        supporting = linked_evidence_ids(data, linkage.supporting_evidence_ids)
        contradicting = (
            linked_evidence_ids(data, linkage.contradicting_evidence_ids) or "없음"
        )
        lines.extend(
            (
                "",
                f"### {linkage.id}",
                f"- 외부 환경 변화: {linkage.external_change}",
                f"- 새 병목·전략적 필요: {linkage.emergent_need}",
                f"- 기업의 기존 강점: {linkage.company_strength}",
                f"- 비자명한 연결: {linkage.linkage_thesis}",
                f"- 시장의 인식 공백: {linkage.market_blind_spot}",
                f"- 가치 포착 경로: {linkage.value_capture_path}",
                f"- 인과 경로: {' → '.join(linkage.causal_chain)}",
                (
                    "- 시장 인식 트리거: "
                    + "; ".join(linkage.recognition_triggers)
                ),
                f"- 반증·철회 조건: {'; '.join(linkage.kill_conditions)}",
                f"- 다음 검증: {'; '.join(linkage.next_checks)}",
                f"- Supporting Evidence: {supporting}",
                f"- Contradicting Evidence: {contradicting}",
                f"- LLM confidence: {linkage.confidence:.0%}",
            )
        )
    return tuple(lines)


def context_strength_linkage_artifact(
    data: dict[str, Any],
) -> dict[str, Any]:
    status, linkages, not_applicable_reason = resolve_context_strength_linkage(
        data
    )
    return {
        "status": status,
        "not_applicable_reason": not_applicable_reason or None,
        "linkages": linkages,
    }


def context_strength_linkage_state(
    data: dict[str, Any],
) -> dict[str, Any]:
    status, linkages, _ = resolve_context_strength_linkage(data)
    return {
        "context_strength_linkage_status": status,
        "context_strength_linkage_ids": [item.id for item in linkages],
    }
