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

    old_helper = (
        "def replace_once(path: Path, old: str, new: str) -> None:\n"
        "    text = path.read_text(encoding=\"utf-8\")\n"
        "    count = text.count(old)\n"
        "    if count != 1:\n"
        "        raise RuntimeError(\n"
        "            f\"{path}: expected one replacement, found {count}: {old[:120]!r}\"\n"
        "        )\n"
        "    path.write_text(text.replace(old, new, 1), encoding=\"utf-8\")\n"
    )
    new_helper = (
        "def replace_once(path: Path, old: str, new: str) -> None:\n"
        "    text = path.read_text(encoding=\"utf-8\")\n"
        "    count = text.count(old)\n"
        "    ambiguous_capex_anchor = (\n"
        "        \"            )\\n\"\n"
        "        \"        for key in (\\\"terminal_growth\\\", \\\"terminal_roic\\\"):\\n\"\n"
        "    )\n"
        "    if count == 2 and old == ambiguous_capex_anchor:\n"
        "        head, separator, tail = text.rpartition(old)\n"
        "        if not separator:\n"
        "            raise RuntimeError(\"CAPEX insertion anchor was not found\")\n"
        "        path.write_text(head + new + tail, encoding=\"utf-8\")\n"
        "        return\n"
        "    if count != 1:\n"
        "        raise RuntimeError(\n"
        "            f\"{path}: expected one replacement, found {count}: {old[:120]!r}\"\n"
        "        )\n"
        "    path.write_text(text.replace(old, new, 1), encoding=\"utf-8\")\n"
    )
    text = replace_once(
        text,
        old_helper,
        new_helper,
        label="finalizer replacement helper",
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
