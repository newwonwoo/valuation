from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests


OUT = Path("artifacts/kodex_pdf_diagnostic")
OUT.mkdir(parents=True, exist_ok=True)
PAGE_URL = "https://www.samsungfund.com/etf/product/view.do?id=2ETF07"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
}
KEYWORDS = (
    "pdf",
    "excel",
    "2ETF07",
    "apply",
    "aply",
    "date",
    "component",
    "hold",
    "portfolio",
    "download",
    "ajax",
    "composition",
    "asset",
    "fund",
)


def relevant_lines(text: str) -> list[str]:
    lines: list[str] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        lowered = line.lower()
        if any(keyword.lower() in lowered for keyword in KEYWORDS):
            compact = re.sub(r"\s+", " ", line).strip()
            if compact:
                lines.append(f"{line_number}: {compact[:2000]}")
    return lines


def main() -> int:
    session = requests.Session()
    response = session.get(PAGE_URL, headers=HEADERS, timeout=60)
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    html = response.text
    (OUT / "page.html").write_text(html, encoding="utf-8")
    (OUT / "page_relevant_lines.txt").write_text(
        "\n".join(relevant_lines(html)), encoding="utf-8"
    )

    scripts = []
    for src in re.findall(r"<script[^>]+src=[\"']([^\"']+)[\"']", html, flags=re.I):
        url = urljoin(PAGE_URL, src)
        scripts.append(url)

    inline_scripts = re.findall(r"<script(?![^>]+src=)[^>]*>([\s\S]*?)</script>", html, flags=re.I)
    for index, script in enumerate(inline_scripts):
        (OUT / f"inline_{index:02d}.js").write_text(script, encoding="utf-8")
        lines = relevant_lines(script)
        if lines:
            (OUT / f"inline_{index:02d}_relevant.txt").write_text(
                "\n".join(lines), encoding="utf-8"
            )

    script_results: list[dict[str, object]] = []
    for index, url in enumerate(dict.fromkeys(scripts)):
        try:
            script_response = session.get(url, headers={**HEADERS, "Referer": PAGE_URL}, timeout=60)
            script_response.raise_for_status()
            script_response.encoding = script_response.apparent_encoding or "utf-8"
            text = script_response.text
            name = f"external_{index:02d}.js"
            (OUT / name).write_text(text, encoding="utf-8")
            lines = relevant_lines(text)
            if lines:
                (OUT / f"external_{index:02d}_relevant.txt").write_text(
                    "\n".join(lines), encoding="utf-8"
                )
            script_results.append(
                {
                    "url": url,
                    "status": script_response.status_code,
                    "size": len(text.encode("utf-8")),
                    "relevant_line_count": len(lines),
                    "file": name,
                }
            )
        except Exception as exc:
            script_results.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})

    urls = sorted(
        set(
            re.findall(
                r"(?:https?:)?//[^\"'\s<>]+|/[A-Za-z0-9_./?=&%:-]+\.(?:do|json|xlsx?|csv)(?:\?[^\"'\s<>]*)?",
                html + "\n" + "\n".join(inline_scripts),
            )
        )
    )
    (OUT / "candidate_urls.txt").write_text("\n".join(urls), encoding="utf-8")
    summary = {
        "page_url": PAGE_URL,
        "page_status": response.status_code,
        "page_size": len(html.encode("utf-8")),
        "cookie_names": sorted(session.cookies.keys()),
        "external_script_count": len(scripts),
        "inline_script_count": len(inline_scripts),
        "external_scripts": script_results,
        "candidate_url_count": len(urls),
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
