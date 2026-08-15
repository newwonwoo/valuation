from __future__ import annotations

from enum import Enum


class IndustryModel(str, Enum):
    ORDER_EQUIPMENT = "order_equipment"
    CONSTRUCTION_SHIPBUILDING = "construction_shipbuilding"
    COMMODITY_MATERIALS = "commodity_materials"
    ENERGY_INFRA = "energy_infrastructure"
    SOFTWARE_PLATFORM = "software_platform"
    CONSUMER_RETAIL = "consumer_retail"
    HOLDING_COMPANY = "holding_company"
    FINANCIALS = "financials"
    PHARMA_BIO = "pharma_bio"
    GENERIC = "generic"


KEYWORDS = {
    IndustryModel.ORDER_EQUIPMENT: ("장비", "수주잔고", "backlog", "equipment"),
    IndustryModel.CONSTRUCTION_SHIPBUILDING: ("건설", "조선", "진행률", "shipbuilding"),
    IndustryModel.COMMODITY_MATERIALS: ("소재", "폴리실리콘", "원재료", "commodity", "materials"),
    IndustryModel.ENERGY_INFRA: ("전력", "발전", "변압기", "터빈", "energy", "power"),
    IndustryModel.SOFTWARE_PLATFORM: ("소프트웨어", "플랫폼", "cloud", "software", "saas"),
    IndustryModel.CONSUMER_RETAIL: ("소비재", "유통", "retail", "consumer"),
    IndustryModel.HOLDING_COMPANY: ("홀딩스", "지주", "holding"),
    IndustryModel.FINANCIALS: ("은행", "보험", "증권", "financial"),
    IndustryModel.PHARMA_BIO: ("제약", "바이오", "pipeline", "clinical"),
}


def route_industry(description: str) -> IndustryModel:
    text = description.lower()
    if any(keyword.lower() in text for keyword in KEYWORDS[IndustryModel.HOLDING_COMPANY]):
        return IndustryModel.HOLDING_COMPANY
    scores = {}
    for model, keywords in KEYWORDS.items():
        if model is IndustryModel.HOLDING_COMPANY:
            continue
        scores[model] = sum(1 for keyword in keywords if keyword.lower() in text)
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else IndustryModel.GENERIC
