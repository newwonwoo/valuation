from pathlib import Path
import re

seg = Path('src/valuation_engine/segment_note.py')
text = seg.read_text(encoding='utf-8')
start = text.index('def _segment_columns(')
end = text.index('\ndef _metric_row(', start)
replacement = '''def _segment_columns(grid: list[list[str]]) -> tuple[int, tuple[tuple[int, str], ...], int] | None:
    """Locate reportable-segment columns in one- or two-tier IFRS 8 headers."""
    for row_index, row in enumerate(grid):
        named: list[tuple[int, str]] = []
        for column_index, cell in enumerate(row):
            squeezed = _squeeze(cell)
            if squeezed in _SEGMENT_TOTAL_CELLS and named:
                if len(named) < 2:
                    break
                return row_index, tuple(named), column_index
            if squeezed in _STRUCTURAL_CELLS or not squeezed:
                continue
            name = " ".join(str(cell).split()).strip()
            if _SEGMENT_NAME.match(_squeeze(name)) and (
                not named or name != named[-1][1]
            ):
                named.append((column_index, name))

    # Some statutory notes group columns under 보고부문/기타부문 and put the
    # actual reportable names one row lower. The economic names may not end in
    # '부문', so locate the grouped columns first, then read the next header row.
    for group_row_index, row in enumerate(grid):
        total_columns = tuple(
            column_index
            for column_index, cell in enumerate(row)
            if _squeeze(cell) in _SEGMENT_TOTAL_CELLS
        )
        for total_column in total_columns:
            if total_column < 3:
                continue
            group_columns = tuple(
                column_index
                for column_index in range(1, total_column)
                if (
                    _squeeze(row[column_index]) == "보고부문"
                    or (
                        _SEGMENT_NAME.match(_squeeze(row[column_index]))
                        and _squeeze(row[column_index]) not in _SEGMENT_TOTAL_CELLS
                    )
                )
            )
            if len(group_columns) < 2:
                continue
            for name_row_index in range(
                group_row_index + 1, min(len(grid), group_row_index + 4)
            ):
                name_row = grid[name_row_index]
                named: list[tuple[int, str]] = []
                valid = True
                for column_index in group_columns:
                    if column_index >= len(name_row):
                        valid = False
                        break
                    candidate = " ".join(str(name_row[column_index]).split()).strip()
                    squeezed = _squeeze(candidate)
                    if (
                        not candidate
                        or squeezed in _STRUCTURAL_CELLS
                        or _amount(candidate) is not None
                    ):
                        valid = False
                        break
                    named.append((column_index, candidate))
                if not valid:
                    continue
                canonical = tuple(
                    re.sub(r"[\\s/·&-]+", "", name).casefold()
                    for _, name in named
                )
                if len(named) >= 2 and len(set(canonical)) == len(named):
                    return name_row_index, tuple(named), total_column
    return None
'''
seg.write_text(text[:start] + replacement + text[end:], encoding='utf-8')

cfg = Path('config/kr_industry_classification_map.yaml')
ctext = cfg.read_text(encoding='utf-8')
anchor = '  - ksic_prefix: "24"\n'
if 'ksic_prefix: "46"' not in ctext:
    addition = '''  - ksic_prefix: "46"\n    label: 도매 및 상품 중개업\n    sector_adapter: trade.commodity\n    archetypes: [process_spread]\n    structure:\n      revenue_recognition: delivery_or_resale\n      price_formation: commodity resale spread over procurement and logistics\n      asset_ownership: working-capital-led trading platform\n      capital_intensity: low-medium\n      regulation_intensity: low\n      customer_structure: industrial buyers and affiliated producers\n      reinvestment_model: inventory and receivables working capital\n      cashflow_duration: commodity-and-working-capital cycle\n  - ksic_prefix: "38"\n    label: 폐기물 수집·처리 및 원료 재생업\n    sector_adapter: recycling.materials\n    archetypes: [process_spread]\n    structure:\n      revenue_recognition: processed_material_delivery\n      price_formation: recovered-metal benchmark less feedstock and processing cost\n      asset_ownership: owned processing and recovery assets\n      capital_intensity: medium-high\n      regulation_intensity: high\n      customer_structure: waste suppliers and commodity or material buyers\n      reinvestment_model: recovery capacity and environmental compliance capex\n      cashflow_duration: commodity-and-feedstock cycle\n'''
    if anchor not in ctext:
        raise SystemExit('classification insertion anchor not found')
    cfg.write_text(ctext.replace(anchor, addition + anchor, 1), encoding='utf-8')

tests = Path('tests/test_segment_note.py')
ttext = tests.read_text(encoding='utf-8')
if 'test_two_tier_reportable_segment_header_is_reconciled' not in ttext:
    ttext += '''\n\ndef test_two_tier_reportable_segment_header_is_reconciled():\n    text = """\n    <table>\n      <tr><td></td><td>부문</td><td>부문</td><td>부문</td><td>부문 합계</td></tr>\n      <tr><td></td><td>보고부문</td><td>보고부문</td><td>기타부문</td><td>부문 합계</td></tr>\n      <tr><td></td><td>비철금속 제조 및 판매</td><td>비철금속 수출입</td><td>기타부문</td><td>부문 합계</td></tr>\n      <tr><td>매출액</td><td>9693315659</td><td>3182281396</td><td>413925299</td><td>13289522354</td></tr>\n      <tr><td>영업이익</td><td>1241489905</td><td>54979012</td><td>(13599031)</td><td>1282869886</td></tr>\n    </table>\n    """\n    disclosure = parse_operating_segment_note(text)\n    assert disclosure.segment_names == (\n        "비철금속 제조 및 판매",\n        "비철금속 수출입",\n        "기타부문",\n    )\n    assert disclosure.total_revenue == Decimal("13289522354")\n    assert disclosure.total_operating_income == Decimal("1282869886")\n    assert disclosure.entries[2].operating_income == Decimal("-13599031")\n'''
    tests.write_text(ttext, encoding='utf-8')
