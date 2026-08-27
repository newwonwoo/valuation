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
