from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests


OUT = Path("artifacts/kodex_semiconductor_cohort")
RAW = OUT / "raw"
RAW.mkdir(parents=True, exist_ok=True)
BASE_URL = "https://www.samsungfund.com"
PAGE_URL = f"{BASE_URL}/etf/product/view.do?id=2ETF07"
API_URL = f"{BASE_URL}/api/v1/kodex/product-pdf/2ETF07.do"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
    "Referer": PAGE_URL,
    "X-Requested-With": "XMLHttpRequest",
}


def quarter_end(year: int, quarter: int) -> date:
    month = quarter * 3
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def periods() -> list[tuple[str, date]]:
    result: list[tuple[str, date]] = []
    for year in range(2015, 2025):
        for quarter in range(1, 5):
            if year == 2024 and quarter > 2:
                break
            result.append((f"{year}Q{quarter}", quarter_end(year, quarter)))
    assert len(result) == 38
    return result


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_first(mapping: dict[str, object], keys: tuple[str, ...]) -> object | None:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def normalize_code(value: object | None) -> str:
    raw = "" if value is None else str(value).strip()
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", raw)
    if match:
        return match.group(1)
    return raw


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


def request_snapshot(
    session: requests.Session, period_id: str, target: date
) -> tuple[date, dict[str, object], list[dict[str, object]]]:
    attempts: list[dict[str, object]] = []
    for offset in range(0, 15):
        candidate = target - timedelta(days=offset)
        formatted = candidate.strftime("%Y.%m.%d")
        response = session.get(
            API_URL,
            params={"gijunYMD": formatted},
            headers=HEADERS,
            timeout=60,
        )
        entry: dict[str, object] = {
            "period_id": period_id,
            "target_date": target.isoformat(),
            "candidate_date": candidate.isoformat(),
            "http_status": response.status_code,
            "content_type": response.headers.get("content-type"),
        }
        try:
            payload = response.json()
            entry["top_level_keys"] = sorted(payload.keys()) if isinstance(payload, dict) else []
        except Exception:
            entry["body_prefix"] = response.text[:500]
            attempts.append(entry)
            time.sleep(0.25)
            continue
        pdf = payload.get("pdf") if isinstance(payload, dict) else None
        rows = pdf.get("list") if isinstance(pdf, dict) else None
        entry["pdf_keys"] = sorted(pdf.keys()) if isinstance(pdf, dict) else []
        entry["row_count"] = len(rows) if isinstance(rows, list) else None
        entry["reported_date"] = pdf.get("gijunYMD") if isinstance(pdf, dict) else None
        attempts.append(entry)
        if response.ok and isinstance(rows, list) and rows:
            return candidate, payload, attempts
        time.sleep(0.25)
    raise RuntimeError(f"No KODEX PDF snapshot resolved for {period_id}: {attempts}")


def main() -> int:
    session = requests.Session()
    page = session.get(PAGE_URL, headers=HEADERS, timeout=60)
    page.raise_for_status()

    normalized_rows: list[dict[str, object]] = []
    attempts_all: list[dict[str, object]] = []
    snapshots: list[dict[str, object]] = []
    raw_item_keys: set[str] = set()

    for period_id, target in periods():
        resolved_candidate, payload, attempts = request_snapshot(session, period_id, target)
        attempts_all.extend(attempts)
        raw_path = RAW / f"{period_id}.json"
        raw_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        pdf = payload["pdf"]
        rows = pdf["list"]
        reported_raw = str(pdf.get("gijunYMD") or "")
        reported_date = reported_raw.replace(".", "-").replace("/", "-")
        period_rows: list[dict[str, object]] = []
        for position, raw in enumerate(rows, 1):
            if not isinstance(raw, dict):
                continue
            raw_item_keys.update(raw.keys())
            code = normalize_code(
                get_first(raw, ("isuSrtCd", "isuCd", "stockCode", "code", "ticker", "itemCode", "secCd"))
            )
            name = str(
                get_first(raw, ("isuNm", "isuKorNm", "itemName", "name", "stockName", "secNm"))
                or ""
            ).strip()
            cash = is_cash(code, name)
            item = {
                "period_id": period_id,
                "target_date": target.isoformat(),
                "request_date": resolved_candidate.isoformat(),
                "reported_date": reported_date,
                "position": position,
                "stock_code": code,
                "company_name": name,
                "is_cash": cash,
                "ratio": get_first(raw, ("ratio", "weight", "wei", "weightRate")),
                "quantity": get_first(raw, ("qty", "quantity", "holdQty", "number")),
                "valuation_amount": get_first(raw, ("evalAmt", "valuationAmount", "amt", "amount")),
                "raw": json.dumps(raw, ensure_ascii=False, sort_keys=True),
            }
            normalized_rows.append(item)
            period_rows.append(item)
        security_count = sum(not bool(row["is_cash"]) for row in period_rows)
        snapshots.append(
            {
                "period_id": period_id,
                "target_date": target.isoformat(),
                "request_date": resolved_candidate.isoformat(),
                "reported_date": reported_date,
                "raw_row_count": len(period_rows),
                "security_count": security_count,
                "raw_sha256": sha256(raw_path),
            }
        )
        time.sleep(0.3)

    frame = pd.DataFrame(normalized_rows)
    all_path = OUT / "kodex_pdf_rows_2015Q1_2024Q2.csv"
    frame.to_csv(all_path, index=False)
    securities = frame.loc[~frame["is_cash"].astype(bool)].copy()
    securities_path = OUT / "kodex_semiconductor_constituents_2015Q1_2024Q2.csv"
    securities.to_csv(securities_path, index=False)
    union = (
        securities[["stock_code", "company_name"]]
        .drop_duplicates()
        .sort_values(["stock_code", "company_name"])
    )
    union_path = OUT / "kodex_semiconductor_union.csv"
    union.to_csv(union_path, index=False)
    attempts_path = OUT / "request_attempts.csv"
    pd.DataFrame(attempts_all).to_csv(attempts_path, index=False)

    summary = {
        "source": {
            "page_url": PAGE_URL,
            "api_url": API_URL,
            "provider": "Samsung Asset Management KODEX official PDF endpoint",
            "product_id": "2ETF07",
            "tracking_index": "KRX Semiconductor",
        },
        "period_count": len(snapshots),
        "raw_rows": int(len(frame)),
        "security_membership_rows": int(len(securities)),
        "unique_stock_codes": int(securities["stock_code"].nunique()),
        "unique_code_name_pairs": int(len(union)),
        "raw_item_keys": sorted(raw_item_keys),
        "snapshot_security_counts": {
            snapshot["period_id"]: snapshot["security_count"] for snapshot in snapshots
        },
        "snapshots": snapshots,
    }
    summary_path = OUT / "cohort_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "summary_sha256": sha256(summary_path),
        "all_rows_sha256": sha256(all_path),
        "constituents_sha256": sha256(securities_path),
        "union_sha256": sha256(union_path),
        "attempts_sha256": sha256(attempts_path),
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "snapshots"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
