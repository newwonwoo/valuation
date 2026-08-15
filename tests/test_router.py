from valuation_engine.router import IndustryModel, route_industry


def test_materials_router():
    assert route_industry("고순도 폴리실리콘 소재 생산 및 웨이퍼") == IndustryModel.COMMODITY_MATERIALS


def test_holding_router():
    assert route_industry("지주회사 및 자회사 포트폴리오") == IndustryModel.HOLDING_COMPANY


def test_holding_override_for_mixed_description():
    assert route_industry("OCI홀딩스: 폴리실리콘 소재와 에너지 자회사 지주회사") == IndustryModel.HOLDING_COMPANY
