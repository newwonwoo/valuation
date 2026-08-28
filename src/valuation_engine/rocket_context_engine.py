from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Mapping

from .module_plan import ModuleRequirementPlan
from .orchestrator import OrchestratorContext, StageAdapter, StageExecutionResult
from .scanner_runtime import ScannerRunner, live_rocket_insight_dispatch_adapter


@dataclass(frozen=True)
class RocketContextPlan:
    """Deterministic RocketTesla scanner deployment contract.

    The plan is compiled from the typed Industry-DNA ModuleRequirementPlan. An
    LLM may consume scanner findings later, but it cannot add, remove, reorder or
    silently skip scanners.
    """

    target_id: str
    mandatory_scanners: tuple[str, ...]
    optional_scanners: tuple[str, ...]
    active_optional_scanners: tuple[str, ...]
    ordered_scanners: tuple[str, ...]
    plan_hash: str

    def validate(self) -> None:
        if not self.target_id or not self.mandatory_scanners or not self.plan_hash:
            raise ValueError("RocketTesla context plan identity is incomplete")
        if len(self.mandatory_scanners) != len(set(self.mandatory_scanners)):
            raise ValueError("RocketTesla mandatory scanner plan contains duplicates")
        if len(self.optional_scanners) != len(set(self.optional_scanners)):
            raise ValueError("RocketTesla optional scanner plan contains duplicates")
        if set(self.mandatory_scanners).intersection(self.optional_scanners):
            raise ValueError("RocketTesla scanner cannot be both mandatory and optional")
        if not set(self.active_optional_scanners).issubset(self.optional_scanners):
            raise ValueError("RocketTesla active optional scanner is outside canonical plan")
        expected = tuple(
            dict.fromkeys((*self.mandatory_scanners, *self.active_optional_scanners))
        )
        if self.ordered_scanners != expected:
            raise ValueError("RocketTesla scanner execution order drift")
        if self.plan_hash != _plan_hash(
            target_id=self.target_id,
            mandatory=self.mandatory_scanners,
            optional=self.optional_scanners,
            active_optional=self.active_optional_scanners,
            ordered=self.ordered_scanners,
        ):
            raise PermissionError("RocketTesla context plan hash mismatch")


def build_rocket_context_plan(
    *,
    target_id: str,
    module_plan: ModuleRequirementPlan,
    active_optional_scanners: tuple[str, ...] = (),
) -> RocketContextPlan:
    module_plan.validate()
    if not target_id:
        raise ValueError("RocketTesla context plan requires target_id")
    if not isinstance(active_optional_scanners, tuple) or not all(
        isinstance(item, str) and item for item in active_optional_scanners
    ):
        raise TypeError("active_optional_scanners must be a tuple[str, ...]")
    active = tuple(dict.fromkeys(active_optional_scanners))
    undeclared = tuple(
        item for item in active if item not in module_plan.optional_scanners
    )
    if undeclared:
        raise PermissionError(
            "RocketTesla optional activation is outside canonical Industry-DNA plan: "
            + ", ".join(undeclared)
        )
    mandatory = tuple(module_plan.mandatory_scanners)
    optional = tuple(module_plan.optional_scanners)
    ordered = tuple(dict.fromkeys((*mandatory, *active)))
    result = RocketContextPlan(
        target_id=target_id,
        mandatory_scanners=mandatory,
        optional_scanners=optional,
        active_optional_scanners=active,
        ordered_scanners=ordered,
        plan_hash=_plan_hash(
            target_id=target_id,
            mandatory=mandatory,
            optional=optional,
            active_optional=active,
            ordered=ordered,
        ),
    )
    result.validate()
    return result


def strict_rocket_insight_dispatch_adapter(
    *,
    runners: Mapping[str, ScannerRunner],
) -> StageAdapter:
    """Run RocketTesla scanners only from a deterministic context plan."""

    inner = live_rocket_insight_dispatch_adapter(runners=runners)

    def run(context: OrchestratorContext) -> StageExecutionResult:
        target_id = context.data.get("target_id")
        module_plan = context.data.get("module_requirement_plan")
        active_optional = context.data.get("active_optional_scanners", ())
        if not isinstance(target_id, str) or not target_id:
            return StageExecutionResult(
                status=__import__("valuation_engine.control_plane", fromlist=["StageStatus"]).StageStatus.RECOVERY_REQUIRED,
                rationale="target_id missing before RocketTesla Context Engine",
                blocking=True,
            )
        if not isinstance(module_plan, ModuleRequirementPlan):
            return StageExecutionResult(
                status=__import__("valuation_engine.control_plane", fromlist=["StageStatus"]).StageStatus.RECOVERY_REQUIRED,
                rationale="ModuleRequirementPlan missing before RocketTesla Context Engine",
                blocking=True,
            )
        try:
            plan = build_rocket_context_plan(
                target_id=target_id,
                module_plan=module_plan,
                active_optional_scanners=(
                    active_optional if isinstance(active_optional, tuple) else ()
                ),
            )
        except Exception as exc:
            return StageExecutionResult(
                status=__import__("valuation_engine.control_plane", fromlist=["StageStatus"]).StageStatus.BLOCKED,
                rationale=f"RocketTesla Context Engine plan failed: {type(exc).__name__}: {exc}",
                blocking=True,
            )
        data = dict(context.data)
        data["mandatory_scanners"] = plan.mandatory_scanners
        data["active_optional_scanners"] = plan.active_optional_scanners
        result = inner(
            OrchestratorContext(
                context.run_id,
                context.execution_mode,
                data,
                context.stage_traces,
                context.freeze_token,
            )
        )
        outputs = dict(result.outputs)
        outputs["rocket_context_plan"] = plan
        outputs["rocket_context_plan_hash"] = plan.plan_hash
        outputs["rocket_context_scanner_ids"] = plan.ordered_scanners
        return StageExecutionResult(
            result.status,
            "RocketTesla Context Engine locked scanner routing | " + result.rationale,
            outputs,
            result.blocking,
        )

    return run


def _plan_hash(
    *,
    target_id: str,
    mandatory: tuple[str, ...],
    optional: tuple[str, ...],
    active_optional: tuple[str, ...],
    ordered: tuple[str, ...],
) -> str:
    payload = {
        "contract": "rocket_context_plan/v1",
        "target_id": target_id,
        "mandatory_scanners": mandatory,
        "optional_scanners": optional,
        "active_optional_scanners": active_optional,
        "ordered_scanners": ordered,
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
