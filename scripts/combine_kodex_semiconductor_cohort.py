from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pandas as pd


INPUT = Path("artifacts/kodex_semiconductor_downloads")
OUT = Path("artifacts/kodex_semiconductor_cohort")
OUT.mkdir(parents=True, exist_ok=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def first(raw: dict[str, object], *keys: str) -> object | None:
    for key in keys:
        value = raw.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def code_of(raw: dict[str, object]) -> str:
    value = first(raw, "itmNo", "isuSrtCd", "isuCd", "stockCode", "code", "ticker")
    text = "" if value is None else str(value).strip()
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
    return match.group(1) if match else text


def name_of(raw: dict[str, object]) -> str:
    return str(first(raw, "secNm", "isuNm", "isuKorNm", "itemName", "name", "stockName") or "").strip()


def is_cash(code: str, name: str) -> bool:
    lowered = name.lower().replace(" ", "")
    return (
        code.startswith("KRD")
        or code in {"", "0", "000000"}
        or "원화예금" in name
        or "현금" in name
        or "cash" in lowered
        or "예금" in name
    )


def main() -> int:
    raw_files = sorted(INPUT.rglob("raw/*.json"))
    if not raw_files:
        raise RuntimeError(f"no raw KODEX cohort files under {INPUT}")

    rows: list[dict[str, object]] = []
    snapshots: list[dict[str, object]] = []
    seen_periods: set[str] = set()
    for raw_path in raw_files:
        period_id = raw_path.stem
        if period_id in seen_periods:
            raise RuntimeError(f"duplicate period raw file: {period_id}")
        seen_periods.add(period_id)
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        pdf = payload.get("pdf") or {}
        items = pdf.get("list") or []
        reported_raw = str(pdf.get("gijunYMD") or "")
        reported_date = (
            f"{reported_raw[0:4]}-{reported_raw[4:6]}-{reported_raw[6:8]}"
            if re.fullmatch(r"\d{8}", reported_raw)
            else reported_raw.replace(".", "-").replace("/", "-")
        )
        security_count = 0
        for position, raw in enumerate(items, 1):
            if not isinstance(raw, dict):
                continue
            code = code_of(raw)
            name = name_of(raw)
            cash = is_cash(code, name)
            if not cash:
                security_count += 1
            rows.append(
                {
                    "period_id": period_id,
                    "reported_date": reported_date,
                    "position": position,
                    "stock_code": code,
                    "company_name": name,
                    "is_cash": cash,
                    "ratio": first(raw, "ratio", "weight", "wei", "weightRate"),
                    "quantity": first(raw, "applyQ", "qty", "quantity", "holdQty", "number"),
                    "valuation_amount": first(raw, "evalA", "evalAmt", "valuationAmount", "amt", "amount"),
                    "source_file": str(raw_path.relative_to(INPUT)),
                    "source_sha256": sha256(raw_path),
                    "raw": json.dumps(raw, ensure_ascii=False, sort_keys=True),
                }
            )
        snapshots.append(
            {
                "period_id": period_id,
                "reported_date": reported_date,
                "raw_row_count": len(items),
                "security_count": security_count,
                "raw_sha256": sha256(raw_path),
            }
        )

    expected_periods = [f"{year}Q{quarter}" for year in range(2015, 2025) for quarter in range(1, 5) if not (year == 2024 and quarter > 2)]
    missing = sorted(set(expected_periods) - seen_periods)
    extra = sorted(seen_periods - set(expected_periods))
    if missing or extra:
        raise RuntimeError(f"period coverage mismatch missing={missing} extra={extra}")

    frame = pd.DataFrame(rows).sort_values(["period_id", "position"])
    all_path = OUT / "kodex_pdf_rows_2015Q1_2024Q2.csv"
    frame.to_csv(all_path, index=False)
    securities = frame.loc[~frame["is_cash"].astype(bool)].copy()
    constituents_path = OUT / "kodex_semiconductor_constituents_2015Q1_2024Q2.csv"
    securities.to_csv(constituents_path, index=False)
    union = securities[["stock_code", "company_name"]].drop_duplicates().sort_values(["stock_code", "company_name"])
    union_path = OUT / "kodex_semiconductor_union.csv"
    union.to_csv(union_path, index=False)
    snapshots_frame = pd.DataFrame(snapshots).sort_values("period_id")
    snapshots_path = OUT / "snapshot_summary.csv"
    snapshots_frame.to_csv(snapshots_path, index=False)

    summary = {
        "source": {
            "provider": "Samsung Asset Management",
            "product": "KODEX Semiconductor 091160",
            "product_id": "2ETF07",
            "tracking_index": "KRX Semiconductor",
            "endpoint": "/api/v1/kodex/product-pdf/2ETF07.do?gijunYMD=YYYY.MM.DD",
        },
        "period_count": len(seen_periods),
        "raw_rows": int(len(frame)),
        "security_membership_rows": int(len(securities)),
        "unique_stock_codes": int(securities["stock_code"].nunique()),
        "unique_code_name_pairs": int(len(union)),
        "snapshot_security_counts": dict(zip(snapshots_frame["period_id"], snapshots_frame["security_count"])),
        "expected_prior_work_reference": {
            "as_of_dates": 38,
            "companies": 76,
            "eligible_cases": 901,
            "note": "Reference only. A mismatch means KODEX PDF is not identical to the earlier KRX point-in-time universe and must not silently replace it.",
        },
    }
    summary_path = OUT / "cohort_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "manifest.json").write_text(
        json.dumps(
            {
                "all_rows_sha256": sha256(all_path),
                "constituents_sha256": sha256(constituents_path),
                "union_sha256": sha256(union_path),
                "snapshots_sha256": sha256(snapshots_path),
                "summary_sha256": sha256(summary_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
