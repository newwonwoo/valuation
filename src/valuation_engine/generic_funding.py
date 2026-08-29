"""Company-neutral upstream funding scanner: an evidence screen that refuses to adjudicate.

A route that declares a funding scan (``funding_scan`` on the archetype module)
is saying funded demand matters to its valuation. The generic scanner does the
part of that job that is honestly deterministic:

- it screens the run's ledger for funding-related Evidence (advances, contract
  liabilities, borrowings, deposits, prepayments);
- it reports the observation as a ``CONFIRMED_FACT`` link — the *existence* of
  that Evidence is a fact — and the funded-demand state as ``UNKNOWN``, because
  deciding FUNDED/CONDITIONAL/UNFUNDED from keyword matches would be a judgment
  this screen has no basis for;
- when a required scan finds no funding Evidence at all it raises, and the
  stage fails closed naming the scans, because a route-required scan with
  nothing to look at is a collection gap, not a clean pass.

``UNKNOWN`` surfaces downstream as a WARNING and a verification request; it
never silently upgrades to funded demand and never touches WACC.
"""

from __future__ import annotations

from .decision_impact import ResearchEffort
from .funding import ClaimStage, FundingLadder, FundingLayer, FundingLink
from .funding_adapter import FundedDemandState, FundingScanContext, FundingScanResult


class GenericFundingScanError(ValueError):
    """Raised when a route-required funding scan has nothing to observe."""


#: Evidence-metric fragments that mark funding-relevant records.
FUNDING_METRIC_KEYWORDS: tuple[str, ...] = (
    "advance",
    "prepayment",
    "contract_liabilit",
    "deposit",
    "borrowing",
    "loan",
    "debt",
    "financing",
    "credit_facility",
    "customer_funding",
)


def generic_ledger_funding_scanner(context: FundingScanContext) -> FundingScanResult:
    matched = tuple(
        record
        for record in context.ledger.active()
        if any(keyword in record.metric.casefold() for keyword in FUNDING_METRIC_KEYWORDS)
    )
    if not matched:
        raise GenericFundingScanError(
            "route-required funding scan(s) "
            + ", ".join(context.required_scan_ids)
            + " found no funding-related Evidence in the ledger; collect advances/"
            "contract-liability/borrowing Evidence before funded demand can be assessed"
        )
    metrics = tuple(dict.fromkeys(record.metric for record in matched))
    evidence_ids = tuple(record.id for record in matched)
    ladder = FundingLadder(
        links=(
            FundingLink(
                lower_layer=FundingLayer.PRODUCT_OR_PROJECT,
                upper_layer=FundingLayer.BUYER_CASH_FLOW,
                statement=(
                    "collected filings evidence funding-related items exist: "
                    + ", ".join(sorted(metrics))
                ),
                claim_stage=ClaimStage.CONFIRMED_FACT,
                confidence=0.5,
                evidence_ids=evidence_ids,
            ),
        )
    )
    return FundingScanResult(
        state=FundedDemandState.UNKNOWN,
        summary=(
            "generic ledger screen observed funding-related Evidence but does not "
            "adjudicate funded-demand state; provider-grade assessment required for "
            + ", ".join(context.required_scan_ids)
        ),
        ladder=ladder,
        evidence_ids=evidence_ids,
        verification_requests=tuple(
            f"assess funded-demand state for required scan {scan_id} from primary sources"
            for scan_id in context.required_scan_ids
        ),
        economic_path_ids=("funding:upstream_demand",),
        effort=ResearchEffort(documents_reviewed=len(matched)),
    )
