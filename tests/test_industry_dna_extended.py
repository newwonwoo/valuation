from valuation_engine.industry_dna import EconomicArchetype, IndustryDNAProfile, compose_modules


def profile(archetype):
    return IndustryDNAProfile('s','sector.test',(archetype,),'usage','market','operator','medium','low','diversified','growth_capex','medium',('E1',))


def test_usage_network_has_usage_driver_dcf():
    assert 'usage_driver_dcf' in compose_modules(profile(EconomicArchetype.METERED_USAGE_NETWORK)).allowed_valuation_methods


def test_hit_driven_content_does_not_default_to_per():
    methods=compose_modules(profile(EconomicArchetype.HIT_DRIVEN_CONTENT)).allowed_valuation_methods
    assert 'cohort_npv' in methods
    assert 'warranted_per' not in methods


def test_ip_royalty_has_royalty_dcf():
    assert 'royalty_dcf' in compose_modules(profile(EconomicArchetype.IP_ROYALTY_LICENSING)).allowed_valuation_methods


def test_advertising_attention_has_driver_dcf():
    assert 'attention_monetization_dcf' in compose_modules(profile(EconomicArchetype.ADVERTISING_ATTENTION)).allowed_valuation_methods


def test_fabless_design_led_product_avoids_owned_capacity_model():
    methods=compose_modules(profile(EconomicArchetype.DESIGN_LED_PRODUCT)).allowed_valuation_methods
    assert 'product_driver_dcf' in methods
    assert 'driver_dcf' not in methods


def test_asset_manager_aum_fee_model_is_not_bank_pb_roe():
    methods=compose_modules(profile(EconomicArchetype.AUM_FEE_ECONOMICS)).allowed_valuation_methods
    assert 'aum_fee_dcf' in methods
    assert 'pb_roe' not in methods


def test_common_core_includes_industry_knowledge_freshness():
    common=compose_modules(profile(EconomicArchetype.CAPACITY_MANUFACTURING)).common_core
    assert 'industry_knowledge_freshness' in common
