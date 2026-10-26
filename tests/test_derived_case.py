"""Synthetic calculator replay; no new financial model or live-company values."""
from copy import deepcopy
from dataclasses import asdict, replace
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from tests.test_broker_research_policy import reviewed_plan, informed_answer
from valuation_engine.derived_case import (
    CaseCalculationModel, CaseCodeFile, DerivedCaseError, DerivedCaseRegistry,
    DerivedDriverContract, DerivedOutputContract, build_derived_case_receipt,
    canonical_case_json, derived_case_hash, derived_case_revision_context,
    load_derived_case_reference, verified_derived_output,
    verify_derived_case_receipt, verify_derived_case_reference,
)
from valuation_engine.research_campaign import run_campaign
from valuation_engine.runtime_authority import llm_proposal_scope


def calculate(baseline, drivers):
    return {"Base:fcff": Decimal(baseline["sales"]) * drivers["utilization"] - Decimal(baseline["cost"]),
            "Base:shares": Decimal(baseline["shares"])}


def mutating_calculator(baseline, drivers):
    baseline["cost"] = "0"
    return calculate(baseline, drivers)


_COUNTER = 0


def nondeterministic_calculator(baseline, drivers):
    global _COUNTER
    _COUNTER += 1
    return {**calculate(baseline, drivers), "Base:shares": _COUNTER}


def missing_output(baseline, drivers):
    return {"Base:fcff": 1}


def extra_authority_output(baseline, drivers):
    return {**calculate(baseline, drivers), "PASS": 1}


def nonfinite_output(baseline, drivers):
    return {**calculate(baseline, drivers), "Base:shares": "NaN"}


def shared_range_calculator(baseline, drivers):
    return {scenario: Decimal(baseline["sales"]) * drivers[scenario] for scenario in ("Down", "Base", "Bull")}


def default_calculator(baseline, drivers, factor="1", *, offset="0"):
    return {key: value * Decimal(factor) + Decimal(offset) for key, value in calculate(baseline, drivers).items()}


def mutable_default_calculator(baseline, drivers, state=[]):
    return calculate(baseline, drivers)


def _model(*, calculator=calculate, extra_code=(), baseline=None):
    source = Path(__file__).resolve()
    contract = DerivedDriverContract("utilization", "utilization", "ratio", "2027-01-01",
        "2027-12-31", "FY", "core", "consolidated", "IFRS", "NONE", "core", "capacity-path", "A1")
    return CaseCalculationModel("SYNTHETIC_EXISTING_CASE", "1",
        (CaseCodeFile(str(source), sha256(source.read_bytes()).hexdigest()), *extra_code),
        canonical_case_json(baseline or {"sales": "100", "cost": "10", "shares": "20"}),
        (contract,), (
            DerivedOutputContract("Base:fcff", "fcff_year_1", "USD_million", "Base", 2027,
                "core", "capacity-path", ("utilization",), ("https://example.test/frozen-baseline",)),
            DerivedOutputContract("Base:shares", "diluted_shares", "shares", "Base", 2026,
                "core", "capital-path", (), ("https://example.test/frozen-capital",)),
        ), calculator)


def _inputs():
    plan = reviewed_plan()
    result = run_campaign(plan, responder=informed_answer)
    assert result["status"] == "READY_FOR_COMPILATION"
    return dict(target_id=plan["target_id"], as_of=plan["as_of"], baseline_run_id="BASELINE",
        new_run_id="INFORMED-1", review_receipt=plan["informed_revision"]["review_receipt"],
        drivers={"utilization": result["outputs"]["utilization"]})


def _receipt(model=None):
    model = model or _model()
    return build_derived_case_receipt(model, **_inputs())


def _rehash(receipt):
    receipt["receipt_hash"] = derived_case_hash({key: value for key, value in receipt.items() if key != "receipt_hash"})
    return receipt


def _artifact(tmp_path, receipt, output="Base:fcff"):
    path = tmp_path / f"{receipt['receipt_hash']}.json"
    path.write_text(canonical_case_json(receipt), encoding="utf-8")
    return {"schema_version": "derived-case-reference/v1", "artifact_path": str(path),
        "artifact_sha256": sha256(path.read_bytes()).hexdigest(), "output_key": output}


def test_registered_existing_calculator_replays_all_case_rows_without_new_financial_authority(tmp_path):
    model = _model()
    inputs = _inputs()
    before = deepcopy(inputs)
    receipt = build_derived_case_receipt(model, **inputs)
    assert inputs == before
    assert receipt["outputs"]["Base:fcff"]["value"] == "70.0"
    registry = DerivedCaseRegistry((model,))
    verified = verify_derived_case_receipt(receipt, registry)
    assert verified.model_hash == model.model_hash
    assert verified.output("Base:shares").value == "20"
    output = verified.output("Base:fcff")
    assert (output.unit, output.scenario_id, output.year, output.segment_id) == ("USD_million", "Base", 2027, "core")
    assert output.proof["upstream_origins"][0]["candidate_id"] == "A1"
    assert output.proof["upstream_origins"][0]["reviewed_broker_source_refs"] == ["https://example.test/report"]
    assert "review_receipt" not in output.proof and "drivers" not in output.proof
    ref = _artifact(tmp_path, receipt)
    assert load_derived_case_reference(ref) == receipt
    assert verified_derived_output(ref, output.proof) == output
    assert verify_derived_case_reference(ref, registry) == output
    assert derived_case_revision_context(ref)["informed_revision"]["new_run_id"] == "INFORMED-1"
    assert "value_commit_allowed" not in output.proof


def test_plain_artifact_and_forged_proof_cannot_create_process_replay_authority(tmp_path):
    # Distinct trusted baseline makes this receipt unseen by earlier tests.
    model = _model(baseline={"sales": "101", "cost": "10", "shares": "20"})
    receipt = _receipt(model)
    ref = _artifact(tmp_path, receipt)
    with pytest.raises(DerivedCaseError, match="no trusted process-local"):
        verified_derived_output(ref, {"receipt_hash": receipt["receipt_hash"], "PASS": True})
    verified = verify_derived_case_receipt(receipt, DerivedCaseRegistry((model,)))
    proof = deepcopy(verified.output(ref["output_key"]).proof)
    proof["new_run_id"] = "UNAUTHORIZED"
    with pytest.raises(DerivedCaseError, match="proof differs"):
        verified_derived_output(ref, proof)


def test_missing_registry_and_receipt_code_paths_never_execute_unregistered_code(tmp_path):
    receipt = _receipt()
    with pytest.raises(DerivedCaseError, match="registry"):
        verify_derived_case_receipt(receipt, None)
    with pytest.raises(DerivedCaseError, match="not registered"):
        verify_derived_case_receipt(receipt, DerivedCaseRegistry(()))
    receipt["model_hash"] = "f" * 64
    receipt["model"]["code_files"] = [{"path": str(tmp_path / "future_code.py"), "sha256": "a" * 64}]
    with pytest.raises(DerivedCaseError, match="not registered"):
        verify_derived_case_receipt(_rehash(receipt), DerivedCaseRegistry((_model(),)))
    assert not (tmp_path / "future_code.py").exists()


def test_code_hash_changes_invalidate_new_and_previously_attested_replay(tmp_path):
    dependency = tmp_path / "trusted_dependency.py"
    dependency.write_text("# First trusted version\n")
    model = _model(extra_code=(CaseCodeFile(str(dependency), sha256(dependency.read_bytes()).hexdigest()),))
    receipt = _receipt(model)
    output = verify_derived_case_receipt(receipt, DerivedCaseRegistry((model,))).output("Base:fcff")
    ref = _artifact(tmp_path, receipt)
    dependency.write_text("# Changed trusted source\n")
    with pytest.raises(DerivedCaseError, match="code hash changed"):
        verify_derived_case_receipt(receipt, DerivedCaseRegistry((model,)))
    with pytest.raises(DerivedCaseError, match="code hash changed"):
        verified_derived_output(ref, output.proof)


def test_rehashed_baseline_parameter_injection_is_not_a_reviewed_input():
    receipt = _receipt()
    receipt["baseline_inputs"]["cost"] = "0"
    receipt["baseline_inputs_hash"] = derived_case_hash(receipt["baseline_inputs"])
    with pytest.raises(DerivedCaseError, match="baseline parameters"):
        verify_derived_case_receipt(_rehash(receipt), DerivedCaseRegistry((_model(),)))


@pytest.mark.parametrize("field,value", [
    ("value", "9999"), ("unit", "KRW_billion"), ("year", 2028),
    ("scenario_id", "Bull"), ("depends_on", []), ("metric", "enterprise_value"),
])
def test_rehashed_output_tampering_cannot_change_value_units_year_scenario_or_dependency_closure(field, value):
    receipt = _receipt()
    receipt["outputs"]["Base:fcff"][field] = value
    receipt["outputs_hash"] = derived_case_hash(receipt["outputs"])
    with pytest.raises(DerivedCaseError, match="deterministic replay"):
        verify_derived_case_receipt(_rehash(receipt), DerivedCaseRegistry((_model(),)))


def test_same_run_capital_share_operand_cannot_be_tampered_separately_from_case():
    receipt = _receipt()
    receipt["outputs"]["Base:shares"]["value"] = "1"
    receipt["outputs_hash"] = derived_case_hash(receipt["outputs"])
    with pytest.raises(DerivedCaseError, match="deterministic replay"):
        verify_derived_case_receipt(_rehash(receipt), DerivedCaseRegistry((_model(),)))


@pytest.mark.parametrize("mutation", ["missing", "extra", "candidate", "response", "mode", "run"])
def test_unapproved_or_changed_upstream_driver_receipts_cannot_be_adopted(mutation):
    model = _model()
    inputs = _inputs()
    if mutation == "missing":
        inputs["drivers"].clear()
    elif mutation == "extra":
        inputs["drivers"]["unreviewed_cost"] = inputs["drivers"]["utilization"]
    elif mutation == "candidate":
        model = replace(model, driver_contracts=(replace(model.driver_contracts[0], candidate_id="OTHER"),))
    elif mutation == "response":
        inputs["drivers"]["utilization"]["response"]["value"] = "0.9"
    elif mutation == "mode":
        inputs["drivers"]["utilization"]["work_order"]["research_mode"] = "BLIND_BASELINE"
    else:
        inputs["new_run_id"] = "UNRELATED"
    with pytest.raises(ValueError):
        build_derived_case_receipt(model, **inputs)


@pytest.mark.parametrize("field,value", [("unit", "count"), ("period_end", "2028-12-31"),
    ("frequency", "QUARTER"), ("scope", "other"), ("accounting_basis", "ADJUSTED"), ("currency", "KRW")])
def test_contract_basis_cannot_be_satisfied_by_a_similarly_named_reviewed_metric(field, value):
    model = _model()
    model = replace(model, driver_contracts=(replace(model.driver_contracts[0], **{field: value}),))
    with pytest.raises(DerivedCaseError, match="metric/unit/basis/candidate"):
        _receipt(model)


@pytest.mark.parametrize("calculator,reason", [
    (mutating_calculator, "mutated"), (nondeterministic_calculator, "nondeterministic"),
    (missing_output, "output closure"), (extra_authority_output, "output closure"),
    (nonfinite_output, "finite"),
])
def test_trusted_calculator_must_be_pure_deterministic_finite_and_complete(calculator, reason):
    with pytest.raises(DerivedCaseError, match=reason):
        _receipt(_model(calculator=calculator))


def test_output_contract_cannot_authorize_final_price_probability_or_audit():
    for metric in ("target_price", "probability", "PASS", "equity_value"):
        with pytest.raises(DerivedCaseError, match="authority"):
            replace(_model().output_contracts[0], metric=metric)
    with pytest.raises(DerivedCaseError, match="closure"):
        replace(_model(), output_contracts=(replace(_model().output_contracts[0], depends_on=("unknown",)),))


def test_fixed_provenance_cannot_borrow_reviewed_source_without_driver_dependency():
    model = _model()
    model = replace(model, output_contracts=(model.output_contracts[0],
        replace(model.output_contracts[1], source_refs=("https://example.test/report",))))
    with pytest.raises(DerivedCaseError, match="unrelated reviewed broker URL"):
        _receipt(model)


def test_artifact_mutation_and_duplicate_keys_fail_closed(tmp_path):
    ref = _artifact(tmp_path, _receipt())
    path = Path(ref["artifact_path"])
    path.write_text(path.read_text() + " ")
    with pytest.raises(DerivedCaseError, match="content hash"):
        load_derived_case_reference(ref)
    path.write_text('{"schema_version":"a","schema_version":"b"}')
    ref["artifact_sha256"] = sha256(path.read_bytes()).hexdigest()
    with pytest.raises(DerivedCaseError, match="duplicate"):
        load_derived_case_reference(ref)


def test_llm_proposal_context_cannot_build_or_attest_case_outputs():
    model = _model()
    receipt = _receipt(model)
    registry = DerivedCaseRegistry((model,))
    with llm_proposal_scope():
        with pytest.raises(PermissionError):
            build_derived_case_receipt(model, **_inputs())
        with pytest.raises(PermissionError):
            verify_derived_case_receipt(receipt, registry)


def test_llm_proposal_context_cannot_construct_trusted_model_or_register_existing_model():
    model = _model()
    with llm_proposal_scope():
        with pytest.raises(PermissionError):
            _model()
        with pytest.raises(PermissionError):
            replace(model, version="LLM-REPLACEMENT")
        with pytest.raises(PermissionError):
            DerivedCaseRegistry((model,))
        with pytest.raises(PermissionError):
            DerivedCaseRegistry(())


def test_one_reviewed_range_can_supply_explicit_scenario_points_without_inventing_midpoint():
    plan = reviewed_plan(ranged=True)
    drivers = {}
    for scenario, value in (("Down", "0.6"), ("Base", "0.8"), ("Bull", "0.9")):
        def answer(order, point=value):
            response = informed_answer(order)
            response["value"] = point
            return response
        drivers[scenario] = run_campaign(plan, responder=answer)["outputs"]["utilization"]
    original = _model()
    model = replace(original,
        driver_contracts=tuple(replace(original.driver_contracts[0], input_name=scenario)
                               for scenario in drivers),
        output_contracts=tuple(DerivedOutputContract(scenario, "fcff_year_1", "USD_million",
            scenario, 2027, "core", "capacity-path", (scenario,)) for scenario in drivers),
        calculate=shared_range_calculator)
    inputs = _inputs()
    inputs.update(review_receipt=plan["informed_revision"]["review_receipt"], drivers=drivers)
    receipt = build_derived_case_receipt(model, **inputs)
    output = verify_derived_case_receipt(receipt, DerivedCaseRegistry((model,)))
    assert [output.output(scenario).value for scenario in drivers] == ["60.0", "80.0", "90.0"]
    assert {row.proof["upstream_origins"][0]["candidate_id"] for row in output.outputs} == {"A1"}
    assert receipt["driver_values"]["Base"] == "0.8"  # Approved range midpoint would be 0.75, not used.


def test_distinct_functions_in_same_bound_source_file_have_different_model_hashes():
    original, replacement = _model(), _model(calculator=missing_output)
    assert original.code_files == replacement.code_files
    assert original.model_hash != replacement.model_hash
    identity = original.metadata()["callable_identity"]
    assert identity["module"] == __name__ and identity["qualname"] == "calculate"
    assert identity["code_sha256"] != replacement.metadata()["callable_identity"]["code_sha256"]
    with pytest.raises(DerivedCaseError, match="not registered"):
        verify_derived_case_receipt(_receipt(original), DerivedCaseRegistry((replacement,)))


@pytest.mark.parametrize("mutation", ["defaults", "default_type", "keyword_defaults", "code"])
def test_in_memory_callable_mutation_invalidates_registration_and_prior_attestation(tmp_path, monkeypatch, mutation):
    model = _model(calculator=default_calculator)
    receipt = _receipt(model)
    registry = DerivedCaseRegistry((model,))
    output = verify_derived_case_receipt(receipt, registry).output("Base:fcff")
    ref = _artifact(tmp_path, receipt)
    if mutation == "defaults":
        monkeypatch.setattr(default_calculator, "__defaults__", ("2",))
    elif mutation == "default_type":
        monkeypatch.setattr(default_calculator, "__defaults__", (Decimal("1"),))
    elif mutation == "keyword_defaults":
        monkeypatch.setattr(default_calculator, "__kwdefaults__", {"offset": "1"})
    else:
        monkeypatch.setattr(default_calculator, "__code__", missing_output.__code__)
    assert model.model_hash != receipt["model_hash"]
    with pytest.raises(DerivedCaseError, match="not registered"):
        verify_derived_case_receipt(receipt, registry)
    with pytest.raises(DerivedCaseError, match="callable identity"):
        verified_derived_output(ref, output.proof)


def test_hidden_closure_state_mutable_defaults_and_bound_callables_cannot_be_registered():
    hidden = {"factor": "2"}
    def closure_calculator(baseline, drivers):
        return {key: value * Decimal(hidden["factor"]) for key, value in calculate(baseline, drivers).items()}
    with pytest.raises(DerivedCaseError, match="closures"):
        _model(calculator=closure_calculator)
    with pytest.raises(DerivedCaseError, match="immutable"):
        _model(calculator=mutable_default_calculator)

    class StatefulCalculator:
        def calculate(self, baseline, drivers):
            return calculate(baseline, drivers)
        def __call__(self, baseline, drivers):
            return self.calculate(baseline, drivers)
    for callback in (StatefulCalculator(), StatefulCalculator().calculate):
        with pytest.raises(DerivedCaseError, match="plain Python function"):
            _model(calculator=callback)
