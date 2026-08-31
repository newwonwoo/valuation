"""The operator's segment map: disclosed names in, economic identities declared.

The engine's doctrine splits every fact three ways: evidence decides what
exists, declarations carry judgment, deterministic code re-derives. Reportable
segments now follow the same split. *Which* segments exist is evidence — the
IFRS 8 operating-segment note names them and the snapshot loader receipts each
one against the filing's archive hash. *What each segment economically is*
cannot come from evidence: the company-level KSIC code types the whole issuer
(대한제강 is "steel"), and nothing filed says that its 운송부문 should be
valued like a logistics business rather than a rebar mill. That is a judgment,
so it arrives here — one declared classification per disclosed segment, with a
rationale, bound to the target and to the disclosing filing.

The declaration cannot invent or drop segments: ``match_note`` demands an
exact bijection with the note's own names, so a segment the filing discloses
but the operator ignores is a refusal, and so is a declared segment the filing
never mentions. Routing still fails closed downstream — a declared KSIC code
the classification map does not cover stops the run exactly as an unmapped
company does.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

import yaml

from .segment_note import OperatingSegmentDisclosure, SegmentNoteEntry

_MIN_RATIONALE_CHARS = 20
_SEGMENT_ID = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


class DeclaredSegmentsError(ValueError):
    """Raised when the segment declaration cannot be honoured as written."""


def _normalize_name(name: str) -> str:
    return re.sub(r"[\s/·&-]+", "", str(name or "")).casefold()


@dataclass(frozen=True)
class DeclaredSegment:
    """One reportable segment's declared economic identity."""

    segment_id: str
    disclosed_name: str
    ksic_code: str
    rationale: str

    def validate(self) -> None:
        if not _SEGMENT_ID.match(self.segment_id):
            raise DeclaredSegmentsError(
                f"segment_id {self.segment_id!r} must be a short lowercase slug "
                "([a-z][a-z0-9_]+); it becomes the run's segment identity"
            )
        if not self.disclosed_name.strip():
            raise DeclaredSegmentsError(
                f"segment {self.segment_id} requires the disclosed_name the "
                "filing's operating-segment note uses"
            )
        if not self.ksic_code.strip() or not self.ksic_code.strip().isdigit():
            raise DeclaredSegmentsError(
                f"segment {self.segment_id} requires a numeric ksic_code to "
                "route its archetype through the classification map"
            )
        if len(self.rationale.strip()) < _MIN_RATIONALE_CHARS:
            raise DeclaredSegmentsError(
                f"segment {self.segment_id} requires a substantive rationale "
                f"(>= {_MIN_RATIONALE_CHARS} chars) for its declared "
                "classification; an untyped segment is a guessed archetype"
            )


@dataclass(frozen=True)
class DeclaredSegments:
    """A loaded, eagerly validated segment map bound to one target."""

    target_id: str
    as_of: str
    source_ref: str
    segments: tuple[DeclaredSegment, ...]

    def validate(self) -> None:
        if not self.target_id or not self.as_of:
            raise DeclaredSegmentsError(
                "segment declaration requires target_id and as_of"
            )
        if not self.source_ref.startswith("https://"):
            raise DeclaredSegmentsError(
                "segment declaration source_ref must be an HTTPS reference to "
                "the disclosing filing"
            )
        if len(self.segments) < 2:
            raise DeclaredSegmentsError(
                "a segment declaration exists to type multiple reportable "
                "segments; a single-segment company needs no declaration"
            )
        ids = tuple(item.segment_id for item in self.segments)
        if len(set(ids)) != len(ids):
            raise DeclaredSegmentsError(f"duplicate segment_ids: {ids}")
        names = tuple(_normalize_name(item.disclosed_name) for item in self.segments)
        if len(set(names)) != len(names):
            raise DeclaredSegmentsError(
                "duplicate disclosed_names in the segment declaration"
            )
        for item in self.segments:
            item.validate()

    def assert_target(self, target_id: str) -> None:
        if self.target_id != target_id:
            raise DeclaredSegmentsError(
                f"segment declaration is bound to {self.target_id}, not "
                f"{target_id}; a declaration cannot be reused across issuers"
            )

    def match_note(
        self, disclosure: OperatingSegmentDisclosure
    ) -> tuple[tuple[DeclaredSegment, SegmentNoteEntry], ...]:
        """Pair every declared segment with the note entry it names — exactly.

        The bijection is the containment: a declared segment the note never
        mentions would let the operator invent a business, and a note segment
        left undeclared would let a run quietly value part of the company as
        if it were the whole. Both refuse.
        """
        by_name = {
            _normalize_name(entry.name): entry for entry in disclosure.entries
        }
        matched: list[tuple[DeclaredSegment, SegmentNoteEntry]] = []
        for declared in self.segments:
            entry = by_name.pop(_normalize_name(declared.disclosed_name), None)
            if entry is None:
                raise DeclaredSegmentsError(
                    f"declared segment {declared.segment_id} names "
                    f"{declared.disclosed_name!r}, which the filing's "
                    "operating-segment note does not disclose"
                )
            matched.append((declared, entry))
        if by_name:
            raise DeclaredSegmentsError(
                "the filing disclosed reportable segments the declaration does "
                f"not cover: {', '.join(entry.name for entry in by_name.values())}; "
                "every disclosed segment must be declared or the run is valuing "
                "part of the company as the whole"
            )
        return tuple(matched)


def load_declared_segments(path: str | Path) -> DeclaredSegments:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise DeclaredSegmentsError("segment declaration must be a mapping")
    rows = payload.get("segments")
    if not isinstance(rows, list):
        raise DeclaredSegmentsError("segment declaration requires a segments list")
    declared = DeclaredSegments(
        target_id=str(payload.get("target_id") or ""),
        as_of=str(payload.get("as_of") or ""),
        source_ref=str(payload.get("source_ref") or ""),
        segments=tuple(
            DeclaredSegment(
                segment_id=str((row or {}).get("segment_id") or ""),
                disclosed_name=str((row or {}).get("disclosed_name") or ""),
                ksic_code=str((row or {}).get("ksic_code") or ""),
                rationale=str((row or {}).get("rationale") or ""),
            )
            for row in rows
        ),
    )
    declared.validate()
    return declared
