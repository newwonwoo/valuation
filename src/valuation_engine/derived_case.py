"""Replay registered existing case calculators from reviewed operating drivers.

This is a provenance adapter, not a valuation model or expression language.
Only the orchestrator's typed registry supplies executable callables. Artifacts
can name a registered hash but cannot supply imports, code paths or authority.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import inspect
import json
import marshal
from pathlib import Path
import re
from typing import Any, Callable, Mapping

from .actual_units import unit_def
from .runtime_authority import DecisionDomain, forbid_llm_decision


class DerivedCaseError(ValueError):
    pass


def _json_value(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise DerivedCaseError("nonfinite derived-case number")
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise DerivedCaseError("derived-case mapping keys must be strings")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    raise DerivedCaseError("derived-case JSON requires exact decimals, not floats or objects")


def canonical_case_json(value) -> str:
    return json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def derived_case_hash(value) -> str:
    return sha256(canonical_case_json(value).encode()).hexdigest()


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DerivedCaseError("duplicate derived-case JSON key")
        result[key] = value
    return result


def _parse(raw: str):
    try:
        return json.loads(raw, object_pairs_hook=_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(DerivedCaseError("nonfinite JSON")))
    except (TypeError, json.JSONDecodeError) as exc:
        raise DerivedCaseError("invalid derived-case JSON") from exc


def _copy(value):
    return _parse(canonical_case_json(value))


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise DerivedCaseError(f"{label} requires text")


def _digest(value, label):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise DerivedCaseError(f"{label} requires SHA-256")


def _number(value):
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise DerivedCaseError("derived output must be an exact numeric scalar")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise DerivedCaseError("invalid derived output number") from exc
    if not result.is_finite():
        raise DerivedCaseError("derived output must be finite")
    return result


_FORBIDDEN_OUTPUTS = frozenset({"pass", "audit", "audit_passed", "freeze", "freeze_token",
    "final_value", "intrinsic_value", "equity_value", "enterprise_value", "fair_value",
    "value_per_share", "target_price", "expected_value_per_share", "probability", "probabilities"})


def _not_final(value):
    normalized = value.casefold().replace("-", "_")
    if normalized in _FORBIDDEN_OUTPUTS or normalized.endswith("_probability"):
        raise DerivedCaseError("derived case cannot output final value, probability or execution authority")


def _immutable_default(value):
    """Defaults are executable inputs too; mutable/private state is not allowed."""
    if type(value) in {type(None), str, bool, int, Decimal}:
        return {"type": type(value).__name__, "value": _json_value(value)}
    if type(value) is tuple:
        return {"type": "tuple", "value": [_immutable_default(item) for item in value]}
    raise DerivedCaseError("trusted callable defaults must be immutable canonical scalar/tuple values")


def _callable_identity(calculate):
    if not inspect.isfunction(calculate):
        raise DerivedCaseError("trusted calculator must be a plain Python function, not a stateful callable")
    if calculate.__closure__ or calculate.__code__.co_freevars:
        raise DerivedCaseError("trusted calculator closures are not allowed; snapshot inputs explicitly")
    _text(calculate.__module__, "callable module")
    _text(calculate.__qualname__, "callable qualified name")
    return {"module": calculate.__module__, "qualname": calculate.__qualname__,
        "code_sha256": sha256(marshal.dumps(calculate.__code__)).hexdigest(),
        "defaults": [_immutable_default(value) for value in (calculate.__defaults__ or ())],
        "keyword_defaults": {key: _immutable_default(value)
                             for key, value in (calculate.__kwdefaults__ or {}).items()}}


@dataclass(frozen=True)
class CaseCodeFile:
    path: str
    sha256: str

    def __post_init__(self):
        _text(self.path, "code path")
        if not Path(self.path).is_absolute():
            raise DerivedCaseError("trusted code path must be absolute")
        _digest(self.sha256, "code hash")


@dataclass(frozen=True)
class DerivedDriverContract:
    input_name: str
    metric: str
    unit: str
    period_start: str
    period_end: str
    frequency: str
    scope: str
    perimeter: str
    accounting_basis: str
    currency: str
    segment_id: str = "core"
    economic_path_id: str = ""
    candidate_id: str = ""

    def __post_init__(self):
        for field in fields(self):
            if field.name not in {"economic_path_id", "candidate_id"}:
                _text(getattr(self, field.name), field.name)
        date.fromisoformat(self.period_start)
        date.fromisoformat(self.period_end)
        if self.period_start > self.period_end:
            raise DerivedCaseError("driver period is reversed")
        _not_final(self.metric)


@dataclass(frozen=True)
class DerivedOutputContract:
    output_key: str
    metric: str
    unit: str
    scenario_id: str
    year: int
    segment_id: str
    economic_path_id: str
    depends_on: tuple[str, ...]
    source_refs: tuple[str, ...] = ()

    def __post_init__(self):
        for name in ("output_key", "metric", "unit", "scenario_id", "segment_id", "economic_path_id"):
            _text(getattr(self, name), name)
        _not_final(self.output_key)
        _not_final(self.metric)
        unit_def(self.unit)
        if type(self.year) is not int or not 1 <= self.year <= 9999:
            raise DerivedCaseError("output year requires a calendar year")
        for name in ("depends_on", "source_refs"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or len(set(values)) != len(values):
                raise DerivedCaseError(f"{name} requires a distinct tuple")
            for value in values:
                _text(value, name)


@dataclass(frozen=True)
class CaseCalculationModel:
    model_id: str
    version: str
    code_files: tuple[CaseCodeFile, ...]
    baseline_inputs_json: str
    driver_contracts: tuple[DerivedDriverContract, ...]
    output_contracts: tuple[DerivedOutputContract, ...]
    calculate: Callable[[Mapping, Mapping[str, Decimal]], Mapping]

    def __post_init__(self):
        forbid_llm_decision(DecisionDomain.ASSUMPTION_COMPILE)
        _text(self.model_id, "model_id")
        _text(self.version, "version")
        for name, cls in (("code_files", CaseCodeFile), ("driver_contracts", DerivedDriverContract),
                          ("output_contracts", DerivedOutputContract)):
            values = getattr(self, name)
            if not isinstance(values, tuple) or not values or not all(isinstance(x, cls) for x in values):
                raise DerivedCaseError(f"{name} requires nonempty typed tuple")
        baseline = _parse(self.baseline_inputs_json)
        if not isinstance(baseline, dict) or canonical_case_json(baseline) != self.baseline_inputs_json:
            raise DerivedCaseError("baseline inputs require immutable canonical JSON object")
        names = [x.input_name for x in self.driver_contracts]
        keys = [x.output_key for x in self.output_contracts]
        if len(names) != len(set(names)) or len(keys) != len(set(keys)):
            raise DerivedCaseError("duplicate driver/output contract")
        if {name for output in self.output_contracts for name in output.depends_on} != set(names):
            raise DerivedCaseError("output dependency closure must cover exactly registered drivers")
        _callable_identity(self.calculate)
        source = inspect.getsourcefile(self.calculate)
        if source is None or Path(source).resolve() not in {Path(x.path).resolve() for x in self.code_files}:
            raise DerivedCaseError("trusted callable source must be one of the bound code files")
        self.verify_code_files()

    @property
    def baseline_inputs_hash(self):
        return sha256(self.baseline_inputs_json.encode()).hexdigest()

    def metadata(self):
        return {"model_id": self.model_id, "version": self.version,
            "code_files": [asdict(x) for x in self.code_files],
            "baseline_inputs_hash": self.baseline_inputs_hash,
            "driver_contracts": [asdict(x) for x in self.driver_contracts],
            "output_contracts": [asdict(x) for x in self.output_contracts],
            "callable_identity": _callable_identity(self.calculate)}

    @property
    def model_hash(self):
        return derived_case_hash(self.metadata())

    def verify_code_files(self):
        for source in self.code_files:
            if sha256(Path(source.path).read_bytes()).hexdigest() != source.sha256:
                raise DerivedCaseError("trusted calculation code hash changed")


@dataclass(frozen=True)
class DerivedCaseRegistry:
    models: tuple[CaseCalculationModel, ...]

    def __post_init__(self):
        forbid_llm_decision(DecisionDomain.ASSUMPTION_COMPILE)
        if not isinstance(self.models, tuple) or not all(isinstance(x, CaseCalculationModel) for x in self.models):
            raise DerivedCaseError("registry requires typed trusted models")
        if len({x.model_hash for x in self.models}) != len(self.models):
            raise DerivedCaseError("duplicate registered model hash")

    def get(self, model_hash):
        matches = [model for model in self.models if model.model_hash == model_hash]
        if len(matches) != 1:
            raise DerivedCaseError("derived case model hash is not registered")
        return matches[0]


@dataclass(frozen=True)
class VerifiedDerivedOutput:
    output_key: str
    metric: str
    unit: str
    scenario_id: str
    year: int
    segment_id: str
    economic_path_id: str
    depends_on: tuple[str, ...]
    value: str
    source_refs: tuple[str, ...]
    proof: Mapping


@dataclass(frozen=True)
class VerifiedDerivedCase:
    receipt_hash: str
    model_hash: str
    outputs: tuple[VerifiedDerivedOutput, ...]

    def output(self, output_key: str) -> VerifiedDerivedOutput:
        matches = [row for row in self.outputs if row.output_key == output_key]
        if len(matches) != 1:
            raise DerivedCaseError("unknown derived output key")
        return matches[0]


def _context(target_id, as_of, baseline_run_id, new_run_id, review_receipt):
    from .research_campaign import _revision_context
    body = {"target_id": target_id, "as_of": as_of, "research_mode": "INFORMED_REVISION",
        "informed_revision": {"schema_version": "broker-informed-revision/v1",
            "baseline_run_id": baseline_run_id, "new_run_id": new_run_id,
            "review_receipt": _copy(review_receipt)}}
    _revision_context(body)
    return body


_BASIS = ("period_start", "period_end", "frequency", "scope", "perimeter", "accounting_basis", "currency")


def _drivers(model, context, drivers):
    from .research_campaign import fingerprint, validate_response
    if not isinstance(drivers, Mapping) or set(drivers) != {x.input_name for x in model.driver_contracts}:
        raise DerivedCaseError("exact registered driver inputs are required")
    values, origins = {}, {}
    review = context["informed_revision"]["review_receipt"]
    decisions = {item["claim_id"]: item for item in review["decisions"]}
    for contract in model.driver_contracts:
        receipt = drivers[contract.input_name]
        if not isinstance(receipt, Mapping):
            raise DerivedCaseError("driver requires an accepted research receipt")
        order, response = receipt.get("work_order", {}), receipt.get("response", {})
        if (order.get("research_mode") != "INFORMED_REVISION"
                or fingerprint(order.get("informed_revision")) != fingerprint(context["informed_revision"])
                or (order.get("target_id"), order.get("as_of")) != (context["target_id"], context["as_of"])):
            raise DerivedCaseError("driver receipt differs from the exact informed run context")
        verified = validate_response(order, response)
        if verified.get("status") != "ACCEPTED" or fingerprint(verified) != fingerprint(receipt):
            raise DerivedCaseError("driver research receipt was altered or not accepted")
        selection = response.get("informed_assumption", {})
        reviewed = decisions.get(selection.get("claim_id"))
        if reviewed is None or reviewed["disposition"] != "PROPOSE_NEW_RUN_ASSUMPTION":
            raise DerivedCaseError("driver has no individually reviewed assumption candidate")
        candidate = reviewed["assumption_candidate"]
        if ((verified["metric"], verified["unit"], verified["segment"])
                != (contract.metric, contract.unit, contract.segment_id)
                or any(candidate[field] != getattr(contract, field) for field in _BASIS)
                or (contract.economic_path_id and verified["economic_path_id"] != contract.economic_path_id)
                or (contract.candidate_id and candidate["candidate_id"] != contract.candidate_id)):
            raise DerivedCaseError("reviewed driver metric/unit/basis/candidate differs from trusted contract")
        values[contract.input_name] = _number(verified["value"])
        refs = tuple(dict.fromkeys(source["url"] for source in response["sources"]))
        broker_refs = tuple(dict.fromkeys(source["url"] for source in response["sources"]
            if source.get("kind") in {"broker_lead", "broker_estimate"} and source.get("review_claim_id") in decisions))
        origins[contract.input_name] = {"input_name": contract.input_name,
            "request_id": verified["request_id"], "candidate_id": candidate["candidate_id"],
            "claim_id": selection["claim_id"], "metric": verified["metric"], "unit": verified["unit"],
            "segment_id": verified["segment"], "value": verified["value"],
            "research_receipt_hash": fingerprint(verified), "source_refs": refs,
            "reviewed_broker_source_refs": broker_refs}
    return values, origins


def _calculate(model, values):
    model.verify_code_files()
    original_identity = model.model_hash
    results = []
    for _ in range(2):
        baseline = _parse(model.baseline_inputs_json)
        frozen_baseline = _copy(baseline)
        arguments = dict(values)
        result = model.calculate(baseline, arguments)
        if baseline != frozen_baseline or arguments != values:
            raise DerivedCaseError("trusted calculator mutated its frozen input snapshots")
        if not isinstance(result, Mapping) or set(result) != {x.output_key for x in model.output_contracts}:
            raise DerivedCaseError("calculator output keys differ from complete registered output closure")
        results.append({key: str(_number(value)) for key, value in result.items()})
    model.verify_code_files()
    if model.model_hash != original_identity:
        raise DerivedCaseError("trusted callable identity changed during replay")
    if results[0] != results[1]:
        raise DerivedCaseError("registered calculator is nondeterministic")
    return results[0]


def _body(model, context, drivers):
    values, origins = _drivers(model, context, drivers)
    calculated = _calculate(model, values)
    outputs = {}
    all_broker_refs = {decision["source_ref"] for decision in context["informed_revision"]["review_receipt"]["decisions"]}
    for contract in model.output_contracts:
        dependency_brokers = {ref for name in contract.depends_on for ref in origins[name]["reviewed_broker_source_refs"]}
        if set(contract.source_refs) & all_broker_refs - dependency_brokers:
            raise DerivedCaseError("fixed output provenance cannot borrow an unrelated reviewed broker URL")
        refs = tuple(dict.fromkeys((*contract.source_refs,
            *(ref for name in contract.depends_on for ref in origins[name]["source_refs"]))))
        if not refs:
            raise DerivedCaseError("derived output requires explicit provenance sources")
        outputs[contract.output_key] = {**asdict(contract), "value": calculated[contract.output_key], "source_refs": refs}
    inputs = {"baseline_inputs_hash": model.baseline_inputs_hash,
              "driver_values": values, "driver_receipt_hashes": {key: item["research_receipt_hash"] for key, item in origins.items()}}
    return {"schema_version": "derived-case-receipt/v1", **context,
        "model_hash": model.model_hash, "model": model.metadata(),
        "baseline_inputs": _parse(model.baseline_inputs_json), "baseline_inputs_hash": model.baseline_inputs_hash,
        "drivers": _copy(drivers), "driver_values": values, "inputs_hash": derived_case_hash(inputs),
        "outputs": outputs, "outputs_hash": derived_case_hash(outputs)}, origins


def build_derived_case_receipt(model: CaseCalculationModel, *, target_id: str, as_of: str,
        baseline_run_id: str, new_run_id: str, review_receipt: Mapping, drivers: Mapping) -> dict:
    forbid_llm_decision(DecisionDomain.ASSUMPTION_COMPILE)
    if not isinstance(model, CaseCalculationModel):
        raise DerivedCaseError("case construction requires a trusted typed model")
    context = _context(target_id, as_of, baseline_run_id, new_run_id, review_receipt)
    body, _ = _body(model, context, drivers)
    return _copy({**body, "receipt_hash": derived_case_hash(body)})


# Only actual deterministic replay writes this process-local capability. A
# JSON receipt/proof alone cannot create it. Stored JSON is immutable and does
# not share mutable dicts with caller-visible proof objects.
_REPLAY_ATTESTATIONS: dict[str, tuple[CaseCalculationModel, str]] = {}


def _verified_output(raw):
    body = _copy(raw)
    body["depends_on"] = tuple(body["depends_on"])
    body["source_refs"] = tuple(body["source_refs"])
    return VerifiedDerivedOutput(**body)


def verify_derived_case_receipt(receipt: Mapping, registry: DerivedCaseRegistry) -> VerifiedDerivedCase:
    forbid_llm_decision(DecisionDomain.ASSUMPTION_COMPILE)
    if not isinstance(registry, DerivedCaseRegistry):
        raise DerivedCaseError("trusted derived-case registry is required")
    if not isinstance(receipt, Mapping):
        raise DerivedCaseError("derived receipt must be a mapping")
    model = registry.get(receipt.get("model_hash"))  # Never execute receipt-selected code.
    expected_keys = {"schema_version", "target_id", "as_of", "research_mode", "informed_revision",
        "model_hash", "model", "baseline_inputs", "baseline_inputs_hash", "drivers", "driver_values",
        "inputs_hash", "outputs", "outputs_hash", "receipt_hash"}
    if set(receipt) != expected_keys or receipt.get("schema_version") != "derived-case-receipt/v1":
        raise DerivedCaseError("derived receipt requires the exact typed schema")
    if (canonical_case_json(receipt["model"]) != canonical_case_json(model.metadata())
            or canonical_case_json(receipt["baseline_inputs"]) != model.baseline_inputs_json
            or receipt["baseline_inputs_hash"] != model.baseline_inputs_hash):
        raise DerivedCaseError("derived receipt changes trusted model contracts or baseline parameters")
    if receipt["receipt_hash"] != derived_case_hash({key: value for key, value in receipt.items() if key != "receipt_hash"}):
        raise DerivedCaseError("derived receipt hash mismatch")
    revision = receipt.get("informed_revision", {})
    context = _context(receipt.get("target_id"), receipt.get("as_of"),
        revision.get("baseline_run_id"), revision.get("new_run_id"), revision.get("review_receipt"))
    if receipt.get("research_mode") != "INFORMED_REVISION":
        raise DerivedCaseError("derived receipt cannot enter a blind baseline")
    expected, origins = _body(model, context, receipt.get("drivers"))
    expected["receipt_hash"] = derived_case_hash(expected)
    if canonical_case_json(receipt) != canonical_case_json(expected):
        raise DerivedCaseError("derived receipt differs from deterministic replay or trusted baseline/contracts")
    common = {"schema_version": "derived-case-proof/v1", "receipt_hash": expected["receipt_hash"],
        "model_hash": model.model_hash, "inputs_hash": expected["inputs_hash"], "outputs_hash": expected["outputs_hash"],
        "target_id": context["target_id"], "as_of": context["as_of"],
        "baseline_run_id": revision["baseline_run_id"], "new_run_id": revision["new_run_id"],
        "review_receipt_hash": revision["review_receipt"]["receipt_hash"]}
    rows = [{**row, "proof": {**common, "output_key": key,
        "upstream_origins": [origins[name] for name in row["depends_on"]]}}
        for key, row in expected["outputs"].items()]
    encoded = canonical_case_json(rows)
    _REPLAY_ATTESTATIONS[expected["receipt_hash"]] = (model, encoded)
    return VerifiedDerivedCase(expected["receipt_hash"], model.model_hash,
        tuple(_verified_output(row) for row in _parse(encoded)))


def load_derived_case_reference(ref: Mapping) -> dict:
    if not isinstance(ref, Mapping) or set(ref) != {"schema_version", "artifact_path", "artifact_sha256", "output_key"}:
        raise DerivedCaseError("derived case reference requires exact artifact/hash/output fields")
    if ref["schema_version"] != "derived-case-reference/v1":
        raise DerivedCaseError("unsupported derived case reference")
    _digest(ref["artifact_sha256"], "artifact hash")
    _text(ref["output_key"], "output key")
    path = Path(ref["artifact_path"])
    if not path.is_absolute():
        raise DerivedCaseError("derived case artifact path must be absolute")
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != ref["artifact_sha256"]:
        raise DerivedCaseError("derived case artifact content hash mismatch")
    receipt = _parse(raw.decode("utf-8"))
    if not isinstance(receipt, dict) or receipt.get("schema_version") != "derived-case-receipt/v1":
        raise DerivedCaseError("unsupported derived case artifact")
    body = {key: value for key, value in receipt.items() if key != "receipt_hash"}
    if derived_case_hash(body) != receipt.get("receipt_hash"):
        raise DerivedCaseError("derived case receipt hash mismatch")
    if ref["output_key"] not in receipt.get("outputs", {}):
        raise DerivedCaseError("unknown derived output key")
    return receipt


def derived_case_revision_context(ref: Mapping) -> dict:
    receipt = load_derived_case_reference(ref)
    revision = receipt["informed_revision"]
    context = _context(receipt["target_id"], receipt["as_of"], revision["baseline_run_id"],
        revision["new_run_id"], revision["review_receipt"])
    if receipt.get("research_mode") != context["research_mode"]:
        raise DerivedCaseError("derived case mode differs from informed context")
    return {"research_mode": context["research_mode"], "informed_revision": context["informed_revision"]}


def verify_derived_case_reference(ref: Mapping, registry: DerivedCaseRegistry) -> VerifiedDerivedOutput:
    return verify_derived_case_receipt(load_derived_case_reference(ref), registry).output(ref["output_key"])


def verified_derived_output(ref: Mapping, proof: Mapping) -> VerifiedDerivedOutput:
    """Read a compact output only after same-process trusted deterministic replay."""
    forbid_llm_decision(DecisionDomain.ASSUMPTION_COMPILE)
    receipt = load_derived_case_reference(ref)
    attestation = _REPLAY_ATTESTATIONS.get(receipt["receipt_hash"])
    if attestation is None:
        raise DerivedCaseError("derived output has no trusted process-local replay attestation")
    model, encoded = attestation
    model.verify_code_files()
    if model.model_hash != receipt["model_hash"]:
        raise DerivedCaseError("trusted callable identity differs from the attested model")
    matches = [row for row in _parse(encoded) if row["output_key"] == ref["output_key"]]
    if len(matches) != 1 or canonical_case_json(matches[0]["proof"]) != canonical_case_json(proof):
        raise DerivedCaseError("derived output proof differs from actual replay")
    return _verified_output(matches[0])
