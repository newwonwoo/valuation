"""Company-neutral LLM staff: the judgment seats, finally inside the rails.

Until now the ``IntelligenceOfficer`` / ``RedTeamOfficer`` / ``BridgeAnalyst``
callback seats were empty — type aliases with no implementation — so every
run's judgment was hand-written Python in a per-company module, entering the
pipeline *outside* the authority boundary. These officers close that hole: the
model is injected as a :class:`~.llm_transport.ProposalTransport`, and
everything around it — prompt rendering, strict parsing, typed construction,
bounded repair — is engine code that is identical for every company.

What the containment guarantees, mechanically:

- the officers run inside ``llm_proposal_scope`` (the existing ``run_*``
  wrappers apply it), so any attempt to call a committing function raises;
- the parser is *strict*: unknown JSON keys are errors, and the probability /
  calibration fields of a hypothesis are not accepted from the model at all —
  a proposal cannot smuggle a calibrated probability;
- every cited Evidence ID must exist in the run's ledger, checked here and
  again by the proposal contracts;
- every bridge value the model proposes is re-derived by the deterministic
  compiler from the cited Evidence through a registered transform, so an
  invented number dies with ``PROPOSAL_RECALC_MISMATCH``.

A malformed response gets exactly ``max_attempts`` tries (the validation error
is fed back verbatim), then the stage fails closed. There is no partial accept.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .assumption_compiler import TRANSFORMS
from .context_strength_linkage import (
    ContextStrengthLinkage,
    ContextStrengthLinkageDecision,
)
from .llm_staff import (
    BridgeDraft,
    BridgeProposalBundle,
    IntelligenceProposal,
    LLMStaffContext,
    RedTeamProposal,
)
from .llm_transport import (
    ROLE_BRIDGE,
    ROLE_INTELLIGENCE,
    ROLE_RED_TEAM,
    ProposalTransport,
)
from .records import (
    AffectedVariable,
    BridgeRecord,
    CriticalIssue,
    Direction,
    HypothesisRecord,
)
from .proposal_parsing import (
    ProposalParseError,
    complete_with_repair as _complete_with_repair_shared,
    number_field as _number,
    parse_json_object as _parse_json_object,
    require_keys as _require_keys_shared,
    str_tuple as _str_tuple,
    text_field as _text,
)
from .scanner_runtime import ScannerFinding


# ------------------------------------------------------------------ rendering


def _render_evidence_table(context: LLMStaffContext) -> str:
    rows = sorted(context.ledger.active(), key=lambda item: item.id)
    lines = ["| evidence_id | metric | value | unit | layer | source | effective |"]
    lines.append("|---|---|---|---|---|---|---|")
    for item in rows:
        lines.append(
            f"| {item.id} | {item.metric} | {item.value} | {item.unit} "
            f"| {item.source_layer.value} | {item.source_name} | {item.effective_date} |"
        )
    return "\n".join(lines)


def _render_scanner_findings(context: LLMStaffContext) -> str:
    lines = []
    for item in context.scanner_findings:
        if isinstance(item, ScannerFinding):
            lines.append(
                f"- [{item.scanner_id}/{item.status.value}] {item.summary}"
            )
    return "\n".join(lines) or "- (none)"


_COMMON_RULES = """\
Rules that are enforced mechanically, not stylistically:
- Cite ONLY evidence_id values from the table. An unknown ID fails the proposal.
- Respond with ONE JSON object and nothing else. Unknown keys fail the proposal.
- You are proposal-only. You cannot commit assumptions, weight probabilities,
  or reference market prices or price targets; none exist in your context.
"""


def _staff_header(context: LLMStaffContext) -> str:
    return (
        f"Company: {context.company} (ticker {context.ticker})\n\n"
        f"Evidence ledger (the complete set you may cite):\n"
        f"{_render_evidence_table(context)}\n\n"
        f"Scanner findings:\n{_render_scanner_findings(context)}\n\n"
        f"{_COMMON_RULES}"
    )


# -------------------------------------------------------------------- parsing


def _require_keys(
    payload: Mapping[str, Any],
    *,
    required: tuple[str, ...],
    optional: tuple[str, ...] = (),
    label: str,
) -> None:
    _require_keys_shared(payload, required=required, optional=optional, label=label)


def _check_evidence_ids(
    ids: tuple[str, ...], context: LLMStaffContext, label: str
) -> None:
    for evidence_id in ids:
        try:
            context.ledger.get(evidence_id)
        except (KeyError, ValueError) as exc:
            raise ProposalParseError(
                f"{label} cites unknown evidence_id: {evidence_id}"
            ) from exc


# --------------------------------------------------------------- repair loop


def _complete_with_repair(
    *,
    transport: ProposalTransport,
    role: str,
    prompt: str,
    parse,
    max_attempts: int,
):
    return _complete_with_repair_shared(
        transport=transport,
        role=role,
        prompt=prompt,
        parse=parse,
        max_attempts=max_attempts,
    )


# ------------------------------------------------------- intelligence officer


_LINKAGE_REQUIRED = (
    "id",
    "external_change",
    "emergent_need",
    "company_strength",
    "linkage_thesis",
    "market_blind_spot",
    "value_capture_path",
    "causal_chain",
    "supporting_evidence_ids",
    "hypothesis_ids",
    "recognition_triggers",
    "kill_conditions",
    "next_checks",
)


@dataclass(frozen=True)
class GenericIntelligenceOfficer:
    transport: ProposalTransport
    max_attempts: int = 2

    def _prompt(self, context: LLMStaffContext) -> str:
        return (
            "You are the Intelligence Officer in an evidence-first valuation "
            "control plane. Propose falsifiable hypotheses about the company's "
            "economics, grounded ONLY in the Evidence below.\n\n"
            + _staff_header(context)
            + """
Return JSON:
{
 "rationale": "why these hypotheses, in one paragraph",
 "hypotheses": [
   {"id": "H1", "statement": "...", "causal_chain": ["cause", "economic variable", "value variable"],
    "supporting_evidence_ids": ["..."], "contradicting_evidence_ids": [],
    "kill_conditions": ["observable condition that falsifies this"],
    "next_checks": ["..."]}
 ],
 "requested_evidence": [],
 "scanner_reinforcements": [],
 "context_strength_linkage": {"not_applicable_reason": "at least 30 characters"}
}
"context_strength_linkage" may instead carry {"linkages": [...]} where each
linkage has exactly these keys: """
            + ", ".join(_LINKAGE_REQUIRED)
            + " (plus optional contradicting_evidence_ids, confidence).\n"
            "Hypothesis IDs must be new and unique. You cannot assign "
            "probabilities or calibration; those fields do not exist for you."
        )

    def _parse_hypothesis(
        self, row: Any, context: LLMStaffContext
    ) -> HypothesisRecord:
        if not isinstance(row, dict):
            raise ProposalParseError("hypothesis must be an object")
        _require_keys(
            row,
            required=(
                "id",
                "statement",
                "causal_chain",
                "supporting_evidence_ids",
                "kill_conditions",
            ),
            optional=("contradicting_evidence_ids", "next_checks"),
            label="hypothesis",
        )
        supporting = _str_tuple(row["supporting_evidence_ids"], "supporting_evidence_ids")
        contradicting = _str_tuple(
            row.get("contradicting_evidence_ids", []), "contradicting_evidence_ids"
        )
        _check_evidence_ids(supporting + contradicting, context, "hypothesis")
        if not supporting:
            raise ProposalParseError("hypothesis requires supporting evidence")
        try:
            return HypothesisRecord(
                id=_text(row["id"], "hypothesis.id"),
                statement=_text(row["statement"], "hypothesis.statement"),
                causal_chain=_str_tuple(row["causal_chain"], "causal_chain"),
                supporting_evidence_ids=supporting,
                contradicting_evidence_ids=contradicting,
                kill_conditions=_str_tuple(row["kill_conditions"], "kill_conditions"),
                next_checks=_str_tuple(row.get("next_checks", []), "next_checks"),
            )
        except ValueError as exc:
            raise ProposalParseError(str(exc)) from exc

    def _parse_linkage(self, row: Any, context: LLMStaffContext) -> ContextStrengthLinkage:
        if not isinstance(row, dict):
            raise ProposalParseError("linkage must be an object")
        _require_keys(
            row,
            required=_LINKAGE_REQUIRED,
            optional=("contradicting_evidence_ids", "confidence"),
            label="context-strength linkage",
        )
        supporting = _str_tuple(row["supporting_evidence_ids"], "linkage evidence")
        contradicting = _str_tuple(
            row.get("contradicting_evidence_ids", []), "linkage contradicting evidence"
        )
        _check_evidence_ids(supporting + contradicting, context, "linkage")
        try:
            return ContextStrengthLinkage(
                id=_text(row["id"], "linkage.id"),
                external_change=_text(row["external_change"], "external_change"),
                emergent_need=_text(row["emergent_need"], "emergent_need"),
                company_strength=_text(row["company_strength"], "company_strength"),
                linkage_thesis=_text(row["linkage_thesis"], "linkage_thesis"),
                market_blind_spot=_text(row["market_blind_spot"], "market_blind_spot"),
                value_capture_path=_text(row["value_capture_path"], "value_capture_path"),
                causal_chain=_str_tuple(row["causal_chain"], "linkage causal_chain"),
                supporting_evidence_ids=supporting,
                hypothesis_ids=_str_tuple(row["hypothesis_ids"], "linkage hypothesis_ids"),
                recognition_triggers=_str_tuple(
                    row["recognition_triggers"], "recognition_triggers"
                ),
                kill_conditions=_str_tuple(row["kill_conditions"], "linkage kill_conditions"),
                next_checks=_str_tuple(row["next_checks"], "linkage next_checks"),
                contradicting_evidence_ids=contradicting,
                confidence=float(row.get("confidence", 0.5)),
            )
        except ValueError as exc:
            raise ProposalParseError(str(exc)) from exc

    def __call__(self, context: LLMStaffContext) -> IntelligenceProposal:
        def parse(text: str) -> IntelligenceProposal:
            payload = _parse_json_object(text)
            _require_keys(
                payload,
                required=("rationale", "hypotheses", "context_strength_linkage"),
                optional=("requested_evidence", "scanner_reinforcements"),
                label="intelligence proposal",
            )
            rows = payload["hypotheses"]
            if not isinstance(rows, list) or not rows:
                raise ProposalParseError("hypotheses must be a non-empty list")
            hypotheses = tuple(self._parse_hypothesis(row, context) for row in rows)
            linkage_row = payload["context_strength_linkage"]
            if not isinstance(linkage_row, dict):
                raise ProposalParseError("context_strength_linkage must be an object")
            _require_keys(
                linkage_row,
                required=(),
                optional=("not_applicable_reason", "linkages"),
                label="context_strength_linkage",
            )
            if "linkages" in linkage_row:
                decision = ContextStrengthLinkageDecision(
                    linkages=tuple(
                        self._parse_linkage(item, context)
                        for item in (linkage_row.get("linkages") or [])
                    ),
                )
            else:
                decision = ContextStrengthLinkageDecision(
                    not_applicable_reason=_text(
                        linkage_row.get("not_applicable_reason"),
                        "not_applicable_reason",
                    ),
                )
            try:
                decision.validate()
            except (TypeError, ValueError) as exc:
                raise ProposalParseError(str(exc)) from exc
            proposal = IntelligenceProposal(
                hypotheses=hypotheses,
                requested_evidence=_str_tuple(
                    payload.get("requested_evidence", []), "requested_evidence"
                ),
                scanner_reinforcements=_str_tuple(
                    payload.get("scanner_reinforcements", []), "scanner_reinforcements"
                ),
                rationale=_text(payload["rationale"], "rationale"),
                context_strength_linkage_decision=decision,
            )
            try:
                proposal.validate(
                    context.ledger,
                    known_hypotheses=(*context.prior_hypotheses, *hypotheses),
                    require_context_strength_linkage=(
                        context.require_context_strength_linkage
                    ),
                )
            except (TypeError, ValueError) as exc:
                raise ProposalParseError(str(exc)) from exc
            return proposal

        return _complete_with_repair(
            transport=self.transport,
            role=ROLE_INTELLIGENCE,
            prompt=self._prompt(context),
            parse=parse,
            max_attempts=self.max_attempts,
        )


# --------------------------------------------------------------- red team


@dataclass(frozen=True)
class GenericRedTeamOfficer:
    transport: ProposalTransport
    max_attempts: int = 2

    def _prompt(
        self,
        context: LLMStaffContext,
        hypotheses: tuple[HypothesisRecord, ...],
    ) -> str:
        listed = "\n".join(
            f"- {item.id}: {item.statement}" for item in hypotheses
        )
        return (
            "You are the Blind Red Team. You see the Evidence and the "
            "hypotheses, and deliberately no market data. Attack the "
            "hypotheses: what would make them wrong?\n\n"
            + _staff_header(context)
            + f"\nHypotheses under review:\n{listed}\n"
            + """
Return JSON:
{
 "counter_thesis": "the strongest coherent case against, one paragraph",
 "issues": [
   {"id": "R1", "description": "...", "blocking": false,
    "requested_evidence": []}
 ],
 "requested_evidence": []
}
Mark an issue blocking ONLY when valuation should not proceed until it is
resolved with new Evidence; a blocking issue stops the run.
"""
        )

    def __call__(
        self,
        context: LLMStaffContext,
        hypotheses: tuple[HypothesisRecord, ...],
    ) -> RedTeamProposal:
        def parse(text: str) -> RedTeamProposal:
            payload = _parse_json_object(text)
            _require_keys(
                payload,
                required=("counter_thesis", "issues"),
                optional=("requested_evidence",),
                label="red-team proposal",
            )
            rows = payload["issues"]
            if not isinstance(rows, list):
                raise ProposalParseError("issues must be a list")
            issues = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ProposalParseError("issue must be an object")
                _require_keys(
                    row,
                    required=("id", "description"),
                    optional=("blocking", "requested_evidence"),
                    label="issue",
                )
                blocking = row.get("blocking", False)
                if not isinstance(blocking, bool):
                    raise ProposalParseError("issue.blocking must be a boolean")
                issues.append(
                    CriticalIssue(
                        id=_text(row["id"], "issue.id"),
                        description=_text(row["description"], "issue.description"),
                        blocking=blocking,
                        resolved=False,
                        requested_evidence=_str_tuple(
                            row.get("requested_evidence", []),
                            "issue.requested_evidence",
                        ),
                    )
                )
            proposal = RedTeamProposal(
                issues=tuple(issues),
                counter_thesis=_text(payload["counter_thesis"], "counter_thesis"),
                requested_evidence=_str_tuple(
                    payload.get("requested_evidence", []), "requested_evidence"
                ),
            )
            try:
                proposal.validate()
            except ValueError as exc:
                raise ProposalParseError(str(exc)) from exc
            return proposal

        return _complete_with_repair(
            transport=self.transport,
            role=ROLE_RED_TEAM,
            prompt=self._prompt(context, hypotheses),
            parse=parse,
            max_attempts=self.max_attempts,
        )


# --------------------------------------------------------------- bridge


_AFFECTED_VARIABLES = {item.value: item for item in AffectedVariable}
_DIRECTIONS = {item.value: item for item in Direction}


@dataclass(frozen=True)
class GenericBridgeAnalyst:
    """Proposes one assumption bridge per (scenario, required key).

    ``scenario_ids`` and ``required_keys`` come from the run configuration, so
    the analyst knows exactly which cells the compiler will demand; a missing
    cell surfaces as a compiler finding, an extra cell as a duplicate error.
    """

    transport: ProposalTransport
    scenario_ids: tuple[str, ...]
    required_keys: tuple[str, ...]
    max_attempts: int = 2

    def _prompt(
        self,
        context: LLMStaffContext,
        hypotheses: tuple[HypothesisRecord, ...],
        red_team: RedTeamProposal,
    ) -> str:
        listed = "\n".join(f"- {item.id}: {item.statement}" for item in hypotheses)
        return (
            "You are the Bridge Analyst. Convert Evidence into assumption "
            "bridge proposals. You propose; a deterministic compiler commits. "
            "Every value you propose is re-derived from the cited Evidence "
            "through the declared transform — a value that does not re-derive "
            "is rejected.\n\n"
            + _staff_header(context)
            + f"\nHypotheses:\n{listed}\n"
            + f"\nRed-team counter thesis: {red_team.counter_thesis}\n"
            + f"\nScenarios: {', '.join(self.scenario_ids)}"
            + f"\nRequired assumption keys per scenario: {', '.join(self.required_keys)}"
            + f"\nRegistered transforms: {', '.join(sorted(TRANSFORMS))}\n"
            + """
Return JSON:
{
 "rationale": "...",
 "drafts": [
   {"assumption_key": "...", "scenario_id": "...",
    "hypothesis_id": "...", "evidence_ids": ["..."],
    "affected_variable": "quantity|price|margin|multiple|net_debt|discount_rate|probability|segment_value|share_count|utilization|mix|yield|funding_gap",
    "direction": "up|down|unchanged",
    "value": 0.0, "unit": "unit of the value",
    "canonical_unit": "unit the compiler should emit",
    "transform_id": "identity_observation",
    "rationale": "...", "confidence": 0.7,
    "kill_condition": "...", "verification_event": "...",
    "economic_path_id": "path:<segment>:<driver>"}
 ]
}
Cover every (scenario, required key) pair exactly once. With
identity_observation the value MUST equal the cited Evidence value.
"""
        )

    def _parse_draft(self, row: Any, context: LLMStaffContext) -> BridgeDraft:
        if not isinstance(row, dict):
            raise ProposalParseError("draft must be an object")
        _require_keys(
            row,
            required=(
                "assumption_key",
                "scenario_id",
                "hypothesis_id",
                "evidence_ids",
                "affected_variable",
                "direction",
                "value",
                "unit",
                "canonical_unit",
                "transform_id",
                "rationale",
                "kill_condition",
                "verification_event",
                "economic_path_id",
            ),
            optional=("confidence", "old_value", "min_value", "max_value"),
            label="bridge draft",
        )
        evidence_ids = _str_tuple(row["evidence_ids"], "draft.evidence_ids")
        if not evidence_ids:
            raise ProposalParseError("bridge draft requires evidence_ids")
        _check_evidence_ids(evidence_ids, context, "bridge draft")
        variable = str(row["affected_variable"])
        if variable not in _AFFECTED_VARIABLES:
            raise ProposalParseError(f"unknown affected_variable: {variable}")
        direction = str(row["direction"])
        if direction not in _DIRECTIONS:
            raise ProposalParseError(f"unknown direction: {direction}")
        transform_id = _text(row["transform_id"], "transform_id")
        if transform_id not in TRANSFORMS:
            raise ProposalParseError(f"unregistered transform: {transform_id}")
        value = _number(row["value"], "draft.value")
        assumption_key = _text(row["assumption_key"], "assumption_key")
        scenario_id = _text(row["scenario_id"], "scenario_id")
        confidence = row.get("confidence", 0.6)
        try:
            bridge = BridgeRecord(
                id=f"B:{assumption_key}:{scenario_id}",
                evidence_ids=evidence_ids,
                hypothesis_id=_text(row["hypothesis_id"], "hypothesis_id"),
                affected_variable=_AFFECTED_VARIABLES[variable],
                direction=_DIRECTIONS[direction],
                old_value=_number(row.get("old_value", value), "draft.old_value"),
                new_value=value,
                unit=_text(row["unit"], "draft.unit"),
                rationale=_text(row["rationale"], "draft.rationale"),
                confidence=float(confidence),
                kill_condition=_text(row["kill_condition"], "kill_condition"),
                verification_event=_text(row["verification_event"], "verification_event"),
                economic_path_id=_text(row["economic_path_id"], "economic_path_id"),
            )
            return BridgeDraft(
                assumption_key=assumption_key,
                scenario_id=scenario_id,
                bridge=bridge,
                canonical_unit=_text(row["canonical_unit"], "canonical_unit"),
                transform_id=transform_id,
                input_evidence_ids=evidence_ids,
                min_value=(
                    str(row["min_value"]) if row.get("min_value") is not None else None
                ),
                max_value=(
                    str(row["max_value"]) if row.get("max_value") is not None else None
                ),
            )
        except ValueError as exc:
            raise ProposalParseError(str(exc)) from exc

    def __call__(
        self,
        context: LLMStaffContext,
        hypotheses: tuple[HypothesisRecord, ...],
        red_team: RedTeamProposal,
    ) -> BridgeProposalBundle:
        expected = {
            (scenario, key)
            for scenario in self.scenario_ids
            for key in self.required_keys
        }

        def parse(text: str) -> BridgeProposalBundle:
            payload = _parse_json_object(text)
            _require_keys(
                payload,
                required=("rationale", "drafts"),
                label="bridge proposal",
            )
            rows = payload["drafts"]
            if not isinstance(rows, list) or not rows:
                raise ProposalParseError("drafts must be a non-empty list")
            drafts = tuple(self._parse_draft(row, context) for row in rows)
            produced = {(item.scenario_id, item.assumption_key) for item in drafts}
            missing = expected - produced
            if missing:
                raise ProposalParseError(
                    "drafts are missing required (scenario, key) cells: "
                    + ", ".join(f"{s}/{k}" for s, k in sorted(missing))
                )
            bundle = BridgeProposalBundle(
                drafts=drafts,
                rationale=_text(payload["rationale"], "rationale"),
            )
            try:
                bundle.validate(context.ledger, hypotheses)
            except (KeyError, ValueError) as exc:
                raise ProposalParseError(str(exc)) from exc
            return bundle

        return _complete_with_repair(
            transport=self.transport,
            role=ROLE_BRIDGE,
            prompt=self._prompt(context, hypotheses, red_team),
            parse=parse,
            max_attempts=self.max_attempts,
        )
