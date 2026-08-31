from valuation_engine.generic_kr_industry import load_kr_industry_classification
from valuation_engine.industry_dna import EconomicArchetype


def test_metal_wholesale_routes_to_process_spread():
    entry = load_kr_industry_classification().lookup("46721")
    assert entry.sector_adapter == "metals.trading"
    assert entry.archetypes == (EconomicArchetype.PROCESS_SPREAD,)


def test_nonhazardous_waste_processing_routes_to_process_spread():
    entry = load_kr_industry_classification().lookup("38210")
    assert entry.sector_adapter == "environmental.waste_processing"
    assert entry.archetypes == (EconomicArchetype.PROCESS_SPREAD,)
