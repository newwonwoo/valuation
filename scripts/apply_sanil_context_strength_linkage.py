from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "valuation_engine" / "sanil_live_primary.py"


def replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"expected one replacement, found {count}: {old[:120]!r}"
        )
    return text.replace(old, new, 1)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from .control_plane import ExecutionMode\n",
        """from .context_strength_linkage import (
    ContextStrengthLinkage,
    ContextStrengthLinkageDecision,
)
from .control_plane import ExecutionMode
""",
    )
    old = '''    return IntelligenceProposal(
        hypotheses=hypotheses,
        rationale=(
            "Sanil is routed as contracted-backlog plus capacity-manufacturing; "
            + ("the typed Capacity Gate requires a Core expansion path." if capacity_required else "no incremental Core capacity path is required.")
        ),
    )
'''
    new = '''    linkage = ContextStrengthLinkage(
        id="CSL:SANIL:POWER_BOTTLENECK_CAPACITY",
        external_change=(
            "Grid replacement, renewable interconnection and data-center power demand "
            "are increasing the scarcity of qualified transformer delivery slots."
        ),
        emergent_need=(
            "Buyers need proven manufacturers with customer qualification, backlog "
            "visibility and physically controllable expansion capacity."
        ),
        company_strength=(
            "Sanil already has export customer access, a high-value specialty-transformer "
            "mix, an 88.9% utilized production base, reported backlog and a controlled "
            "second-factory site with committed CAPEX."
        ),
        linkage_thesis=(
            "The external power-equipment bottleneck specifically revalues Sanil's "
            "existing customer relationships and pre-invested site because those assets "
            "can convert scarce delivery slots into backlog conversion and FCFF."
        ),
        market_blind_spot=(
            "A generic small-transformer framing can separate current earnings from the "
            "option value of land-controlled capacity and overlook that the site, customer "
            "access and production know-how already exist."
        ),
        value_capture_path=(
            "land control and committed CAPEX → equipment/ramp execution → effective "
            "capacity → backlog conversion → revenue, margin and free cash flow"
        ),
        causal_chain=(
            "power-infrastructure demand and transformer-slot scarcity rise",
            "qualified delivery capacity becomes the binding buyer constraint",
            "Sanil's existing customer access, operating base and controlled site absorb the need",
            "capacity, CAPEX and ramp are consumed together in the Core scenario",
            "incremental shipments convert backlog into revenue and FCFF",
        ),
        supporting_evidence_ids=(
            _evidence_id("orders"),
            _evidence_id("backlog"),
            _evidence_id("utilization"),
            _evidence_id("expansion_land_control"),
            _evidence_id("expansion_site_area"),
            _evidence_id("expansion_capex_committed"),
        ),
        hypothesis_ids=("H:SANIL:CAPACITY", "H:SANIL:Core"),
        recognition_triggers=(
            "official second-factory equipment or production ramp disclosure",
            "effective-capacity growth with backlog conversion",
            "high-value product mix and margin retention after ramp",
        ),
        kill_conditions=(
            "the company cancels the program or confirms it is fully included in the frozen baseline",
            "backlog or orders decline before capacity converts to shipments",
            "ramp costs and margin normalization offset the added production ceiling",
        ),
        next_checks=(
            "next quarterly filing for factory ramp, CAPEX and utilization",
            "orders-to-revenue conversion and customer concentration",
            "cash conversion after expansion spending",
        ),
        confidence=0.78,
    )
    return IntelligenceProposal(
        hypotheses=hypotheses,
        rationale=(
            "Sanil is routed as contracted-backlog plus capacity-manufacturing; "
            + (
                "the typed Capacity Gate requires a Core expansion path."
                if capacity_required
                else "no incremental Core capacity path is required."
            )
        ),
        context_strength_linkage_decision=ContextStrengthLinkageDecision(
            linkages=(linkage,),
        ),
    )
'''
    text = replace_once(text, old, new)
    TARGET.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
