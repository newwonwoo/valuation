#!/usr/bin/env python3
"""Prepare parallel host research, accept replies, and feed the strict KR runner.

No LLM SDK or API key is required. Read <workspace>/requests/*.json with the
host model, search original sources, then write <workspace>/responses/<id>.json.
Rerun the same command. Optional --provider module:callable returns a response
for each work order using the host's own tools. Proposals never authorize value.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import importlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import yaml

from valuation_engine.research_campaign import (
    ResearchCampaignError, fingerprint, merge_underwriting, run_campaign,
    validate_campaign, validate_response, write_immutable_json, research_recovery_context,
)


def _read(path: Path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _code_identity() -> str:
    paths = [*sorted((ROOT / "src/valuation_engine").glob("*.py")),
             *sorted((ROOT / "config").glob("*.yaml")), Path(__file__),
             ROOT / "scripts/research_report_completion.py", ROOT / "scripts/run_kr_live.py"]
    return fingerprint({str(p.relative_to(ROOT)): sha256(p.read_bytes()).hexdigest() for p in paths})


def _set_driver(model: dict, path: str, value: Decimal):
    parts = path.split(".")
    node = model
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    key = parts[-1]
    if key != "value" or not isinstance(node, dict) or "unit" not in node:
        raise ResearchCampaignError("driver binding must name a typed estimate's value")
    node[key] = str(value)


def _driver_node(model: dict, path: str):
    node = model
    for part in path.split(".")[:-1]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def model_evaluator(plan: dict):
    """Use the same operating path for sensitivity; never load share prices."""
    model = plan.get("business_model")
    if model is None:
        return None
    from valuation_engine.new_business_research import business_path_from_dict, build_cashflow_proposal

    if model.get("kind") != "contracted_business":
        raise ResearchCampaignError("unsupported research business model")
    bindings = model.get("driver_bindings", {})
    if len(set(bindings.values())) != len(bindings):
        raise ResearchCampaignError("two requests cannot overwrite the same model driver")
    requests = {r["request_id"]: r for r in plan["requests"]}
    for rid, path in bindings.items():
        if rid not in requests:
            raise ResearchCampaignError("model binding references an unknown request")
        node = _driver_node(model["path"], path)
        req = requests[rid]
        if req["segment"] != model.get("segment"):
            raise ResearchCampaignError("model driver segment differs from research request")
        if (node.get("unit"), node.get("economic_path_id")) != (req["unit"], req["economic_path_id"]):
            raise ResearchCampaignError("model driver unit/economic path differs from research request")
    discount = Decimal(str(model["discount_rate"]))
    if not discount.is_finite() or discount <= -1:
        raise ResearchCampaignError("research discount rate must be finite and > -1")
    if not model.get("discount_rate_source_refs") or not model.get("discount_rate_rationale"):
        raise ResearchCampaignError("discount rate needs source references and rationale")

    def evaluate(overrides):
        path = deepcopy(model["path"])
        for key, value in overrides.items():
            if key not in bindings:
                raise ResearchCampaignError(f"driver {key} has no model binding")
            _set_driver(path, bindings[key], value)
        proposal = build_cashflow_proposal(business_path_from_dict(path))
        # A signed finite contract does not automatically earn a Gordon tail.
        as_of_year = int(plan["as_of"][:4])
        if proposal.periods[0].year <= as_of_year:
            raise ResearchCampaignError("annual research forecast must start after the as-of year")
        return sum((period.fcff / (Decimal(1) + discount) ** (period.year - as_of_year)
                    for period in proposal.periods), Decimal(0))

    return evaluate


def _execute_campaign(plan_path: Path, workspace: Path, *, underwriting_path: Path | None = None,
                     provider=None, max_rounds: int = 5, max_workers: int = 4) -> tuple[dict, Path | None]:
    if not 1 <= max_rounds <= 10:
        raise ResearchCampaignError("max_rounds must be between 1 and 10")
    plan = validate_campaign(_read(plan_path))
    underwriting = _read(underwriting_path) if underwriting_path else None
    # Actual code/policy and baseline content join the semantic request identity.
    plan["source_version"] = fingerprint({"declared": plan.get("source_version", "1"),
        "engine": _code_identity(), "underwriting": underwriting, "business_model": plan.get("business_model")})
    workspace.mkdir(parents=True, exist_ok=True)
    requests_dir = workspace / "requests"
    responses_dir = workspace / "responses"
    responses_dir.mkdir(exist_ok=True)
    cache_path = workspace / "cache.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    evaluator = model_evaluator(plan)
    attempts = {}

    def versions():
        return {req["request_id"]: sha256(path.read_bytes()).hexdigest()
            for req in plan["requests"]
            if (path := responses_dir / f"{req['request_id']}.json").exists()}

    def priority_hints():
        hints = {}
        for req in plan["requests"]:
            response_path = responses_dir / f"{req['request_id']}.json"
            if not response_path.exists():
                continue
            try:
                response = json.loads(response_path.read_text())
                # Work-order filenames hash the complete object, so locate its
                # matching semantic request hash without trusting latest aliases.
                for path in requests_dir.glob(f"{req['request_id']}-*.json"):
                    order = json.loads(path.read_text())
                    if order.get("request_hash") == response.get("request_hash"):
                        hints[req["request_id"]] = validate_response(order, response)
                        break
            except (ValueError, TypeError, KeyError, OSError):
                continue
        return hints

    def respond(order):
        write_immutable_json(requests_dir, order, order["request"]["request_id"])
        path = responses_dir / f"{order['request']['request_id']}.json"
        feedback = None
        if path.exists():
            try:
                response = json.loads(path.read_text())
                if isinstance(response, dict) and response.get("request_hash") == order["request_hash"]:
                    accepted = validate_response(order, response)
                    if accepted["status"] == "ACCEPTED" or provider is None:
                        return response
                    feedback = accepted.get("reason", "additional research is required")
            except (ValueError, TypeError, KeyError) as exc:
                feedback = str(exc)
                if provider is None:
                    raise
        if provider is None:
            return None
        # Advisory feedback is outside the canonical work-order identity. The
        # response still cites the original request_hash, never a repaired value.
        rid = order["request"]["request_id"]
        attempt = attempts.get(rid, 0)
        prompt = {**order, "recovery": research_recovery_context(attempt, feedback)}
        if feedback:
            prompt["repair_feedback"] = feedback
        attempts[rid] = attempt + 1
        response = provider(prompt)
        write_immutable_json(workspace / "attempt_history", {
            "request_hash": order["request_hash"], "recovery": prompt["recovery"],
            "response": response,
        }, rid)
        if response is not None:
            write_immutable_json(workspace / "response_history", response, order["request"]["request_id"])
            path.write_text(json.dumps(response, indent=2, ensure_ascii=False, allow_nan=False) + "\n")
        return response

    for _ in range(max_rounds if provider else 1):
        response_versions = versions()
        result = run_campaign(plan, responder=respond, cache=cache, evaluator=evaluator,
                              response_versions=response_versions, priority_hints=priority_hints(),
                              max_workers=max_workers)
        if versions() != response_versions:
            # Materialized provider replies now have their final file identities.
            # Prime those receipts without calling the provider again.
            first_execution = result["executed_task_ids"]
            result = run_campaign(plan, responder=lambda order: json.loads(
                (responses_dir / f"{order['request']['request_id']}.json").read_text())
                if (responses_dir / f"{order['request']['request_id']}.json").exists() else None,
                cache=cache, evaluator=evaluator, response_versions=versions(), max_workers=max_workers)
            result["executed_task_ids"] = first_execution
            result["reused_task_ids"] = [rid for rid in result["reused_task_ids"] if rid not in first_execution]
        write_immutable_json(workspace / "history", result, "campaign")
        # Operational cache is replaceable; reports/accepted history above are immutable.
        temp = workspace / "cache.tmp"
        temp.write_text(json.dumps(cache, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n")
        temp.replace(cache_path)
        if result["status"] == "READY_FOR_COMPILATION":
            break
    if result["status"] != "READY_FOR_COMPILATION":
        result["continuation"] = {
            "status": "HOST_ACTION_REQUIRED" if provider is None else "RESEARCH_BUDGET_REACHED",
            "instructions": "추가 조사 또는 범위·유사기업 조정 추정을 보완하고 같은 작업공간에서 재개하세요.",
            "attempts": attempts,
        }
    merged_path = None
    if result["status"] == "READY_FOR_COMPILATION" and underwriting is not None:
        merged = merge_underwriting(underwriting, result)
        if plan.get("business_model"):
            from valuation_engine.new_business_research import (
                business_path_from_dict, build_cashflow_proposal, cashflow_path_hash,
            )
            model = plan["business_model"]
            path = deepcopy(model["path"])
            for rid, binding in model["driver_bindings"].items():
                if result["outputs"][rid]["status"] != "ACCEPTED":
                    raise ResearchCampaignError("every bound model driver must have an accepted response")
                _set_driver(path, binding, Decimal(result["outputs"][rid]["value"]))
                node = _driver_node(path, binding)
                node["source_refs"] = list(dict.fromkeys(
                    source["url"] for source in result["outputs"][rid]["response"]["sources"]))
                node["assumption_refs"] = [rid]
            proposal = build_cashflow_proposal(business_path_from_dict(path))
            if proposal.periods[0].year != int(plan["as_of"][:4]) + 1:
                raise ResearchCampaignError("compiler cashflow path requires every year starting after as_of; supply the missing years explicitly")
            prefix = model.get("metric_prefix", "fcff_year_")
            segment = model["segment"]
            rows = proposal.underwriting_rows(prefix)
            for metric, row in rows.items():
                row["segment"] = segment
                row["business_cashflow_receipt"] = {
                    "schema_version": "business-cashflow-receipt/v1", "plan_hash": result["plan_hash"],
                    "path_hash": cashflow_path_hash(path), "path": path, "metric_prefix": prefix,
                    "source_refs": row["source_refs"], "assumptions": result["outputs"],
                }
                if metric in {x["metric"] for x in result["outputs"].values() if x["status"] == "ACCEPTED"}:
                    raise ResearchCampaignError("cashflow output collides with a direct research answer")
                prior = merged["declarations"].get(metric)
                if prior is None or (isinstance(prior, dict) and prior.get("segment", "core") == segment):
                    merged["declarations"][metric] = row
                else:
                    others = prior if isinstance(prior, list) else [prior]
                    merged["declarations"][metric] = [x for x in others if x.get("segment", "core") != segment] + [row]
            result["business_cashflow"] = proposal.to_dict()
            write_immutable_json(workspace / "history", result, "campaign-model")
        merged_path = workspace / f"underwriting-{fingerprint(merged)}.yaml"
        if not merged_path.exists():
            merged_path.write_text(yaml.safe_dump(merged, allow_unicode=True, sort_keys=False))
        # Use the real collector immediately; do not declare a proposal compilable
        # merely because a file was written.
        from valuation_engine.generic_underwriting import load_declared_underwriting
        load_declared_underwriting(merged_path)
    return result, merged_path


def execute_campaign(plan_path: Path, workspace: Path, **kwargs) -> tuple[dict, Path | None]:
    """One coordinator owns the mutable cache; worker replies remain isolated."""
    import fcntl

    workspace.mkdir(parents=True, exist_ok=True)
    with (workspace / ".coordinator.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ResearchCampaignError("another coordinator is using this research workspace") from exc
        try:
            return _execute_campaign(plan_path, workspace, **kwargs)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--underwriting", type=Path)
    parser.add_argument("--provider", help="optional host module:callable, no vendor SDK required")
    parser.add_argument("--max-rounds", type=int, default=5)
    parser.add_argument("--recovery-provider", help="host module:callable to repair isolated run inputs and staff replies")
    parser.add_argument("--completion-rounds", type=int, default=10)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--run-dir", type=Path, help="optional prepared KR run; final audit remains mandatory")
    parser.add_argument("--staff-mode", choices=("assisted", "live", "replay"), default="assisted")
    args = parser.parse_args(argv)
    provider = None
    if args.provider:
        module, separator, name = args.provider.partition(":")
        if not separator:
            parser.error("provider must be module:callable")
        provider = getattr(importlib.import_module(module), name)
    recovery_provider = None
    if args.recovery_provider:
        module, separator, name = args.recovery_provider.partition(":")
        if not separator:
            parser.error("recovery-provider must be module:callable")
        recovery_provider = getattr(importlib.import_module(module), name)
    underwriting = args.underwriting
    if args.run_dir and underwriting is None:
        underwriting = args.run_dir / "declarations/underwriting.yaml"
    try:
        result, merged = execute_campaign(args.plan, args.workspace,
            underwriting_path=underwriting, provider=provider,
            max_rounds=args.max_rounds, max_workers=args.max_workers)
        print(f"조사 상태: {result['status']}; 수행 {len(result['executed_task_ids'])}, 재사용 {len(result['reused_task_ids'])}")
        if result["status"] != "READY_FOR_COMPILATION":
            print(f"보완 작업지시: {args.workspace / 'requests'}")
            print(f"응답 저장: {args.workspace / 'responses'} — 저장 후 같은 명령으로 재개")
            return 2
        if merged:
            print(f"추정값과 근거: {merged}")
        if args.run_dir:
            from run_kr_live import _run_input_sha256
            from research_report_completion import complete_research_report
            identity = fingerprint({"source": _run_input_sha256(args.run_dir),
                                    "underwriting": sha256(merged.read_bytes()).hexdigest()})
            completion = complete_research_report(args.run_dir,
                args.workspace / "completion" / identity, merged,
                staff_mode=args.staff_mode, recovery_provider=recovery_provider,
                max_rounds=args.completion_rounds)
            if completion["status"] == "COMPLETED":
                print(f"투자보고서: {completion['versioned_report_path']}")
                print(f"보고서 검증 기록: {completion['latest_manifest_path']}")
                return 0
            path = write_immutable_json(args.workspace / "requests", completion, "valuation-gap")
            print(f"후속 보완: {completion['blocked_stage']} — {completion['reason']}")
            print(f"재개 작업지시: {path}")
            return 2
        return 0
    except (ValueError, TypeError, OSError) as exc:
        print(f"조사 입력 오류: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
