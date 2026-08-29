"""The LLM reads the filing; only the extractor writes the number.

Static patterns cover the statutory table layouts they cover. Real disclosures
vary by company, and reading semi-structured Korean filing text is exactly what
a language model is for — so the model gets the job, inside the cage:

1. The analyst sees the filing's normalized visible text and the target
   metrics (definition, anchor vocabulary, allowed unit tokens). It answers
   with **locators**: for each metric, the member path, a VERBATIM quote from
   the text containing label, value and unit, and which characters in that
   quote are the value and the unit.
2. The verifier is deterministic and unforgiving:
   - the quote must exist in that member's visible text, exactly once — a
     fabricated number has no quote to be found in, so it dies here;
   - the quote must contain one of the metric's registered anchor terms — the
     model can only point at spans carrying the metric's own disclosure
     vocabulary, not relabel an unrelated figure;
   - the unit token must be in the metric's registered unit map — no invented
     units;
   - the claimed value text must appear inside the quote.
3. A surviving locator is compiled into an ordinary
   :class:`~.dart_kpi.DartKPIExtractionSpec` whose pattern is the escaped
   quote itself, and ``extract_dart_kpi`` re-reads the document through the
   same machinery the static path uses. The Evidence therefore carries the
   same receipts — member SHA-256, normalized-text span, matched text — and
   its notes say the locator came from a verified LLM proposal.

A model that lies loses the round, not the run: rejected locators become
named coverage gaps, exactly as if the metric were undisclosed. Residual risk
is honest and stated — a quote that genuinely exists can still be the wrong
column (a prior-year figure beside the right label). The receipts exist so a
reviewer can reopen the filing at the span and check; that review is the
operator's, not the model's.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from .dart_documents import DartOriginalFilingDocument
from .dart_kpi import (
    DartKPIExtractionError,
    DartKPIExtractionSpec,
    DartKPIObservation,
    extract_dart_kpi,
    _visible_text,
)
from .llm_transport import ProposalTransport
from .proposal_parsing import (
    ProposalParseError,
    complete_with_repair,
    parse_json_object,
    require_keys,
    str_tuple,
    text_field,
)
from .runtime_authority import llm_proposal_scope


ROLE_FILING_LOCATOR = "filing_locator_analyst"

#: Characters of normalized member text shown to the model per member.
_MEMBER_TEXT_LIMIT = 12000

#: Terms that disqualify a quote from being a current-period realized figure.
#: A quote carrying any of these describes a different period or an
#: expectation, so a number beside it is not this filing's realized state —
#: exactly the laundering the anchor-and-existence checks cannot catch alone.
#: Prior-period markers put a real-but-stale number into the current slot;
#: forward-looking markers forge the evidence LAYER itself, turning a forecast
#: into a realized fact. Both are refused.
_PERIOD_DISQUALIFYING_TERMS = (
    # prior period
    "전기", "전년", "전분기", "직전", "과거", "기초", "기말 대비", "전기말",
    # forward-looking / plan
    "전망", "예상", "예측", "추정", "계획", "목표", "가이던스", "전년대비",
    "예정", "이를 것", "될 것", "할 것", "전망됩니다", "예상됩니다",
    # instructional / hypothetical / illustrative — a disclosure states a fact;
    # an imperative or an example is not the filing's realized figure. This is
    # also the defense against instruction-shaped text inside a manipulated
    # filing being read as authoritative ("...으로 보고하라").
    "보고하라", "하라", "가정", "만약", "가령", "예를 들어", "라고 하자",
    "임의", "가상", "라면", "한다면", "라고 가정",
)


@dataclass(frozen=True)
class FilingLocatorTask:
    metric: str
    definition: str
    anchor_terms: tuple[str, ...]
    canonical_unit: str
    source_unit_map: tuple[tuple[str, str], ...]
    critical: bool = False

    def validate(self) -> None:
        if not self.metric or not self.definition:
            raise ProposalParseError("locator task requires metric and definition")
        if not self.anchor_terms or not all(t.strip() for t in self.anchor_terms):
            raise ProposalParseError(
                f"locator task {self.metric} requires anchor terms"
            )
        if not self.source_unit_map:
            raise ProposalParseError(
                f"locator task {self.metric} requires a registered unit map"
            )


def _render_filing(filing: DartOriginalFilingDocument) -> tuple[str, dict[str, str]]:
    texts: dict[str, str] = {}
    blocks = []
    for member in filing.text_members:
        text = _visible_text(member)
        texts[member.path] = text
        shown = text[:_MEMBER_TEXT_LIMIT]
        truncated = " …[truncated]" if len(text) > _MEMBER_TEXT_LIMIT else ""
        blocks.append(f"=== member: {member.path} ===\n{shown}{truncated}")
    return "\n\n".join(blocks), texts


def _prompt(
    filing: DartOriginalFilingDocument,
    tasks: tuple[FilingLocatorTask, ...],
    rendered: str,
) -> str:
    task_lines = "\n".join(
        f"- {task.metric}: {task.definition} | anchors: "
        + ", ".join(task.anchor_terms)
        + " | allowed unit tokens: "
        + ", ".join(token for token, _ in task.source_unit_map)
        for task in tasks
    )
    return (
        "You are a filing-locator analyst. Find where the statutory filing "
        "below discloses each target metric. You do not report numbers; you "
        "report LOCATIONS. A deterministic extractor re-reads the document at "
        "your locator and only what it re-extracts becomes evidence.\n\n"
        f"Filing {filing.rcept_no} normalized visible text:\n{rendered}\n\n"
        f"Target metrics:\n{task_lines}\n\n"
        """Return ONE JSON object:
{
 "locators": [
   {"metric": "...", "member_path": "...",
    "quote": "verbatim substring of the member text containing the anchor, the value and the unit token",
    "value_text": "the value exactly as it appears inside the quote (keep commas)",
    "unit_token": "one allowed unit token, exactly as it appears inside the quote"}
 ],
 "not_found": ["metrics you could not locate"]
}
Rules enforced mechanically: the quote must occur exactly once in that member's
text; it must contain one of the metric's anchors; value_text and unit_token
must appear inside the quote; unlisted unit tokens are rejected. Do not guess —
report a metric in not_found when the filing does not disclose it."""
    )


def _verify_and_extract(
    row: dict,
    *,
    filing: DartOriginalFilingDocument,
    tasks: dict[str, FilingLocatorTask],
    member_texts: dict[str, str],
    segment: str,
    effective_date: str,
) -> DartKPIObservation:
    require_keys(
        row,
        required=("metric", "member_path", "quote", "value_text", "unit_token"),
        label="locator",
    )
    metric = text_field(row["metric"], "locator.metric")
    task = tasks.get(metric)
    if task is None:
        raise ProposalParseError(f"locator names an unrequested metric: {metric}")
    member_path = text_field(row["member_path"], "locator.member_path")
    if member_path not in member_texts:
        raise ProposalParseError(
            f"locator for {metric} names an unknown member: {member_path}"
        )
    quote = text_field(row["quote"], "locator.quote")
    value_text = text_field(row["value_text"], "locator.value_text")
    unit_token = text_field(row["unit_token"], "locator.unit_token")

    body = member_texts[member_path]
    occurrences = body.count(quote)
    if occurrences == 0:
        raise ProposalParseError(
            f"locator quote for {metric} does not occur in {member_path}; "
            "a fabricated quote cannot become evidence"
        )
    if occurrences > 1:
        raise ProposalParseError(
            f"locator quote for {metric} occurs {occurrences} times in "
            f"{member_path}; extend the quote until it is unique"
        )
    if not any(anchor in quote for anchor in task.anchor_terms):
        raise ProposalParseError(
            f"locator quote for {metric} contains none of its anchor terms "
            f"({', '.join(task.anchor_terms)}); the model may only point at "
            "spans carrying the metric's own disclosure vocabulary"
        )
    disqualifying = tuple(
        term for term in _PERIOD_DISQUALIFYING_TERMS if term in quote
    )
    if disqualifying:
        raise ProposalParseError(
            f"locator quote for {metric} carries period/expectation markers "
            f"({', '.join(disqualifying)}); a prior-period or forward-looking "
            "figure cannot enter as a current realized value. Quote the "
            "current-period disclosure, or report the metric in not_found"
        )
    registered = {token for token, _ in task.source_unit_map}
    if unit_token not in registered:
        raise ProposalParseError(
            f"locator for {metric} claims unregistered unit token {unit_token!r}"
        )
    if value_text not in quote:
        raise ProposalParseError(
            f"locator value_text for {metric} does not appear in its quote"
        )
    if unit_token not in quote:
        raise ProposalParseError(
            f"locator unit token for {metric} does not appear in its quote"
        )

    escaped = re.escape(quote)
    escaped_value = re.escape(value_text)
    escaped_unit = re.escape(unit_token)
    if escaped.count(escaped_value) < 1:
        raise ProposalParseError(
            f"locator for {metric} could not bind its value capture"
        )
    pattern = escaped.replace(escaped_value, f"(?P<value>{escaped_value})", 1)
    if escaped_unit not in pattern:
        raise ProposalParseError(
            f"locator for {metric} could not bind its unit capture"
        )
    # Bind the LAST unit-token occurrence so a unit mentioned inside the label
    # ("(단위: 백만원)") does not shadow the one attached to the value.
    head, _, tail = pattern.rpartition(escaped_unit)
    pattern = head + f"(?P<unit>{escaped_unit})" + tail

    spec = DartKPIExtractionSpec(
        metric=metric,
        segment=segment,
        member_path_pattern=re.escape(member_path),
        value_pattern=pattern,
        canonical_unit=task.canonical_unit,
        effective_date=effective_date,
        locator_label=f"LLM locator (verified): {task.anchor_terms[0]}",
        source_unit_map=task.source_unit_map,
        critical=task.critical,
    )
    try:
        return extract_dart_kpi(filing, spec)
    except DartKPIExtractionError as exc:
        raise ProposalParseError(
            f"deterministic re-extraction rejected the locator for {metric}: {exc}"
        ) from exc


def propose_and_verify_filing_kpis(
    *,
    transport: ProposalTransport,
    filing: DartOriginalFilingDocument,
    tasks: tuple[FilingLocatorTask, ...],
    segment: str,
    effective_date: str,
    max_attempts: int = 2,
) -> tuple[DartKPIObservation, ...]:
    """Ask the model where each metric is disclosed; keep only what re-extracts.

    Returns observations for the locators that survived verification. Metrics
    the model reports in ``not_found``, and metrics whose locators are rejected
    after the bounded repair, simply produce no observation — they surface as
    named coverage gaps downstream, never as blocked collection.
    """
    if not tasks:
        return ()
    for task in tasks:
        task.validate()
    by_metric = {task.metric: task for task in tasks}
    rendered, member_texts = _render_filing(filing)
    prompt = _prompt(filing, tasks, rendered)

    def parse(text: str) -> tuple[DartKPIObservation, ...]:
        payload = parse_json_object(text)
        require_keys(
            payload,
            required=("locators",),
            optional=("not_found",),
            label="filing locator proposal",
        )
        rows = payload["locators"]
        if not isinstance(rows, list):
            raise ProposalParseError("locators must be a list")
        str_tuple(payload.get("not_found", []), "not_found")
        observations = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ProposalParseError("locator must be an object")
            observation = _verify_and_extract(
                row,
                filing=filing,
                tasks=by_metric,
                member_texts=member_texts,
                segment=segment,
                effective_date=effective_date,
            )
            if observation.metric in seen:
                raise ProposalParseError(
                    f"duplicate locator for metric {observation.metric}"
                )
            seen.add(observation.metric)
            observations.append(observation)
        return tuple(observations)

    with llm_proposal_scope():
        try:
            return complete_with_repair(
                transport=transport,
                role=ROLE_FILING_LOCATOR,
                prompt=prompt,
                parse=parse,
                max_attempts=max_attempts,
            )
        except ProposalParseError:
            # A model that cannot produce a verifiable locator leaves gaps,
            # not a blocked run: undisclosed and unlocatable end the same way.
            return ()
