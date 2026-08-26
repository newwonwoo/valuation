from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{path}: expected one replacement, found {count}: {old[:100]!r}"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> int:
    runtime = ROOT / "src" / "valuation_engine" / "live_runtime.py"
    replace_once(
        runtime,
        """    method_choices: tuple[SegmentMethodChoice, ...] = ()
    capacity_core_scenario_id: str | None = None
""",
        """    method_choices: tuple[SegmentMethodChoice, ...] = ()
    additional_required_evidence: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict
    )
    capacity_core_scenario_id: str | None = None
""",
    )
    replace_once(
        runtime,
        """        self.scenario_binding_spec.validate()
        self.providers.validate()
        if self.providers.market_loader is not None and not self.market_currency:
""",
        """        self.scenario_binding_spec.validate()
        self.providers.validate()
        if not isinstance(self.additional_required_evidence, Mapping) or not all(
            isinstance(segment_id, str)
            and segment_id
            and isinstance(metrics, tuple)
            and all(isinstance(metric, str) and metric for metric in metrics)
            for segment_id, metrics in self.additional_required_evidence.items()
        ):
            raise TypeError(
                "additional_required_evidence must be a segment_id→tuple[str, ...] mapping"
            )
        if self.providers.market_loader is not None and not self.market_currency:
""",
    )
    replace_once(
        runtime,
        """        "MODULE_REQUIREMENT_PLAN": module_requirement_plan_adapter(
            registry_path=config.archetype_registry_path,
            control_requirements_path=config.archetype_control_requirements_path,
        ),
""",
        """        "MODULE_REQUIREMENT_PLAN": module_requirement_plan_adapter(
            registry_path=config.archetype_registry_path,
            control_requirements_path=config.archetype_control_requirements_path,
            additional_required_evidence=config.additional_required_evidence,
        ),
""",
    )

    sanil = ROOT / "src" / "valuation_engine" / "sanil_live_primary.py"
    replace_once(
        sanil,
        """        providers=providers,
        method_choices=(SegmentMethodChoice(SEGMENT_ID, "capacity_manufacturing", "driver_dcf", "1"),),
""",
        """        providers=providers,
        additional_required_evidence={
            SEGMENT_ID: tuple(item.metric for item in records)
        },
        method_choices=(SegmentMethodChoice(SEGMENT_ID, "capacity_manufacturing", "driver_dcf", "1"),),
""",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
