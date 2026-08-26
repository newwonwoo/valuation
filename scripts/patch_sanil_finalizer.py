from __future__ import annotations

from pathlib import Path


TARGET = Path(__file__).resolve().with_name("finalize_sanil_live_primary.py")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one replacement, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = TARGET.read_text(encoding="utf-8")

    old_capex_anchor = (
        "        '''            )\n"
        "        for key in (\"terminal_growth\", \"terminal_roic\"):\n"
        "''',\n"
        "        '''            )\n"
        "\n"
        "        capex_metric = f\"model_{scenario.lower()}_expansion_capex\"\n"
        "''',\n"
    )
    new_capex_anchor = (
        "        '''                min_value=\"0\",\n"
        "            )\n"
        "        )\n"
        "\n"
        "        for key in (\"terminal_growth\", \"terminal_roic\"):\n"
        "''',\n"
        "        '''                min_value=\"0\",\n"
        "            )\n"
        "        )\n"
        "\n"
        "        capex_metric = f\"model_{scenario.lower()}_expansion_capex\"\n"
        "''',\n"
    )
    text = replace_once(
        text,
        old_capex_anchor,
        new_capex_anchor,
        label="unique FCFF-to-CAPEX insertion anchor",
    )

    old_market_anchor = (
        "    replace_once(\n"
        "        path,\n"
        "        '''- 현재가: {snapshot.market['source_ref']}\n"
        "''',\n"
        "        '''- 현재가: {market_snapshot.source_ref}\n"
        "''',\n"
        "    )\n"
    )
    new_market_anchor = (
        "    replace_once(\n"
        "        path,\n"
        "        '''- Underwriting assumptions: {snapshot.sources['underwriting']['source_ref']}\n"
        "''',\n"
        "        '''- Underwriting assumptions: {snapshot.sources['underwriting']['source_ref']}\n"
        "- 현재가: {market_snapshot.source_ref}\n"
        "''',\n"
        "    )\n"
    )
    text = replace_once(
        text,
        old_market_anchor,
        new_market_anchor,
        label="post-Freeze market source insertion anchor",
    )

    TARGET.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
