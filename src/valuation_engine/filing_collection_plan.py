"""Choose a filing's sections by the role they play, not by their heading number.

Collecting the original text of a Korean statutory filing has been a manual
hunt: open the viewer, read the table of contents, and copy out the element ids
of the sections a run needs. It is slow, it is easy to get wrong, and it has to
be redone for every issuer because the numbering moves — 고려아연's operating
segment note is item 31 of its half-year report and item 39 of its annual — and
because the headings themselves drift between issuers and years.

This module turns that hunt into a plan. Roles are declared once in
``config/kr_filing_toc_roles.yaml`` ("the segment note", "the share-count
table"), each carrying the heading vocabularies an accepted run has actually
seen. Matching strips the leading numbering first, so a section keeps its role
when the filing renumbers it.

Two properties keep this honest. A role that matches nothing is *reported*, not
skipped, so a missing required section stops the collection with its own name.
And a role that matches several headings keeps all of them: a note split across
sub-sections is collected whole rather than truncated to the first hit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

import yaml

from .runtime_resources import runtime_registry_path


class FilingCollectionError(ValueError):
    """Raised when the table of contents or the role registry is not readable."""


def resolver_input_hashes(raw: Path) -> dict[str, str]:
    """Bind a resolver result to the identity and filing index it actually read."""
    return {
        name: sha256((raw / name).read_bytes()).hexdigest()
        for name in ("corp_search.json", "company.json", "list.json")
    }


def collection_binding(run_dir: Path, rcept: str, receipt: Path | None) -> dict[str, Any]:
    """Check optional collection scope; this does not authorize valuation Evidence.

    Archival collection remains available without a run declaration. Bound
    collection reuses the resolver's selections, never a second filing selector.
    """
    if receipt is None:
        return {"status": "ARCHIVAL_UNBOUND", "rcept_no": rcept}
    try:
        receipt_bytes = receipt.read_bytes()
        resolved = json.loads(receipt_bytes)
        config = yaml.safe_load((run_dir / "run.yaml").read_text(encoding="utf-8"))
        cutoff = date.fromisoformat(str(resolved["as_of"]))
        if (str(config["as_of"]) != cutoff.isoformat()
                or config["company_query"] != resolved["company_query"]):
            raise ValueError("run target/as_of differs from resolver")
        if resolved["input_sha256"] != resolver_input_hashes(run_dir / "raw"):
            raise ValueError("resolver inputs changed")
        profile = json.loads((run_dir / "raw" / "company.json").read_bytes())
        if (profile["corp_code"] != resolved["corp_code"]
                or profile["stock_code"] != resolved["stock_code"]):
            raise ValueError("resolver target differs from company profile")
        selected = [resolved.get(key) for key in ("adopted_annual", "latest_periodic")]
        matches = [item for item in selected if item and item["rcept_no"] == rcept]
        if not matches or rcept in resolved.get("superseded_rcept_nos", ()):
            raise ValueError("receipt is not an adopted resolver filing")
        if any(date.fromisoformat(item["received_on"]) > cutoff for item in matches):
            raise ValueError("filing was published after as_of")
        return {
            "status": "RESOLVER_BOUND",
            "rcept_no": rcept,
            "corp_code": resolved["corp_code"],
            "stock_code": resolved["stock_code"],
            "as_of": cutoff.isoformat(),
            "resolver_sha256": sha256(receipt_bytes).hexdigest(),
        }
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise FilingCollectionError(f"FILING_SELECTION_MISMATCH: {error}") from error


DEFAULT_TOC_ROLE_REGISTRY = runtime_registry_path("kr_filing_toc_roles.yaml")

#: ``31.``, ``2-1.``, ``Ⅲ.`` and the roman-numeral headings the viewer emits.
_LEADING_NUMBER = re.compile(
    r"^\s*(?:[0-9]+(?:[-.][0-9]+)*|[IVXⅠ-Ⅹ]+)\s*[.)]?\s*"
)

#: The window the locator prompt shows of one member; a section at or beyond it
#: reaches a reader truncated, which is a fact the manifest records so a gap can
#: be named TRUNCATED rather than guessed at.
_MEMBER_TEXT_LIMIT = 12000


def normalize_heading(title: str) -> str:
    """Drop the numbering and squeeze the spacing out of a heading."""
    text = re.sub(r"\s+", " ", str(title or "")).strip()
    previous = None
    while previous != text:
        previous = text
        text = _LEADING_NUMBER.sub("", text).strip()
    return text


@dataclass(frozen=True)
class TocEntry:
    """One row of a filing's table of contents, as the viewer serves it."""

    ele_id: str
    dcm_no: str
    offset: int
    length: int
    dtd: str
    title: str

    @property
    def heading(self) -> str:
        return normalize_heading(self.title)

    def viewer_url(self, rcept_no: str) -> str:
        return (
            "https://dart.fss.or.kr/report/viewer.do?"
            f"rcpNo={rcept_no}&dcmNo={self.dcm_no}&eleId={self.ele_id}"
            f"&offset={self.offset}&length={self.length}&dtd={self.dtd}"
        )

    def member_name(self, rcept_no: str) -> str:
        return f"{rcept_no}_{self.ele_id}.xml"


@dataclass(frozen=True)
class SectionRole:
    """A section a run needs, and the headings that have carried it."""

    role: str
    patterns: tuple[str, ...]
    required: bool
    purpose: str = ""

    def matches(self, entry: TocEntry) -> bool:
        heading = entry.heading
        return any(pattern in heading for pattern in self.patterns)


@dataclass(frozen=True)
class SectionPlan:
    """Which entries serve which role, and which roles nothing served."""

    selected: tuple[tuple[str, tuple[TocEntry, ...]], ...]
    unmatched: tuple[str, ...]
    missing_required: tuple[str, ...]

    @property
    def entries(self) -> tuple[TocEntry, ...]:
        seen: dict[str, TocEntry] = {}
        for _role, items in self.selected:
            for entry in items:
                seen.setdefault(entry.ele_id, entry)
        return tuple(seen.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected": {
                role: [
                    {"ele_id": entry.ele_id, "title": entry.title}
                    for entry in entries
                ]
                for role, entries in self.selected
            },
            "unmatched_roles": list(self.unmatched),
            "missing_required_roles": list(self.missing_required),
        }


def parse_toc(text: str) -> tuple[TocEntry, ...]:
    """Read the tab-separated table of contents a collected filing carries."""
    entries: list[TocEntry] = []
    for line_number, line in enumerate(str(text or "").splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 6:
            raise FilingCollectionError(
                f"table-of-contents line {line_number} has {len(parts)} fields, "
                "expected ele_id, dcm_no, offset, length, dtd and title"
            )
        ele_id, dcm_no, offset, length, dtd = (item.strip() for item in parts[:5])
        title = "\t".join(parts[5:]).strip()
        if not offset.isdigit() or not length.isdigit():
            raise FilingCollectionError(
                f"table-of-contents line {line_number} has a non-numeric "
                "offset or length"
            )
        entries.append(
            TocEntry(
                ele_id=ele_id,
                dcm_no=dcm_no,
                offset=int(offset),
                length=int(length),
                dtd=dtd,
                title=title,
            )
        )
    if not entries:
        raise FilingCollectionError("the table of contents carries no sections")
    return tuple(entries)


#: The viewer builds its contents tree in JavaScript, one assignment per field
#: and one variable per depth (``node1`` for parts, ``node3`` for the notes
#: inside them). The depth is captured and back-referenced so fields are only
#: ever paired within a single node — a fixed ``node1|node2`` pattern reads
#: fewer than half of a real filing's sections and silently loses every note.
_TOC_NODE = re.compile(
    r"node(?P<depth>\d+)\['text'\]\s*=\s*\"(?P<title>[^\"]+)\";[\s\S]{0,600}?"
    r"node(?P=depth)\['dcmNo'\]\s*=\s*\"(?P<dcm_no>\d+)\";[\s\S]{0,600}?"
    r"node(?P=depth)\['eleId'\]\s*=\s*\"(?P<ele_id>\d+)\";[\s\S]{0,600}?"
    r"node(?P=depth)\['offset'\]\s*=\s*\"(?P<offset>\d+)\";[\s\S]{0,600}?"
    r"node(?P=depth)\['length'\]\s*=\s*\"(?P<length>\d+)\";"
    r"(?:[\s\S]{0,600}?node(?P=depth)\['dtd'\]\s*=\s*\"(?P<dtd>[^\"]+)\";)?"
)


def parse_viewer_toc(
    html_text: str, *, dtd: str = "dart4.xsd"
) -> tuple[TocEntry, ...]:
    """Read the contents tree out of the filing viewer's own page.

    ``dtd`` is only the fallback for a node that does not carry its own.
    """
    seen: dict[str, TocEntry] = {}
    for match in _TOC_NODE.finditer(str(html_text or "")):
        entry = TocEntry(
            ele_id=match.group("ele_id"),
            dcm_no=match.group("dcm_no"),
            offset=int(match.group("offset")),
            length=int(match.group("length")),
            dtd=(match.group("dtd") or dtd).strip(),
            title=re.sub(r"\s+", " ", match.group("title")).strip(),
        )
        seen.setdefault(entry.ele_id, entry)
    if not seen:
        raise FilingCollectionError(
            "the viewer page carries no contents tree; the filing may not be "
            "an original-text report"
        )
    return tuple(
        sorted(seen.values(), key=lambda entry: (int(entry.ele_id), entry.offset))
    )


def render_toc(entries: Iterable[TocEntry]) -> str:
    """Write the contents tree the way a collected run directory stores it."""
    return "".join(
        f"{entry.ele_id}\t{entry.dcm_no}\t{entry.offset}\t{entry.length}"
        f"\t{entry.dtd}\t{entry.title}\n"
        for entry in entries
    )


def load_section_roles(
    path: str | Path = DEFAULT_TOC_ROLE_REGISTRY,
) -> tuple[SectionRole, ...]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    rows = (payload or {}).get("roles")
    if not isinstance(rows, Mapping) or not rows:
        raise FilingCollectionError("the role registry requires roles")
    roles: list[SectionRole] = []
    for role, spec in rows.items():
        if not isinstance(spec, Mapping):
            raise FilingCollectionError(f"role {role} must be a mapping")
        patterns = tuple(
            normalize_heading(item) for item in spec.get("patterns") or ()
        )
        if not patterns or not all(patterns):
            raise FilingCollectionError(f"role {role} requires non-empty patterns")
        roles.append(
            SectionRole(
                role=str(role),
                patterns=patterns,
                required=bool(spec.get("required", False)),
                purpose=str(spec.get("purpose") or ""),
            )
        )
    return tuple(roles)


def plan_sections(
    entries: Iterable[TocEntry], roles: Sequence[SectionRole]
) -> SectionPlan:
    """Match every declared role against the filing's own table of contents."""
    rows = tuple(entries)
    selected: list[tuple[str, tuple[TocEntry, ...]]] = []
    unmatched: list[str] = []
    missing_required: list[str] = []
    for role in roles:
        hits = tuple(entry for entry in rows if role.matches(entry))
        if hits:
            selected.append((role.role, hits))
            continue
        unmatched.append(role.role)
        if role.required:
            missing_required.append(role.role)
    return SectionPlan(
        selected=tuple(selected),
        unmatched=tuple(unmatched),
        missing_required=tuple(missing_required),
    )


def build_raw_manifest(raw_dir: str | Path) -> dict[str, Any]:
    """Record what was collected, byte for byte, and what a reader will see cut.

    The hash makes a re-collection provable rather than assumed, and the
    truncation flag lets a downstream read name TRUNCATED as its reason instead
    of reporting a section as absent.
    """
    root = Path(raw_dir)
    if not root.is_dir():
        raise FilingCollectionError(f"no raw directory at {root}")
    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "manifest.json":
            continue
        data = path.read_bytes()
        row: dict[str, Any] = {
            "path": path.relative_to(root).as_posix(),
            "bytes": len(data),
            "sha256": sha256(data).hexdigest(),
        }
        if path.suffix == ".xml":
            characters = len(data.decode("utf-8", errors="replace"))
            row["characters"] = characters
            row["truncated_for_reader"] = characters > _MEMBER_TEXT_LIMIT
        files.append(row)
    return {
        "version": 1,
        "member_text_limit": _MEMBER_TEXT_LIMIT,
        "files": files,
    }
