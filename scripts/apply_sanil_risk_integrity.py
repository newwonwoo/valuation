from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "src" / "valuation_engine" / "sanil_live_primary.py"


def replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"expected one replacement, found {count}: {old[:100]!r}"
        )
    return text.replace(old, new, 1)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")

    text = replace_once(
        text,
        'source_grade=("A" if source_layer is not EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN else "B"),',
        '''source_grade=(
            "A"
            if source_layer is EvidenceSourceLayer.REALIZED_OR_FILING
            else (
                "B"
                if source_layer is EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN
                else "C"
            )
        ),''',
    )

    start = text.index("def _underwriting_records(")
    end = text.index("\ndef _all_records(", start)
    underwriting = text[start:end]
    underwriting = underwriting.replace(
        "EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN",
        "EvidenceSourceLayer.EXTERNAL_REFERENCE",
    )
    text = text[:start] + underwriting + text[end:]

    text = replace_once(
        text,
        'source_ref=str(_source(snapshot, "risk_snapshot")["source_ref"]),\n                        beta_standard_error=float(row["beta_standard_error"]),\n                        estimation_method="frozen OLS-equivalent public-market snapshot",',
        '''source_ref=str(
                            row.get("source_ref")
                            or _source(snapshot, "risk_snapshot")["source_ref"]
                        ),
                        beta_standard_error=(
                            float(row["beta_standard_error"])
                            if row.get("beta_standard_error") is not None
                            else None
                        ),
                        estimation_method=str(
                            row.get(
                                "estimation_method",
                                "external provider Beta snapshot",
                            )
                        ),''',
    )

    TARGET.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
