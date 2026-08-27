from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "src" / "valuation_engine" / "dcf_evaluators.py"
FIELD = "    trace_assumption_keys: tuple[str, ...] = ()\n"
CAPEX_FIELD = "    additional_expansion_capex: tuple[tuple[str, int], ...] = ()\n"
VALIDATION = '''        for key in self.trace_assumption_keys:\n            _validate_relative_key(key, "trace assumption key")\n        if len(self.trace_assumption_keys) != len(set(self.trace_assumption_keys)):\n            raise ValueError("trace assumption keys must be unique")\n'''
CAPEX_VALIDATION = '''        _validate_capex_entries(\n            forecast_years=self.forecast_years,\n            primary_key=self.expansion_capex_key,\n            primary_year=self.expansion_capex_year,\n            additional=self.additional_expansion_capex,\n        )\n'''


def normalize(block: str, class_name: str) -> str:
    block = block.replace(FIELD, "")
    if block.count(CAPEX_FIELD) != 1:
        raise RuntimeError(f"{class_name}: expected one additional CAPEX field")
    block = block.replace(CAPEX_FIELD, CAPEX_FIELD + FIELD, 1)

    block = block.replace(VALIDATION, "")
    if block.count(CAPEX_VALIDATION) != 1:
        raise RuntimeError(f"{class_name}: expected one CAPEX validation block")
    block = block.replace(CAPEX_VALIDATION, CAPEX_VALIDATION + VALIDATION, 1)
    return block


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    registration_start = text.index("@dataclass(frozen=True)\nclass LiveDCFRegistration")
    evaluator_start = text.index(
        "@dataclass(frozen=True)\nclass ExplicitFCFFDCFEvaluator",
        registration_start,
    )
    evaluator_end = text.index("\n\nRegistryLoader", evaluator_start)

    prefix = text[:registration_start]
    registration = normalize(
        text[registration_start:evaluator_start],
        "LiveDCFRegistration",
    )
    evaluator = normalize(
        text[evaluator_start:evaluator_end],
        "ExplicitFCFFDCFEvaluator",
    )
    suffix = text[evaluator_end:]
    normalized = prefix + registration + evaluator + suffix

    if normalized.count(FIELD) != 2:
        raise RuntimeError("DCF trace field normalization did not produce exactly two fields")
    if normalized.count(VALIDATION) != 2:
        raise RuntimeError(
            "DCF trace validation normalization did not produce exactly two blocks"
        )
    PATH.write_text(normalized, encoding="utf-8")
    print("normalized DCF registration/evaluator trace fields")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
