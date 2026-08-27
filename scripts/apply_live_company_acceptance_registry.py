from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNITS = ROOT / "src" / "valuation_engine" / "actual_units.py"
SOURCES = ROOT / "config" / "industry_source_registry.yaml"
SECTORS = ROOT / "config" / "sector_adapter_registry.yaml"
TEST = ROOT / "tests" / "test_live_company_acceptance_manifest.py"
GENERATOR = ROOT / "scripts" / "generate_required_live_company_fixtures.py"

VALUATION_METRICS = (
    "normalized_ebitda",
    "normalized_ebitda_multiple",
    "normalized_multiple",
    "ownership",
    "ev_adjustment",
    "diluted_shares",
)


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"patch target not found in {path.relative_to(ROOT)}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def add_metrics_after(path: Path, anchor: str) -> None:
    text = path.read_text(encoding="utf-8")
    block = "".join(f"    - {metric}\n" for metric in VALUATION_METRICS)
    if block in text:
        return
    if anchor not in text:
        raise RuntimeError(f"sector anchor not found: {anchor!r}")
    path.write_text(text.replace(anchor, anchor + block, 1), encoding="utf-8")


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
    SOURCES.write_text(text.replace(marker, descriptor + marker, 1), encoding="utf-8")


def patch_sectors() -> None:
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
    add_metrics_after(SECTORS, "    - capex\n  software.marketplace:\n")
    add_metrics_after(SECTORS, "    - lead_time\n  power.project_developer:\n")
    add_metrics_after(SECTORS, "    - fuel_or_input_economics\n  financials.asset_manager:\n")
    add_metrics_after(SECTORS, "    - price\n  real_assets.midstream:\n")


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
    patch_units()
    patch_sources()
    patch_sectors()
    patch_manifest_test()
    print("live-company acceptance registries integrated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
