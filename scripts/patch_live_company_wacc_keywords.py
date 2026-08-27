from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "src" / "valuation_engine" / "required_company_live.py"


def replace_once(old: str, new: str) -> None:
    text = PATH.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"WACC observation patch target not found: {old[:80]!r}")
    PATH.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    for variable, methodology in (
        ("risk_free", "explicit acceptance-underwriting risk-free observation"),
        ("erp", "explicit acceptance-underwriting market ERP"),
        ("debt_cost", "explicit acceptance-underwriting marginal debt cost"),
    ):
        replace_once(
            f'''RateObservation(
                {variable},
                currency,
                spec.as_of,
                source,
                "{methodology}",
            )''',
            f'''RateObservation(
                value={variable},
                currency=currency,
                as_of=spec.as_of,
                source_ref=source,
                methodology="{methodology}",
            )''',
        )
    replace_once(
        '''            target_capital_structure=_risk_structure(spec),
        )''',
        '''            target_capital_structure=_risk_structure(spec),
            funding_credit_evidence_ids=(
                _evidence_id(spec, "normalized_ebitda"),
            ),
        )''',
    )
    print("acceptance WACC observations and Evidence trace normalized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
