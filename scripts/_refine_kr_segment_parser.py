from pathlib import Path

seg = Path('src/valuation_engine/segment_note.py')
text = seg.read_text(encoding='utf-8')
old = '''        revenue = sum((entry.revenue for entry in self.entries), Decimal(0))
        income = sum((entry.operating_income for entry in self.entries), Decimal(0))
        if revenue != self.total_revenue or income != self.total_operating_income:
            raise SegmentNoteError(
                "reportable segment values do not sum to the disclosed segment total"
            )
'''
new = '''        revenue = sum((entry.revenue for entry in self.entries), Decimal(0))
        income = sum((entry.operating_income for entry in self.entries), Decimal(0))
        # Statutory tables can round each displayed segment independently. A one-unit
        # residual in the table's own disclosed unit (for example KRW thousand) is
        # presentation rounding, not evidence that a segment is missing. Anything
        # larger remains fail-closed; the disclosed total stays authoritative.
        if (
            abs(revenue - self.total_revenue) > Decimal("1")
            or abs(income - self.total_operating_income) > Decimal("1")
        ):
            raise SegmentNoteError(
                "reportable segment values do not sum and do not reconcile to the disclosed segment total"
            )
'''
if old not in text:
    raise SystemExit('validation block not found')
text = text.replace(old, new, 1)
old2 = '''                if (
                    _squeeze(row[column_index]) == "보고부문"
                    or (
                        _SEGMENT_NAME.match(_squeeze(row[column_index]))
                        and _squeeze(row[column_index]) not in _SEGMENT_TOTAL_CELLS
                    )
                )
'''
new2 = '''                if (
                    _squeeze(row[column_index]) in {"보고부문", "부문"}
                    or (
                        _SEGMENT_NAME.match(_squeeze(row[column_index]))
                        and _squeeze(row[column_index]) not in _SEGMENT_TOTAL_CELLS
                    )
                )
'''
if old2 not in text:
    raise SystemExit('group column block not found')
seg.write_text(text.replace(old2, new2, 1), encoding='utf-8')

tests = Path('tests/test_segment_note.py')
t = tests.read_text(encoding='utf-8')
if 'test_generic_two_tier_segment_header_without_reportable_group_label' not in t:
    t += '''\n\ndef test_generic_two_tier_segment_header_without_reportable_group_label():\n    text = """\n    <table>\n      <tr><td></td><td>부문</td><td>부문</td><td>부문</td><td>부문 합계</td></tr>\n      <tr><td></td><td>제조 및 판매</td><td>상품 수출입</td><td>폐기물처리 및 기타사업</td><td>부문 합계</td></tr>\n      <tr><td>매출액</td><td>12504635145</td><td>4396467004</td><td>633818286</td><td>17534920435</td></tr>\n      <tr><td>영업이익(손실)</td><td>1209732583</td><td>71756426</td><td>(31778827)</td><td>1249710182</td></tr>\n    </table>\n    """\n    disclosure = parse_operating_segment_note(text)\n    assert disclosure.segment_names == (\n        "제조 및 판매",\n        "상품 수출입",\n        "폐기물처리 및 기타사업",\n    )\n    assert disclosure.total_revenue == Decimal("17534920435")\n    assert disclosure.total_operating_income == Decimal("1249710182")\n\n\ndef test_one_disclosed_unit_rounding_residual_is_tolerated_but_larger_is_not():\n    rounded = """\n    <table>\n      <tr><td></td><td>보고부문</td><td>보고부문</td><td>기타부문</td><td>부문 합계</td></tr>\n      <tr><td></td><td>제조 및 판매</td><td>상품 수출입</td><td>기타부문</td><td>부문 합계</td></tr>\n      <tr><td>매출액</td><td>9693315659</td><td>3182281396</td><td>413925299</td><td>13289522354</td></tr>\n      <tr><td>영업이익</td><td>1241489905</td><td>54979012</td><td>(13599032)</td><td>1282869886</td></tr>\n    </table>\n    """\n    disclosure = parse_operating_segment_note(rounded)\n    assert sum(x.operating_income for x in disclosure.entries) == Decimal("1282869885")\n    assert disclosure.total_operating_income == Decimal("1282869886")\n\n    bad = rounded.replace("(13599032)", "(13599034)")\n    with pytest.raises(SegmentNoteError, match="do not reconcile"):\n        parse_operating_segment_note(bad)\n'''
    tests.write_text(t, encoding='utf-8')
