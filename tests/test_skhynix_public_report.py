from pathlib import Path

from valuation_engine.skhynix_brokerage_html import (
    render_skhynix_brokerage_html,
    validate_skhynix_brokerage_html,
)
from valuation_engine.skhynix_continuous_live_primary import (
    render_calibrated_probability_summary,
    run_skhynix_live_primary,
)
from valuation_engine.skhynix_public_report import (
    PUBLIC_REQUIRED_HEADINGS,
    render_skhynix_public_report,
    render_skhynix_public_visual,
)
from valuation_engine.strict_live_runtime import require_canonical_live_result


def test_skhynix_public_report_uses_korean_standard_form(tmp_path: Path):
    authority = run_skhynix_live_primary(tmp_path)
    result = require_canonical_live_result(authority)
    probability_snapshot = result.data["continuous_probability_calibration_snapshot"]
    report = render_calibrated_probability_summary(
        str(result.data["final_report"]),
        probability_snapshot,
        result.data.get("probability_distribution_status"),
    )
    report = render_skhynix_public_report(report)

    assert report.startswith("# SK하이닉스(000660) 투자보고서")
    positions = [report.index(heading) for heading in PUBLIC_REQUIRED_HEADINGS]
    assert positions == sorted(positions)
    assert "하방 15.7% · 기준 43.9% · 상방 40.4%" in report
    assert "확률가중 기대값:** 주당 3,726,580원" in report
    assert "사업모델과 강점" in report
    assert "고대역폭메모리" in report
    assert "현금흐름할인법" in report
    assert "기업잉여현금흐름" in report
    assert "투하자본이익률" in report
    assert "프리즘 기준 내재가치" in report
    assert "SK hynix Inc." not in report
    assert "post-freeze" not in report
    assert "frozen LIVE" not in report
    assert "commodity_price_taker" not in report
    assert "E:SKHYNIX:" not in report
    assert "COMPANY_RESOLUTION" not in report
    assert "github.com/newwonwoo/valuation" not in report
    assert "초고압" not in report

    run_dir = Path(str(result.data["saved_run_dir"]))
    visual_names = tuple(str(name) for name in result.data["saved_report_visuals"])
    assert len(visual_names) == 2
    cards = tuple(
        render_skhynix_public_visual(
            (run_dir / name).read_text(encoding="utf-8"),
            card_number=index,
        )
        for index, name in enumerate(visual_names, start=1)
    )
    for card in cards:
        assert "SK하이닉스" in card
        assert "프리즘 최종보고서" in card
        assert "SK hynix Inc." not in card
        assert "Down" not in card
        assert "Core" not in card
        assert "Bull" not in card
        assert "commodity_price_taker" not in card
        assert "초고압" not in card
    assert "4세대 고대역폭메모리 양산 출하" in cards[0]
    assert "시나리오 확률" in cards[1]
    assert "15.7%" in cards[1]
    assert "43.9%" in cards[1]
    assert "40.4%" in cards[1]

    html = render_skhynix_brokerage_html(
        report,
        summary_filename=visual_names[0],
        assumptions_filename=visual_names[1],
        as_of="2026-08-28",
        markdown_filename="SKHYNIX_000660_LIVE_PRIMARY_REPORT.md",
    )
    validate_skhynix_brokerage_html(html)
    assert "*{box-sizing:border-box}" in html
    assert "body{margin:0;font-size:13pt" in html
    assert (
        "@media(max-width:760px){.report{padding:0}.page{width:100%;min-height:0;"
        "margin:0 0 12px;padding:24px 20px;box-shadow:none}"
    ) in html
