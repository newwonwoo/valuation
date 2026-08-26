from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class AuthorityClass(str, Enum):
    REGULATOR_PRIMARY = "regulator_primary"
    OFFICIAL_STATISTICS = "official_statistics"
    PUBLIC_RESEARCH = "public_research"
    INDUSTRY_ASSOCIATION = "industry_association"
    MULTILATERAL = "multilateral"
    COMPANY_PRIMARY = "company_primary"
    LICENSED_RESEARCH = "licensed_research"
    SECONDARY = "secondary"


class SourceRole(str, Enum):
    OBSERVED_STATE = "observed_state"
    INDUSTRY_STRUCTURE = "industry_structure"
    FORWARD_HYPOTHESIS = "forward_hypothesis"
    REGULATION_POLICY = "regulation_policy"
    MARKET_REFERENCE = "market_reference"
    DEFINITION_STANDARD = "definition_standard"


class AccessMode(str, Enum):
    API = "api"
    HTML_INDEX = "html_index"
    PUBLIC_FILE = "public_file"
    LICENSED = "licensed"
    MANUAL = "manual"


class ClaimKind(str, Enum):
    FACT = "fact"
    DEFINITION = "definition"
    FORECAST = "forecast"
    MECHANISM = "mechanism"
    BENCHMARK = "benchmark"
    LEADING_INDICATOR = "leading_indicator"
    VALUATION_LINK = "valuation_link"
    KILL_CONDITION = "kill_condition"
    POLICY_INTENT = "policy_intent"
    TRANSMISSION_EFFECT = "transmission_effect"


class PromotionStatus(str, Enum):
    SINGLE_SOURCE_CANDIDATE = "single_source_candidate"
    CORROBORATED = "corroborated"
    MODULE_RULE_CANDIDATE = "module_rule_candidate"
    MANUAL_APPROVAL_REQUIRED = "manual_approval_required"


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    source_family: str
    authority: AuthorityClass
    roles: tuple[SourceRole, ...]
    access_mode: AccessMode
    industries: tuple[str, ...]
    cadence: str
    public_fulltext_allowed: bool
    url: str
    notes: str = ""

    def validate(self) -> None:
        if not self.source_id or not self.source_family:
            raise ValueError("source_id and source_family are required")
        if not self.roles:
            raise ValueError("source requires at least one role")
        if self.access_mode is AccessMode.LICENSED and self.public_fulltext_allowed:
            raise ValueError("licensed source full text must not be stored in the public repository")


@dataclass(frozen=True)
class StructuredClaim:
    claim_id: str
    source_id: str
    source_family: str
    industry_node: str
    kind: ClaimKind
    statement: str
    period: str
    metric: str | None = None
    value: float | None = None
    unit: str | None = None
    definition_id: str | None = None
    economic_path_id: str | None = None
    lead_lag: str | None = None
    confidence: str = "unrated"


@dataclass(frozen=True)
class MechanismEvidence:
    claim: StructuredClaim
    source_role: SourceRole
    unresolved_definition_conflict: bool = False
    contradicted: bool = False


@dataclass(frozen=True)
class MechanismAssessment:
    status: PromotionStatus
    independent_source_families: int
    distinct_periods: int
    has_observed_state: bool
    has_industry_structure: bool
    has_leading_indicator: bool
    has_valuation_link: bool
    has_kill_condition: bool
    blocking_reason: str | None = None


def validate_claim_role(claim: StructuredClaim, source: SourceSpec, role: SourceRole) -> None:
    source.validate()
    if role not in source.roles:
        raise ValueError(f"source {source.source_id} is not eligible for role {role.value}")
    if claim.kind is ClaimKind.FORECAST and role is SourceRole.OBSERVED_STATE:
        raise ValueError("forecast cannot be promoted to observed state")
    if claim.kind is ClaimKind.POLICY_INTENT and role is SourceRole.OBSERVED_STATE:
        raise ValueError("policy intent is not realized operating evidence")


def assess_mechanism(evidence: Iterable[MechanismEvidence]) -> MechanismAssessment:
    items = tuple(evidence)
    if not items:
        return MechanismAssessment(
            PromotionStatus.SINGLE_SOURCE_CANDIDATE, 0, 0, False, False, False, False, False,
            "no evidence",
        )

    if any(x.unresolved_definition_conflict for x in items):
        return MechanismAssessment(
            PromotionStatus.SINGLE_SOURCE_CANDIDATE,
            len({x.claim.source_family for x in items}),
            len({x.claim.period for x in items}),
            any(x.source_role is SourceRole.OBSERVED_STATE for x in items),
            any(x.source_role is SourceRole.INDUSTRY_STRUCTURE for x in items),
            any(x.claim.kind is ClaimKind.LEADING_INDICATOR for x in items),
            any(x.claim.kind is ClaimKind.VALUATION_LINK for x in items),
            any(x.claim.kind is ClaimKind.KILL_CONDITION for x in items),
            "unresolved definition conflict",
        )

    families = len({x.claim.source_family for x in items if not x.contradicted})
    periods = len({x.claim.period for x in items if not x.contradicted})
    observed = any(x.source_role is SourceRole.OBSERVED_STATE and not x.contradicted for x in items)
    structure = any(x.source_role is SourceRole.INDUSTRY_STRUCTURE and not x.contradicted for x in items)
    leading = any(x.claim.kind is ClaimKind.LEADING_INDICATOR and not x.contradicted for x in items)
    valuation = any(x.claim.kind is ClaimKind.VALUATION_LINK and not x.contradicted for x in items)
    kill = any(x.claim.kind is ClaimKind.KILL_CONDITION and not x.contradicted for x in items)

    # One source family never establishes an industry mechanism, even if it repeats the same claim.
    if families < 2:
        status = PromotionStatus.SINGLE_SOURCE_CANDIDATE
    elif observed and structure:
        status = PromotionStatus.CORROBORATED
    else:
        status = PromotionStatus.SINGLE_SOURCE_CANDIDATE

    # A rule candidate must be connected to both monitoring and valuation and be falsifiable.
    if status is PromotionStatus.CORROBORATED and leading and valuation and kill and periods >= 2:
        status = PromotionStatus.MODULE_RULE_CANDIDATE

    # Canonical module rules are never auto-approved from ingestion output.
    if status is PromotionStatus.MODULE_RULE_CANDIDATE:
        return MechanismAssessment(
            PromotionStatus.MANUAL_APPROVAL_REQUIRED,
            families,
            periods,
            observed,
            structure,
            leading,
            valuation,
            kill,
            None,
        )

    return MechanismAssessment(status, families, periods, observed, structure, leading, valuation, kill, None)


def can_publish_raw_content(source: SourceSpec) -> bool:
    source.validate()
    return source.public_fulltext_allowed and source.access_mode is not AccessMode.LICENSED
