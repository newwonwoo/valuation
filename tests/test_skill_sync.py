from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_root_and_repo_scoped_skill_are_identical():
    assert (ROOT / "SKILL.md").read_text(encoding="utf-8") == (
        ROOT / ".agents" / "skills" / "valuation-analysis" / "SKILL.md"
    ).read_text(encoding="utf-8")
