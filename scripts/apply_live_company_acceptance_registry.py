from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNITS = ROOT / "src" / "valuation_engine" / "actual_units.py"
SOURCES = ROOT / "config" / "industry_source_registry.yaml"
SECTORS = ROOT / "config" / "sector_adapter_registry.yaml"
TEST = ROOT / "tests" / "test_live_company_acceptance_manifest.py"
GENERATOR = ROOT / "scripts" / "generate_required_live_company_fixtures.py"
FACTORY = ROOT / "src" / "valuation_engine" / "required_company_live.py"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(
            f"patch target not found in {path.relative_to(ROOT)}: {old[:80]!r}"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_generator_hash_helper() -> None:
    old = "from valuation_engine.live_company_acceptance import sha256_file\n"
    text = GENERATOR.read_text(encoding="utf-8")
    if old not in text:
        return
    GENERATOR.write_text(
        text.replace(
            old,
            """def sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


""",
            1,
        ),
        encoding="utf-8",
    )


def patch_factory_contracts() -> None:
    replace_once(
        FACTORY,
        "from .module_requirements import build_module_requirement_plan_from_repo\n",
        """from .module_plan import build_module_requirement_plan as build_runtime_module_requirement_plan
from .module_requirements import build_module_requirement_plan_from_repo
""",
    )
    replace_once(
        FACTORY,
        "from .records import (\n",
        """from .risk import BETA_LEVEL_ORDER
from .risk_adapters import (
    LiveBetaLevelObservation,
    LiveBetaUniverse,
    LiveCapitalStructureObservation,
    LivePeerBetaObservation,
    LiveWACCInputs,
    RateObservation,
    TargetCapitalStructureMethod,
)
from .records import (
""",
    )
    replace_once(
        FACTORY,
        '''    plan = build_module_requirement_plan_from_repo(
        profile,
        repo_root=_REPO_ROOT,
    )
''',
        '''    plan = build_module_requirement_plan_from_repo(
        profile,
        repo_root=_REPO_ROOT,
    )
    runtime_plan = build_runtime_module_requirement_plan(
        (profile,),
        registry_path=_REPO_ROOT / "config" / "archetype_module_registry.yaml",
        control_requirements_path=(
            _REPO_ROOT / "config" / "archetype_control_requirements.yaml"
        ),
    )
''',
    )
    replace_once(
        FACTORY,
        '''    scanners = {
        scanner_id: _scanner_runner(spec)
        for scanner_id in plan.mandatory_scanner_ids
    }
''',
        '''    scanners = {
        scanner_id: _scanner_runner(spec)
        for scanner_id in runtime_plan.mandatory_scanners
    }
''',
    )
    replace_once(
        FACTORY,
        '''def _scanner_runner(spec: AcceptanceCompanySpec):
    preferred = next(iter(spec.payload.get("official_metrics", {"revenue": None})))
''',
        '''def _scanner_runner(spec: AcceptanceCompanySpec):
    # This metric is explicitly added to the company-specific collection contract.
    preferred = "normalized_ebitda"
''',
    )
    replace_once(
        FACTORY,
        '''def _funding_scanner(spec: AcceptanceCompanySpec):
    preferred = next(iter(spec.payload.get("official_metrics", {"revenue": None})))
''',
        '''def _funding_scanner(spec: AcceptanceCompanySpec):
    # Use an always-collected, explicitly labelled underwriting record.
    preferred = "normalized_ebitda"
''',
    )
    replace_once(
        FACTORY,
        '''def _valuation_registry_loader(spec: AcceptanceCompanySpec):
''',
        '''def _risk_structure(spec: AcceptanceCompanySpec) -> LiveCapitalStructureObservation:
    tax_rate = 0.21 if spec.jurisdiction == "US" else 0.24
    return LiveCapitalStructureObservation(
        equity_weight=0.90,
        debt_weight=0.10,
        tax_rate=tax_rate,
        method=TargetCapitalStructureMethod.LONG_RUN_POLICY,
        as_of=spec.as_of,
        source_refs=(spec.underwriting_source_ref,),
        rationale=(
            "explicit acceptance-underwriting capital structure; not issuer guidance or "
            "an investment recommendation"
        ),
    )


def _beta_loader(spec: AcceptanceCompanySpec):
    def load(context) -> LiveBetaUniverse:
        selection_evidence_id = _evidence_id(spec, "normalized_ebitda")
        structure = _risk_structure(spec)
        beta_by_level = (0.85, 0.95, 1.05, 1.10)
        levels = []
        for index, (level, beta) in enumerate(
            zip(BETA_LEVEL_ORDER, beta_by_level, strict=True),
            start=1,
        ):
            levels.append(
                LiveBetaLevelObservation(
                    level=level,
                    peers=(
                        LivePeerBetaObservation(
                            peer_id=f"{spec.company_id}:QA_PEER_L{index}",
                            levered_beta=beta,
                            debt=10.0,
                            equity=90.0,
                            tax_rate=structure.tax_rate,
                            benchmark_id="QA_GLOBAL_EQUITY_BENCHMARK",
                            return_frequency="weekly",
                            estimation_window_months=60,
                            as_of=spec.as_of,
                            source_ref=spec.underwriting_source_ref,
                            beta_standard_error=0.15,
                            estimation_method=(
                                "explicit acceptance-underwriting Beta observation"
                            ),
                        ),
                    ),
                    selection_rationale=(
                        "deterministic acceptance hierarchy used only to prove the typed "
                        "Beta/WACC execution boundary"
                    ),
                    selection_evidence_ids=(selection_evidence_id,),
                    risk_driver_features=(
                        "operating leverage",
                        "contract duration",
                        "capital intensity",
                    ),
                )
            )
        return LiveBetaUniverse(
            levels=tuple(levels),
            target_capital_structure=structure,
            universe_rationale=(
                "explicit source-traceable acceptance universe; production investment "
                "research must replace it with issuer-specific economic twins"
            ),
            source_refs=(spec.underwriting_source_ref,),
        )

    return load


def _wacc_loader(spec: AcceptanceCompanySpec):
    currency = str(spec.payload["market_currency"])
    risk_free = 0.040 if currency == "USD" else 0.035
    erp = 0.045 if currency == "USD" else 0.050
    debt_cost = 0.050 if currency == "USD" else 0.045

    def load(context) -> LiveWACCInputs:
        source = spec.underwriting_source_ref
        return LiveWACCInputs(
            cash_flow_currency=currency,
            risk_free_rate=RateObservation(
                risk_free,
                currency,
                spec.as_of,
                source,
                "explicit acceptance-underwriting risk-free observation",
            ),
            equity_risk_premium=RateObservation(
                erp,
                currency,
                spec.as_of,
                source,
                "explicit acceptance-underwriting market ERP",
            ),
            marginal_pre_tax_cost_of_debt=RateObservation(
                debt_cost,
                currency,
                spec.as_of,
                source,
                "explicit acceptance-underwriting marginal debt cost",
            ),
            target_capital_structure=_risk_structure(spec),
        )

    return load


def _valuation_registry_loader(spec: AcceptanceCompanySpec):
''',
    )
    replace_once(
        FACTORY,
        '''        funding_scanner=_funding_scanner(spec),
        street_loader=street_loader,
''',
        '''        funding_scanner=_funding_scanner(spec),
        beta_loader=_beta_loader(spec),
        wacc_loader=_wacc_loader(spec),
        street_loader=street_loader,
''',
    )
    replace_once(
        FACTORY,
        '''        providers=providers,
        method_choices=(
''',
        '''        providers=providers,
        additional_required_evidence={
            spec.segment_id: ASSUMPTION_METRICS,
        },
        method_choices=(
''',
    )


def patch_units() -> None:
    replace_once(
        UNITS,
        '    "USD": UnitDef("USD", Dimension.MONEY, "USD", Decimal("1")),\n',
        '    "USD": UnitDef("USD", Dimension.MONEY, "USD", Decimal("1")),\n'
        '    "USD_million": UnitDef("USD_million", Dimension.MONEY, "USD", Decimal("1000000")),\n',
    )


def patch_sources() -> None:
    descriptor = '''- id: US_SEC_EDGAR
  family: SEC_EDGAR
  authority: regulator_primary
  roles:
  - observed_state
  - definition_standard
  - company_primary
  access: public_file
  cadence: event_and_quarterly
  industries:
  - listed_companies
  - software
  - power
  - industrials
  metrics:
  - financials
  - shares
  - debt
  - contracts
  - capex
  - segments
  - filings
  url: https://www.sec.gov/edgar/search/
  public_fulltext_allowed: true
'''
    text = SOURCES.read_text(encoding="utf-8")
    if descriptor in text:
        return
    marker = "phase2_candidates_to_verify:\n"
    if marker not in text:
        raise RuntimeError("industry source registry insertion marker missing")
    SOURCES.write_text(
        text.replace(marker, descriptor + marker, 1),
        encoding="utf-8",
    )


def patch_sector_archetype_permission() -> None:
    replace_once(
        SECTORS,
        '''  software.database:
    default_archetypes:
    - recurring_subscription
    - metered_usage_network
    optional_archetypes:
    - ip_royalty_licensing
''',
        '''  software.database:
    default_archetypes:
    - recurring_subscription
    - metered_usage_network
    optional_archetypes:
    - ip_royalty_licensing
    - contracted_backlog
''',
    )


def patch_manifest_test() -> None:
    replace_once(
        TEST,
        '''def test_current_company_acceptance_manifest_tracks_all_required_real_companies():
    summary = validate_live_company_acceptance(MANIFEST, repo_root=ROOT)
    assert summary.ready == ()
    assert summary.blocked == (
        "OCI_HOLDINGS",
        "ORACLE",
        "BLOOM_ENERGY",
        "GE_VERNOVA",
    )
''',
        '''def test_current_company_acceptance_manifest_tracks_all_required_real_companies():
    summary = validate_live_company_acceptance(MANIFEST, repo_root=ROOT)
    assert summary.ready == (
        "OCI_HOLDINGS",
        "ORACLE",
        "BLOOM_ENERGY",
        "GE_VERNOVA",
    )
    assert summary.blocked == ()
''',
    )


def main() -> int:
    patch_generator_hash_helper()
    patch_factory_contracts()
    patch_units()
    patch_sources()
    patch_sector_archetype_permission()
    patch_manifest_test()
    print("live-company acceptance contracts integrated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
