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

    text = replace_once(
        text,
        '''        '''            )
        for key in ("terminal_growth", "terminal_roic"):
''',
        '''            )

        capex_metric = f"model_{scenario.lower()}_expansion_capex"
''',
''',
        '''        '''                min_value="0",
            )
        )

        for key in ("terminal_growth", "terminal_roic"):
''',
        '''                min_value="0",
            )
        )

        capex_metric = f"model_{scenario.lower()}_expansion_capex"
''',
''',
        label="unique FCFF-to-CAPEX insertion anchor",
    )

    text = replace_once(
        text,
        '''    replace_once(
        path,
        '''- 현재가: {snapshot.market['source_ref']}
''',
        '''- 현재가: {market_snapshot.source_ref}
''',
    )
''',
        '''    replace_once(
        path,
        '''- Underwriting assumptions: {snapshot.sources['underwriting']['source_ref']}
''',
        '''- Underwriting assumptions: {snapshot.sources['underwriting']['source_ref']}
- 현재가: {market_snapshot.source_ref}
''',
    )
''',
        label="post-Freeze market source insertion anchor",
    )

    TARGET.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
