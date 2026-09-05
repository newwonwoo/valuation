"""The wheel validator stays a repository-only bootstrap, and its pin stays true.

The validator runs against an *installed* wheel from outside the repository, so
it may not reach back into the source tree — no PYTHONPATH, and every registry
read through the packaged ``valuation_engine._registry_data``.

Its ``EXPECTED_METHOD_COUNT`` is a drift alarm: a wheel that ships incomplete
registry data still imports and still answers, just with fewer archetype/method
bindings than the repository has. Asserting the literal number here would only
have said that some pin exists, and would have to be edited in lockstep with
the script every time a reviewed contract is added. Reading the pin and
comparing it against the registry the repository actually loads says the thing
worth saying: the alarm is set to the right value.
"""

from pathlib import Path
import re

from valuation_engine.method_capabilities import (
    load_default_method_capability_registry,
)

VALIDATOR = (
    Path(__file__).resolve().parents[1] / "scripts" / "validate_installed_wheel.py"
)


def test_installed_wheel_validator_is_repository_only_bootstrap():
    text = VALIDATOR.read_text(encoding="utf-8")
    assert "PYTHONPATH" not in text
    assert "valuation_engine._registry_data" in text
    assert "load_default_unit_contract_registry" in text


def test_the_wheel_method_pin_matches_the_registry_it_guards():
    text = VALIDATOR.read_text(encoding="utf-8")
    match = re.search(r"^EXPECTED_METHOD_COUNT\s*=\s*(\d+)$", text, re.M)
    assert match is not None, "the wheel validator must pin a method count"
    expected = load_default_method_capability_registry().coverage_summary().total
    assert int(match.group(1)) == expected
