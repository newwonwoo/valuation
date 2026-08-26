from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .assumption_compiler import AssumptionSpec, TRANSFORMS
from .capacity_commitment import CapacityCommitmentAssessment
from .control_plane import LLMAction, validate_llm_authority
from .ledger import EvidenceLedger
from .records import BridgeRecord, CriticalIssue, HypothesisRecord


@dataclass(frozen=True)
class IntelligenceProposal:
    hypotheses: tuple[HypothesisRecord, ...]
    requested_evidence: tuple[str, ...] = ()
    scanner_reinforcements: tuple[str, ...] = ()
    rationale: str = ""

    def validate(self, ledger: EvidenceLedger) -> None:
        if not self.rationale:
            raise ValueError("intelligence proposal requires rationale")
        seen: set[str] = set()
        for hypothesis in self.hypotheses:
            if hypothesis.id in seen:
                raise ValueError(f"duplicate hypothesis proposal: {hypothesis.id}")
            seen.add(hypothesis.id)
            for evidence_id in (
                *hypothesis.supporting_evidence_ids,
                *hypothesis.contradicting_evidence_ids,
            ):
                ledger.get(evidence_id)


@dataclass(frozen=True)
class RedTeamProposal:
    issues: tuple[CriticalIssue, ...]
    counter_thesis: str
    requested_evidence: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.counter_thesis:
            raise ValueError("red-team proposal requires counter thesis")
        ids = tuple(item.id for item in self.issues)
        if len(ids) != len(set(ids)):
            raise ValueError("red-team issue IDs must be unique")


@dataclass(frozen=True)
class BridgeDraft:
    assumption_key: str
    scenario_id: str
    bridge: BridgeRecord
    canonical_unit: str
    transform_id: str
    input_evidence_ids: tuple[str, ...] = ()
    min_value: str | None = None
    max_value: str | None = None
    probability_only_if_calibrated: bool = False

    def validate(
        self,
        ledger: EvidenceLedger,
        hypotheses: dict[str, HypothesisRecord],
    ) -> None:
        if not all(
            (
                self.assumption_key,
                self.scenario_id,
                self.canonical_unit,
                self.transform_id,
            )
        ):
            raise ValueError(
                "bridge draft requires assumption key, scenario, unit and transform"
            )
        if self.transform_id not in TRANSFORMS:
            raise ValueError(
                f"LLM proposed an unregistered transform: {self.transform_id}"
            )
        if self.bridge.hypothesis_id not in hypotheses:
            raise ValueError(
                f"bridge draft references unknown hypothesis: {self.bridge.hypothesis_id}"
            )
        input_ids = self.input_evidence_ids or self.bridge.evidence_ids
        if not input_ids:
            raise ValueError("bridge draft requires Evidence inputs")
        for evidence_id in input_ids:
            ledger.get(evidence_id)


@dataclass(frozen=True)
class BridgeProposalBundle:
    drafts: tuple[BridgeDraft, ...]
    rationale: str

    def validate(
        self,
        ledger: EvidenceLedger,
        hypotheses: tuple[HypothesisRecord, ...],
    ) -> None:
        if not self.rationale:
            raise ValueError("bridge proposal bundle requires rationale")
        hypothesis_map = {item.id: item for item in hypotheses}
        if len(hypothesis_map) != len(hypotheses):
            raise ValueError("duplicate hypothesis IDs in bridge proposal context")
        keys: set[tuple[str, str]] = set()
        bridge_ids: set[str] = set()
        for draft in self.drafts:
            draft.validate(ledger, hypothesis_map)
            key = (draft.scenario_id, draft.assumption_key)
            if key in keys:
                raise ValueError(f"duplicate assumption bridge draft: {key}")
            if draft.bridge.id in bridge_ids:
                raise ValueError(f"duplicate bridge ID: {draft.bridge.id}")
            keys.add(key)
            bridge_ids.add(draft.bridge.id)


@dataclass(frozen=True)
class LLMStaffContext:
    company: str
    ticker: str
    ledger: EvidenceLedger
    prior_hypotheses: tuple[HypothesisRecord, ...] = ()
    module_requirement_plan: object | None = None
    scanner_findings: tuple[object, ...] = ()
    funding_scan_result: object | None = None
    capacity_commitment_assessment: CapacityCommitmentAssessment | None = None


IntelligenceOfficer = Callable[[LLMStaffContext], IntelligenceProposal]
RedTeamOfficer = Callable[
    [LLMStaffContext, tuple[HypothesisRecord, ...]], RedTeamProposal
]
BridgeAnalyst = Callable[
    [LLMStaffContext, tuple[HypothesisRecord, ...], RedTeamProposal],
    BridgeProposalBundle,
]


def merge_hypothesis_context(
    prior: tuple[HypothesisRecord, ...],
    proposed: tuple[HypothesisRecord, ...],
) -> tuple[HypothesisRecord, ...]:
    combined = prior + proposed
    ids = tuple(item.id for item in combined)
    if len(ids) != len(set(ids)):
        raise ValueError(
            "prior and proposed hypothesis IDs must be unique; revisions require a new ID"
        )
    return combined


def run_intelligence_officer(
    context: LLMStaffContext,
    officer: IntelligenceOfficer,
) -> IntelligenceProposal:
    validate_llm_authority(LLMAction.OBSERVE)
    validate_llm_authority(LLMAction.REASON)
    validate_llm_authority(LLMAction.PROPOSE)
    proposal = officer(context)
    proposal.validate(context.ledger)
    merge_hypothesis_context(context.prior_hypotheses, proposal.hypotheses)
    return proposal


def run_red_team(
    context: LLMStaffContext,
    hypotheses: tuple[HypothesisRecord, ...],
    officer: RedTeamOfficer,
) -> RedTeamProposal:
    validate_llm_authority(LLMAction.REASON)
    validate_llm_authority(LLMAction.PROPOSE)
    if any(
        item.source_layer.value == "market_comparison"
        for item in context.ledger.active()
    ):
        raise PermissionError(
            "Blind Red Team context contains market-comparison Evidence"
        )
    proposal = officer(context, hypotheses)
    proposal.validate()
    return proposal


def run_bridge_analyst(
    context: LLMStaffContext,
    hypotheses: tuple[HypothesisRecord, ...],
    red_team: RedTeamProposal,
    analyst: BridgeAnalyst,
) -> BridgeProposalBundle:
    validate_llm_authority(LLMAction.PROPOSE)
    bundle = analyst(context, hypotheses, red_team)
    bundle.validate(context.ledger, hypotheses)
    return bundle


def materialize_bridge_bundle(
    bundle: BridgeProposalBundle,
) -> tuple[
    tuple[BridgeRecord, ...],
    tuple[AssumptionSpec, ...],
    dict[str, tuple[str, ...]],
]:
    """Turn validated LLM proposals into compiler requests, not committed assumptions."""
    from decimal import Decimal

    bridges: list[BridgeRecord] = []
    specs: list[AssumptionSpec] = []
    input_map: dict[str, tuple[str, ...]] = {}
    for draft in bundle.drafts:
        bridges.append(draft.bridge)
        specs.append(
            AssumptionSpec(
                key=draft.assumption_key,
                scenario_id=draft.scenario_id,
                bridge_id=draft.bridge.id,
                canonical_unit=draft.canonical_unit,
                transform_id=draft.transform_id,
                min_value=(
                    Decimal(draft.min_value)
                    if draft.min_value is not None
                    else None
                ),
                max_value=(
                    Decimal(draft.max_value)
                    if draft.max_value is not None
                    else None
                ),
                probability_only_if_calibrated=(
                    draft.probability_only_if_calibrated
                ),
            )
        )
        input_map[draft.bridge.id] = (
            draft.input_evidence_ids or draft.bridge.evidence_ids
        )
    return tuple(bridges), tuple(specs), input_map
