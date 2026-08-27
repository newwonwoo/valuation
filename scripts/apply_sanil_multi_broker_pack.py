from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SANIL = ROOT / "src" / "valuation_engine" / "sanil_live_primary.py"
TEST = ROOT / "tests" / "test_sanil_live_primary.py"
REGISTRY = ROOT / "config" / "broker_research_source_registry.yaml"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def patch_sanil() -> None:
    text = SANIL.read_text(encoding="utf-8")
    constants_old = '''_MIRAE_2Q26_REPORT_URL = (
    "https://securities.miraeasset.com/bbs/board/message/view.do"
    "?categoryId=1800&messageId=2341906"
)
'''
    constants_new = constants_old + '''_MIRAE_POWER_SOLUTION_REPORT_URL = (
    "https://securities.miraeasset.com/bbs/board/message/list.do"
    "?categoryId=1800&searchStartYear=2026&searchStartMonth=07&searchStartDay=16"
    "&searchEndYear=2026&searchEndMonth=07&searchEndDay=16"
)
_IBK_2Q26_REPORT_URL = "https://www.yna.co.kr/view/AKR20260810017900008"
_SHINHAN_2Q26_REPORT_URL = "https://www.yna.co.kr/amp/view/AKR20260811028700008"
'''
    text = replace_once(text, constants_old, constants_new, "broker URL constants")

    start = text.index("def _broker_research_loader(snapshot: SanilSnapshot):")
    end = text.index("def _valuation_plan_inputs", start)
    replacement = '''def _broker_research_loader(snapshot: SanilSnapshot):
    def load(_context: OrchestratorContext) -> BrokerResearchBatch:
        return BrokerResearchBatch(
            checked_at=snapshot.cutoff,
            observations=(
                BrokerResearchObservation(
                    claim=BrokerClaim(
                        claim_id="B:SANIL:MIRAE:POWER_SOLUTION_CONTEXT",
                        source_id="KR_MIRAE_INDUSTRY_RESEARCH",
                        broker_family="MiraeAssetSecurities",
                        report_type=BrokerReportType.INDUSTRY_DEEP_DIVE,
                        field_class=BrokerFieldClass.MECHANISM_CANDIDATE,
                        industry_node="power_transformers",
                        statement=(
                            "Mirae's same-date power-solution coverage frames qualified "
                            "transformer capacity expansion and delivery-slot scarcity as "
                            "sector mechanisms; Sanil-specific facts remain primary-verified."
                        ),
                        target_company_specific=False,
                        underlying_data_families=("company_filing", "company_ir"),
                        report_date="2026-07-16",
                    ),
                    segment_id=SEGMENT_ID,
                    source_ref=_MIRAE_POWER_SOLUTION_REPORT_URL,
                    verification_metrics=("effective_capacity", "utilization", "lead_time"),
                    verification_requests=(
                        "verify capacity, utilization and delivery lead time in company primary sources",
                    ),
                    primary_source_hints=("2025 annual report", "2Q26 company IR"),
                ),
                BrokerResearchObservation(
                    claim=BrokerClaim(
                        claim_id="B:SANIL:MIRAE:2Q26_PRIMARY_LEADS",
                        source_id="KR_MIRAE_INDUSTRY_RESEARCH",
                        broker_family="MiraeAssetSecurities",
                        report_type=BrokerReportType.EARNINGS_REVIEW,
                        field_class=BrokerFieldClass.UNDERLYING_DATA_REFERENCE,
                        industry_node="power_transformers",
                        statement=(
                            "Mirae identifies order/backlog, specialty-transformer mix "
                            "and capacity utilization as key Sanil operating signals; "
                            "the runtime must verify them in company primary sources."
                        ),
                        target_company_specific=True,
                        underlying_data_families=("company_ir", "company_filing"),
                        report_date="2026-08-07",
                    ),
                    segment_id=SEGMENT_ID,
                    source_ref=_MIRAE_2Q26_REPORT_URL,
                    verification_metrics=("orders", "backlog", "mix", "utilization"),
                    verification_requests=(
                        "verify orders, backlog, mix and utilization in official filing/IR",
                    ),
                    primary_source_hints=("2025 annual report", "2Q26 company IR"),
                ),
                BrokerResearchObservation(
                    claim=BrokerClaim(
                        claim_id="B:SANIL:MIRAE:UHV_PRIMARY_LEADS",
                        source_id="KR_MIRAE_INDUSTRY_RESEARCH",
                        broker_family="MiraeAssetSecurities",
                        report_type=BrokerReportType.COMPANY_UPDATE,
                        field_class=BrokerFieldClass.UNDERLYING_DATA_REFERENCE,
                        industry_node="power_transformers",
                        statement=(
                            "Mirae flags a separate UHV expansion path; exact future "
                            "capacity and timing are not accepted until company primary "
                            "evidence establishes land control, committed spend and ramp boundaries."
                        ),
                        target_company_specific=True,
                        underlying_data_families=("company_filing",),
                        report_date="2026-08-07",
                    ),
                    segment_id=SEGMENT_ID,
                    source_ref=_MIRAE_2Q26_REPORT_URL,
                    verification_metrics=(
                        "expansion_land_control",
                        "expansion_site_area",
                        "expansion_capex_committed",
                        "expansion_ramp_date",
                    ),
                    verification_requests=(
                        "verify UHV land control, disclosed consideration and ramp boundary in company filing",
                    ),
                    primary_source_hints=("company property-acquisition filing",),
                ),
                BrokerResearchObservation(
                    claim=BrokerClaim(
                        claim_id="B:SANIL:IBK:2Q26_PRIMARY_LEADS",
                        source_id="KR_IBK_RESEARCH",
                        broker_family="IBKSecurities",
                        report_type=BrokerReportType.EARNINGS_REVIEW,
                        field_class=BrokerFieldClass.UNDERLYING_DATA_REFERENCE,
                        industry_node="power_transformers",
                        statement=(
                            "IBK highlights record quarterly revenue, specialty-transformer "
                            "order/backlog mix and follow-on data-center orders; all company "
                            "facts are verification leads until matched to official filing/IR."
                        ),
                        target_company_specific=True,
                        underlying_data_families=("company_ir", "company_filing"),
                        report_date="2026-08-10",
                    ),
                    segment_id=SEGMENT_ID,
                    source_ref=_IBK_2Q26_REPORT_URL,
                    verification_metrics=(
                        "revenue_h1_2026",
                        "operating_profit_h1_2026",
                        "orders",
                        "backlog",
                        "mix",
                    ),
                    verification_requests=(
                        "verify H1 revenue/profit, orders, backlog and specialty mix in company primary sources",
                    ),
                    primary_source_hints=("2Q26 company IR", "2025 annual report"),
                ),
                BrokerResearchObservation(
                    claim=BrokerClaim(
                        claim_id="B:SANIL:SHINHAN:ORDER_PRIMARY_LEADS",
                        source_id="KR_SHINHAN_RESEARCH",
                        broker_family="ShinhanSecurities",
                        report_type=BrokerReportType.COMPANY_UPDATE,
                        field_class=BrokerFieldClass.UNDERLYING_DATA_REFERENCE,
                        industry_node="power_transformers",
                        statement=(
                            "Shinhan cites strong results and order acceleration as the "
                            "operating catalyst; orders and backlog must be re-verified "
                            "from company primary sources before intrinsic use."
                        ),
                        target_company_specific=True,
                        underlying_data_families=("company_ir", "company_filing"),
                        report_date="2026-08-11",
                    ),
                    segment_id=SEGMENT_ID,
                    source_ref=_SHINHAN_2Q26_REPORT_URL,
                    verification_metrics=("orders", "backlog"),
                    verification_requests=(
                        "verify order acceleration and backlog in company filing/IR",
                    ),
                    primary_source_hints=("2Q26 company IR", "2025 annual report"),
                ),
                BrokerResearchObservation(
                    claim=BrokerClaim(
                        claim_id="B:SANIL:MIRAE:FORWARD_FORECAST",
                        source_id="KR_MIRAE_INDUSTRY_RESEARCH",
                        broker_family="MiraeAssetSecurities",
                        report_type=BrokerReportType.EARNINGS_REVIEW,
                        field_class=BrokerFieldClass.TARGET_COMPANY_FORECAST,
                        industry_node="power_transformers",
                        statement="Mirae publishes a target-company forward earnings path.",
                        target_company_specific=True,
                        report_date="2026-08-07",
                    ),
                    segment_id=SEGMENT_ID,
                    source_ref=_MIRAE_2Q26_REPORT_URL,
                ),
                BrokerResearchObservation(
                    claim=BrokerClaim(
                        claim_id="B:SANIL:MIRAE:TARGET_PRICE",
                        source_id="KR_MIRAE_INDUSTRY_RESEARCH",
                        broker_family="MiraeAssetSecurities",
                        report_type=BrokerReportType.VALUATION_CHANGE,
                        field_class=BrokerFieldClass.TARGET_PRICE,
                        industry_node="power_transformers",
                        statement="Mirae target price is KRW 250,000.",
                        target_company_specific=True,
                        report_date="2026-08-07",
                    ),
                    segment_id=SEGMENT_ID,
                    source_ref=_MIRAE_2Q26_REPORT_URL,
                ),
                BrokerResearchObservation(
                    claim=BrokerClaim(
                        claim_id="B:SANIL:IBK:TARGET_PRICE",
                        source_id="KR_IBK_RESEARCH",
                        broker_family="IBKSecurities",
                        report_type=BrokerReportType.VALUATION_CHANGE,
                        field_class=BrokerFieldClass.TARGET_PRICE,
                        industry_node="power_transformers",
                        statement="IBK target price is KRW 220,000.",
                        target_company_specific=True,
                        report_date="2026-08-10",
                    ),
                    segment_id=SEGMENT_ID,
                    source_ref=_IBK_2Q26_REPORT_URL,
                ),
                BrokerResearchObservation(
                    claim=BrokerClaim(
                        claim_id="B:SANIL:SHINHAN:TARGET_PRICE",
                        source_id="KR_SHINHAN_RESEARCH",
                        broker_family="ShinhanSecurities",
                        report_type=BrokerReportType.VALUATION_CHANGE,
                        field_class=BrokerFieldClass.TARGET_PRICE,
                        industry_node="power_transformers",
                        statement="Shinhan target price is KRW 310,000.",
                        target_company_specific=True,
                        report_date="2026-08-11",
                    ),
                    segment_id=SEGMENT_ID,
                    source_ref=_SHINHAN_2Q26_REPORT_URL,
                ),
            ),
            source_refs=(
                _MIRAE_POWER_SOLUTION_REPORT_URL,
                _MIRAE_2Q26_REPORT_URL,
                _IBK_2Q26_REPORT_URL,
                _SHINHAN_2Q26_REPORT_URL,
            ),
        )

    return load


def _street_reports() -> tuple[StreetResearchReport, ...]:
    return (
        StreetResearchReport(
            broker="Mirae Asset Securities",
            analyst="Kim Tae-hyung",
            published_date="2026-08-07",
            target_price=250000.0,
            target_price_currency="KRW",
            valuation_method="PER-based target framework",
            base_year="2028",
            estimates=(),
            source_ref=_MIRAE_2Q26_REPORT_URL,
        ),
        StreetResearchReport(
            broker="IBK Securities",
            analyst="Kim Tae-hyun",
            published_date="2026-08-10",
            target_price=220000.0,
            target_price_currency="KRW",
            valuation_method="broker target-price framework",
            base_year="2027",
            estimates=(),
            source_ref=_IBK_2Q26_REPORT_URL,
        ),
        StreetResearchReport(
            broker="Shinhan Securities",
            analyst="Choi Seung-hwan / Lee Byung-hwa",
            published_date="2026-08-11",
            target_price=310000.0,
            target_price_currency="KRW",
            valuation_method="2027E PER 35x",
            base_year="2027",
            estimates=(),
            source_ref=_SHINHAN_2Q26_REPORT_URL,
        ),
    )


'''
    text = text[:start] + replacement + text[end:]
    SANIL.write_text(text, encoding="utf-8")


def patch_registry() -> None:
    text = REGISTRY.read_text(encoding="utf-8")
    if "- id: KR_IBK_RESEARCH\n" in text:
        return
    marker = "- id: KR_SHINHAN_RESEARCH\n"
    addition = '''- id: KR_IBK_RESEARCH
  broker_family: IBKSecurities
  region: KR
  access_mode: public_summary
  public_raw_storage_allowed: false
  url: https://www.ibks.com/investment/research/businessAnalysis_list.do
  strengths:
  - company_updates
  - earnings_review
  - industry_research
  notes: Use public report metadata/derived claims only; verify target-company operating facts in primary filings/IR and quarantine target prices/forecasts until Freeze.
'''
    text = replace_once(text, marker, addition + marker, "IBK registry insertion")
    REGISTRY.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    text = TEST.read_text(encoding="utf-8")
    old = '''    assert tuple(
        item.claim_id for item in broker_result.primary_verification_claims
    ) == (
        "B:SANIL:MIRAE:2Q26_PRIMARY_LEADS",
        "B:SANIL:MIRAE:UHV_PRIMARY_LEADS",
    )
    assert tuple(item.claim_id for item in broker_result.quarantined_claims) == (
        "B:SANIL:MIRAE:FORWARD_FORECAST",
        "B:SANIL:MIRAE:TARGET_PRICE",
    )
    assert not any(
        "securities.miraeasset.com" in item.source_ref
        for item in ledger.active()
    )
'''
    new = '''    primary_claim_ids = tuple(
        item.claim_id for item in broker_result.primary_verification_claims
    )
    assert primary_claim_ids == (
        "B:SANIL:MIRAE:2Q26_PRIMARY_LEADS",
        "B:SANIL:MIRAE:UHV_PRIMARY_LEADS",
        "B:SANIL:IBK:2Q26_PRIMARY_LEADS",
        "B:SANIL:SHINHAN:ORDER_PRIMARY_LEADS",
    )
    assert tuple(item.claim_id for item in broker_result.context_claims) == (
        "B:SANIL:MIRAE:POWER_SOLUTION_CONTEXT",
    )
    assert tuple(item.claim_id for item in broker_result.quarantined_claims) == (
        "B:SANIL:MIRAE:FORWARD_FORECAST",
        "B:SANIL:MIRAE:TARGET_PRICE",
        "B:SANIL:IBK:TARGET_PRICE",
        "B:SANIL:SHINHAN:TARGET_PRICE",
    )
    primary_broker_families = {
        item.broker_family for item in broker_result.primary_verification_claims
    }
    assert primary_broker_families == {
        "MiraeAssetSecurities",
        "IBKSecurities",
        "ShinhanSecurities",
    }
    broker_domains = (
        "securities.miraeasset.com",
        "yna.co.kr",
        "ibks.com",
        "shinhansec.com",
    )
    assert not any(
        any(domain in item.source_ref for domain in broker_domains)
        for item in ledger.active()
    )
'''
    text = replace_once(text, old, new, "Sanil broker assertions")
    text = replace_once(
        text,
        '    assert result.data["street_comparison"].consensus.report_count == 2\n',
        '    assert result.data["street_comparison"].consensus.report_count == 3\n',
        "Street report count",
    )
    TEST.write_text(text, encoding="utf-8")


def main() -> int:
    patch_sanil()
    patch_registry()
    patch_tests()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
