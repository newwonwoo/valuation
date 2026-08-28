from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests


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


def all_periods() -> list[tuple[str, date]]:
    result: list[tuple[str, date]] = []
    for year in range(2015, 2025):
        for quarter in range(1, 5):
            if year == 2024 and quarter > 2:
                break
            result.append((f"{year}Q{quarter}", quarter_end(year, quarter)))
    assert len(result) == 38
    return result


def selected_periods() -> list[tuple[str, date]]:
    rows = all_periods()
    start = os.getenv("KODEX_PERIOD_START", rows[0][0])
    end = os.getenv("KODEX_PERIOD_END", rows[-1][0])
    by_id = {period_id: index for index, (period_id, _) in enumerate(rows)}
    if start not in by_id or end not in by_id or by_id[start] > by_id[end]:
        raise ValueError(f"invalid period range: {start}..{end}")
    return rows[by_id[start] : by_id[end] + 1]


def output_dir() -> Path:
    chunk = os.getenv("KODEX_CHUNK", "all")
    path = Path("artifacts/kodex_semiconductor_chunks") / chunk
    (path / "raw").mkdir(parents=True, exist_ok=True)
    return path


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
    return match.group(1) if match else raw


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


def refresh_session(session: requests.Session) -> None:
    response = session.get(PAGE_URL, headers=HEADERS, timeout=60)
    response.raise_for_status()


def request_snapshot(
    session: requests.Session, period_id: str, target: date
) -> tuple[date, dict[str, object], list[dict[str, object]]]:
    attempts: list[dict[str, object]] = []
    for offset in range(0, 15):
        candidate = target - timedelta(days=offset)
        formatted = candidate.strftime("%Y.%m.%d")
        for retry in range(4):
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
                "retry": retry,
                "http_status": response.status_code,
                "content_type": response.headers.get("content-type"),
            }
            try:
                payload = response.json()
                entry["top_level_keys"] = sorted(payload.keys()) if isinstance(payload, dict) else []
            except Exception:
                entry["body_prefix"] = response.text[:500]
                attempts.append(entry)
                if response.status_code == 429:
                    time.sleep(8 * (retry + 1))
                    refresh_session(session)
                    continue
                break
            pdf = payload.get("pdf") if isinstance(payload, dict) else None
            rows = pdf.get("list") if isinstance(pdf, dict) else None
            entry["pdf_keys"] = sorted(pdf.keys()) if isinstance(pdf, dict) else []
            entry["row_count"] = len(rows) if isinstance(rows, list) else None
            entry["reported_date"] = pdf.get("gijunYMD") if isinstance(pdf, dict) else None
            attempts.append(entry)
            if response.ok and isinstance(rows, list) and rows:
                return candidate, payload, attempts
            break
        time.sleep(0.8)
    raise RuntimeError(f"No KODEX PDF snapshot resolved for {period_id}: {attempts}")


def main() -> int:
    out = output_dir()
    raw_dir = out / "raw"
    session = requests.Session()
    refresh_session(session)

    normalized_rows: list[dict[str, object]] = []
    attempts_all: list[dict[str, object]] = []
    snapshots: list[dict[str, object]] = []
    raw_item_keys: set[str] = set()

    periods = selected_periods()
    for period_id, target in periods:
        resolved_candidate, payload, attempts = request_snapshot(session, period_id, target)
        attempts_all.extend(attempts)
        raw_path = raw_dir / f"{period_id}.json"
        raw_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        pdf = payload["pdf"]
        rows = pdf["list"]
        reported_raw = str(pdf.get("gijunYMD") or "")
        reported_date = (
            f"{reported_raw[0:4]}-{reported_raw[4:6]}-{reported_raw[6:8]}"
            if re.fullmatch(r"\d{8}", reported_raw)
            else reported_raw.replace(".", "-").replace("/", "-")
        )
        period_rows: list[dict[str, object]] = []
        for position, raw in enumerate(rows, 1):
            if not isinstance(raw, dict):
                continue
            raw_item_keys.update(raw.keys())
            code = normalize_code(
                get_first(
                    raw,
                    (
                        "itmNo",
                        "isuSrtCd",
                        "isuCd",
                        "stockCode",
                        "code",
                        "ticker",
                        "itemCode",
                        "secCd",
                    ),
                )
            )
            name = str(
                get_first(
                    raw,
                    ("secNm", "isuNm", "isuKorNm", "itemName", "name", "stockName"),
                )
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
                "quantity": get_first(raw, ("applyQ", "qty", "quantity", "holdQty", "number")),
                "valuation_amount": get_first(
                    raw, ("evalA", "evalAmt", "valuationAmount", "amt", "amount")
                ),
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
        time.sleep(1.2)

    frame = pd.DataFrame(normalized_rows)
    chunk = os.getenv("KODEX_CHUNK", "all")
    all_path = out / f"kodex_pdf_rows_{chunk}.csv"
    frame.to_csv(all_path, index=False)
    securities = frame.loc[~frame["is_cash"].astype(bool)].copy()
    securities_path = out / f"kodex_constituents_{chunk}.csv"
    securities.to_csv(securities_path, index=False)
    attempts_path = out / f"request_attempts_{chunk}.csv"
    pd.DataFrame(attempts_all).to_csv(attempts_path, index=False)

    summary = {
        "chunk": chunk,
        "period_start": periods[0][0],
        "period_end": periods[-1][0],
        "period_count": len(snapshots),
        "raw_rows": int(len(frame)),
        "security_membership_rows": int(len(securities)),
        "unique_stock_codes": int(securities["stock_code"].nunique()),
        "raw_item_keys": sorted(raw_item_keys),
        "snapshot_security_counts": {
            snapshot["period_id"]: snapshot["security_count"] for snapshot in snapshots
        },
        "snapshots": snapshots,
    }
    summary_path = out / f"summary_{chunk}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / f"manifest_{chunk}.json").write_text(
        json.dumps(
            {
                "summary_sha256": sha256(summary_path),
                "all_rows_sha256": sha256(all_path),
                "constituents_sha256": sha256(securities_path),
                "attempts_sha256": sha256(attempts_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "snapshots"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
