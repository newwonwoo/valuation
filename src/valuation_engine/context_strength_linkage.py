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
            _validate_text(str(getattr(self, field_name)), field_name)

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
