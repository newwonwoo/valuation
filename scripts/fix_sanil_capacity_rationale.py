from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SANIL = ROOT / "src" / "valuation_engine" / "sanil_live_primary.py"
TEST = ROOT / "tests" / "test_sanil_live_primary.py"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one replacement, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = SANIL.read_text(encoding="utf-8")
    text = replace_once(
        text,
        """def _intelligence_officer(context) -> IntelligenceProposal:
    capacity = context.capacity_commitment_assessment
    capacity_required = bool(capacity and capacity.core_inclusion_required_projects)
""",
        """def _intelligence_officer(context) -> IntelligenceProposal:
""",
        label="remove pre-gate capacity lookup",
    )
    text = replace_once(
        text,
        """        rationale=(
            "Sanil is routed as contracted-backlog plus capacity-manufacturing; "
            + (
                "the typed Capacity Gate requires a Core expansion path."
                if capacity_required
                else "no incremental Core capacity path is required."
            )
        ),
""",
        """        rationale=(
            "Sanil is routed as contracted-backlog plus capacity-manufacturing; "
            "the declared land-controlled second-factory project must be classified "
            "by the typed Capacity Gate and, when confirmed incremental, consumed "
            "as one Core capacity, CAPEX and ramp path."
        ),
""",
        label="replace contradictory capacity rationale",
    )
    SANIL.write_text(text, encoding="utf-8")

    tests = TEST.read_text(encoding="utf-8")
    marker = """    assert \"산일전기\" in result.data[\"final_report\"]

    run_root = tmp_path / \"runs\" / TICKER / \"SANIL-062040-20260825\"
"""
    replacement = """    assert \"산일전기\" in result.data[\"final_report\"]
    assert \"must be classified by the typed Capacity Gate\" in result.data[\"final_report\"]
    assert \"no incremental Core capacity path is required\" not in result.data[\"final_report\"]

    run_root = tmp_path / \"runs\" / TICKER / \"SANIL-062040-20260825\"
"""
    tests = replace_once(
        tests,
        marker,
        replacement,
        label="add capacity-rationale regression",
    )
    TEST.write_text(tests, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
