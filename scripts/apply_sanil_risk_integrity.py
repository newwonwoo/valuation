from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SANIL = ROOT / "src" / "valuation_engine" / "sanil_live_primary.py"
RECORDS = ROOT / "src" / "valuation_engine" / "records.py"
COLLECTION = ROOT / "src" / "valuation_engine" / "evidence_collection.py"


def replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"expected one replacement, found {count}: {old[:100]!r}"
        )
    return text.replace(old, new, 1)


def main() -> int:
    records = RECORDS.read_text(encoding="utf-8")
    records = replace_once(
        records,
        '''    POLICY_PRIMARY_SOURCE = "policy_primary_source"
    EXTERNAL_REFERENCE = "external_reference"
''',
        '''    POLICY_PRIMARY_SOURCE = "policy_primary_source"
    AUTHORIZED_MARKET_DATA = "authorized_market_data"
    ANALYST_UNDERWRITING = "analyst_underwriting"
    EXTERNAL_REFERENCE = "external_reference"
''',
    )
    RECORDS.write_text(records, encoding="utf-8")

    collection = COLLECTION.read_text(encoding="utf-8")
    collection = replace_once(
        collection,
        '''                EvidenceSourceLayer.POLICY_PRIMARY_SOURCE,
            }:
''',
        '''                EvidenceSourceLayer.POLICY_PRIMARY_SOURCE,
                EvidenceSourceLayer.AUTHORIZED_MARKET_DATA,
                EvidenceSourceLayer.ANALYST_UNDERWRITING,
            }:
''',
    )
    COLLECTION.write_text(collection, encoding="utf-8")

    text = SANIL.read_text(encoding="utf-8")
    text = replace_once(
        text,
        'source_grade=("A" if source_layer is not EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN else "B"),',
        '''source_grade=(
            "A"
            if source_layer
            in {
                EvidenceSourceLayer.REALIZED_OR_FILING,
                EvidenceSourceLayer.POLICY_PRIMARY_SOURCE,
            }
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
        "EvidenceSourceLayer.ANALYST_UNDERWRITING",
    )
    underwriting = replace_once(
        underwriting,
        '''                source_key="risk_snapshot",
                source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
''',
        '''                source_key="risk_snapshot",
                source_layer=EvidenceSourceLayer.AUTHORIZED_MARKET_DATA,
''',
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

    SANIL.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
