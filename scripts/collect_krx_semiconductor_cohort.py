from __future__ import annotations

import hashlib
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests


OUT = Path("artifacts/skhynix_calibration_cohort")
OUT.mkdir(parents=True, exist_ok=True)
URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.krx.co.kr/contents/MDC/MDI/outerLoader/index.cmd",
    "X-Requested-With": "XMLHttpRequest",
}
INDEX_GROUP = "5"
INDEX_TICKER = "044"
INDEX_NAME = "KRX 반도체"


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


def fetch_json(session: requests.Session, payload: dict[str, str]) -> dict[str, object]:
    response = session.post(URL, data=payload, headers=HEADERS, timeout=60)
    response.raise_for_status()
    try:
        return response.json()
    except Exception as exc:
        raise RuntimeError(
            f"KRX returned non-JSON status={response.status_code} body={response.text[:500]!r}"
        ) from exc


def resolve_snapshot(
    session: requests.Session, period_id: str, target: date
) -> tuple[date, list[dict[str, object]], list[dict[str, object]]]:
    attempts: list[dict[str, object]] = []
    for offset in range(0, 15):
        candidate = target - timedelta(days=offset)
        payload = {
            "bld": "dbms/MDC/STAT/standard/MDCSTAT00601",
            "indIdx": INDEX_GROUP,
            "indIdx2": INDEX_TICKER,
            "trdDd": candidate.strftime("%Y%m%d"),
        }
        data = fetch_json(session, payload)
        rows = data.get("output") or data.get("OutBlock_1") or []
        attempts.append(
            {
                "period_id": period_id,
                "target_date": target.isoformat(),
                "candidate_date": candidate.isoformat(),
                "keys": sorted(data.keys()),
                "row_count": len(rows) if isinstance(rows, list) else None,
                "error_code": data.get("_error_code"),
                "error_message": data.get("_error_message"),
            }
        )
        if isinstance(rows, list) and rows:
            return candidate, rows, attempts
        time.sleep(0.2)
    raise RuntimeError(f"No KRX constituent snapshot resolved for {period_id}: {attempts}")


def main() -> int:
    session = requests.Session()
    warmup_urls = [
        "https://data.krx.co.kr/contents/MDC/MDI/outerLoader/index.cmd",
        "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201010105",
    ]
    warmup: list[dict[str, object]] = []
    for url in warmup_urls:
        try:
            response = session.get(url, headers=HEADERS, timeout=30)
            warmup.append(
                {
                    "url": url,
                    "status": response.status_code,
                    "content_type": response.headers.get("content-type"),
                    "cookies": sorted(session.cookies.keys()),
                }
            )
        except Exception as exc:
            warmup.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})

    index_payload = {
        "bld": "dbms/MDC/STAT/standard/MDCSTAT00401",
        "idxIndMidclssCd": "01",
    }
    index_data = fetch_json(session, index_payload)
    index_rows = index_data.get("output") or []
    matching = [
        row
        for row in index_rows
        if "반도체" in str(row.get("IDX_NM", ""))
        or "Semicon" in str(row.get("IDX_ENG_NM", ""))
    ]
    (OUT / "krx_index_list.json").write_text(
        json.dumps(index_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    all_rows: list[dict[str, object]] = []
    all_attempts: list[dict[str, object]] = []
    snapshots: list[dict[str, object]] = []
    for period_id, target in periods():
        resolved_date, rows, attempts = resolve_snapshot(session, period_id, target)
        all_attempts.extend(attempts)
        normalized: list[dict[str, object]] = []
        for row in rows:
            code = str(row.get("ISU_SRT_CD", "")).zfill(6)
            name = str(row.get("ISU_ABBRV", "")).strip()
            item = {
                "period_id": period_id,
                "target_date": target.isoformat(),
                "resolved_date": resolved_date.isoformat(),
                "stock_code": code,
                "company_name": name,
                "market_cap": str(row.get("MKTCAP", "")),
            }
            normalized.append(item)
            all_rows.append(item)
        snapshots.append(
            {
                "period_id": period_id,
                "target_date": target.isoformat(),
                "resolved_date": resolved_date.isoformat(),
                "member_count": len(normalized),
                "members": normalized,
            }
        )
        time.sleep(0.25)

    frame = pd.DataFrame(all_rows)
    frame.to_csv(OUT / "krx_semiconductor_constituents_2015Q1_2024Q2.csv", index=False)
    union = (
        frame[["stock_code", "company_name"]]
        .drop_duplicates()
        .sort_values(["stock_code", "company_name"])
    )
    union.to_csv(OUT / "krx_semiconductor_union.csv", index=False)
    pd.DataFrame(all_attempts).to_csv(OUT / "krx_request_attempts.csv", index=False)

    summary = {
        "index": {
            "name": INDEX_NAME,
            "group_id": INDEX_GROUP,
            "ticker": INDEX_TICKER,
            "live_index_matches": matching,
        },
        "warmup": warmup,
        "period_count": len(snapshots),
        "membership_rows": int(len(frame)),
        "unique_stock_codes": int(frame["stock_code"].nunique()),
        "snapshot_member_counts": {
            snapshot["period_id"]: snapshot["member_count"] for snapshot in snapshots
        },
        "snapshots": snapshots,
    }
    summary_path = OUT / "cohort_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    manifest = {
        "summary_sha256": sha256(summary_path),
        "constituents_sha256": sha256(
            OUT / "krx_semiconductor_constituents_2015Q1_2024Q2.csv"
        ),
        "union_sha256": sha256(OUT / "krx_semiconductor_union.csv"),
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "snapshots"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
