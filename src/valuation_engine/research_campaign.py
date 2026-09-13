"""Sensitivity-led, host-operated research; proposals enter the existing compiler.

This module validates provenance structure, not the truth of a downloaded source.
The host must read sources. An accepted response remains analyst underwriting.
No stage pass, freeze, probability calibration or current price is accepted here.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import date
from decimal import Decimal, InvalidOperation
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
    "freeze_token", "audit_passed", "stage_status",
}


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
            "검색 실패는 부재 증거가 아닙니다. 방어 가능한 값이 없으면 unresolved로 반환하세요. "
            "내부 사고과정 대신 검토 가능한 산식·입력·판단 근거 요약을 제출하세요."
        ),
    }
    body["request_hash"] = fingerprint(body)
    return body


def validate_response(order: Mapping, response: Mapping) -> dict:
    """Accept traceable proposals; never pretend this is source-authenticity review."""
    response = deepcopy(dict(_mapping(response, "response")))
    order_body = {key: value for key, value in order.items() if key != "request_hash"}
    if fingerprint(order_body) != order.get("request_hash"):
        raise ResearchCampaignError("work order hash mismatch")
    _no_market_fields(response)
    req = order["request"]
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
        published = _date(source.get("published_at"), "published_at")
        seen = _date(source.get("first_seen_at"), "first_seen_at")
        if published > cutoff or seen > cutoff or seen < published:
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
            _text(response.get("independent_reasoning"), "independent reasoning", 20)
    calculation = response.get("calculation")
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
    return {
        "status": "ACCEPTED", "request_id": req["request_id"],
        "request_hash": order["request_hash"], "response_hash": fingerprint(response),
        "metric": req["metric"], "segment": req["segment"],
        "economic_path_id": req["economic_path_id"], "unit": req["unit"],
        "value": str(value), "response": response,
        "work_order": deepcopy(dict(order)),
        "source_family_count": len(families),
        "authority": "analyst_underwriting", "validation": "provenance_structure_only",
    }


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
        inputs={"request": req, "response_version": (response_versions or {}).get(req["request_id"], "")},
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

    execution = run_research_tasks(tasks, runner, context=context, cache=cache,
        max_workers=max_workers, cacheable=lambda row: row.get("status") == "ACCEPTED")
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
    return {"schema_version": "research-campaign-result/v1", "target_id": plan["target_id"],
        "as_of": plan["as_of"], "plan_hash": fingerprint(plan),
        "status": "READY_FOR_COMPILATION" if complete else "WORK_REQUIRED",
        "outputs": outputs, "executed_task_ids": list(execution.executed_task_ids),
        "reused_task_ids": list(execution.reused_task_ids),
        "priorities": json.loads(json.dumps([asdict(row) for row in priorities], default=str)),
        "updated_priorities": json.loads(json.dumps([asdict(row) for row in updated_priorities], default=str)),
        "warnings": ["Accepted research is analyst underwriting; source truth and final model audit remain required."]}


def merge_underwriting(existing: Mapping, result: Mapping) -> dict:
    if result["status"] != "READY_FOR_COMPILATION":
        raise ResearchCampaignError("required research is incomplete")
    if (existing.get("target_id"), str(existing.get("as_of"))) != (result["target_id"], result["as_of"]):
        raise ResearchCampaignError("underwriting target or cutoff mismatch")
    merged = deepcopy(dict(existing))
    declarations = merged.setdefault("declarations", {})
    for item in result["outputs"].values():
        if item["status"] != "ACCEPTED":
            continue
        response = item["response"]
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
