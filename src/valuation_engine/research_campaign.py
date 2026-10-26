"""Sensitivity-led, host-operated research; proposals enter the existing compiler.

This module validates provenance structure, not the truth of a downloaded source.
The host must read sources. An accepted response remains analyst underwriting.
No stage pass, freeze, probability calibration or current price is accepted here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import date, datetime
from decimal import Context, Decimal, InvalidOperation, localcontext
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Callable, Mapping

from .actual_units import measure_from_raw, unit_def, Dimension
from .research_sensitivity import DriverRange, assess_research_sensitivity, rank_research_priorities
from .research_task_execution import ResearchContext, ResearchTask, run_research_tasks
from .source_reporting import canonical_verification_url


class ResearchCampaignError(ValueError):
    pass


RESEARCH_APPROACHES = (
    ("primary", "공시·IR·계약·인허가에서 직접 확인하거나 공시 숫자로 산출하세요."),
    ("independent", "고객·공급자·산업 통계와 증권사 원자료 단서로 교차 확인하세요."),
    ("peer_adjusted", "유사 사업의 실제 이익률·단위경제성을 찾고 규모·가동률·회계기준 차이를 명시적으로 조정하세요."),
    ("bounded_inference", "남은 불확실성을 명시적 가정과 하방·기준·상방 범위로 수치화하세요. 근거 없는 기본값은 쓰지 마세요."),
)


def research_recovery_context(attempt: int, feedback: str | None = None) -> dict:
    """Change the research method after a miss without changing answer identity."""
    tier, instructions = RESEARCH_APPROACHES[min(attempt, len(RESEARCH_APPROACHES) - 1)]
    return {"attempt": attempt + 1, "approach": tier, "instructions": instructions,
            "previous_issue": feedback,
            "remaining_approaches": [x[0] for x in RESEARCH_APPROACHES[attempt + 1:]]}


def fingerprint(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _number(value: object, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ResearchCampaignError(f"{label}: a finite number is required")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise ResearchCampaignError(f"{label}: invalid number") from exc
    if not number.is_finite():
        raise ResearchCampaignError(f"{label}: non-finite number")
    return number


def _text(value: object, label: str, minimum: int = 1) -> str:
    if not isinstance(value, str) or len(value.strip()) < minimum:
        raise ResearchCampaignError(f"{label}: substantive text is required")
    return value.strip()


def _date(value: object, label: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise ResearchCampaignError(f"{label}: YYYY-MM-DD required") from exc


def _safe_id(value: object) -> str:
    value = _text(value, "request_id")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ResearchCampaignError("request_id must be a safe file component")
    return value


def _mapping(value: object, label: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise ResearchCampaignError(f"{label} must be an object")
    return value


def _key(value: object) -> str:
    return re.sub(r"[\s-]+", "_", str(value).strip().lower())


_FORBIDDEN = {
    "current_price", "target_price", "market_price", "market_cap", "rating",
    "target_multiple", "target_per", "consensus_target", "calibrated_probability",
    "freeze_token", "audit_passed", "stage_status", "target_company_forecast",
    "target_company_consensus", "street_forecast", "street_consensus",
    "final_value", "intrinsic_value", "fair_value", "value_per_share", "probabilities",
}

_BROKER_SOURCE_KINDS = {"broker_lead", "broker_estimate"}
_BROKER_CLAIM_TYPES = {
    "realized_fact", "company_guidance", "broker_forecast", "industry_forecast",
    "industry_mechanism", "method_assumption", "target_price", "rating",
    "target_multiple", "consensus", "aggregate",
}
_COMPARISON_ONLY_CLAIMS = {"target_price", "rating", "target_multiple"}
_CLAIM_BASIS_FIELDS = (
    "subject_id", "metric", "period", "perimeter", "accounting_basis", "unit",
)


_INFERENCE_BASIS_FIELDS = (
    "period_start", "period_end", "frequency", "scope", "perimeter", "accounting_basis", "currency",
)


def _revision_context(container: Mapping):
    """A review is a proposal receipt; it never grants same-run value authority."""
    mode = container.get("research_mode", "BLIND_BASELINE")
    context = container.get("informed_revision")
    if mode not in {"BLIND_BASELINE", "INFORMED_REVISION"}:
        raise ResearchCampaignError("unknown research_mode")
    if mode == "BLIND_BASELINE":
        if context is not None:
            raise ResearchCampaignError("broker review requires an explicitly INFORMED_REVISION")
        return None
    context = _mapping(context, "informed_revision")
    if set(context) != {"schema_version", "baseline_run_id", "new_run_id", "review_receipt"}:
        raise ResearchCampaignError("informed revision requires exact baseline/new-run/review bindings")
    if context["schema_version"] != "broker-informed-revision/v1":
        raise ResearchCampaignError("unsupported informed revision schema")
    from .broker_judgment import broker_review_receipt_from_dict
    receipt = broker_review_receipt_from_dict(context["review_receipt"])
    baseline = _text(context["baseline_run_id"], "baseline_run_id")
    new_run = _text(context["new_run_id"], "new_run_id")
    if baseline != receipt.run_id or new_run == baseline or container["target_id"] != receipt.target_id:
        raise ResearchCampaignError("informed revision target or baseline/new-run binding mismatch")
    cutoff = _date(container["as_of"], "as_of")
    try:
        comparison_date = datetime.fromisoformat(receipt.comparison_as_of.replace("Z", "+00:00")).date()
    except (ValueError, AttributeError) as exc:
        raise ResearchCampaignError("invalid comparison cutoff") from exc
    if comparison_date > cutoff or _date(receipt.intrinsic_as_of, "intrinsic_as_of") > cutoff:
        raise ResearchCampaignError("informed revision cannot precede its frozen baseline or review")
    return receipt



def _bind_context_evidence(response: Mapping, sources: list, receipt) -> tuple[set, list]:
    """Bind cited frozen records/artifacts, preserving their original authority."""
    index = {item.context_id: item for item in getattr(receipt, "context_evidence_index", ())}
    bindings = response.get("context_evidence_bindings", [])
    if not isinstance(bindings, list):
        raise ResearchCampaignError("context_evidence_bindings must be a list")
    source_map = {item["source_id"]: item for item in sources}
    known, seen, verified = set(), set(), []
    required = {"context_id", "source_ref", "source_content_sha256", "payload_hash"}
    for binding in bindings:
        _mapping(binding, "context evidence binding")
        if not required.issubset(binding) or set(binding) - required - {"source_id"}:
            raise ResearchCampaignError("context evidence requires exact ID/source/content/payload bindings")
        context = index.get(binding["context_id"])
        if context is None or context.context_id in seen:
            raise ResearchCampaignError("unknown or duplicate context evidence identity")
        seen.add(context.context_id)
        if any(binding[key] != getattr(context, key) for key in ("source_ref", "source_content_sha256", "payload_hash")):
            raise ResearchCampaignError("context evidence source/hash differs from reviewed context")
        if context.context_kind == "FOLLOWUP_RESEARCH_ARTIFACT":
            artifact = Path(context.source_ref)
            if not artifact.is_file() or sha256(artifact.read_bytes()).hexdigest() != context.source_content_sha256:
                raise ResearchCampaignError("research artifact bytes differ from reviewed context hash")
            if "source_id" in binding:
                raise ResearchCampaignError("research artifact cannot be relabelled as an original public source")
        elif context.context_kind != "FROZEN_EVIDENCE":
            raise ResearchCampaignError("unsupported context evidence kind")
        if (context.context_kind == "FROZEN_EVIDENCE" and "source_id" not in binding
                and any(source["url"] == context.source_ref for source in sources)):
            raise ResearchCampaignError("a supplied primary context source requires its exact source_id binding")
        if "source_id" in binding:
            source = source_map.get(binding["source_id"])
            if source is None or source["url"] != context.source_ref:
                raise ResearchCampaignError("context binding cites a different or unknown response source")
            if context.source_content_sha256 is not None:
                if source["content_sha256"] != context.source_content_sha256:
                    raise ResearchCampaignError("primary context source content hash mismatch")
            elif source.get("context_payload_hash") != context.payload_hash:
                raise ResearchCampaignError("unknown document hash requires the exact frozen payload binding")
            if (context.underlying_source_families and source["source_family"] not in context.underlying_source_families
                    or not context.underlying_source_families and source["source_family"] != "UNKNOWN"):
                raise ResearchCampaignError("context source family differs from reviewed provenance")
            layer = _key(context.source_layer)
            if source["kind"] == "primary" and layer not in {"realized_or_filing", "policy_primary_source"}:
                raise ResearchCampaignError("context evidence cannot be promoted to primary fact")
            if source["kind"] == "company_plan" and layer != "company_official_plan":
                raise ResearchCampaignError("context evidence cannot be promoted to a company plan")
        known.update(context.citation_aliases)
        known.add(context.context_id)
        verified.append({"context_id": context.context_id, "context_kind": context.context_kind,
            "source_ref": context.source_ref, "source_content_sha256": context.source_content_sha256,
            "payload_hash": context.payload_hash, "source_layer": context.source_layer,
            "status": context.status})
    return known, verified


def _informed_original_source_policy(order: Mapping, response: Mapping, sources: list, receipt) -> dict:
    """Original facts/calculations keep their type in an informed investigation."""
    if response["kind"] not in {"observed", "calculated"}:
        raise ResearchCampaignError("informed assumption requires an individually reviewed candidate")
    broker_urls = {item.source_ref for item in receipt.decisions}
    broker_hashes = {item.reference_content_sha256 for item in receipt.decisions}
    for source in sources:
        if (source["kind"] != "primary" or source.get("review_claim_id") is not None
                or source["url"] in broker_urls or source["content_sha256"] in broker_hashes
                or _key(source.get("claim_type", "")) in {"broker_forecast", "industry_forecast", "consensus",
                    "aggregate", "company_guidance", "target_price", "rating", "target_multiple"}):
            raise ResearchCampaignError("original factual research cannot relabel broker/forecast/plan evidence")
    _, context = _bind_context_evidence(response, sources, receipt)
    return {"policy_version": "broker-research-ingress/v2", "research_mode": "INFORMED_REVISION",
        "review_receipt_hash": receipt.receipt_hash, "baseline_run_id": receipt.run_id,
        "new_run_id": order["informed_revision"]["new_run_id"],
        "classification_required_source_ids": [], "dispositions": [{"source_id": source["source_id"],
            "disposition": "ORIGINAL_PRIMARY_FACT_RESEARCH", "research_kind": response["kind"]} for source in sources],
        "context_evidence": context, "verification": "provenance_and_calculation_not_source_truth"}


def _informed_broker_policy(order: Mapping, response: Mapping, sources: list, receipt) -> dict:
    """Check the individually judged candidate's lineage, not its persuasiveness."""
    if response["kind"] != "inferred":
        raise ResearchCampaignError("informed broker assumptions must remain inferred underwriting")
    selection = _mapping(response.get("informed_assumption"), "informed_assumption")
    if set(selection) != {"claim_id", "candidate_id", "review_receipt_hash", "inference_basis", "derivation"}:
        raise ResearchCampaignError("informed assumption requires complete review and basis bindings")
    if selection["review_receipt_hash"] != receipt.receipt_hash:
        raise ResearchCampaignError("informed assumption review receipt hash mismatch")
    selected = next((x for x in receipt.decisions if x.claim_id == selection["claim_id"]), None)
    if (selected is None or selected.disposition != "PROPOSE_NEW_RUN_ASSUMPTION"
            or selected.assumption_candidate is None):
        raise ResearchCampaignError("selected claim has no individually reviewed assumption candidate")
    candidate = selected.assumption_candidate
    if selection["candidate_id"] != candidate.candidate_id:
        raise ResearchCampaignError("informed assumption candidate mismatch")
    req = order["request"]
    if candidate.metric != req["metric"] or candidate.unit != req["unit"]:
        raise ResearchCampaignError("candidate metric/unit differs from requested driver")
    basis = {key: getattr(candidate, key) for key in _INFERENCE_BASIS_FIELDS}
    if selection["inference_basis"] != basis or req.get("inference_basis") != basis:
        raise ResearchCampaignError("candidate and requested period/perimeter/accounting basis differ")
    _text(selection["derivation"], "informed adoption/adjustment derivation", 20)
    value = _number(response["value"], "value")
    low = _number(response["bounds"]["low"], "low")
    high = _number(response["bounds"]["high"], "high")
    if candidate.value is not None:
        if value != _number(candidate.value, "candidate value"):
            raise ResearchCampaignError("response value differs from reviewed point candidate")
    elif (low, high) != (_number(candidate.low, "candidate low"), _number(candidate.high, "candidate high")):
        raise ResearchCampaignError("response bounds differ from reviewed range candidate")
    index = {x.claim_id: x for x in receipt.decisions}
    known_refs, context_evidence = _bind_context_evidence(response, sources, receipt)
    bound_claims, dispositions = set(), []
    context_aliases = {alias for item in getattr(receipt, "context_evidence_index", ())
                       for alias in (*item.citation_aliases, item.context_id)}
    for source in sources:
        # Context aliases are admitted only by the exact context binding above.
        known_refs.update(ref for ref in (source["source_id"], source["url"]) if ref not in context_aliases)
        review_id = source.get("review_claim_id")
        if source["kind"] not in _BROKER_SOURCE_KINDS and review_id is None:
            if _key(source.get("claim_type", "")) == "broker_forecast":
                raise ResearchCampaignError("broker forecast cannot be relabelled as a non-broker source")
            continue
        reviewed = index.get(review_id)
        if reviewed is None:
            raise ResearchCampaignError("broker source requires a known review_claim_id")
        if source["url"] != reviewed.source_ref or source["content_sha256"] != reviewed.reference_content_sha256:
            raise ResearchCampaignError("broker source URL/content differs from reviewed original")
        if source["kind"] not in _BROKER_SOURCE_KINDS:
            raise ResearchCampaignError("reviewed broker source cannot be relabelled as primary or industry")
        if (reviewed.underlying_source_families and source["source_family"] not in reviewed.underlying_source_families
                or not reviewed.underlying_source_families and source["source_family"] != "UNKNOWN"):
            raise ResearchCampaignError("broker source family differs from reviewed provenance; unknown must remain UNKNOWN")
        if source.get("claim_type") and _key(source["claim_type"]) != _key(reviewed.claim_type):
            raise ResearchCampaignError("broker source classification differs from reviewed claim")
        original = reviewed.original_published_at
        expected_date = original[:10] if original is not None else None
        if source.get("published_at") != expected_date:
            raise ResearchCampaignError("broker source original publication date differs from review")
        if expected_date is None and source.get("publication_status") != "UNKNOWN":
            raise ResearchCampaignError("unknown original date must remain explicitly UNKNOWN")
        bound_claims.add(reviewed.claim_id)
        known_refs.update((reviewed.claim_id, reviewed.reference_id, reviewed.source_ref))
        dispositions.append({"source_id": source["source_id"], "claim_id": reviewed.claim_id,
            "claim_type": reviewed.claim_type, "disposition": "INDIVIDUALLY_REVIEWED_INFERENCE",
            "claim_payload_hash": reviewed.claim_payload_hash,
            "underlying_source_families": list(reviewed.underlying_source_families)})
    if selected.claim_id not in bound_claims or set(candidate.evidence_refs) - known_refs:
        raise ResearchCampaignError("candidate evidence lacks exact source/content bindings")
    return {"policy_version": "broker-research-ingress/v2", "research_mode": "INFORMED_REVISION",
        "review_receipt_hash": receipt.receipt_hash, "candidate_id": candidate.candidate_id,
        "baseline_run_id": receipt.run_id, "new_run_id": order["informed_revision"]["new_run_id"],
        "classification_required_source_ids": [], "dispositions": dispositions,
        "context_evidence": context_evidence,
        "verification": "lineage_and_basis_only_not_source_truth_or_economic_persuasiveness"}


def _broker_source_policy(order: Mapping, response: Mapping, sources: list) -> dict:
    """Classify broker assistance without certifying a source's factual truth.

    A broker-discovered fact/plan may support *our* bounded inference after its
    exact claim basis is tied to an independent source. A broker's issuer
    forecast is not such a fact, even when labelled inferred or discovery.
    Unclassified legacy estimates remain a repairable research gap, not a
    grandfathered path into the compiler.
    """
    receipt = _revision_context(order)
    if receipt is not None:
        if "informed_assumption" in response:
            return _informed_broker_policy(order, response, sources, receipt)
        return _informed_original_source_policy(order, response, sources, receipt)
    if "informed_assumption" in response:
        raise ResearchCampaignError("informed assumption cannot enter a blind baseline")
    source_map = {source["source_id"]: source for source in sources}
    missing, dispositions = [], []
    for source in sources:
        kind = source["kind"]
        claim_type = _key(source.get("claim_type", ""))
        scope = _key(source.get("subject_scope", ""))
        # Classification also applies to non-broker labels: changing kind to
        # primary must not launder an explicitly identified Street forecast.
        if claim_type in _COMPARISON_ONLY_CLAIMS or (scope == "target" and claim_type in {"consensus", "aggregate"}):
            raise ResearchCampaignError("comparison-only broker claim cannot be a valuation input")
        if scope == "target" and claim_type in {"broker_forecast", "industry_forecast"}:
            raise ResearchCampaignError(
                "target-company broker forecast is post-freeze only; independently rebuild "
                "operating assumptions in a new canonical run"
            )
        if claim_type == "broker_forecast" and kind not in _BROKER_SOURCE_KINDS:
            raise ResearchCampaignError("broker forecast cannot be relabelled as a non-broker source")
        if kind not in _BROKER_SOURCE_KINDS:
            continue
        if claim_type not in _BROKER_CLAIM_TYPES or scope not in {"target", "industry", "peer"}:
            # Existing unclassified leads may remain pointers alongside actual
            # independent evidence, but cannot carry the numerical proposal.
            legacy_lead = kind == "broker_lead" and not claim_type and not scope
            independent = tuple(s["source_id"] for s in sources
                                if s["kind"] in {"primary", "company_plan", "industry"})
            if legacy_lead and independent:
                dispositions.append({"source_id": source["source_id"],
                                     "disposition": "LEGACY_DISCOVERY_ONLY",
                                     "independent_source_ids": list(independent)})
                continue
            missing.append(source["source_id"])
            continue
        if source.get("subject_id") == order["target_id"] and scope != "target":
            raise ResearchCampaignError("target subject identity cannot be relabelled as industry or peer")
        if scope == "target":
            if source.get("subject_id") != order["target_id"]:
                raise ResearchCampaignError("target broker claim requires the exact target subject_id")
            support_ids = source.get("independent_support_source_ids")
            if (not isinstance(support_ids, list) or not support_ids
                    or len(support_ids) != len(set(support_ids))):
                raise ResearchCampaignError("target broker lead requires independent support source IDs")
            for field in _CLAIM_BASIS_FIELDS:
                _text(source.get(field), f"broker claim {field}")
            if source["metric"] != order["request"]["metric"]:
                raise ResearchCampaignError("broker claim metric differs from the requested driver")
            if source["unit"] != order["request"]["unit"]:
                raise ResearchCampaignError("broker claim unit differs from the requested driver")
            if claim_type in {"realized_fact", "company_guidance"}:
                _number(source.get("claim_value"), "broker fact/plan claim value")
            for sid in support_ids:
                support = source_map.get(sid)
                if support is None or support["kind"] not in {"primary", "company_plan", "industry"}:
                    raise ResearchCampaignError("broker support must reference a known non-broker source")
                if any(source[field] != support.get(field) for field in _CLAIM_BASIS_FIELDS):
                    raise ResearchCampaignError("broker support has different period, perimeter or claim basis")
                if support.get("evidence_relation") != "supports":
                    raise ResearchCampaignError("contradictory or contextual evidence cannot certify broker support")
                if claim_type in {"realized_fact", "company_guidance"}:
                    if _number(support.get("claim_value"), "support claim value") != _number(source["claim_value"], "broker claim value"):
                        raise ResearchCampaignError("independent source does not support the broker fact/plan value")
                    allowed = {"primary"} if claim_type == "realized_fact" else {"primary", "company_plan"}
                    if support["kind"] not in allowed:
                        raise ResearchCampaignError("fact or company plan requires an original fact/plan source")
            if response["kind"] != "inferred":
                raise ResearchCampaignError("broker-assisted target inference must remain analyst underwriting")
            _text(response.get("independent_reasoning"), "independent reasoning", 20)
            dispositions.append({"source_id": source["source_id"],
                                 "disposition": "INDEPENDENTLY_SUPPORTED_INFERENCE",
                                 "independent_source_ids": support_ids,
                                 "note": "Original-source verification does not add an independent underlying observation."})
        else:
            dispositions.append({"source_id": source["source_id"],
                                 "disposition": "SCOPED_SECONDARY_AID",
                                 "subject_scope": scope, "claim_type": claim_type})
    return {"policy_version": "broker-research-ingress/v1",
            "classification_required_source_ids": missing,
            "dispositions": dispositions,
            "verification": "provenance_and_claim_basis_only_not_source_truth"}


def _no_market_fields(value: object) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = _key(key)
            if normalized in _FORBIDDEN:
                raise ResearchCampaignError(f"forbidden research input: {key}")
            _no_market_fields(item)
    elif isinstance(value, (tuple, list)):
        for item in value:
            _no_market_fields(item)


def validate_campaign(plan: Mapping) -> dict:
    plan = deepcopy(dict(_mapping(plan, "plan")))
    if plan.get("schema_version") != "research-campaign/v1":
        raise ResearchCampaignError("unsupported campaign schema")
    for key in ("target_id", "model_version", "policy_version"):
        _text(plan.get(key), key)
    _date(plan.get("as_of"), "as_of")
    _no_market_fields(plan)
    _revision_context(plan)
    requests = plan.get("requests")
    if not isinstance(requests, list) or not requests:
        raise ResearchCampaignError("requests must be a nonempty list")
    ids, bindings = set(), set()
    for request in requests:
        _mapping(request, "request")
        rid = _safe_id(request.get("request_id"))
        if rid in ids:
            raise ResearchCampaignError("duplicate request_id")
        ids.add(rid)
        for key in ("metric", "segment", "unit", "question", "economic_path_id"):
            _text(request.get(key), key)
        if _key(request["metric"]) in _FORBIDDEN:
            raise ResearchCampaignError("target valuation references cannot be researched as inputs")
        binding = request["metric"], request["segment"]
        if binding in bindings:
            raise ResearchCampaignError("multiple writers for one metric/segment")
        bindings.add(binding)
        if not isinstance(request.get("mandatory", True), bool):
            raise ResearchCampaignError("mandatory must be boolean")
        dependencies = request.get("depends_on", [])
        if not isinstance(dependencies, list) or not all(isinstance(x, str) for x in dependencies):
            raise ResearchCampaignError("depends_on must be a list of request IDs")
        if request.get("bounds") is not None:
            bounds = _mapping(request["bounds"], "bounds")
            low, base, high = (_number(bounds.get(k), k) for k in ("low", "base", "high"))
            if not low <= base <= high:
                raise ResearchCampaignError("bounds require low <= base <= high")
            _text(bounds.get("rationale"), "bounds rationale", 20)
            refs = bounds.get("source_refs")
            if not isinstance(refs, list) or not refs or not all(canonical_verification_url(x) for x in refs):
                raise ResearchCampaignError("bounds require direct source links")
    return plan


def make_work_order(plan: Mapping, request: Mapping, dependencies: Mapping) -> dict:
    body = {
        "schema_version": "research-work-order/v1",
        "target_id": plan["target_id"], "as_of": plan["as_of"],
        "model_version": plan["model_version"], "policy_version": plan["policy_version"],
        "source_version": plan.get("source_version", "1"),
        "request": dict(request), "dependencies": dict(dependencies),
        "instructions": (
            "공시·IR와 고객·공급자·인허가 원문을 읽고 확인값/추정값을 구분하세요. "
            "부족하면 증권사 자료에서 영업 단서와 추정 방법을 찾되 목표가·현재가·등급·목표배수는 제외하세요. "
            "추정 범위, 근거 요약, 반대 증거, 폐기 조건과 원문 출처를 기록하세요. "
            "직접 수치가 없으면 유사 사업자 실적을 조정하거나 단위경제성·범위 추정을 사용하세요. "
            "검색 실패는 부재 증거가 아닙니다. unresolved는 다음 조사 방식으로 넘기는 보완 요청이며 최종 판단이 아닙니다. "
            "내부 사고과정 대신 검토 가능한 산식·입력·판단 근거 요약을 제출하세요."
        ),
    }
    if plan.get("research_mode") == "INFORMED_REVISION":
        _revision_context(plan)
        body["research_mode"] = "INFORMED_REVISION"
        body["informed_revision"] = deepcopy(plan["informed_revision"])
        body["instructions"] += (
            " 이번 조사는 동결된 원평가와 구분되는 informed revision입니다. "
            "개별 검토된 전망은 채택·조정·배제 근거, 출처 의존성, 반례 및 불확실성을 기록하여 "
            "새 inferred 가정 후보로 제출할 수 있습니다. 미래 결과의 확정은 요구하지 않습니다. "
            "가격 격차를 정답으로 삼거나 기존 동결값·확률·감사상태를 바꾸지 마세요."
        )
    body["request_hash"] = fingerprint(body)
    return body


def _validate_peer_margin(order: Mapping, response: Mapping, value: Decimal,
                          low: Decimal, high: Decimal, sources: list) -> None:
    """Bind adjusted margins or margin × target revenue to a research answer."""
    from .peer_margin_research import build_peer_margin_proposal, validate_peer_margin_receipt

    selection = _mapping(response["peer_margin"], "peer_margin")
    receipt = _mapping(selection.get("receipt"), "peer margin receipt")
    expected = build_peer_margin_proposal(receipt.get("inputs"))
    req = order["request"]
    if response["kind"] != "inferred":
        raise ResearchCampaignError("peer margins remain inferred assumptions")
    if (expected["target"], expected["segment"], expected["as_of"], expected["economic_path_id"]) != (
        order["target_id"], req["segment"], order["as_of"], req["economic_path_id"]
    ):
        raise ResearchCampaignError("peer margin target/segment/cutoff/path mismatch")
    if req.get("margin_basis") != expected["metric"]:
        raise ResearchCampaignError("request margin_basis must explicitly match EBIT or EBITDA")
    source_map = {s["source_id"]: s for s in sources}
    for s in expected["inputs"]["sources"]:
        original = source_map.get(s["source_id"], {})
        if any(original.get(k) != s.get(k) for k in (
            "url", "published_at", "first_seen_at", "locator", "content_sha256"
        )):
            raise ResearchCampaignError("peer margin sources differ from response provenance")
    year, case = selection.get("year"), selection.get("case")
    if req.get("forecast_year") != year:
        raise ResearchCampaignError("peer margin forecast year differs from requested year")
    expected_basis = {
        "normalized_ebitda": "EBITDA", "ebitda": "EBITDA", "ebitda_margin": "EBITDA",
        "normalized_ebit": "EBIT", "ebit": "EBIT", "operating_profit": "EBIT", "operating_margin": "EBIT",
    }.get(req["metric"])
    if expected_basis and expected_basis != expected["metric"]:
        raise ResearchCampaignError("peer metric cannot substitute EBIT for EBITDA or vice versa")
    row = next((r for r in expected["years"] if r["year"] == year), None)
    if row is None or case not in {"low", "base", "high"}:
        raise ResearchCampaignError("peer margin forecast year/case missing")
    validate_peer_margin_receipt(receipt, year, case, row[case], economic_path_id=req["economic_path_id"])
    factor = Decimal(1)
    revenue = selection.get("revenue")
    if revenue is not None:
        _mapping(revenue, "target revenue")
        if revenue.get("unit") != req["unit"] or unit_def(req["unit"]).dimension != Dimension.MONEY:
            raise ResearchCampaignError("peer profit projection requires target revenue in output money unit")
        refs = revenue.get("source_ids")
        if not isinstance(refs, list) or not refs or not set(refs).issubset(source_map):
            raise ResearchCampaignError("target revenue requires known source IDs")
        if revenue.get("year") != year:
            raise ResearchCampaignError("target revenue and peer margin forecast years must match")
        _text(revenue.get("rationale"), "target revenue rationale", 20)
        factor = _number(revenue.get("value"), "target revenue")
        if factor < 0:
            raise ResearchCampaignError("target revenue must be nonnegative")
    elif req["unit"] != "ratio":
        raise ResearchCampaignError("margin-only output requires ratio unit")
    with localcontext(Context(prec=40)):
        if value != Decimal(row[case]) * factor or low != Decimal(row["low"]) * factor or high != Decimal(row["high"]) * factor:
            raise ResearchCampaignError("peer margin projection or bounds do not reproduce response")


def validate_response(order: Mapping, response: Mapping) -> dict:
    """Accept traceable proposals; never pretend this is source-authenticity review."""
    response = deepcopy(dict(_mapping(response, "response")))
    order_body = {key: value for key, value in order.items() if key != "request_hash"}
    if fingerprint(order_body) != order.get("request_hash"):
        raise ResearchCampaignError("work order hash mismatch")
    _no_market_fields(response)
    revision = _revision_context(order)
    req = order["request"]
    if _key(req["metric"]) in _FORBIDDEN:
        raise ResearchCampaignError("target or final valuation references cannot be researched as inputs")
    for key, expected in (("schema_version", "research-response/v1"),
                          ("request_id", req["request_id"]),
                          ("request_hash", order["request_hash"])):
        if response.get(key) != expected:
            raise ResearchCampaignError(f"stale or mismatched {key}")
    if response.get("status") == "unresolved":
        return {"status": "WORK_REQUIRED", "request_id": req["request_id"],
                "reason": _text(response.get("reason"), "unresolved reason"),
                "request_hash": order["request_hash"]}
    if response.get("status") != "answered":
        raise ResearchCampaignError("response status must be answered or unresolved")
    if response.get("kind") not in {"observed", "calculated", "inferred"}:
        raise ResearchCampaignError("response kind must separate observation and inference")
    if response.get("unit") != req["unit"]:
        raise ResearchCampaignError("response unit differs from requested unit")
    value = _number(response.get("value"), "value")
    bounds = _mapping(response.get("bounds", {}), "response bounds")
    low, high = _number(bounds.get("low"), "low"), _number(bounds.get("high"), "high")
    if not low <= value <= high:
        raise ResearchCampaignError("accepted value must lie inside its uncertainty range")
    _text(response.get("rationale"), "rationale", 20)
    _text(response.get("invalidation_condition"), "invalidation_condition", 10)
    _text(response.get("uncertainty"), "uncertainty", 10)
    if not isinstance(response.get("counterevidence"), list):
        raise ResearchCampaignError("counterevidence must be an explicit list (possibly empty)")
    if not response["counterevidence"]:
        _text(response.get("counterevidence_search"), "counterevidence search", 10)
    sources = response.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ResearchCampaignError("sources are required")
    ids, families, source_kinds = set(), set(), set()
    cutoff = _date(order["as_of"], "as_of")
    for source in sources:
        _mapping(source, "source")
        sid = _text(source.get("source_id"), "source_id")
        if sid in ids:
            raise ResearchCampaignError("duplicate source_id")
        ids.add(sid)
        families.add(_text(source.get("source_family"), "source_family"))
        if not canonical_verification_url(source.get("url")):
            raise ResearchCampaignError("source URL must be credential-free HTTP(S)")
        seen = _date(source.get("first_seen_at"), "first_seen_at")
        unknown_publication = (revision is not None and source.get("published_at") is None
                               and source.get("publication_status") == "UNKNOWN"
                               and source.get("review_claim_id"))
        published = None if unknown_publication else _date(source.get("published_at"), "published_at")
        if seen > cutoff or (published is not None and (published > cutoff or seen < published)):
            raise ResearchCampaignError("source was not knowable at the analysis cutoff")
        for key in ("locator", "excerpt"):
            _text(source.get(key), key)
        if not re.fullmatch(r"[0-9a-f]{64}", str(source.get("content_sha256", ""))):
            raise ResearchCampaignError("source content_sha256 is required")
        kind = source.get("kind")
        if kind not in {"primary", "company_plan", "industry", "broker_lead", "broker_estimate"}:
            raise ResearchCampaignError("unsupported source kind")
        source_kinds.add(kind)
    has_broker = bool(source_kinds & {"broker_lead", "broker_estimate"})
    if response["kind"] == "observed" and not source_kinds & {"primary", "company_plan"}:
        raise ResearchCampaignError("a discovery lead cannot certify an observed value")
    if has_broker:
        attempts = response.get("search_attempts", [])
        if not isinstance(attempts, list) or not {"primary", "independent"}.issubset(
            {x.get("tier") for x in attempts if isinstance(x, dict)}
        ):
            raise ResearchCampaignError("broker clues require recorded primary and independent searches")
        for attempt in attempts:
            _mapping(attempt, "search attempt")
            _text(attempt.get("query"), "search query")
            _text(attempt.get("outcome"), "search outcome")
        if "broker_estimate" in source_kinds:
            if response["kind"] != "inferred":
                raise ResearchCampaignError("broker estimates remain inferred underwriting")
            if revision is None:
                _text(response.get("independent_reasoning"), "independent reasoning", 20)
            else:
                _text(response.get("adoption_reasoning", response.get("independent_reasoning")),
                      "individual adoption/adjustment reasoning", 20)
    broker_policy = _broker_source_policy(order, response, sources)
    if broker_policy["classification_required_source_ids"]:
        return {
            "status": "WORK_REQUIRED", "request_id": req["request_id"],
            "request_hash": order["request_hash"], "work_order": deepcopy(dict(order)),
            "reason": "Broker claim_type and subject_scope require explicit classification before numerical ingress.",
            "broker_source_policy": broker_policy,
            # Kept for legacy response-shape readers. This names the possible
            # future layer, not acceptance; no value/response is admissible.
            "authority": "analyst_underwriting",
        }
    calculation = response.get("calculation")
    if "peer_margin" in response:
        if calculation is not None:
            raise ResearchCampaignError("peer margin response cannot declare a second calculation authority")
        _validate_peer_margin(order, response, value, low, high, sources)
    if response["kind"] == "calculated":
        if not isinstance(calculation, dict):
            raise ResearchCampaignError("calculated values require a reproducible calculation")
        operands = calculation.get("inputs", [])
        if not isinstance(operands, list) or not operands:
            raise ResearchCampaignError("calculation inputs are required")
        numbers = []
        for operand in operands:
            _mapping(operand, "calculation operand")
            if not operand.get("source_ids") or not set(operand["source_ids"]).issubset(ids):
                raise ResearchCampaignError("calculation input cites unknown source")
            _text(operand.get("unit"), "calculation input unit")
            numbers.append(_number(operand.get("value"), "calculation input"))
        operation = calculation.get("operation")
        if operation in {"sum", "difference"}:
            if any(x["unit"] != req["unit"] for x in operands):
                raise ResearchCampaignError("additive calculation units must match output")
            calculated = sum(numbers, Decimal(0)) if operation == "sum" else numbers[0] - sum(numbers[1:], Decimal(0))
        elif operation == "scale" and len(numbers) == 2:
            if operands[0]["unit"] != req["unit"] or operands[1]["unit"] != "ratio":
                raise ResearchCampaignError("scale requires output unit times ratio")
            calculated = numbers[0] * numbers[1]
        else:
            raise ResearchCampaignError("unsupported research calculation; use inferred with explicit rationale")
        if calculated != value:
            raise ResearchCampaignError("research calculation does not reproduce proposed value")
    # Validate the value's unit without turning an analyst claim into a filing.
    try:
        measure_from_raw(str(value), req["unit"], order["as_of"])
    except ValueError:
        # Annual capacity pricing is an upstream research driver, not a raw
        # compiler input. The business bridge explicitly supplies its dimensions.
        annual_price = re.fullmatch(r"(.+)_per_(.+)_year", req["unit"])
        if annual_price is None or unit_def(annual_price[1]).dimension != Dimension.MONEY or unit_def(annual_price[2]).dimension not in {
            Dimension.POWER, Dimension.COUNT, Dimension.MASS, Dimension.AREA
        }:
            raise
    response["value"] = str(value)
    response["bounds"] = {"low": str(low), "high": str(high)}
    accepted = {
        "status": "ACCEPTED", "request_id": req["request_id"],
        "request_hash": order["request_hash"], "response_hash": fingerprint(response),
        "metric": req["metric"], "segment": req["segment"],
        "economic_path_id": req["economic_path_id"], "unit": req["unit"],
        "value": str(value), "response": response,
        "work_order": deepcopy(dict(order)),
        "source_family_count": len(families - {"UNKNOWN"}) if revision is not None else len(families),
        "authority": "analyst_underwriting", "validation": "provenance_structure_only",
    }
    if has_broker or revision is not None:
        accepted["broker_source_policy"] = broker_policy
    return accepted


def campaign_priorities(plan: Mapping, evaluator: Callable | None = None) -> tuple:
    drivers = []
    for req in plan["requests"]:
        bounds = req.get("bounds") or {}
        drivers.append(DriverRange(req["request_id"],
            *(_number(bounds[k], k) if k in bounds else None for k in ("low", "base", "high")),
            mandatory=req.get("mandatory", True), rationale=bounds.get("rationale", req["question"])))
    # A missing evaluator represents unmeasured impact, not a made-up value.
    if evaluator is None:
        drivers = [DriverRange(x.driver_id, None, None, None, mandatory=x.mandatory,
                               rationale=x.rationale) for x in drivers]
    rows = assess_research_sensitivity(drivers, evaluator or (lambda _: Decimal(0)),
                                       pairs=tuple(tuple(x) for x in plan.get("sensitivity_pairs", [])))
    return rank_research_priorities(rows)


def run_campaign(plan: Mapping, *, responder: Callable[[dict], Mapping | None],
                 cache: dict | None = None, evaluator: Callable | None = None,
                 response_versions: Mapping[str, str] | None = None,
                 priority_hints: Mapping[str, Mapping] | None = None,
                 max_workers: int = 4) -> dict:
    plan = validate_campaign(plan)
    scheduling_plan = deepcopy(plan)
    for req in scheduling_plan["requests"]:
        hint = (priority_hints or {}).get(req["request_id"])
        if not hint or hint.get("status") != "ACCEPTED":
            continue
        order = hint.get("work_order", {})
        if order.get("request") != req or any(order.get(key) != plan.get(key, "1")
            for key in ("target_id", "as_of", "model_version", "policy_version", "source_version")):
            continue
        if (order.get("research_mode", "BLIND_BASELINE") != plan.get("research_mode", "BLIND_BASELINE")
                or fingerprint(order.get("informed_revision")) != fingerprint(plan.get("informed_revision"))):
            continue
        if fingerprint(validate_response(order, hint["response"])) != fingerprint(hint):
            continue
        req["bounds"] = {**hint["response"]["bounds"], "base": hint["value"],
            "rationale": hint["response"]["rationale"],
            "source_refs": [s["url"] for s in hint["response"]["sources"]]}
    priorities = campaign_priorities(scheduling_plan, evaluator)
    ranks = {}
    for index, row in enumerate(priorities):
        for rid in row.driver_ids:
            ranks.setdefault(rid, index)
    context = ResearchContext(target=plan["target_id"], as_of=plan["as_of"],
        model_version=plan["model_version"], policy_version=plan["policy_version"],
        source_version=plan.get("source_version", "1"))
    tasks = tuple(ResearchTask(req["request_id"],
        inputs={"request": req, "response_version": (response_versions or {}).get(req["request_id"], ""),
                **({"informed_revision_hash": fingerprint(plan["informed_revision"])}
                   if plan.get("research_mode") == "INFORMED_REVISION" else {})},
        depends_on=tuple(req.get("depends_on", [])),
        read_set=tuple(f"response/{rid}" for rid in req.get("depends_on", [])),
        write_set=(f"response/{req['request_id']}",), priority=ranks[req["request_id"]])
        for req in plan["requests"])

    def runner(task, dependencies):
        if any(row["status"] != "ACCEPTED" for row in dependencies.values()):
            return {"status": "WAITING", "request_id": task.task_id, "reason": "dependency unanswered"}
        order = make_work_order(plan, task.inputs["request"], dependencies)
        try:
            response = responder(order)
            if response is None:
                return {"status": "WORK_REQUIRED", "request_id": task.task_id, "work_order": order}
            return validate_response(order, response)
        except (ValueError, TypeError, AttributeError, KeyError, OSError) as exc:
            return {"status": "WORK_REQUIRED", "request_id": task.task_id,
                    "reason": str(exc), "work_order": order}

    def currently_accepted(row):
        if row.get("status") != "ACCEPTED":
            return False
        try:
            return fingerprint(validate_response(row["work_order"], row["response"])) == fingerprint(row)
        except (ValueError, TypeError, AttributeError, KeyError, OSError):
            return False

    execution = run_research_tasks(tasks, runner, context=context, cache=cache,
        max_workers=max_workers, cacheable=currently_accepted)
    outputs = execution.outputs
    required = [req["request_id"] for req in plan["requests"] if req.get("mandatory", True)]
    complete = all(outputs[rid]["status"] == "ACCEPTED" for rid in required)
    updated_plan = deepcopy(plan)
    for req in updated_plan["requests"]:
        item = outputs[req["request_id"]]
        if item["status"] == "ACCEPTED":
            response = item["response"]
            req["bounds"] = {**response["bounds"], "base": item["value"],
                "rationale": response["rationale"],
                "source_refs": [s["url"] for s in response["sources"]]}
    updated_priorities = campaign_priorities(updated_plan, evaluator)
    result = {"schema_version": "research-campaign-result/v1", "target_id": plan["target_id"],
        "as_of": plan["as_of"], "plan_hash": fingerprint(plan),
        "status": "READY_FOR_COMPILATION" if complete else "WORK_REQUIRED",
        "outputs": outputs, "executed_task_ids": list(execution.executed_task_ids),
        "reused_task_ids": list(execution.reused_task_ids),
        "priorities": json.loads(json.dumps([asdict(row) for row in priorities], default=str)),
        "updated_priorities": json.loads(json.dumps([asdict(row) for row in updated_priorities], default=str)),
        "warnings": ["Accepted research is analyst underwriting; source truth and final model audit remain required."]}
    if plan.get("research_mode") == "INFORMED_REVISION":
        result["research_mode"] = "INFORMED_REVISION"
        result["informed_revision"] = deepcopy(plan["informed_revision"])
    return result


def merge_underwriting(existing: Mapping, result: Mapping) -> dict:
    if result["status"] != "READY_FOR_COMPILATION":
        raise ResearchCampaignError("required research is incomplete")
    if (existing.get("target_id"), str(existing.get("as_of"))) != (result["target_id"], result["as_of"]):
        raise ResearchCampaignError("underwriting target or cutoff mismatch")
    revision = _revision_context(result)
    merged = deepcopy(dict(existing))
    if revision is not None:
        merged["research_mode"] = "INFORMED_REVISION"
        merged["informed_revision"] = deepcopy(result["informed_revision"])
    declarations = merged.setdefault("declarations", {})
    for item in result["outputs"].values():
        if item["status"] != "ACCEPTED":
            continue
        response = item["response"]
        if (item["work_order"].get("research_mode", "BLIND_BASELINE") != result.get("research_mode", "BLIND_BASELINE")
                or fingerprint(item["work_order"].get("informed_revision")) != fingerprint(result.get("informed_revision"))):
            raise ResearchCampaignError("research result and accepted proposal revision bindings differ")
        if fingerprint(validate_response(item["work_order"], response)) != fingerprint(item):
            raise ResearchCampaignError("accepted result has changed since validation")
        proposal = {"value": item["value"], "unit": item["unit"], "segment": item["segment"],
            "source_refs": list(dict.fromkeys(s["url"] for s in response["sources"])),
            "rationale": response["rationale"], "research_receipt": deepcopy(item)}
        prior = declarations.get(item["metric"])
        if prior is None:
            declarations[item["metric"]] = proposal
        elif isinstance(prior, dict) and prior.get("segment", "core") == item["segment"]:
            declarations[item["metric"]] = proposal
        else:
            rows = prior if isinstance(prior, list) else [prior]
            rows = [row for row in rows if row.get("segment", "core") != item["segment"]]
            declarations[item["metric"]] = [*rows, proposal]
    merged["research_campaign_hash"] = result["plan_hash"]
    return merged


def write_immutable_json(root: Path, payload: Mapping, prefix: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{prefix}-{fingerprint(payload)}.json"
    content = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") != content:
        raise ResearchCampaignError("immutable artifact content mismatch")
    if not path.exists():
        with path.open("x", encoding="utf-8") as stream:
            stream.write(content)
    return path
