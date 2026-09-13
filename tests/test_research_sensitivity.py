from decimal import Decimal as D
import pytest
from valuation_engine.research_sensitivity import DriverRange, assess_research_sensitivity, rank_research_priorities


def test_unknown_is_not_zero_and_negative_values_are_measured():
    rows = assess_research_sensitivity((DriverRange('missing', None, None, None), DriverRange('x', D(-1), D(0), D(1))), lambda v: v['x'])
    assert rows[0].absolute_swing is None
    assert rows[1].absolute_swing == 2
    assert rows[1].pct_swing is None
    assert rank_research_priorities(rows)[0].driver_ids == ('missing',)


def test_pair_corners_detect_cancellation_hidden_by_joint_endpoints():
    rows = assess_research_sensitivity(tuple(DriverRange(k, D(-1), D(0), D(1)) for k in ('a', 'b')), lambda v: v.get('a', D(0)) - v.get('b', D(0)), pairs=(('a', 'b'),))
    assert rows[-1].low_value == rows[-1].high_value == 0
    assert rows[-1].absolute_swing == 4


def test_mandatory_precedes_unknown_and_duplicates_rejected():
    rows = assess_research_sensitivity((DriverRange('missing', None, None, None), DriverRange('x', D(0), D(1), D(2), mandatory=True)), lambda v: v['x'])
    assert rank_research_priorities(rows)[0].driver_ids == ('x',)
    with pytest.raises(ValueError, match='duplicate'):
        assess_research_sensitivity((DriverRange('x', None, None, None),)*2, lambda v: D(1))
    with pytest.raises(ValueError, match='finite'):
        DriverRange('x', D('NaN'), None, None)


def test_observed_equal_bounds_have_zero_swing():
    row, = assess_research_sensitivity((DriverRange('x', D(2), D(2), D(2)),), lambda v: v['x'] * 3)
    assert row.status == 'MEASURED'
    assert row.absolute_swing == row.pct_swing == 0


def test_every_experiment_uses_same_complete_known_baseline():
    rows = assess_research_sensitivity((DriverRange('a', D(1), D(2), D(3)), DriverRange('b', D(10), D(20), D(30))), lambda v: v['a'] + v['b'])
    assert [row.base_value for row in rows] == [D(22), D(22)]
    assert rows[0].low_value == 21 and rows[1].low_value == 12


def test_infeasible_experiment_preserves_fault_and_does_not_abort_other_driver():
    def evaluator(v):
        if v['a'] == 0:
            raise ValueError('capacity must cover signed minimum')
        return v['a'] + v['b']
    rows = assess_research_sensitivity((DriverRange('a', D(0), D(1), D(2)), DriverRange('b', D(2), D(3), D(4))), evaluator)
    assert rows[0].status == 'NOT_MEASURABLE'
    assert rows[0].absolute_swing is None
    assert 'ValueError: capacity must cover signed minimum' in rows[0].rationale
    assert rows[1].status == 'MEASURED'
    assert rank_research_priorities(rows)[0] == rows[0]
