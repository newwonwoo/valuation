from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "src" / "valuation_engine" / "report_form.py"
TEST = ROOT / "tests" / "test_report_form.py"
MARKER = "test_manual_or_partial_result_cannot_be_labelled_verified"


def main() -> int:
    text = REPORT.read_text(encoding="utf-8")
    if "class RunAttestation" not in text:
        text = text.replace(
            "from typing import Any\n",
            "from dataclasses import dataclass\nfrom typing import Any\n",
            1,
        )
        marker = "REPORT_SECTIONS = ("
        insertion = '''@dataclass(frozen=True)\nclass RunAttestation:\n    checks: tuple[tuple[str, bool], ...]\n\n    @property\n    def passed(self) -> bool:\n        return all(passed for _, passed in self.checks)\n\n\ndef attest_controlled_run(result: ControlledRunResult) -> RunAttestation:\n    data = result.data\n    selected_methods = data.get("selected_methods", ())\n    requires_risk = (\n        isinstance(selected_methods, tuple)\n        and any(\n            token in method.lower()\n            for method in selected_methods\n            for token in ("dcf", "npv", "ddm", "pb_roe", "residual_income", "rate_base_roe")\n        )\n    )\n    assessment = data.get("capacity_commitment_assessment")\n    core_capacity_required = bool(\n        getattr(assessment, "core_inclusion_required_projects", ())\n    )\n    checks = [\n        ("run_unblocked", not result.blocked_reasons),\n        ("intrinsic_freeze_present", result.freeze_token is not None),\n        ("canonical_stage_trace_complete", len(result.stage_traces) == 33),\n        ("evidence_ledger_hash_present", bool(data.get("ledger_snapshot_hash"))),\n        ("valuation_hash_present", bool(data.get("valuation_hash"))),\n    ]\n    if requires_risk:\n        checks.extend(\n            (\n                ("beta_snapshot_hash_present", bool(data.get("beta_snapshot_hash"))),\n                ("wacc_snapshot_hash_present", bool(data.get("wacc_snapshot_hash"))),\n            )\n        )\n    if core_capacity_required:\n        checks.extend(\n            (\n                ("capacity_assessment_hash_present", bool(data.get("capacity_commitment_assessment_hash"))),\n                ("capacity_consumption_hash_present", bool(data.get("capacity_bridge_consumption_hash"))),\n                ("capacity_scenario_binding_hash_present", bool(data.get("capacity_scenario_binding_hash"))),\n                ("capacity_valuation_binding_hash_present", bool(data.get("capacity_valuation_binding_hash"))),\n                ("capacity_audit_hash_present", bool(data.get("capacity_audit_hash"))),\n            )\n        )\n    return RunAttestation(tuple(checks))\n\n\n'''
        if marker not in text:
            raise RuntimeError("report section marker missing")
        text = text.replace(marker, insertion + marker, 1)
        old = '''    blocked = bool(result.blocked_reasons)\n    frozen = result.freeze_token is not None\n    status = "BLOCKED" if blocked else ("VERIFIED_FROZEN" if frozen else "INCOMPLETE")\n'''
        new = '''    blocked = bool(result.blocked_reasons)\n    attestation = attest_controlled_run(result)\n    status = (\n        "BLOCKED"\n        if blocked\n        else ("VERIFIED_FROZEN" if attestation.passed else "INCOMPLETE")\n    )\n'''
        if old not in text:
            raise RuntimeError("report status block missing")
        text = text.replace(old, new, 1)
        stage = '''    lines.extend(("", "## Stage Trace", "", "| Stage | Status | Rationale |", "|---|---|---|"))\n'''
        attestation = '''    lines.extend(("", "## Execution Attestation", "", "| Check | Result |", "|---|---:|"))\n    for check, passed in attestation.checks:\n        lines.append(f"| `{check}` | {'PASS' if passed else 'FAIL'} |")\n    lines.extend(("", "## Stage Trace", "", "| Stage | Status | Rationale |", "|---|---|---|"))\n'''
        if stage not in text:
            raise RuntimeError("stage trace marker missing")
        text = text.replace(stage, attestation, 1)
        template = '''        "| Capacity audit hash | `{{ capacity_audit_hash }}` |",\n        "",\n        "## Stage Trace",\n'''
        template_new = '''        "| Capacity audit hash | `{{ capacity_audit_hash }}` |",\n        "",\n        "## Execution Attestation",\n        "",\n        "| Check | Result |",\n        "|---|---:|",\n        "| `{{ check }}` | `{{ PASS_OR_FAIL }}` |",\n        "",\n        "## Stage Trace",\n'''
        if template not in text:
            raise RuntimeError("report template marker missing")
        text = text.replace(template, template_new, 1)
        REPORT.write_text(text, encoding="utf-8")

    tests = TEST.read_text(encoding="utf-8")
    if MARKER not in tests:
        tests += '''\n\ndef test_manual_or_partial_result_cannot_be_labelled_verified():\n    from types import SimpleNamespace\n\n    from valuation_engine.control_plane import ExecutionMode\n    from valuation_engine.orchestrator import ControlledRunResult\n    from valuation_engine.report_form import render_controlled_run_report\n\n    result = ControlledRunResult(\n        run_id="MANUAL",\n        execution_mode=ExecutionMode.LIVE_PRIMARY,\n        stage_traces=(),\n        data={"valuation_hash": "V", "ledger_snapshot_hash": "L"},\n        blocked_reasons=(),\n        freeze_token=SimpleNamespace(token_hash="FORGED"),\n    )\n    report = render_controlled_run_report(result)\n    assert "Run status: **INCOMPLETE**" in report\n    assert "canonical_stage_trace_complete` | FAIL" in report\n\n\ndef test_capacity_report_requires_all_capacity_execution_hashes():\n    from types import SimpleNamespace\n\n    from valuation_engine.control_plane import ExecutionMode\n    from valuation_engine.orchestrator import ControlledRunResult\n    from valuation_engine.report_form import render_controlled_run_report\n\n    assessment = SimpleNamespace(core_inclusion_required_projects=("P1",))\n    traces = tuple(\n        SimpleNamespace(stage=f"S{index}", status="PASS", rationale="ok")\n        for index in range(33)\n    )\n    result = ControlledRunResult(\n        run_id="CAPACITY",\n        execution_mode=ExecutionMode.LIVE_PRIMARY,\n        stage_traces=traces,\n        data={\n            "valuation_hash": "V",\n            "ledger_snapshot_hash": "L",\n            "capacity_commitment_assessment": assessment,\n            "capacity_commitment_assessment_hash": "A",\n        },\n        blocked_reasons=(),\n        freeze_token=SimpleNamespace(token_hash="F"),\n    )\n    report = render_controlled_run_report(result)\n    assert "Run status: **INCOMPLETE**" in report\n    assert "capacity_audit_hash_present` | FAIL" in report\n'''
        TEST.write_text(tests, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
