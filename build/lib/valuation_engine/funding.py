from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from math import isfinite


class FundingLayer(IntEnum):
    PRODUCT_OR_PROJECT = 1
    BUYER_CASH_FLOW = 2
    FINANCING_CHANNEL = 3
    COLLATERAL_VALUE = 4
    LENDING_TERMS = 5
    CREDIT_SPREAD = 6
    BENCHMARK_RATE = 7
    MARKET_PLUMBING = 8
    POLICY_BACKSTOP = 9


class ClaimStage(IntEnum):
    CONFIRMED_FACT = 0
    FIRST_ORDER_MECHANISM = 1
    SECOND_ORDER_TRANSMISSION = 2
    INVESTMENT_HYPOTHESIS = 3


@dataclass(frozen=True)
class FundingLink:
    lower_layer: FundingLayer
    upper_layer: FundingLayer
    statement: str
    claim_stage: ClaimStage
    confidence: float
    evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.statement:
            raise ValueError("funding link statement is required")
        if self.upper_layer <= self.lower_layer:
            raise ValueError("funding link must move upstream")
        if not isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("funding link confidence must be in [0, 1]")
        if self.claim_stage is ClaimStage.CONFIRMED_FACT and not self.evidence_ids:
            raise ValueError("confirmed funding fact requires evidence_ids")


@dataclass(frozen=True)
class PolicyTransmission:
    official_purpose: str
    transmission_effect: str
    official_evidence_ids: tuple[str, ...]
    transmission_evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.official_purpose or not self.transmission_effect:
            raise ValueError("policy purpose and transmission effect are required")
        if not self.official_evidence_ids:
            raise ValueError("official policy purpose requires primary evidence")
        if self.official_purpose.strip() == self.transmission_effect.strip():
            raise ValueError("Policy Intent must be stored separately from Transmission Effect")


@dataclass(frozen=True)
class FundingLadder:
    links: tuple[FundingLink, ...]

    def validate(self) -> None:
        if not self.links:
            raise ValueError("funding ladder requires at least one link")
        previous_upper: FundingLayer | None = None
        for link in self.links:
            if previous_upper is not None and link.lower_layer != previous_upper:
                raise ValueError("funding ladder links must form one contiguous upstream chain")
            previous_upper = link.upper_layer

    @property
    def highest_layer(self) -> FundingLayer:
        self.validate()
        return self.links[-1].upper_layer
