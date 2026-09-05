"""Can the coordinate reader replace the anchor registry yet? Ask the filings.

`config/kr_filing_kpi_patterns.yaml` carries an anchor vocabulary that grows
once per issuer, and PR #173 built the coordinate reader to retire it. Retiring
it means every locator the committed runs rely on can be re-expressed as a
verified coordinate. Whether that is true today is a measurement, not an
opinion — so this probe makes it one.

For each committed run it takes the locators the run actually declares, finds
the table each one points at in the same raw filing member, and asks the
coordinate reader to read the same cell. It prints one line per locator: the
value if the coordinate is accepted, the verifier's own refusal if not.

Run it with no arguments::

    PYTHONPATH=src python scripts/probe_table_reader_coverage.py

Exit code 0 means every declared locator has a working coordinate — the point
at which the issuer-specific anchors can be deleted. A nonzero exit names what
is still missing, in the verifier's words rather than in a summary of them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from valuation_engine.filing_table_cells import (  # noqa: E402
    TableCellProposal,
    _grids,
    load_table_reading_tasks,
    read_table_cell,
)

RUNS = ROOT / "runs"

#: The coordinate each declared locator would become. Written by hand from the
#: filing, because that is exactly what the reader is meant to replace a person
#: doing — and because a probe that guessed the coordinate would be measuring
#: the guess rather than the reader.
COORDINATES: dict[tuple[str, str], dict[str, object]] = {
    ("daehansteel-084010", "realized_price"): {
        "table_index": 1,
        "row_path": ["대한제강(주)", "철 근"],
        "column_path": ["2026년 반기"],
        "unit_token": "천원/톤",
        "unit_source": {
            "quote": "제품 가격은 품목별 매출액에서 판매량을 나눈 값으로 산출하였습니다. (단위: 천원/톤)"
        },
        "period_source": {"cell": [1, 0, 2]},
    },
    ("daehansteel-084010", "utilization"): {
        "table_index": 2,
        "row_path": ["대한제강(주)", "압 연(철근)", "소계"],
        "column_path": ["평균가동률"],
        "unit_token": "%",
        "unit_source": {
            "quote": "제74기 반기 동안의 생산실적, 생산능력 및 가동률은 아래와 같습니다. (단위: 천톤, %)"
        },
        "period_source": {"cell": [2, 0, 3]},
    },
    ("kisco-104700", "realized_price"): {
        "table_index": 3,
        "row_path": ["철강", "철 근"],
        "column_path": ["제19기 반기"],
        "unit_token": "원/Ton",
        "unit_source": {"cell": [2, 0, 0]},
        "period_source": {"cell": [3, 0, 2]},
    },
    ("kisco-104700", "utilization"): {
        "table_index": 11,
        "row_path": ["철 강", "철 근"],
        "column_path": ["가동률 (%)"],
        "unit_token": "%",
        "unit_source": {"cell": [11, 0, 4]},
        "period_source": {"cell": [11, 0, 4]},
    },
}


def _effective_date(run_dir: Path) -> str:
    import yaml

    config = yaml.safe_load((run_dir / "run.yaml").read_text(encoding="utf-8"))
    as_of = str(config["as_of"])[:10]
    # The filing's fiscal period end, which the runner derives from the report
    # title. The committed runs are all half-year reports of the same year.
    return f"{as_of[:4]}-06-30"


def _member(run_dir: Path, member_path: str) -> str | None:
    for candidate in sorted(run_dir.glob(f"raw/filing_*/{member_path}")):
        return candidate.read_text(encoding="utf-8")
    return None


def main() -> int:
    tasks = load_table_reading_tasks()
    unmigrated: list[str] = []
    for run_dir in sorted(path for path in RUNS.iterdir() if (path / "run.yaml").is_file()):
        staff = run_dir / "declarations" / "staff" / "filing_locator_analyst.json"
        if not staff.is_file():
            continue
        locators = json.loads(staff.read_text(encoding="utf-8")).get("locators", [])
        if not locators:
            continue
        print(f"{run_dir.name}:")
        for locator in locators:
            metric = locator["metric"]
            label = f"  {metric:16}"
            coordinate = COORDINATES.get((run_dir.name, metric))
            if coordinate is None:
                print(f"{label} NO COORDINATE WRITTEN")
                unmigrated.append(f"{run_dir.name}/{metric}: no coordinate written")
                continue
            text = _member(run_dir, locator["member_path"])
            if text is None:
                print(f"{label} member not committed: {locator['member_path']}")
                unmigrated.append(f"{run_dir.name}/{metric}: member missing")
                continue
            if coordinate["table_index"] >= len(_grids(text)):
                print(f"{label} table {coordinate['table_index']} is not in the member")
                unmigrated.append(f"{run_dir.name}/{metric}: table index out of range")
                continue
            proposal = TableCellProposal.from_row(
                {"metric": metric, "member_path": locator["member_path"], **coordinate}
            )
            try:
                reading = read_table_cell(
                    text, proposal, tasks[metric],
                    effective_date=_effective_date(run_dir),
                )
            except Exception as error:  # noqa: BLE001 - the refusal is the result
                print(f"{label} REFUSED  {error}")
                unmigrated.append(f"{run_dir.name}/{metric}: {error}")
                continue
            print(f"{label} {reading.value} {reading.unit}  (locator said {locator['value_text']})")

    print()
    if unmigrated:
        print(f"{len(unmigrated)} declared locator(s) have no working coordinate:")
        for line in unmigrated:
            print(f"  - {line}")
        print()
        print("The issuer-specific anchors in config/kr_filing_kpi_patterns.yaml")
        print("cannot be retired while these runs depend on them.")
        return 1
    print("Every declared locator reads by coordinate; the anchors can retire.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
