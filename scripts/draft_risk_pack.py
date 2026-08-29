#!/usr/bin/env python3
"""Operator tool: draft and check a declared risk pack before a run uses it.

The declared risk pack (``VALUATION_RISK_PACK_PATH``) is the discount rate's
front door, and writing one by hand is where mistakes will happen. This tool
does two small things and nothing more:

    # 1. print a skeleton to fill in — placeholders, not defaults
    PYTHONPATH=src python scripts/draft_risk_pack.py template > runs/x/risk_pack.yaml

    # 2. check a filled file exactly the way the runtime will
    PYTHONPATH=src python scripts/draft_risk_pack.py check runs/x/risk_pack.yaml \
        --ticker 900881 --corp-code 00888801

``check`` loads the file through ``load_declared_risk_pack`` — the same eager
validation a run performs — then prints what the pack derives (peer-normalized
capital structure, partially pooled levered Beta, WACC) so the operator reviews
the actual numbers a run would use. With ``--ticker``/``--corp-code`` it also
runs the target-as-peer refusal locally. It computes nothing the runtime does
not compute and writes nothing; fixing the file stays a deliberate human edit.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from valuation_engine.declared_risk_pack import (  # noqa: E402
    DeclaredRiskPackError,
    load_declared_risk_pack,
)
from valuation_engine.live_primary_adapters import ResolvedCompanyIdentity  # noqa: E402
from valuation_engine.risk import hierarchical_partial_pool, relever_beta  # noqa: E402


_TEMPLATE = """\
# Declared risk pack — the discount rate's front door.
# Every value below is a placeholder the operator must replace; the loader
# refuses missing levels, thin rationales, non-HTTP references and implicit
# rate units, and a run refuses a pack bound to a different company.
target_id: KR:DART:<8-digit corp code>
as_of: "<YYYY-MM-DD>"
source_ref: https://<provenance of this declaration>
cash_flow_currency: KRW
risk_free_rate:
  time: "<YYYYMMDD>"
  value: <e.g. 3.10>
  unit: 연%            # 연% or ratio — implicit units are refused
  name: 국고채 10년
  source_ref: https://ecos.bok.or.kr/...
country_risk:
  country: Korea
  as_of: "<YYYY-MM-DD>"
  mature_market_erp: <e.g. 0.0508>
  country_risk_premium: <e.g. 0.0057>
  total_equity_risk_premium: <erp + crp>
  adjusted_default_spread: <e.g. 0.0030>
  corporate_tax_rate: <e.g. 0.24>
  rating: <e.g. AA>
  # source_ref defaults to the Damodaran country-risk page; override if needed
marginal_debt:
  series:
    time: "<YYYYMMDD>"
    value: <e.g. 4.35>
    unit: 연%
    name: <e.g. 회사채 AA- 3년>
    source_ref: https://ecos.bok.or.kr/...
  credit_rating: <issuer-matched rating, e.g. AA->
  maturity: <e.g. 3Y>
  rating_source_ref: https://<rating provenance>
# All four levels are required — a missing level is a missing judgment.
# Every peer needs: one shared benchmark and estimation window across the whole
# pack, a capital observation, and HTTP provenance on every reference.
# The TARGET MAY NOT APPEAR AMONG ITS OWN PEERS (ticker or corp code).
beta_levels:
  L1_BROAD_SECTOR:
    selection_rationale: <why these peers form the broad-sector prior — 20+ chars>
    risk_driver_features: [<feature>]
    peers: &peer_shape
      - peer_id: "<stock code>"
        beta:
          benchmark: 코스피
          beta: <levered regression beta>
          observations: <e.g. 250>
          start_date: "<YYYY-MM-DD>"
          end_date: "<YYYY-MM-DD>"
        capital:
          debt: <total debt>
          equity_market_value: <market cap, same unit as debt>
          tax_rate: <e.g. 0.24>
          as_of: "<YYYY-MM-DD>"
          source_ref: https://<capital observation provenance>
        beta_source_ref: https://<regression beta provenance>
        # beta_standard_error: <optional>
  L2_INDUSTRY:
    selection_rationale: <industry peer set rationale>
    risk_driver_features: [<feature>]
    peers: *peer_shape
  L3_RISK_DRIVER_SUBINDUSTRY:
    selection_rationale: <sub-industry / shared-risk-driver rationale>
    risk_driver_features: [<feature>]
    peers: *peer_shape
  L4_ECONOMIC_TWINS:
    selection_rationale: <economic-twin conditions met>
    risk_driver_features: [<feature>, <feature>]
    peers: *peer_shape
# country_risk_lambda: 0.0
# country_risk_exposure_source_ref: https://<required if lambda > 0>
"""


def _check(path: str, *, ticker: str | None, corp_code: str | None) -> int:
    try:
        declared = load_declared_risk_pack(path)
    except (DeclaredRiskPackError, Exception) as exc:
        print(f"REFUSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    universe = declared.beta_universe()
    inputs = declared.wacc_inputs()

    if ticker or corp_code:
        external = [("check", ticker or corp_code or "")]
        if corp_code:
            external.append(("corp_code", corp_code))
        identity = ResolvedCompanyIdentity(
            target_id=declared.target_id,
            legal_name="(check)",
            ticker=ticker or "",
            jurisdiction="KR",
            external_ids=tuple(external),
            source_refs=("https://check.local",),
        )
        try:
            declared.assert_target_not_a_peer(identity)
        except DeclaredRiskPackError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 1

    structure = universe.target_capital_structure
    estimate = hierarchical_partial_pool(
        tuple(level.to_engine_level() for level in universe.levels)
    )
    levered = relever_beta(
        estimate.asset_beta,
        debt=structure.debt_weight,
        equity=structure.equity_weight,
        tax_rate=structure.tax_rate,
    )
    print(f"pack OK: {declared.target_id}  as_of={declared.as_of}")
    print(f"  file_sha256           {declared.file_sha256}")
    for level in universe.levels:
        peers = ", ".join(peer.peer_id for peer in level.peers)
        print(f"  {level.level.value:28s} peers: {peers}")
    print(f"  debt/(debt+equity)    {structure.debt_weight:.4f} (peer-normalized)")
    print(f"  asset beta (pooled)   {estimate.asset_beta:.4f}")
    print(f"  levered beta          {levered:.4f}")
    print(f"  risk-free             {inputs.risk_free_rate.value:.4%}")
    print(f"  ERP / CRP             {inputs.equity_risk_premium.value:.4%} / "
          f"{inputs.country_risk_premium.value:.4%}")
    print(f"  pre-tax cost of debt  {inputs.marginal_pre_tax_cost_of_debt.value:.4%}")
    print("  (the WACC itself is computed and validated inside the run)")
    print("evidence the run will require in its ledger:")
    for evidence_id in declared.selection_evidence_ids():
        print(f"  {evidence_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("template", help="print a skeleton risk pack to stdout")
    check = sub.add_parser("check", help="validate a filled pack the way a run will")
    check.add_argument("path")
    check.add_argument("--ticker", help="target stock code, for the target-as-peer refusal")
    check.add_argument("--corp-code", help="target DART corp code, same refusal")
    args = parser.parse_args()
    if args.command == "template":
        sys.stdout.write(_TEMPLATE)
        return 0
    return _check(args.path, ticker=args.ticker, corp_code=args.corp_code)


if __name__ == "__main__":
    raise SystemExit(main())
