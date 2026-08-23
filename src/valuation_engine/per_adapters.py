from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from hashlib import sha256
import json
from math import isfinite
from typing import Callable

from .assumption_compiler import CompiledAssumptionSet
from .control_plane import StageStatus
from .ledger import EvidenceLedger
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .per import (
    PER_LEVEL_ORDER,
    EconomicAssumptionFingerprint,
    FundamentalPERAssumptions,
    HierarchicalWarrantedPER,
    PERLevel,
    PERLevelName,
    PeerPERInput,
    build_hierarchical_warranted_per,
    validate_dcf_per_assumption_consistency,
)
from .risk_adapters import LiveWACCStageResult


_FORBIDDEN_PRE_FREEZE_KEYS = {
    "current_market_price",
    "market_price",
    "market_observation",
    "target_market_cap",
    "target_price",
    "consensus_target",
    "target_multiple",
    "target_company_consensus_eps",
    "street_reference",
}


class PERApplicability(str, Enum):
    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class LivePERAssumptionKeys:
    scenario_id: str
    normalized_forward_eps_key: str
    normalized_forward_eps_unit: str
    explicit_growth_rate_keys: tuple[str, ...]
    fcfe_conversion_rate_keys: tuple[str, ...]
    terminal_growth_key: str
    terminal_roe_key: str
    margin_path_keys: tuple[str, ...]
    reinvestment_path_keys: tuple[str, ...]

    def validate(self) -> None:
        if not all(
            (
                self.scenario_id,
                self.normalized_forward_eps_key,
                self.normalized_forward_eps_unit,
                self.terminal_growth_key,
                self.terminal_roe_key,
            )
        ):
            raise ValueError("PER assumption-key contract has missing required fields")
        if len(self.fcfe_conversion_rate_keys) != len(self.explicit_growth_rate_keys) + 1:
            raise ValueError("PER FCFE conversion keys must cover EPS1 plus every growth year")
        if not self.margin_path_keys or not self.reinvestment_path_keys:
            raise ValueError("PER assumption fingerprint requires margin and reinvestment paths")
        all_keys = (
            self.normalized_forward_eps_key,
            *self.explicit_growth_rate_keys,
            *self.fcfe_conversion_rate_keys,
            self.terminal_growth_key,
            self.terminal_roe_key,
            *self.margin_path_keys,
            *self.reinvestment_path_keys,
        )
        if len(all_keys) != len(set(all_keys)):
            raise ValueError("PER assumption-key contract reuses one key across economic roles")


@dataclass(frozen=True)
class LiveExpansionPERConfig:
    assumption_keys: LivePERAssumptionKeys
    committed_or_preinvested_evidence_ids: tuple[str, ...]
    rationale: str

    def validate(self) -> None:
        self.assumption_keys.validate()
        if not self.committed_or_preinvested_evidence_ids or not self.rationale:
            raise ValueError("Expansion PER requires committed/pre-invested Evidence and rationale")


@dataclass(frozen=True)
class LivePeerPERObservation:
    peer_id: str
    market_forward_per: float
    fundamental_forward_per: float
    as_of: str
    market_source_ref: str
    fundamental_model_ref: str
    methodology: str

    def validate(self) -> None:
        if not all(
            (
                self.peer_id,
                self.as_of,
                self.market_source_ref,
                self.fundamental_model_ref,
                self.methodology,
            )
        ):
            raise ValueError("live peer PER observation has missing identity/source/method fields")
        _parse_date(self.as_of, "peer PER as_of")
        PeerPERInput(
            self.peer_id,
            self.market_forward_per,
            self.fundamental_forward_per,
        )

    def to_engine_input(self) -> PeerPERInput:
        self.validate()
        return PeerPERInput(
            self.peer_id,
            self.market_forward_per,
            self.fundamental_forward_per,
        )


@dataclass(frozen=True)
class LivePERLevelObservation:
    level: PERLevelName
    peers: tuple[LivePeerPERObservation, ...]
    selection_rationale: str
    selection_evidence_ids: tuple[str, ...]
    economic_twin_features: tuple[str, ...]

    def validate(self) -> None:
        if not self.peers or not self.selection_rationale or not self.selection_evidence_ids:
            raise ValueError(f"{self.level.value} requires peers, rationale and Evidence IDs")
        peer_ids = tuple(peer.peer_id for peer in self.peers)
        if len(peer_ids) != len(set(peer_ids)):
            raise ValueError(f"duplicate peer inside {self.level.value}")
        for peer in self.peers:
            peer.validate()
        if self.level is PERLevelName.L4_ECONOMIC_TWINS and not self.economic_twin_features:
            raise ValueError("L4 PER Economic Twins require explicit fundamental features")

    def to_engine_level(self) -> PERLevel:
        self.validate()
        return PERLevel(self.level, tuple(peer.to_engine_input() for peer in self.peers))


@dataclass(frozen=True)
class LivePERInputs:
    target_id: str
    applicability: PERApplicability
    applicability_rationale: str
    core_assumption_keys: LivePERAssumptionKeys | None = None
    expansion: LiveExpansionPERConfig | None = None
    residual_levels: tuple[LivePERLevelObservation, ...] | None = None
    require_dcf_consistency: bool = True
    source_refs: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.target_id or not self.applicability_rationale:
            raise ValueError("live PER inputs require target_id and applicability rationale")
        if self.applicability is PERApplicability.NOT_APPLICABLE:
            if any((self.core_assumption_keys, self.expansion, self.residual_levels)):
                raise ValueError("NOT_APPLICABLE PER input cannot carry valuation inputs")
            return
        if self.core_assumption_keys is None:
            raise ValueError("applicable PER input requires core assumption keys")
        self.core_assumption_keys.validate()
        if self.expansion is not None:
            self.expansion.validate()
        if self.residual_levels is not None:
            if tuple(level.level for level in self.residual_levels) != PER_LEVEL_ORDER:
                raise ValueError("live PER residual hierarchy must be exactly L1→L2→L3→L4")
            seen: set[str] = set()
            as_of_dates: set[str] = set()
            for level in self.residual_levels:
                level.validate()
                for peer in level.peers:
                    if peer.peer_id == self.target_id:
                        raise ValueError("target company cannot enter its own PER residual peer pool")
                    if peer.peer_id in seen:
                        raise ValueError(
                            f"peer {peer.peer_id} appears in multiple PER hierarchy levels"
                        )
                    seen.add(peer.peer_id)
                    as_of_dates.add(peer.as_of[:10])
            if len(as_of_dates) != 1:
                raise ValueError("peer PER residual observations require one normalized as-of date")


@dataclass(frozen=True)
class LiveWarrantedPERStageResult:
    result: HierarchicalWarrantedPER
    core_fingerprint: EconomicAssumptionFingerprint
    expansion_fingerprint: EconomicAssumptionFingerprint | None
    source_refs: tuple[str, ...]
    selection_evidence_ids: tuple[str, ...]
    expansion_evidence_ids: tuple[str, ...]
    snapshot_hash: str


PERInputsLoader = Callable[[OrchestratorContext], LivePERInputs]


def _parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value[:10])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be ISO date") from exc


def _reject_target_market_leakage(context: OrchestratorContext) -> None:
    leaked = tuple(sorted(key for key in _FORBIDDEN_PRE_FREEZE_KEYS if key in context.data))
    if leaked:
        raise PermissionError(
            "pre-freeze PER context contains target Street/market fields: "
            + ", ".join(leaked)
        )


def _active_evidence_ids(context: OrchestratorContext) -> set[str]:
    ledger = context.data.get("evidence_ledger")
    if not isinstance(ledger, EvidenceLedger):
        raise ValueError("EvidenceLedger is required before live PER")
    return {item.id for item in ledger.active()}


def _validate_evidence_ids(label: str, evidence_ids: tuple[str, ...], active_ids: set[str]) -> None:
    unknown = tuple(sorted(set(evidence_ids) - active_ids))
    if unknown:
        raise ValueError(f"{label} references inactive/unknown Evidence IDs: {', '.join(unknown)}")


def _measure_float(
    compiled: CompiledAssumptionSet,
    *,
    scenario_id: str,
    key: str,
    unit: str,
) -> float:
    value = compiled.get(key, scenario_id).measure.convert_to(unit).amount
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"compiled assumption {scenario_id}/{key} is not finite")
    return result


def _build_assumptions_and_fingerprint(
    compiled: CompiledAssumptionSet,
    keys: LivePERAssumptionKeys,
    *,
    cost_of_equity: float,
) -> tuple[FundamentalPERAssumptions, EconomicAssumptionFingerprint]:
    keys.validate()
    growth = tuple(
        _measure_float(compiled, scenario_id=keys.scenario_id, key=key, unit="ratio")
        for key in keys.explicit_growth_rate_keys
    )
    conversion = tuple(
        _measure_float(compiled, scenario_id=keys.scenario_id, key=key, unit="ratio")
        for key in keys.fcfe_conversion_rate_keys
    )
    margin = tuple(
        _measure_float(compiled, scenario_id=keys.scenario_id, key=key, unit="ratio")
        for key in keys.margin_path_keys
    )
    reinvestment = tuple(
        _measure_float(compiled, scenario_id=keys.scenario_id, key=key, unit="ratio")
        for key in keys.reinvestment_path_keys
    )
    assumptions = FundamentalPERAssumptions(
        normalized_forward_eps=_measure_float(
            compiled,
            scenario_id=keys.scenario_id,
            key=keys.normalized_forward_eps_key,
            unit=keys.normalized_forward_eps_unit,
        ),
        explicit_growth_rates=growth,
        fcfe_conversion_rates=conversion,
        cost_of_equity=cost_of_equity,
        terminal_growth=_measure_float(
            compiled, scenario_id=keys.scenario_id, key=keys.terminal_growth_key, unit="ratio"
        ),
        terminal_roe=_measure_float(
            compiled, scenario_id=keys.scenario_id, key=keys.terminal_roe_key, unit="ratio"
        ),
    )
    fingerprint = EconomicAssumptionFingerprint(
        growth_rates=growth,
        margin_path=margin,
        reinvestment_path=reinvestment,
        growth_duration_years=len(growth),
    )
    return assumptions, fingerprint


def _stable_hash(payload: dict) -> str:
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def live_hierarchical_warranted_per_adapter(*, loader: PERInputsLoader) -> StageAdapter:
    def run(context: OrchestratorContext) -> StageExecutionResult:
        try:
            _reject_target_market_leakage(context)
            active_ids = _active_evidence_ids(context)
            compiled = context.data.get("compiled_assumption_set")
            if not isinstance(compiled, CompiledAssumptionSet):
                raise ValueError("CompiledAssumptionSet is required before live PER")
            wacc_result = context.data.get("live_wacc_result")
            if not isinstance(wacc_result, LiveWACCStageResult):
                raise ValueError("LiveWACCStageResult is required before live PER")
            inputs = loader(context)
            if not isinstance(inputs, LivePERInputs):
                raise TypeError("PER loader must return LivePERInputs")
            inputs.validate()
            if inputs.target_id != compiled.target_id:
                raise ValueError("PER target_id must match CompiledAssumptionSet target")
            if inputs.applicability is PERApplicability.NOT_APPLICABLE:
                return StageExecutionResult(
                    StageStatus.SKIPPED_NOT_APPLICABLE,
                    inputs.applicability_rationale,
                    {"warranted_per_applicable": False},
                )

            core, core_fingerprint = _build_assumptions_and_fingerprint(
                compiled,
                inputs.core_assumption_keys,
                cost_of_equity=wacc_result.wacc_result.cost_of_equity,
            )
            if inputs.require_dcf_consistency:
                dcf_fingerprint = context.data.get("dcf_assumption_fingerprint")
                if not isinstance(dcf_fingerprint, EconomicAssumptionFingerprint):
                    raise ValueError(
                        "DCF EconomicAssumptionFingerprint is required for Core PER consistency"
                    )
                validate_dcf_per_assumption_consistency(dcf_fingerprint, core_fingerprint)

            expansion_assumptions: FundamentalPERAssumptions | None = None
            expansion_fingerprint: EconomicAssumptionFingerprint | None = None
            expansion_ids: tuple[str, ...] = ()
            if inputs.expansion is not None:
                expansion_ids = inputs.expansion.committed_or_preinvested_evidence_ids
                _validate_evidence_ids("Expansion PER", expansion_ids, active_ids)
                expansion_assumptions, expansion_fingerprint = _build_assumptions_and_fingerprint(
                    compiled,
                    inputs.expansion.assumption_keys,
                    cost_of_equity=wacc_result.wacc_result.cost_of_equity,
                )

            residual_engine_levels: tuple[PERLevel, ...] | None = None
            selection_ids: tuple[str, ...] = ()
            residual_source_refs: set[str] = set()
            if inputs.residual_levels is not None:
                selection_ids = tuple(
                    dict.fromkeys(
                        evidence_id
                        for level in inputs.residual_levels
                        for evidence_id in level.selection_evidence_ids
                    )
                )
                _validate_evidence_ids("PER peer selection", selection_ids, active_ids)
                residual_engine_levels = tuple(
                    level.to_engine_level() for level in inputs.residual_levels
                )
                residual_source_refs = {
                    source
                    for level in inputs.residual_levels
                    for peer in level.peers
                    for source in (peer.market_source_ref, peer.fundamental_model_ref)
                }

            result = build_hierarchical_warranted_per(
                core,
                expansion=expansion_assumptions,
                expansion_is_committed_or_preinvested=inputs.expansion is not None,
                residual_levels=residual_engine_levels,
            )
            source_refs = tuple(sorted(set(inputs.source_refs) | residual_source_refs))
            payload = {
                "target_id": inputs.target_id,
                "assumption_set_hash": compiled.assumption_set_hash,
                "wacc_snapshot_hash": wacc_result.snapshot_hash,
                "core_fingerprint": {
                    "growth_rates": core_fingerprint.growth_rates,
                    "margin_path": core_fingerprint.margin_path,
                    "reinvestment_path": core_fingerprint.reinvestment_path,
                    "growth_duration_years": core_fingerprint.growth_duration_years,
                },
                "expansion_fingerprint": (
                    {
                        "growth_rates": expansion_fingerprint.growth_rates,
                        "margin_path": expansion_fingerprint.margin_path,
                        "reinvestment_path": expansion_fingerprint.reinvestment_path,
                        "growth_duration_years": expansion_fingerprint.growth_duration_years,
                    }
                    if expansion_fingerprint is not None
                    else None
                ),
                "selection_evidence_ids": sorted(selection_ids),
                "expansion_evidence_ids": sorted(expansion_ids),
                "source_refs": source_refs,
                "result": {
                    "core_fundamental_per": result.core_fundamental_per,
                    "expansion_adjusted_fundamental_per": result.expansion_adjusted_fundamental_per,
                    "market_realization_per": result.market_realization_per,
                    "residual_premium_multiplier": result.residual_premium_multiplier,
                },
            }
            stage_result = LiveWarrantedPERStageResult(
                result=result,
                core_fingerprint=core_fingerprint,
                expansion_fingerprint=expansion_fingerprint,
                source_refs=source_refs,
                selection_evidence_ids=selection_ids,
                expansion_evidence_ids=expansion_ids,
                snapshot_hash=_stable_hash(payload),
            )
        except Exception as exc:
            return StageExecutionResult(
                StageStatus.BLOCKED,
                f"live Hierarchical Warranted PER failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )

        return StageExecutionResult(
            StageStatus.PASS,
            "Core, Expansion and peer-residual Warranted PER layers computed from compiled economics",
            {
                "live_warranted_per_result": stage_result,
                "core_fundamental_per": result.core_fundamental_per,
                "expansion_adjusted_fundamental_per": result.expansion_adjusted_fundamental_per,
                "market_realization_per": result.market_realization_per,
                "per_assumption_fingerprint": core_fingerprint,
                "per_snapshot_hash": stage_result.snapshot_hash,
                "per_source_refs": stage_result.source_refs,
                "warranted_per_applicable": True,
            },
        )

    return run
