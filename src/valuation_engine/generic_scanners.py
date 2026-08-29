"""Company-neutral scanner runners: deterministic screens over the Evidence ledger.

A scanner's job in the Control Plane is to route attention — surface the
collected Evidence a given risk lens should look at, and say explicitly when
that lens could not observe its subject. The generic runners do exactly that
and nothing more:

- Evidence whose metric matches the scanner's declared keywords is cited in a
  PASS finding, connected downstream as a hypothesis candidate for LLM staff.
- No matching Evidence produces a WARNING finding whose verification request
  names what the scan needed — never a silent pass, never an invented
  observation.

What these runners deliberately do not do is reach out to external scan
sources (news, dockets, satellite feeds). That is provider work with its own
entitlements. The readiness registry records this stage as PARTIAL_LIVE for
exactly that reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

from .decision_impact import ResearchEffort
from .scanner_runtime import ScannerContext, ScannerFinding, ScannerFindingStatus


_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCREEN_CONFIG_PATH = _REPO_ROOT / "config" / "generic_scanner_screens.yaml"


class GenericScannerError(ValueError):
    """Raised when the screen configuration is malformed."""


@dataclass(frozen=True)
class ScannerScreen:
    scanner_id: str
    metric_keywords: tuple[str, ...]

    def validate(self) -> None:
        if not self.scanner_id:
            raise GenericScannerError("scanner screen requires scanner_id")
        if not self.metric_keywords or not all(
            keyword.strip() for keyword in self.metric_keywords
        ):
            raise GenericScannerError(
                f"scanner screen {self.scanner_id} requires non-empty metric keywords"
            )


def load_scanner_screens(
    path: str | Path = DEFAULT_SCREEN_CONFIG_PATH,
) -> tuple[ScannerScreen, ...]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise GenericScannerError("scanner screen config must be a mapping")
    rows = payload.get("screens")
    if not isinstance(rows, Mapping) or not rows:
        raise GenericScannerError("scanner screen config requires screens")
    screens = tuple(
        ScannerScreen(
            scanner_id=str(scanner_id),
            metric_keywords=tuple(
                str(item).casefold() for item in (row or {}).get("metric_keywords", ())
            ),
        )
        for scanner_id, row in rows.items()
    )
    for screen in screens:
        screen.validate()
    ids = tuple(item.scanner_id for item in screens)
    if len(ids) != len(set(ids)):
        raise GenericScannerError("scanner screen config has duplicate scanner ids")
    return screens


def ledger_screen_scanner_runner(screen: ScannerScreen):
    """Build one ScannerRunner that screens the run's ledger for its keywords."""
    screen.validate()

    def run(context: ScannerContext) -> ScannerFinding:
        matched = tuple(
            record
            for record in context.ledger.active()
            if any(
                keyword in record.metric.casefold()
                for keyword in screen.metric_keywords
            )
        )
        if matched:
            metrics = tuple(dict.fromkeys(record.metric for record in matched))
            return ScannerFinding(
                scanner_id=screen.scanner_id,
                status=ScannerFindingStatus.PASS,
                summary=(
                    f"{screen.scanner_id} observed collected Evidence for: "
                    + ", ".join(sorted(metrics))
                ),
                evidence_ids=tuple(record.id for record in matched),
                hypothesis_candidates=(
                    f"{screen.scanner_id}: assess valuation impact of "
                    + ", ".join(sorted(metrics)),
                ),
                economic_path_ids=(f"scanner:{screen.scanner_id}",),
                effort=ResearchEffort(documents_reviewed=len(matched)),
            )
        return ScannerFinding(
            scanner_id=screen.scanner_id,
            status=ScannerFindingStatus.WARNING,
            summary=(
                f"{screen.scanner_id} found no collected Evidence matching its "
                "screen; the lens could not observe its subject in this run"
            ),
            verification_requests=(
                f"collect primary Evidence for {screen.scanner_id.lower()} metrics: "
                + ", ".join(screen.metric_keywords),
            ),
            effort=ResearchEffort(documents_reviewed=0),
        )

    return run


def generic_scanner_runners(
    path: str | Path = DEFAULT_SCREEN_CONFIG_PATH,
) -> dict[str, object]:
    """ScannerRunner mapping covering every scanner the control requirements declare."""
    return {
        screen.scanner_id: ledger_screen_scanner_runner(screen)
        for screen in load_scanner_screens(path)
    }
