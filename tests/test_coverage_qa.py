from valuation_engine.coverage_qa import CoverageEvidence, score_coverage


def test_single_family_is_flagged():
    score = score_coverage("reit", [CoverageEvidence("reit", "NAREIT", "observed_state", 3, True, True)])
    assert "needs independent source family" in score.gaps


def test_mechanism_corroboration_counts_as_structure_support_without_fake_claims():
    score = score_coverage(
        "power.grid",
        [
            CoverageEvidence("power.grid", "IEA", "industry_structure", 2, True, True),
            CoverageEvidence("power.grid", "DOE", "mechanism_corroboration", 0, True, True),
        ],
    )
    assert score.independent_source_families == 2
    assert score.claim_count == 2
    assert "missing structure/mechanism source" not in score.gaps


def test_high_coverage_requires_multiple_dimensions():
    evidence = [
        CoverageEvidence("semi", "A", "observed_state", 2, True, True),
        CoverageEvidence("semi", "B", "industry_structure", 1, True, True),
        CoverageEvidence("semi", "C", "forward_hypothesis", 1, True, True),
        CoverageEvidence("semi", "D", "definition_standard", 1, True, True),
    ]
    score = score_coverage("semi", evidence)
    assert score.grade == "A"
    assert not score.gaps
