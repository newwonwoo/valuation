from __future__ import annotations

from typing import Any

from .context_strength_linkage import (
    ContextStrengthLinkage,
    ContextStrengthLinkageDecision,
)
_MISSING_REQUIRED = "MISSING_REQUIRED"
_MAX_LLM_REPORT_CHARS = 1000


def _short(value: str, limit: int) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "…"


def _bounded_llm_section(lines: list[str]) -> tuple[str, ...]:
    rendered = "\n".join(lines)
    if len(rendered) <= _MAX_LLM_REPORT_CHARS:
        return tuple(lines)
    fallback = (
        "## 인공지능 인사이트 — 환경 변화 × 기업 강점",
        "- 적용범위: 인공지능은 연결 가설과 반증 조건만 제시하며 가치평가 계산·가정 확정에는 관여하지 않습니다.",
        "- 요약이 1,000자 상한을 초과해 본문 표시를 축약했습니다.",
        "- 전체 형식화 인사이트와 근거 ID는 불변 `context_strength_linkages.json`에 보존됩니다.",
    )
    if len("\n".join(fallback)) > _MAX_LLM_REPORT_CHARS:
        raise ValueError("인공지능 인사이트 보고 구역이 1,000자 상한을 초과했습니다")
    return fallback


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
        "## 인공지능 인사이트 — 환경 변화 × 기업 강점",
        (
            "- 적용범위: 인공지능은 외부 환경 변화와 기업 강점의 연결 가설·반증 조건만 "
            "제시하며 가치평가 계산이나 가정 확정에는 관여하지 않습니다."
        ),
    ]
    if status == _MISSING_REQUIRED:
        lines.append(
            "- 상태: 필수정보 누락 (`MISSING_REQUIRED`) — 표준 가치평가 보고서에 필요한 "
            "연결 인사이트 결정이 없습니다."
        )
        return _bounded_llm_section(lines)
    if not linkages:
        lines.extend(
            (
                "- 상태: 해당 없음 (`NOT_APPLICABLE`)",
                f"- 사유: {not_applicable_reason}",
            )
        )
        return _bounded_llm_section(lines)

    lines.append("- 상태: 적용 (`APPLICABLE`)")
    linkage = linkages[0]
    evidence_ids = ", ".join(linkage.supporting_evidence_ids[:4]) or "없음"
    lines.extend(
        (
            f"- 연결: {_short(linkage.external_change, 105)} → {_short(linkage.company_strength, 105)}",
            f"- 핵심 추론: {_short(linkage.linkage_thesis, 170)}",
            f"- 가치 포착: {_short(linkage.value_capture_path, 120)}",
            f"- 반증 조건: {_short('; '.join(linkage.kill_conditions[:2]), 145)}",
            f"- 다음 검증: {_short('; '.join(linkage.next_checks[:2]), 110)}",
            f"- 근거 ID: {_short(evidence_ids, 120)} — 원문 링크는 `정보 출처 — 원문 직접 검증` 참조",
            f"- 인공지능 판단 신뢰도: {linkage.confidence:.0%}",
            "- 전체 형식화 인사이트는 불변 `context_strength_linkages.json`에 보존됩니다.",
        )
    )
    if len(linkages) > 1:
        lines.append(f"- 추가 연결 {len(linkages) - 1}건은 불변 산출물에 보존됩니다.")
    return _bounded_llm_section(lines)


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
