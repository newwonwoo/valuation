"""Turn a ticker and an as-of date into the parts of a run that evidence decides.

Preparing a live run has always started the same way: copy another run's
``run.yaml``, then hand-edit the company, the fiscal calendar, the adopted
report and the calibration binding. Every one of those edits is a lookup, not a
judgment — the corporate registry, the company profile and the filing index
already contain the answer, and a person retyping them is a place for a typo to
enter a valuation.

This module makes those lookups deterministic and reviewable. It reads the
three metadata payloads a collector fetches first (the corp-code search hit,
the company profile and the filing index) and returns every decision it can
justify together with the evidence that justified it. What it cannot decide it
refuses to guess: an ambiguous company, a filing index with no annual report,
an unmapped KSIC code and a segment structure that only the IFRS 8 note can
settle all come back as named gaps, in the same spirit as the runner's stop
messages.

The division of labour is the doctrine's own: evidence decides existence, and
declarations carry judgment. The resolver therefore writes the company, the
filing selection, the fiscal calendar and the scenario/calibration binding, and
it never writes an underwriting value, a segment type or a method choice that
more than one candidate could satisfy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
import calendar
import re
from typing import Any, Iterable, Mapping, Sequence

import yaml

from .calibration_cohort_registry import (
    ProductionCalibrationCohort,
    load_production_calibration_registry,
    resolve_production_calibration_cohort,
)


class RunResolverError(ValueError):
    """Raised when the inputs are not the payloads the resolver reads."""


#: The statutory periodic reports, and the OpenDART report code each carries.
#: The annual report is the only one that fixes a full fiscal year, so it is
#: the one the financial-statement selection binds.
_ANNUAL = "사업보고서"
_PERIODIC_REPORT_CODES = {
    _ANNUAL: "11011",
    "반기보고서": "11012",
    "분기보고서": "11013",
}

#: ``[기재정정]사업보고서 (2025.12)`` — a correction keeps the original title and
#: period, so the period is what groups a report with the filings it restates.
_REPORT_TITLE = re.compile(
    r"^(?P<prefix>\[[^\]]*\])?\s*(?P<kind>사업보고서|반기보고서|분기보고서)"
    r"\s*\((?P<year>\d{4})\.(?P<month>\d{2})\)"
)


@dataclass(frozen=True)
class ResolverDecision:
    """One field the resolver set, and the evidence that set it."""

    field: str
    value: str
    basis: str
    source_ref: str = ""

    def as_dict(self) -> dict[str, str]:
        payload = {"field": self.field, "value": self.value, "basis": self.basis}
        if self.source_ref:
            payload["source_ref"] = self.source_ref
        return payload


@dataclass(frozen=True)
class ResolverGap:
    """Something the resolver refused to guess, named the way a stop message is."""

    reason: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"reason": self.reason, "detail": self.detail}


@dataclass(frozen=True)
class PeriodicFiling:
    """One statutory periodic report as the filing index lists it."""

    rcept_no: str
    report_name: str
    kind: str
    period_end: date
    received_on: date
    is_correction: bool

    @property
    def report_code(self) -> str:
        return _PERIODIC_REPORT_CODES[self.kind]


@dataclass(frozen=True)
class ResolvedRun:
    """Everything the metadata decided, plus what it could not."""

    company_query: str
    as_of: str
    corp_code: str
    corp_name: str
    stock_code: str
    ksic_code: str
    fiscal_month: int
    scenario_ids: tuple[str, ...]
    forecast_years: int
    adopted_annual: PeriodicFiling | None
    latest_periodic: PeriodicFiling | None
    superseded_rcept_nos: tuple[str, ...]
    archetypes: tuple[str, ...]
    method_candidates: tuple[str, ...]
    calibration_cohort: ProductionCalibrationCohort | None
    fs_div: str
    decisions: tuple[ResolverDecision, ...] = field(default=())
    gaps: tuple[ResolverGap, ...] = field(default=())

    @property
    def run_id(self) -> str:
        return f"LIVE-{self.stock_code}-1"

    @property
    def blocking(self) -> bool:
        """A run.yaml may only be written when nothing is left to guess."""
        return bool(self.gaps)

    def as_dict(self) -> dict[str, Any]:
        return {
            "company_query": self.company_query,
            "as_of": self.as_of,
            "corp_code": self.corp_code,
            "corp_name": self.corp_name,
            "stock_code": self.stock_code,
            "ksic_code": self.ksic_code,
            "fiscal_month": self.fiscal_month,
            "scenario_ids": list(self.scenario_ids),
            "forecast_years": self.forecast_years,
            "adopted_annual": _filing_payload(self.adopted_annual),
            "latest_periodic": _filing_payload(self.latest_periodic),
            "superseded_rcept_nos": list(self.superseded_rcept_nos),
            "archetypes": list(self.archetypes),
            "method_candidates": list(self.method_candidates),
            "calibration_cohort": (
                {
                    "registry_id": self.calibration_cohort.registry_id,
                    "cohort_key": self.calibration_cohort.cohort_key,
                    "forecast_class": self.calibration_cohort.forecast_class,
                    "external_probability_source": (
                        self.calibration_cohort.external_probability_source
                    ),
                }
                if self.calibration_cohort is not None
                else None
            ),
            "fs_div": self.fs_div,
            "decisions": [item.as_dict() for item in self.decisions],
            "gaps": [item.as_dict() for item in self.gaps],
        }

    def to_run_yaml(self) -> str:
        """Render the run declaration for the parts evidence decided.

        Refuses while any gap stands: a ``run.yaml`` written over an unresolved
        company, filing or route would carry a guess into the ledger.
        """
        if self.blocking:
            names = ", ".join(sorted({gap.reason for gap in self.gaps}))
            raise RunResolverError(
                f"cannot render run.yaml while decisions are unresolved: {names}"
            )
        assert self.adopted_annual is not None  # guaranteed by the gap above
        annual = self.adopted_annual
        lines = [
            f"# Resolved from public metadata for {self.corp_name} "
            f"({self.stock_code}) as of {self.as_of}.",
            "# Every field below is a lookup the filing index and company profile",
            "# decided; see resolver.json for each decision and its basis. The",
            "# judgment layer — underwriting, segments, risk pack — is declared",
            "# separately and is not written here.",
            f"company_query: {self.company_query}",
            f"run_id: {self.run_id}",
            f'as_of: "{self.as_of}"',
            "jurisdiction: KR",
            "scenario_ids: [" + ", ".join(self.scenario_ids) + "]",
            f"method: {self.method_candidates[0]}",
            "market_currency: KRW",
            "filing:",
            f'  business_year: "{annual.period_end.year}"',
            f'  report_code: "{annual.report_code}"',
            f"  fs_div: {self.fs_div}",
            f'  fiscal_period_end: "{annual.period_end.isoformat()}"',
            "  segment_id: core",
        ]
        if self.calibration_cohort is not None:
            cohort = self.calibration_cohort
            lines += [
                "# A production cohort is registered for this industry, so the run",
                "# must bind it; the target-excluded artifact, provenance,",
                "# conditioning and sealed hashes stay explicit per-run inputs.",
                "calibration:",
                f"  cohort_key: {cohort.cohort_key}",
                f"  external_probability_source: {cohort.external_probability_source}",
                f"  forecast_class: {cohort.forecast_class}",
                f"  horizon: {self.forecast_years}y_path",
            ]
        return "\n".join(lines) + "\n"


def _filing_payload(filing: PeriodicFiling | None) -> dict[str, Any] | None:
    if filing is None:
        return None
    return {
        "rcept_no": filing.rcept_no,
        "report_name": filing.report_name,
        "kind": filing.kind,
        "report_code": filing.report_code,
        "period_end": filing.period_end.isoformat(),
        "received_on": filing.received_on.isoformat(),
        "is_correction": filing.is_correction,
        "source_ref": (
            "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + filing.rcept_no
        ),
    }


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _parse_date(value: str, *, label: str) -> date:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{8}", text):
        raise RunResolverError(f"{label} must be YYYYMMDD, got {value!r}")
    return date(int(text[:4]), int(text[4:6]), int(text[6:]))


def read_periodic_filings(
    rows: Iterable[Mapping[str, Any]], *, as_of: date
) -> tuple[PeriodicFiling, ...]:
    """Read the statutory periodic reports out of a filing index.

    Ad-hoc disclosures are ignored here, and anything received after the as-of
    date is dropped: a run may not see a filing that did not exist when it was
    dated.
    """
    filings: list[PeriodicFiling] = []
    for row in rows:
        title = str(row.get("report_nm") or "")
        match = _REPORT_TITLE.match(title.strip())
        if match is None:
            continue
        received = _parse_date(row.get("rcept_dt"), label="rcept_dt")
        if received > as_of:
            continue
        rcept_no = str(row.get("rcept_no") or "").strip()
        if not re.fullmatch(r"\d{14}", rcept_no):
            raise RunResolverError(f"filing index row has no rcept_no: {title!r}")
        filings.append(
            PeriodicFiling(
                rcept_no=rcept_no,
                report_name=title.strip(),
                kind=match.group("kind"),
                period_end=_month_end(
                    int(match.group("year")), int(match.group("month"))
                ),
                received_on=received,
                is_correction=bool(match.group("prefix")),
            )
        )
    return tuple(filings)


def adopt_annual_report(
    filings: Sequence[PeriodicFiling],
) -> tuple[PeriodicFiling | None, tuple[str, ...]]:
    """Pick the annual report a run reads, and name what it restates.

    The newest fiscal year wins, and inside that year the most recently
    received filing wins — which is how a restatement takes effect. 고려아연's
    2026-08-13 correction of FY2025 supersedes both the original 2026-03-16
    filing and the June correction, and all of them are recorded rather than
    dropped, because a reader has to be able to see that the numbers moved.
    """
    annuals = [item for item in filings if item.kind == _ANNUAL]
    if not annuals:
        return None, ()
    newest_period = max(item.period_end for item in annuals)
    for_period = [item for item in annuals if item.period_end == newest_period]
    adopted = max(for_period, key=lambda item: (item.received_on, item.rcept_no))
    superseded = tuple(
        sorted(item.rcept_no for item in for_period if item is not adopted)
    )
    return adopted, superseded


def _classification_rows(path: str | Path) -> list[Mapping[str, Any]]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    rows = (payload or {}).get("classifications")
    if not isinstance(rows, list) or not rows:
        raise RunResolverError("classification map requires classifications")
    return rows


def route_industry(
    ksic_code: str, *, classification_map_path: str | Path
) -> tuple[str, ...]:
    """Longest-prefix KSIC route to economic archetypes, or nothing."""
    matches = [
        row
        for row in _classification_rows(classification_map_path)
        if ksic_code.startswith(str(row.get("ksic_prefix") or ""))
    ]
    if not matches:
        return ()
    longest = max(len(str(row["ksic_prefix"])) for row in matches)
    finalists = [row for row in matches if len(str(row["ksic_prefix"])) == longest]
    if len(finalists) != 1:
        prefixes = ", ".join(sorted(str(row["ksic_prefix"]) for row in finalists))
        raise RunResolverError(
            f"ambiguous KSIC classification for {ksic_code}: prefixes {prefixes}"
        )
    return tuple(str(item) for item in finalists[0].get("archetypes") or ())


def _method_candidates(
    archetypes: Iterable[str], *, archetype_registry_path: str | Path
) -> tuple[str, ...]:
    payload = yaml.safe_load(Path(archetype_registry_path).read_text(encoding="utf-8"))
    modules = (payload or {}).get("modules")
    if not isinstance(modules, Mapping):
        raise RunResolverError("archetype module registry requires modules")
    candidates: list[str] = []
    for archetype in archetypes:
        spec = modules.get(archetype)
        if not isinstance(spec, Mapping):
            raise RunResolverError(
                f"archetype module registry has no module for {archetype}"
            )
        for method in spec.get("allowed_valuation_methods") or ():
            candidates.append(f"{archetype}/{method}")
    return tuple(dict.fromkeys(candidates))


def resolve_run(
    *,
    corp_search: Mapping[str, Any],
    company: Mapping[str, Any],
    filing_index: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    as_of: str,
    company_query: str | None = None,
    stock_code: str | None = None,
    method: str | None = None,
    declared_segment_ids: Sequence[str] = (),
    scenario_ids: Sequence[str] = ("Down", "Base", "Bull"),
    forecast_years: int = 5,
    consolidated: bool | None = None,
    classification_map_path: str | Path,
    archetype_registry_path: str | Path,
    calibration_registry_path: str | Path | None = None,
) -> ResolvedRun:
    """Resolve one run from the metadata a collector fetches first.

    ``consolidated`` is the collector's report of whether the consolidated
    financial statement endpoint returned data for this company; leaving it
    unset is itself a gap, because CFS-versus-OFS is not guessable from the
    profile.
    """
    as_of_date = date.fromisoformat(str(as_of))
    requested_stock_code = str(stock_code or "").strip()
    decisions: list[ResolverDecision] = []
    gaps: list[ResolverGap] = []

    companies = list((corp_search or {}).get("companies") or ())
    corp_code = str(company.get("corp_code") or "").strip()
    corp_name = str(company.get("corp_name") or "").strip()
    stock_code = str(company.get("stock_code") or "").strip()
    # A name search hits unlisted namesakes as well — 한국철강 also returns
    # 한국철강산업 and 한국철강자원, neither of which has a ticker. A run values
    # a listed company, so the listed hits are the candidates; a supplied
    # ticker narrows them exactly.
    listed = [row for row in companies if str(row.get("stock_code") or "").strip()]
    if requested_stock_code:
        listed = [
            row
            for row in listed
            if str(row.get("stock_code")).strip() == requested_stock_code
        ]
    if not companies:
        gaps.append(
            ResolverGap("COMPANY_NOT_FOUND", "the corp-code search returned no company")
        )
    elif not listed:
        detail = (
            f"no search hit carries the ticker {requested_stock_code}"
            if requested_stock_code
            else "no search hit is listed; a run values a listed company"
        )
        gaps.append(ResolverGap("COMPANY_NOT_LISTED", detail))
    elif len(listed) > 1:
        names = ", ".join(
            f"{row.get('corp_name')}({row.get('stock_code')})" for row in listed[:6]
        )
        gaps.append(
            ResolverGap(
                "AMBIGUOUS_COMPANY",
                f"{len(listed)} listed companies match the query: {names}; "
                "supply the ticker to resolve it",
            )
        )
    else:
        searched = str(listed[0].get("corp_code") or "").strip()
        if searched and corp_code and searched != corp_code:
            gaps.append(
                ResolverGap(
                    "COMPANY_MISMATCH",
                    f"search resolved {searched} but the profile is {corp_code}",
                )
            )
        decisions.append(
            ResolverDecision(
                "corp_code",
                corp_code,
                f"the one listed corp-code search hit, {listed[0].get('corp_name')}"
                + (
                    f", matched on ticker {requested_stock_code}"
                    if requested_stock_code
                    else f" (of {len(companies)} name matches)"
                ),
                "https://opendart.fss.or.kr/api/corpCode.xml",
            )
        )
    if not stock_code:
        gaps.append(
            ResolverGap("NOT_LISTED", f"{corp_name or corp_code} has no stock code")
        )

    accounting_month = str(company.get("acc_mt") or "").strip()
    if not re.fullmatch(r"\d{1,2}", accounting_month or ""):
        gaps.append(
            ResolverGap("FISCAL_MONTH_UNKNOWN", "company profile carries no acc_mt")
        )
        fiscal_month = 12
    else:
        fiscal_month = int(accounting_month)
        decisions.append(
            ResolverDecision(
                "fiscal_month",
                f"{fiscal_month:02d}",
                "company profile acc_mt; a non-December close makes business_year "
                "the year the fiscal year ends",
                "https://opendart.fss.or.kr/api/company.json",
            )
        )

    ksic_code = str(company.get("induty_code") or "").strip()
    archetypes: tuple[str, ...] = ()
    methods: tuple[str, ...] = ()
    if not ksic_code:
        gaps.append(
            ResolverGap("KSIC_UNKNOWN", "company profile carries no induty_code")
        )
    else:
        archetypes = route_industry(
            ksic_code, classification_map_path=classification_map_path
        )
        if not archetypes:
            gaps.append(
                ResolverGap(
                    "KSIC_UNMAPPED",
                    f"KSIC {ksic_code} is not covered by the classification map; "
                    "routing an unmapped company would be a guessed archetype",
                )
            )
        else:
            decisions.append(
                ResolverDecision(
                    "archetypes",
                    ", ".join(archetypes),
                    f"longest KSIC prefix match for {ksic_code}",
                    str(classification_map_path),
                )
            )
            methods = _method_candidates(
                archetypes, archetype_registry_path=archetype_registry_path
            )
            chosen = str(method or "").strip()
            if chosen:
                if chosen not in methods:
                    gaps.append(
                        ResolverGap(
                            "METHOD_OFF_ROUTE",
                            f"{chosen} is not a method this KSIC route exposes: "
                            + ", ".join(methods),
                        )
                    )
                else:
                    methods = (chosen,) + tuple(
                        item for item in methods if item != chosen
                    )
                    decisions.append(
                        ResolverDecision(
                            "method",
                            chosen,
                            "operator's choice among the methods the route exposes",
                            str(archetype_registry_path),
                        )
                    )
            elif len(methods) != 1:
                gaps.append(
                    ResolverGap(
                        "METHOD_DECISION_REQUIRED",
                        "the route exposes more than one economic method, which is "
                        "an operator decision: " + ", ".join(methods),
                    )
                )

    # Decomposition comes before method selection. The company's own KSIC can
    # only describe a single-segment issuer; for a filing whose IFRS 8 note
    # names several reportable segments, one company-level method and a
    # ``segment_id: core`` would be the wrong shape, and writing it over a
    # prepared sum-of-the-parts declaration would lose that work. The note
    # decides, so when segments are already declared the resolver stops rather
    # than describing the run as single-segment.
    segments = tuple(str(item) for item in declared_segment_ids if str(item).strip())
    if len(segments) > 1:
        gaps.append(
            ResolverGap(
                "MULTI_SEGMENT_DECLARED",
                "the run declares reportable segments ("
                + ", ".join(segments)
                + "), so it needs one method per segment; a company-level route "
                "cannot describe it and this resolver does not choose per-segment "
                "methods",
            )
        )
    else:
        decisions.append(
            ResolverDecision(
                "segments",
                segments[0] if segments else "undetermined",
                "no segment declaration is present; the IFRS 8 note in the "
                "filing decides whether this issuer is multi-segment, and the "
                "run stops at the decomposition screen if it is",
                "declarations/segments.yaml",
            )
        )

    rows = (
        filing_index.get("list")
        if isinstance(filing_index, Mapping)
        else list(filing_index)
    )
    filings = read_periodic_filings(rows or (), as_of=as_of_date)
    adopted, superseded = adopt_annual_report(filings)
    latest = max(
        filings, key=lambda item: (item.received_on, item.rcept_no), default=None
    )
    if adopted is None:
        gaps.append(
            ResolverGap(
                "NO_ANNUAL_REPORT",
                f"the filing index lists no 사업보고서 received on or before {as_of}",
            )
        )
    else:
        decisions.append(
            ResolverDecision(
                "filing",
                f"{adopted.report_name} (rcept {adopted.rcept_no})",
                (
                    "newest annual report at the as-of date; "
                    + (
                        f"supersedes {', '.join(superseded)}"
                        if superseded
                        else "no correction filed for this year"
                    )
                ),
                "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + adopted.rcept_no,
            )
        )
        if adopted.period_end.month != fiscal_month:
            gaps.append(
                ResolverGap(
                    "FISCAL_CALENDAR_MISMATCH",
                    f"the annual report closes {adopted.period_end.isoformat()} but "
                    f"the profile says the fiscal month is {fiscal_month:02d}",
                )
            )
    if latest is not None:
        decisions.append(
            ResolverDecision(
                "sections_source",
                f"{latest.report_name} (rcept {latest.rcept_no})",
                "newest periodic filing at the as-of date; the engine reads its "
                "original sections, which may be a half-year while the financial "
                "statements bind the annual",
                "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + latest.rcept_no,
            )
        )

    if consolidated is None:
        gaps.append(
            ResolverGap(
                "FS_SCOPE_UNPROBED",
                "whether the company files consolidated statements is decided by "
                "the financial-statement endpoint, not the profile; the collector "
                "must report it",
            )
        )
        fs_div = "CFS"
    else:
        fs_div = "CFS" if consolidated else "OFS"
        decisions.append(
            ResolverDecision(
                "fs_div",
                fs_div,
                "the consolidated financial-statement endpoint "
                + ("returned data" if consolidated else "returned no data"),
                "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
            )
        )

    cohort = None
    if ksic_code:
        registry = (
            load_production_calibration_registry(calibration_registry_path)
            if calibration_registry_path is not None
            else load_production_calibration_registry()
        )
        cohort = resolve_production_calibration_cohort(
            registry,
            ksic_code=ksic_code,
            forecast_years=forecast_years,
            scenario_ids=scenario_ids,
        )
        decisions.append(
            ResolverDecision(
                "calibration",
                cohort.registry_id if cohort else "none registered",
                (
                    "a production cohort is registered for this industry and must "
                    "be bound"
                    if cohort
                    else "no production cohort matches this industry; the run may "
                    "complete without an expected value and must say so"
                ),
                str(calibration_registry_path or "config/kr_calibration_cohort_registry.yaml"),
            )
        )

    return ResolvedRun(
        company_query=str(company_query or corp_name or stock_code),
        as_of=str(as_of),
        corp_code=corp_code,
        corp_name=corp_name,
        stock_code=stock_code,
        ksic_code=ksic_code,
        fiscal_month=fiscal_month,
        scenario_ids=tuple(scenario_ids),
        forecast_years=int(forecast_years),
        adopted_annual=adopted,
        latest_periodic=latest,
        superseded_rcept_nos=superseded,
        archetypes=archetypes,
        method_candidates=methods,
        calibration_cohort=cohort,
        fs_div=fs_div,
        decisions=tuple(decisions),
        gaps=tuple(gaps),
    )
