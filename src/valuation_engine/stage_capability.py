"""Capability receipts for the 33 canonical stages.

This repository already refuses a number that has no receipt: an Evidence record
needs a source ref, a layer, a first-seen time and a hash before it can become a
valuation input, and an execution family whose ``canonical_refs`` name a file
that does not exist fails the registry validator.

It did not apply the same rule to its own capability claims.
``config/live_primary_readiness.yaml`` carried one hand-written status word per
stage, and ``live_readiness.load_live_primary_readiness`` checked only that the
word was a valid enum member and that the reason string was non-empty. A stage
could claim ``LIVE_READY`` with the reason "it works" and pass CI.

That single word also collapsed three different questions:

1. **Contract** — does a type or protocol exist for this stage's provider?
2. **Implementation** — does *this repository* supply a company-neutral
   implementation of it?
3. **Cold execution** — does the stage actually run for a company that has no
   hand-written module in this repository?

``RUNTIME_READY`` answers only (1). ``LIVE_READY`` answers (1) and (2). Nothing
answered (3), and the project-status rollup counted both as ready, so a stage
whose model provider was never connected reported as a completed stage.

This module separates the three axes and *derives* the status instead of reading
it. Declarations name symbols; the symbols are imported and must resolve. A
declaration can therefore understate what exists — naming ``null`` where an
implementation is available — but it cannot overstate it, because every claim it
makes is executed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from importlib import import_module
from pathlib import Path
from typing import Any, Mapping

import yaml


class CapabilityAxis(str, Enum):
    CONTRACT = "contract"
    IMPLEMENTATION = "implementation"
    COLD_EXECUTION = "cold_execution"


class AxisOutcome(str, Enum):
    #: contract / implementation
    PRESENT = "present"
    ABSENT = "absent"
    #: cold execution
    PROVEN = "proven"
    BLOCKED = "blocked"
    UNREACHED = "unreached"
    NOT_PROBED = "not_probed"


class DerivedCapability(str, Enum):
    """What the three axes together say about a stage.

    Ordered from strongest to weakest. Only ``COLD_PROVEN`` and ``IMPLEMENTED``
    count as ready; everything below is an honest gap.
    """

    COLD_PROVEN = "COLD_PROVEN"
    IMPLEMENTED = "IMPLEMENTED"
    CONTRACT_ONLY = "CONTRACT_ONLY"
    PROVIDER_REQUIRED = "PROVIDER_REQUIRED"
    UNDECLARED = "UNDECLARED"


READY_CAPABILITIES = frozenset(
    {DerivedCapability.COLD_PROVEN, DerivedCapability.IMPLEMENTED}
)


class StageCapabilityError(ValueError):
    """Raised when a capability declaration does not survive its own probe."""


@dataclass(frozen=True)
class SymbolResolution:
    ref: str | None
    outcome: AxisOutcome
    detail: str

    @property
    def present(self) -> bool:
        return self.outcome is AxisOutcome.PRESENT


def resolve_symbol(
    ref: str | None,
    *,
    company_bound_modules: frozenset[str] = frozenset(),
) -> SymbolResolution:
    """Import ``module:symbol`` and report whether it is really there.

    A symbol that lives in a company-bound module does not satisfy an axis: it is
    the hand-written work this probe exists to make visible, not a capability the
    engine has.
    """
    if ref is None:
        return SymbolResolution(None, AxisOutcome.ABSENT, "not declared")
    text = str(ref).strip()
    if text.count(":") != 1 or not all(part.strip() for part in text.split(":")):
        raise StageCapabilityError(
            f"capability reference must be 'module:symbol', got {ref!r}"
        )
    module_name, symbol_name = (part.strip() for part in text.split(":"))
    if module_name in company_bound_modules:
        return SymbolResolution(
            text,
            AxisOutcome.ABSENT,
            f"{module_name} is company-bound; a hand-written module is not a capability",
        )
    try:
        module = import_module(module_name)
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        raise StageCapabilityError(
            f"capability reference {text!r} names an unimportable module: "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    if not hasattr(module, symbol_name):
        raise StageCapabilityError(
            f"capability reference {text!r} names a symbol that does not exist"
        )
    return SymbolResolution(text, AxisOutcome.PRESENT, "resolved")


@dataclass(frozen=True)
class ColdStartOutcome:
    """What a run for a company with no hand-written module actually did.

    ``reached`` is the stages that executed. ``blocking_stage`` is where it
    stopped, and ``blocking_reason`` is the engine's own words, never a summary
    written by hand. ``config_blocked_reason`` is set when the run never started
    because the providers could not be assembled at all — the honest answer today.
    """

    probed: bool = False
    reached: tuple[str, ...] = ()
    blocking_stage: str | None = None
    blocking_reason: str = ""
    config_blocked_reason: str = ""
    missing_provider_slots: tuple[str, ...] = ()

    def outcome_for(self, stage: str) -> tuple[AxisOutcome, str]:
        if not self.probed:
            return AxisOutcome.NOT_PROBED, "cold-start probe was not run"
        if self.config_blocked_reason:
            return AxisOutcome.UNREACHED, self.config_blocked_reason
        if stage in self.reached:
            return AxisOutcome.PROVEN, "executed in a cold-start run"
        if stage == self.blocking_stage:
            return AxisOutcome.BLOCKED, self.blocking_reason
        return AxisOutcome.UNREACHED, "cold-start run stopped before this stage"


@dataclass(frozen=True)
class StageCapabilityDeclaration:
    stage: str
    provider_slot: str | None
    contract: str | None
    generic_implementation: str | None
    note: str

    def validate(self) -> None:
        if not self.stage:
            raise StageCapabilityError("capability declaration requires a stage")
        if not self.note.strip():
            raise StageCapabilityError(
                f"capability declaration for {self.stage} requires a note"
            )
        if self.generic_implementation is not None and self.contract is None:
            raise StageCapabilityError(
                f"{self.stage} declares an implementation without a contract"
            )


@dataclass(frozen=True)
class StageCapability:
    stage: str
    provider_slot: str | None
    contract: SymbolResolution
    implementation: SymbolResolution
    cold_execution: AxisOutcome
    cold_execution_detail: str
    note: str

    @property
    def derived(self) -> DerivedCapability:
        if not self.contract.present:
            return DerivedCapability.UNDECLARED
        if not self.implementation.present:
            return DerivedCapability.PROVIDER_REQUIRED
        if self.cold_execution is AxisOutcome.PROVEN:
            return DerivedCapability.COLD_PROVEN
        return DerivedCapability.IMPLEMENTED

    @property
    def ready(self) -> bool:
        return self.derived in READY_CAPABILITIES


@dataclass(frozen=True)
class StageCapabilityReport:
    stages: tuple[StageCapability, ...]
    cold_start: ColdStartOutcome

    def by_stage(self, stage: str) -> StageCapability:
        for item in self.stages:
            if item.stage == stage:
                return item
        raise KeyError(stage)

    def counts(self) -> dict[DerivedCapability, int]:
        result = {item: 0 for item in DerivedCapability}
        for item in self.stages:
            result[item.derived] += 1
        return result

    @property
    def ready_count(self) -> int:
        return sum(1 for item in self.stages if item.ready)

    @property
    def cold_proven_count(self) -> int:
        return sum(
            1 for item in self.stages if item.derived is DerivedCapability.COLD_PROVEN
        )

    @property
    def gaps(self) -> tuple[StageCapability, ...]:
        return tuple(item for item in self.stages if not item.ready)


def _declaration(stage: str, row: Any) -> StageCapabilityDeclaration:
    if not isinstance(row, Mapping):
        raise StageCapabilityError(f"capability row must be a mapping: {stage}")
    unknown = set(row) - {
        "provider_slot",
        "contract",
        "generic_implementation",
        "note",
    }
    if unknown:
        raise StageCapabilityError(
            f"{stage} capability row has unknown keys: {', '.join(sorted(unknown))}"
        )

    def text(key: str) -> str | None:
        value = row.get(key)
        return None if value is None else str(value)

    declaration = StageCapabilityDeclaration(
        stage=stage,
        provider_slot=text("provider_slot"),
        contract=text("contract"),
        generic_implementation=text("generic_implementation"),
        note=str(row.get("note") or ""),
    )
    declaration.validate()
    return declaration


def load_stage_capability_declarations(
    path: str | Path,
) -> tuple[tuple[StageCapabilityDeclaration, ...], frozenset[str]]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise StageCapabilityError("stage capability registry must be a mapping")
    rows = payload.get("stages")
    if not isinstance(rows, Mapping) or not rows:
        raise StageCapabilityError("stage capability registry requires stages")
    company_bound = payload.get("company_bound_modules") or ()
    if not isinstance(company_bound, (list, tuple)):
        raise StageCapabilityError("company_bound_modules must be a list")
    declarations = tuple(
        _declaration(str(stage), row) for stage, row in rows.items()
    )
    return declarations, frozenset(str(item) for item in company_bound)


def build_stage_capability_report(
    *,
    declarations: tuple[StageCapabilityDeclaration, ...],
    company_bound_modules: frozenset[str],
    canonical_stages: tuple[str, ...],
    cold_start: ColdStartOutcome = ColdStartOutcome(),
) -> StageCapabilityReport:
    declared = {item.stage: item for item in declarations}
    missing = tuple(stage for stage in canonical_stages if stage not in declared)
    extra = tuple(stage for stage in declared if stage not in canonical_stages)
    if missing or extra:
        raise StageCapabilityError(
            f"capability/stage-registry mismatch: missing={missing}, extra={extra}"
        )

    stages: list[StageCapability] = []
    for stage in canonical_stages:
        item = declared[stage]
        contract = resolve_symbol(item.contract)
        implementation = resolve_symbol(
            item.generic_implementation,
            company_bound_modules=company_bound_modules,
        )
        outcome, detail = cold_start.outcome_for(stage)
        stages.append(
            StageCapability(
                stage=stage,
                provider_slot=item.provider_slot,
                contract=contract,
                implementation=implementation,
                cold_execution=outcome,
                cold_execution_detail=detail,
                note=item.note,
            )
        )
    return StageCapabilityReport(tuple(stages), cold_start)


# --------------------------------------------------------------- cold start

#: Provider slots ``LivePrimaryProviders.validate`` refuses to run without. A
#: stage whose slot is in this set and has no generic implementation makes a
#: cold start impossible: the config cannot even be constructed, so no stage
#: executes and no stage may claim cold execution.
REQUIRED_PROVIDER_SLOTS = frozenset(
    {
        "company_resolver",
        "industry_snapshot_loader",
        "freshness_loader",
        "segment_decomposer",
        "industry_dna_router",
        "collectors",
        "scanner_runners",
        "intelligence_officer",
        "red_team_officer",
        "bridge_analyst",
        "evaluator_registry_loader",
        "valuation_plan_inputs_loader",
    }
)


def probe_cold_start(
    stages: tuple[StageCapability, ...],
) -> ColdStartOutcome:
    """Ask whether this repository could value a company it has never seen.

    The question is answered before any network call, because the first thing a
    cold start needs is a full set of providers. Any required slot with no
    company-neutral implementation makes ``LivePrimaryProviders.validate`` fail,
    so the run never starts and every stage is honestly ``UNREACHED``.

    When every required slot is filled this probe deliberately reports
    ``NOT_PROBED`` rather than success: a provider existing is not the same as a
    run completing, and only an executed cold run may set ``PROVEN``.
    """
    missing = tuple(
        sorted(
            {
                stage.provider_slot
                for stage in stages
                if stage.provider_slot in REQUIRED_PROVIDER_SLOTS
                and not stage.implementation.present
            }
        )
    )
    if missing:
        return ColdStartOutcome(
            probed=True,
            config_blocked_reason=(
                "LivePrimaryProviders cannot be assembled for an unseen company: "
                "no company-neutral implementation for "
                + ", ".join(missing)
            ),
            missing_provider_slots=missing,
        )
    return ColdStartOutcome(probed=False)
