import pytest

from valuation_engine.router import (
    IndustryModel,
    LegacyRouterAccessError,
    route_industry,
    route_industry_for_execution,
)


def test_materials_router():
    assert route_industry("고순도 폴리실리콘 소재 생산 및 웨이퍼") == IndustryModel.COMMODITY_MATERIALS


def test_holding_router():
    assert route_industry("지주회사 및 자회사 포트폴리오") == IndustryModel.HOLDING_COMPANY


def test_holding_override_for_mixed_description():
    assert route_industry("OCI홀딩스: 폴리실리콘 소재와 에너지 자회사 지주회사") == IndustryModel.HOLDING_COMPANY


def test_legacy_router_allowed_only_for_legacy_regression():
    assert route_industry_for_execution(
        "고순도 폴리실리콘 소재 생산",
        execution_mode="legacy_regression",
    ) is IndustryModel.COMMODITY_MATERIALS

    with pytest.raises(LegacyRouterAccessError):
        route_industry_for_execution(
            "고순도 폴리실리콘 소재 생산",
            execution_mode="live_primary",
        )

    with pytest.raises(LegacyRouterAccessError):
        route_industry_for_execution(
            "unknown business",
            execution_mode="primary_shadow",
        )
