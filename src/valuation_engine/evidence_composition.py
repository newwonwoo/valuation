"""Evidence-layer composition of the numbers that actually drove value.

The Evidence Ledger already records a ``source_layer`` on every record, but nothing
downstream ever counted them. A run can therefore be fully hash-bound, fully
traceable and fully audited while every assumption that moved the valuation came
from ``analyst_underwriting`` — an analyst's or model's own judgement written into
the provider snapshot — and no reader would see that from the report.

This module measures two different populations and keeps them separate:

ledger composition
    Every active record. Answers "what did we collect?"

valuation-input composition
    Only the records reachable from ``CompiledAssumptionSet.assumptions[].evidence_ids``.
    Answers "what actually became a number in the model?" Filings sitting in the
    ledger as background context do not count here.

The findings never block. Underwriting is a legitimate, explicitly labelled layer;
the point is that its share must be visible rather than implicit.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
import json

from .assumption_compiler import CompiledAssumptionSet
from .control_plane import StageStatus
from .ledger import EvidenceLedger
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .records import AuditFinding, EvidenceRecord, EvidenceSourceLayer


_ZERO = Decimal("0")
_ONE = Decimal("1")

# Layers whose authority is an external filing or an official company statement,
# as opposed to a judgement supplied by the analyst or model.
PRIMARY_BACKED_LAYERS = (
    EvidenceSourceLayer.REALIZED_OR_FILING,
    EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN,
)

_LAYER_LABEL_KO = {
    EvidenceSourceLayer.REALIZED_OR_FILING: "공시·실적 원문",
    EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN: "회사 공식 계획",
    EvidenceSourceLayer.POLICY_PRIMARY_SOURCE: "정책 원문",
    EvidenceSourceLayer.AUTHORIZED_MARKET_DATA: "인가 시장데이터",
    EvidenceSourceLayer.ANALYST_UNDERWRITING: "분석가 추정",
    EvidenceSourceLayer.EXTERNAL_REFERENCE: "외부 참고자료",
    EvidenceSourceLayer.MARKET_COMPARISON: "시장 비교",
}


class EvidenceCompositionError(ValueError):
    """Raised when evidence-composition inputs violate their contract."""


def layer_label_ko(layer: EvidenceSourceLayer | str) -> str:
    if isinstance(layer, str):
        try:
            layer = EvidenceSourceLayer(layer)
        except ValueError:
            return layer
    return _LAYER_LABEL_KO.get(layer, layer.value)


@dataclass(frozen=True)
class EvidenceCompositionPolicy:
    """Visibility thresholds for how much of the model is judgement.

    These are reporting thresholds, not gates. A route can legitimately be
    underwriting-heavy; what is not legitimate is for that to be invisible.
    """

    min_valuation_primary_backed_share: Decimal = Decimal("0.20")
    max_valuation_underwriting_share: Decimal = Decimal("0.80")

    def validate(self) -> None:
        for name, value in (
            ("min_valuation_primary_backed_share", self.min_valuation_primary_backed_share),
            ("max_valuation_underwriting_share", self.max_valuation_underwriting_share),
        ):
            if not value.is_finite() or not _ZERO <= value <= _ONE:
                raise EvidenceCompositionError(f"{name} must be within [0, 1]")


@dataclass(frozen=True)
class LayerComposition:
    source_layer: str
    label: str
    count: int
    share: Decimal


@dataclass(frozen=True)
class EvidenceCompositionReport:
    ledger_active_count: int
    ledger_layers: tuple[LayerComposition, ...]
    valuation_input_count: int
    valuation_input_layers: tuple[LayerComposition, ...]
    valuation_input_evidence_ids: tuple[str, ...]
    valuation_primary_backed_share: Decimal
    valuation_underwriting_share: Decimal
    valuation_mean_confidence: Decimal | None
    findings: tuple[AuditFinding, ...]
    report_hash: str

    @property
    def passed(self) -> bool:
        return all(item.passed for item in self.findings)

    @property
    def warnings(self) -> tuple[AuditFinding, ...]:
        return tuple(item for item in self.findings if not item.passed)

    def layer_count(self, layer: EvidenceSourceLayer) -> int:
        for item in self.valuation_input_layers:
            if item.source_layer == layer.value:
                return item.count
        return 0

    @property
    def summary_ko(self) -> str:
        """One-line reader-facing composition of the value-driving assumptions."""
        if not self.valuation_input_count:
            return "가치 투입 가정이 없습니다"
        parts = " · ".join(
            f"{item.label} {item.count}건({item.share * 100:.0f}%)"
            for item in self.valuation_input_layers
        )
        return f"가치 투입 근거 {self.valuation_input_count}건 — {parts}"


def _compose(records: tuple[EvidenceRecord, ...]) -> tuple[LayerComposition, ...]:
    total = len(records)
    if not total:
        return ()
    counts: dict[EvidenceSourceLayer, int] = {}
    for record in records:
        counts[record.source_layer] = counts.get(record.source_layer, 0) + 1
    # Enum declaration order keeps the rendered composition stable across runs.
    return tuple(
        LayerComposition(
            source_layer=layer.value,
            label=layer_label_ko(layer),
            count=counts[layer],
            share=Decimal(counts[layer]) / Decimal(total),
        )
        for layer in EvidenceSourceLayer
        if layer in counts
    )


def _share(records: tuple[EvidenceRecord, ...], layers: tuple[EvidenceSourceLayer, ...]) -> Decimal:
    if not records:
        return _ZERO
    matched = sum(1 for record in records if record.source_layer in layers)
    return Decimal(matched) / Decimal(len(records))


def _findings(
    *,
    valuation_records: tuple[EvidenceRecord, ...],
    primary_backed_share: Decimal,
    underwriting_share: Decimal,
    policy: EvidenceCompositionPolicy,
) -> tuple[AuditFinding, ...]:
    measured = bool(valuation_records)
    findings = [
        AuditFinding(
            "evidence_composition_measured",
            measured,
            False,
            (
                f"가치 투입 근거 {len(valuation_records)}건의 출처 계층을 집계했습니다"
                if measured
                else "가치 투입 근거를 확인할 수 없어 출처 구성을 집계하지 못했습니다"
            ),
        )
    ]
    if not measured:
        return tuple(findings)

    primary_ok = primary_backed_share >= policy.min_valuation_primary_backed_share
    findings.append(
        AuditFinding(
            "evidence_composition_primary_backing",
            primary_ok,
            False,
            (
                f"가치 투입 근거의 {primary_backed_share * 100:.1f}%가 공시·회사 공식계획에서 "
                "직접 인용되었습니다"
                if primary_ok
                else (
                    f"가치 투입 근거 중 공시·회사 공식계획 직접 인용이 "
                    f"{primary_backed_share * 100:.1f}%로 기준"
                    f"({policy.min_valuation_primary_backed_share * 100:.0f}%)에 미달합니다"
                )
            ),
        )
    )

    underwriting_ok = underwriting_share <= policy.max_valuation_underwriting_share
    findings.append(
        AuditFinding(
            "evidence_composition_underwriting_concentration",
            underwriting_ok,
            False,
            (
                f"분석가 추정 비중이 {underwriting_share * 100:.1f}%로 기준 이내입니다"
                if underwriting_ok
                else (
                    f"가치 투입 근거의 {underwriting_share * 100:.1f}%가 분석가 추정입니다. "
                    "이 결과는 공시 사실의 정리가 아니라 추정 위에 세워진 모델입니다"
                )
            ),
        )
    )
    return tuple(findings)


def _report_hash(
    *,
    assumption_set_hash: str,
    ledger_layers: tuple[LayerComposition, ...],
    valuation_layers: tuple[LayerComposition, ...],
    valuation_input_evidence_ids: tuple[str, ...],
    findings: tuple[AuditFinding, ...],
) -> str:
    payload = {
        "contract": "evidence_composition/v1",
        "assumption_set_hash": assumption_set_hash,
        "ledger_layers": [[item.source_layer, item.count] for item in ledger_layers],
        "valuation_layers": [[item.source_layer, item.count] for item in valuation_layers],
        "valuation_input_evidence_ids": list(valuation_input_evidence_ids),
        "findings": [
            [item.check, item.passed, item.blocking, item.detail] for item in findings
        ],
    }
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def build_evidence_composition_report(
    *,
    ledger: EvidenceLedger,
    compiled: CompiledAssumptionSet,
    policy: EvidenceCompositionPolicy | None = None,
) -> EvidenceCompositionReport:
    """Measure how much of the committed model is filing versus judgement."""
    effective_policy = policy or EvidenceCompositionPolicy()
    effective_policy.validate()

    active = ledger.active()
    valuation_ids = tuple(
        sorted(
            {
                evidence_id
                for assumption in compiled.assumptions
                for evidence_id in assumption.evidence_ids
            }
        )
    )
    try:
        valuation_records = tuple(ledger.get(evidence_id) for evidence_id in valuation_ids)
    except ValueError as exc:
        raise EvidenceCompositionError(
            f"compiled assumption references unknown evidence: {exc}"
        ) from exc

    primary_backed_share = _share(valuation_records, PRIMARY_BACKED_LAYERS)
    underwriting_share = _share(
        valuation_records, (EvidenceSourceLayer.ANALYST_UNDERWRITING,)
    )
    mean_confidence = (
        sum((Decimal(str(item.confidence)) for item in valuation_records), _ZERO)
        / Decimal(len(valuation_records))
        if valuation_records
        else None
    )
    findings = _findings(
        valuation_records=valuation_records,
        primary_backed_share=primary_backed_share,
        underwriting_share=underwriting_share,
        policy=effective_policy,
    )
    ledger_layers = _compose(active)
    valuation_layers = _compose(valuation_records)
    return EvidenceCompositionReport(
        ledger_active_count=len(active),
        ledger_layers=ledger_layers,
        valuation_input_count=len(valuation_records),
        valuation_input_layers=valuation_layers,
        valuation_input_evidence_ids=valuation_ids,
        valuation_primary_backed_share=primary_backed_share,
        valuation_underwriting_share=underwriting_share,
        valuation_mean_confidence=mean_confidence,
        findings=findings,
        report_hash=_report_hash(
            assumption_set_hash=compiled.assumption_set_hash,
            ledger_layers=ledger_layers,
            valuation_layers=valuation_layers,
            valuation_input_evidence_ids=valuation_ids,
            findings=findings,
        ),
    )


def evidence_composition_audit_adapter(
    *,
    policy: EvidenceCompositionPolicy | None = None,
) -> StageAdapter:
    """Publish evidence composition as a non-blocking audit guardrail.

    Composition is a disclosure, not a gate: an underwriting-heavy route stays
    executable, but its findings are bound into the audit hash so the share cannot
    be quietly dropped from the record afterwards.
    """

    def run(context: OrchestratorContext) -> StageExecutionResult:
        ledger = context.data.get("evidence_ledger")
        compiled = context.data.get("compiled_assumption_set")
        if not isinstance(ledger, EvidenceLedger):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "EvidenceLedger is required before evidence-composition measurement",
                blocking=True,
            )
        if not isinstance(compiled, CompiledAssumptionSet):
            return StageExecutionResult(
                StageStatus.RECOVERY_REQUIRED,
                "CompiledAssumptionSet is required before evidence-composition measurement",
                blocking=True,
            )
        try:
            report = build_evidence_composition_report(
                ledger=ledger,
                compiled=compiled,
                policy=policy,
            )
        except (EvidenceCompositionError, TypeError, ValueError) as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"evidence-composition measurement failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )

        outputs = {
            "evidence_composition_report": report,
            "evidence_composition_hash": report.report_hash,
            "evidence_composition_summary": report.summary_ko,
        }
        if report.passed:
            return StageExecutionResult(
                StageStatus.PASS,
                "가치 투입 근거의 출처 계층 구성을 집계했습니다: " + report.summary_ko,
                outputs,
            )
        return StageExecutionResult(
            StageStatus.WARNING,
            "가치 투입 근거 구성에 확인이 필요합니다: " + report.summary_ko,
            outputs,
        )

    return run
