from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


_PLACEHOLDER_TEXT = frozenset(
    {
        "n/a",
        "na",
        "none",
        "unknown",
        "tbd",
        "not applicable",
        "not_applicable",
    }
)


def _validate_text(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} requires non-empty text")
    if value.strip().lower() in _PLACEHOLDER_TEXT:
        raise ValueError(f"{field_name} cannot use placeholder text")


def _validate_text_tuple(
    values: tuple[str, ...],
    field_name: str,
    *,
    required: bool = True,
) -> None:
    if not isinstance(values, tuple):
        raise TypeError(f"{field_name} must be a tuple")
    if required and not values:
        raise ValueError(f"{field_name} requires at least one item")
    for value in values:
        _validate_text(value, field_name)
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")


class ContextStrengthLinkageStatus(str, Enum):
    APPLICABLE = "APPLICABLE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ContextStrengthReasoningPriority(str, Enum):
    PRIMARY_GATE = "PRIMARY_GATE"


@dataclass(frozen=True)
class ContextStrengthLinkageDoctrine:
    """Provider-visible reasoning order for the primary LLM insight layer."""

    priority: ContextStrengthReasoningPriority
    reasoning_sequence: tuple[str, ...]
    prohibited_shortcuts: tuple[str, ...]
    valuation_boundary: str

    def validate(self) -> None:
        if self.priority is not ContextStrengthReasoningPriority.PRIMARY_GATE:
            raise ValueError(
                "context-strength linkage reasoning must remain the primary gate"
            )
        _validate_text_tuple(self.reasoning_sequence, "reasoning_sequence")
        if len(self.reasoning_sequence) < 7:
            raise ValueError(
                "reasoning_sequence must cover discovery, linkage, market blind "
                "spot, value capture, falsification and hypothesis handoff"
            )
        _validate_text_tuple(
            self.prohibited_shortcuts,
            "prohibited_shortcuts",
        )
        _validate_text(self.valuation_boundary, "valuation_boundary")


DEFAULT_CONTEXT_STRENGTH_LINKAGE_DOCTRINE = ContextStrengthLinkageDoctrine(
    priority=ContextStrengthReasoningPriority.PRIMARY_GATE,
    reasoning_sequence=(
        "Start outside the company: identify material structural changes in geopolitics, policy, regulation, industry bottlenecks, technology adoption, supply chains, financing or customer behavior without using current price or Street targets.",
        "Translate each external change into a newly scarce capability, bottleneck, strategic need or decision constraint.",
        "Inventory the company's already-existing strengths, assets, rights, location, network, installed base, operating know-how, customer access and capacity before relying on a future product claim.",
        "Test why the emergent need connects specifically and causally to this company's existing strength, including its right and capacity to absorb the benefit.",
        "Explain why the market may not yet have made the connection, such as category error, organizational research silos, legacy framing, financial-statement lag or attention captured by a more obvious technology narrative.",
        "Define the value-capture path and the first observable recognition triggers through contracts, utilization, pricing, margins, cash flow, strategic partnerships, regulation, procurement or acquisition interest.",
        "Search for contradicting evidence, bottlenecks and kill conditions that would break the linkage before assigning confidence.",
        "Only after the linkage survives falsification, formulate valuation hypotheses and send numeric inputs to deterministic Bridge and valuation controls.",
    ),
    prohibited_shortcuts=(
        "Do not treat keyword overlap or generic sector exposure as a non-obvious linkage.",
        "Do not make superior technology or a future product claim the entire investment thesis when the investor cannot independently verify it.",
        "Do not infer monetization from strategic importance without a right, capacity, contract or economic path to capture value.",
        "Do not invent a linkage merely to satisfy the gate; submit an explicit evidence-based not-applicable decision instead.",
        "Do not use target-company price, Street forecasts, target multiples or desired upside to select the linkage or its confidence.",
    ),
    valuation_boundary=(
        "This doctrine discovers and falsifies an investment idea. It cannot commit "
        "assumptions, scenario probabilities, discount rates, multiples, target "
        "prices or valuation arithmetic."
    ),
)
DEFAULT_CONTEXT_STRENGTH_LINKAGE_DOCTRINE.validate()


@dataclass(frozen=True)
class ContextStrengthLinkage:
    """Auditable LLM output for a non-obvious environment-to-strength connection.

    This is an idea-discovery record. It cannot commit assumptions, probabilities,
    valuation inputs, target prices, or market-comparison claims.
    """

    id: str
    external_change: str
    emergent_need: str
    company_strength: str
    linkage_thesis: str
    market_blind_spot: str
    value_capture_path: str
    causal_chain: tuple[str, ...]
    supporting_evidence_ids: tuple[str, ...]
    hypothesis_ids: tuple[str, ...]
    recognition_triggers: tuple[str, ...]
    kill_conditions: tuple[str, ...]
    next_checks: tuple[str, ...]
    contradicting_evidence_ids: tuple[str, ...] = ()
    confidence: float = 0.5

    def validate(self) -> None:
        for field_name in (
            "id",
            "external_change",
            "emergent_need",
            "company_strength",
            "linkage_thesis",
            "market_blind_spot",
            "value_capture_path",
        ):
            _validate_text(getattr(self, field_name), field_name)

        _validate_text_tuple(self.causal_chain, "causal_chain")
        if len(self.causal_chain) < 5:
            raise ValueError(
                "causal_chain must cover external change, emergent need, "
                "company strength, value capture and observable outcome"
            )
        _validate_text_tuple(
            self.supporting_evidence_ids,
            "supporting_evidence_ids",
        )
        _validate_text_tuple(self.hypothesis_ids, "hypothesis_ids")
        _validate_text_tuple(
            self.recognition_triggers,
            "recognition_triggers",
        )
        _validate_text_tuple(self.kill_conditions, "kill_conditions")
        _validate_text_tuple(self.next_checks, "next_checks")
        _validate_text_tuple(
            self.contradicting_evidence_ids,
            "contradicting_evidence_ids",
            required=False,
        )
        overlap = set(self.supporting_evidence_ids).intersection(
            self.contradicting_evidence_ids
        )
        if overlap:
            raise ValueError(
                "linkage Evidence cannot be both supporting and contradicting: "
                + ", ".join(sorted(overlap))
            )
        if isinstance(self.confidence, bool) or not isinstance(
            self.confidence,
            (int, float),
        ):
            raise TypeError("confidence must be numeric")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class ContextStrengthLinkageDecision:
    linkages: tuple[ContextStrengthLinkage, ...] = ()
    not_applicable_reason: str = ""

    def validate(self) -> None:
        if not isinstance(self.linkages, tuple):
            raise TypeError("context-strength linkages must be a tuple")
        if self.linkages and self.not_applicable_reason:
            raise ValueError(
                "linkages and not_applicable_reason are mutually exclusive"
            )
        if not self.linkages and not self.not_applicable_reason:
            raise ValueError(
                "context-strength linkage decision requires linkages or an "
                "explicit not-applicable reason"
            )
        if self.not_applicable_reason:
            _validate_text(
                self.not_applicable_reason,
                "not_applicable_reason",
            )
            if len(self.not_applicable_reason.strip()) < 30:
                raise ValueError(
                    "not_applicable_reason must explain why linkage reasoning "
                    "does not apply"
                )
        ids: set[str] = set()
        for linkage in self.linkages:
            if not isinstance(linkage, ContextStrengthLinkage):
                raise TypeError(
                    "context-strength linkage decision contains an untyped item"
                )
            linkage.validate()
            if linkage.id in ids:
                raise ValueError(
                    f"duplicate context-strength linkage ID: {linkage.id}"
                )
            ids.add(linkage.id)

    @property
    def status(self) -> ContextStrengthLinkageStatus:
        self.validate()
        return (
            ContextStrengthLinkageStatus.APPLICABLE
            if self.linkages
            else ContextStrengthLinkageStatus.NOT_APPLICABLE
        )
