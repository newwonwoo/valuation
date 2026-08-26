from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{path}: expected one replacement, found {count}: {old[:120]!r}"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_provider() -> None:
    path = ROOT / "src" / "valuation_engine" / "sanil_live_primary.py"

    replace_once(
        path,
        "from .risk import BetaLevelName\n",
        "from .risk import BetaLevelName\nfrom .runtime_resources import runtime_registry_path\n",
    )
    replace_once(
        path,
        '''_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SNAPSHOT_PATH = _REPO_ROOT / "config" / "sanil_live_snapshot.yaml"
''',
        '''_DEFAULT_SNAPSHOT_FILENAME = "sanil_live_snapshot.yaml"
_DEFAULT_MARKET_SNAPSHOT_FILENAME = "sanil_market_snapshot.yaml"
''',
    )
    replace_once(
        path,
        '''    @property
    def market(self) -> Mapping[str, Any]:
        return self.payload["market"]

''',
        "",
    )
    replace_once(
        path,
        '''        if int(self.payload.get("version", 0)) != 1:
            raise ValueError("Sanil snapshot version must be 1")
''',
        '''        if int(self.payload.get("version", 0)) != 1:
            raise ValueError("Sanil snapshot version must be 1")
        if "market" in self.payload:
            raise ValueError(
                "pre-Freeze Sanil snapshot cannot contain target market price"
            )
''',
    )
    replace_once(
        path,
        '''def load_sanil_snapshot(path: str | Path = _DEFAULT_SNAPSHOT_PATH) -> SanilSnapshot:
    target = Path(path)
    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Sanil snapshot root must be a mapping")
    snapshot = SanilSnapshot(payload, target)
    snapshot.validate()
    return snapshot

''',
        '''def load_sanil_snapshot(path: str | Path | None = None) -> SanilSnapshot:
    target = (
        Path(path)
        if path is not None
        else runtime_registry_path(_DEFAULT_SNAPSHOT_FILENAME)
    )
    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError("Sanil snapshot root must be a mapping")
    snapshot = SanilSnapshot(payload, target)
    snapshot.validate()
    return snapshot


@dataclass(frozen=True)
class SanilMarketSnapshot:
    target_id: str
    ticker: str
    price: float
    currency: str
    as_of: str
    source_ref: str
    path: Path

    def validate(self) -> None:
        if self.target_id != TARGET_ID or self.ticker != TICKER:
            raise ValueError("Sanil market snapshot identity drifted")
        if not self.price > 0 or self.currency != "KRW":
            raise ValueError("Sanil market snapshot price/currency is invalid")
        if not self.as_of or not self.source_ref.startswith("https://"):
            raise ValueError("Sanil market snapshot requires date and public source")


def load_sanil_market_snapshot(
    path: str | Path | None = None,
) -> SanilMarketSnapshot:
    target = (
        Path(path)
        if path is not None
        else runtime_registry_path(_DEFAULT_MARKET_SNAPSHOT_FILENAME)
    )
    payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or int(payload.get("version", 0)) != 1:
        raise ValueError("Sanil market snapshot root/version is invalid")
    snapshot = SanilMarketSnapshot(
        target_id=str(payload["target_id"]),
        ticker=str(payload["ticker"]),
        price=float(payload["price"]),
        currency=str(payload["currency"]),
        as_of=str(payload["as_of"]),
        source_ref=str(payload["source_ref"]),
        path=target,
    )
    snapshot.validate()
    return snapshot

''',
    )

    replace_once(
        path,
        '''    def collect(_: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        return EvidenceCollectionBatch(
            source_id="KR_OPENDART",
            checked_at=snapshot.cutoff,
            records=records,
            source_fingerprint=fingerprint,
            document_ids=tuple(str(item["document_id"]) for item in snapshot.sources.values()),
        )
''',
        '''    records_by_metric = {item.metric: item for item in records}
    if len(records_by_metric) != len(records):
        raise ValueError("Sanil collector metrics must be unique")

    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:
        unknown = tuple(
            metric
            for metric in request.required_metrics
            if metric not in records_by_metric
        )
        if unknown:
            raise ValueError(
                "Sanil collector received unsupported requested metrics: "
                + ", ".join(unknown)
            )
        selected = tuple(
            records_by_metric[metric]
            for metric in dict.fromkeys(request.required_metrics)
        )
        return EvidenceCollectionBatch(
            source_id="KR_OPENDART",
            checked_at=snapshot.cutoff,
            records=selected,
            source_fingerprint=fingerprint,
            document_ids=tuple(
                str(item["document_id"]) for item in snapshot.sources.values()
            ),
        )
''',
    )

    scanner_start = '''def _scanner_runner(context) -> ScannerFinding:
    evidence = _evidence_id("backlog")
    return ScannerFinding(
        scanner_id=context.scanner_id,
        status=ScannerFindingStatus.PASS,
        summary=f"{context.scanner_id} checked against the frozen Sanil filing/IR snapshot",
        evidence_ids=(evidence,),
        context_only=True,
    )
'''
    scanner_replacement = '''def _scanner_runner(context) -> ScannerFinding:
    ledger = context.ledger
    scanner_id = context.scanner_id

    def value(metric: str):
        return ledger.get(_evidence_id(metric)).value

    if scanner_id == "BACKLOG_QUALITY":
        book_to_bill = float(value("book_to_bill"))
        backlog = float(value("backlog"))
        status = (
            ScannerFindingStatus.PASS
            if backlog > 0 and book_to_bill >= 1.0
            else ScannerFindingStatus.WARNING
        )
        return ScannerFinding(
            scanner_id=scanner_id,
            status=status,
            summary=(
                f"backlog={backlog:.1f} KRWbn and book-to-bill={book_to_bill:.2f}; "
                "conversion remains the operating hinge"
            ),
            evidence_ids=(
                _evidence_id("orders"),
                _evidence_id("backlog"),
                _evidence_id("book_to_bill"),
                _evidence_id("backlog_conversion"),
            ),
            verification_requests=("next filing backlog conversion and ageing",),
            economic_path_ids=("sanil:backlog_conversion",),
        )

    if scanner_id == "CANCELLATION_TERMS":
        return ScannerFinding(
            scanner_id=scanner_id,
            status=ScannerFindingStatus.WARNING,
            summary=(
                "contract-specific cancellation terms are identified, but no normalized "
                "company cancellation-rate series is disclosed"
            ),
            evidence_ids=(
                _evidence_id("cancellation_terms"),
                _evidence_id("cancellation_rate"),
            ),
            verification_requests=("obtain order cancellation and backlog ageing disclosure",),
            economic_path_ids=("sanil:backlog_conversion",),
        )

    if scanner_id == "CUSTOMER_ADVANCE_FUNDING":
        liabilities = float(value("contract_liabilities"))
        return ScannerFinding(
            scanner_id=scanner_id,
            status=ScannerFindingStatus.WARNING,
            summary=(
                f"normalized contract-liability evidence is {liabilities:.1f} KRWbn in the "
                "frozen pack; backlog is not treated as automatic WACC relief"
            ),
            evidence_ids=(
                _evidence_id("contract_liabilities"),
                _evidence_id("backlog"),
            ),
            verification_requests=("reconcile customer advances and contract liabilities",),
            economic_path_ids=("funding:backlog_to_buyer_cash_flow",),
        )

    if scanner_id == "CAPACITY_RAMP":
        active = bool(value("expansion_land_control")) and bool(
            value("expansion_capacity_committed")
        ) and not bool(value("expansion_cancelled"))
        return ScannerFinding(
            scanner_id=scanner_id,
            status=(
                ScannerFindingStatus.PASS
                if active
                else ScannerFindingStatus.WARNING
            ),
            summary=(
                "land control, committed expansion and a dated ramp are present"
                if active
                else "capacity-ramp commitment is incomplete or cancelled"
            ),
            evidence_ids=(
                _evidence_id("expansion_land_control"),
                _evidence_id("expansion_capacity_committed"),
                _evidence_id("expansion_ramp_date"),
                _evidence_id("expansion_cancelled"),
            ),
            verification_requests=("next official factory-ramp milestone",),
            economic_path_ids=(CAPACITY_PATH_ROOT,),
            final_output_refs=("capacity_commitment_assessment",),
        )

    if scanner_id == "QUALIFICATION":
        return ScannerFinding(
            scanner_id=scanner_id,
            status=ScannerFindingStatus.WARNING,
            summary=(
                "orders and backlog evidence buyer acceptance, but customer-by-customer "
                "qualification status is not separately disclosed"
            ),
            evidence_ids=(_evidence_id("orders"), _evidence_id("backlog")),
            verification_requests=("customer qualification and concentration update",),
            economic_path_ids=("sanil:backlog_conversion",),
        )

    if scanner_id == "UTILIZATION":
        utilization = float(value("utilization"))
        return ScannerFinding(
            scanner_id=scanner_id,
            status=(
                ScannerFindingStatus.WARNING
                if utilization >= 0.85
                else ScannerFindingStatus.PASS
            ),
            summary=(
                f"reported utilization is {utilization:.1%}; production capacity, not demand, "
                "is the near-term conversion bottleneck"
            ),
            evidence_ids=(
                _evidence_id("utilization"),
                _evidence_id("effective_capacity"),
            ),
            verification_requests=("effective capacity and utilization after ramp",),
            economic_path_ids=(CAPACITY_PATH_ROOT,),
        )

    if scanner_id == "CAPEX_EXECUTION":
        capex = float(value("expansion_capex_committed"))
        equipment = bool(value("expansion_equipment_commitment"))
        passed = capex > 0 and equipment
        return ScannerFinding(
            scanner_id=scanner_id,
            status=(
                ScannerFindingStatus.PASS
                if passed
                else ScannerFindingStatus.WARNING
            ),
            summary=(
                f"committed expansion CAPEX is {capex:.1f} KRWbn and equipment commitment "
                f"is {'confirmed' if equipment else 'unconfirmed'}"
            ),
            evidence_ids=(
                _evidence_id("expansion_capex_committed"),
                _evidence_id("expansion_equipment_commitment"),
                _evidence_id("expansion_ramp_date"),
            ),
            verification_requests=("CAPEX spend-to-date and commissioning evidence",),
            economic_path_ids=(f"{CAPACITY_PATH_ROOT}:capex",),
        )

    raise ValueError(f"unsupported Sanil scanner: {scanner_id}")
'''
    replace_once(path, scanner_start, scanner_replacement)

    replace_once(
        path,
        '''        rows.extend(
            (
                _record(snapshot, metric=f"model_{scenario.lower()}_ownership", value=1.0, unit="ratio", source_key=source, source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING, effective_date=snapshot.cutoff, notes="Mechanical ownership input."),
''',
        '''        rows.append(
            _record(
                snapshot,
                metric=f"model_{scenario.lower()}_expansion_capex",
                value=inputs["expansion_capex_krw_billion"],
                unit="KRW_billion",
                source_key=source,
                source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,
                effective_date=snapshot.cutoff,
                confidence=(0.65 if scenario != "Core" else 0.75),
                notes=(
                    "Scenario cash-outflow input anchored to committed company CAPEX; "
                    "the DCF deducts it in the explicit forecast."
                ),
            )
        )
        rows.extend(
            (
                _record(snapshot, metric=f"model_{scenario.lower()}_ownership", value=1.0, unit="ratio", source_key=source, source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING, effective_date=snapshot.cutoff, notes="Mechanical ownership input."),
''',
    )

    replace_once(
        path,
        '''            elif scenario == "Core" and year == 2:
                bridge_id = "B:SANIL:CAPEX"
                economic_path = f"{CAPACITY_PATH_ROOT}:capex"
                evidence_ids = (_evidence_id("expansion_capex_committed"), _evidence_id(metric))
                direction = Direction.UP
                old_value = 0.0
''',
        "",
    )
    replace_once(
        path,
        '''            )
        for key in ("terminal_growth", "terminal_roic"):
''',
        '''            )

        capex_metric = f"model_{scenario.lower()}_expansion_capex"
        capex_value = float(context.ledger.get(_evidence_id(capex_metric)).value)
        capex_bridge_id = f"B:SANIL:{scenario}:expansion_capex"
        capex_path = f"sanil:{scenario.lower()}:expansion_capex"
        capex_evidence_ids = (_evidence_id(capex_metric),)
        capex_hypothesis = hypothesis_id
        if scenario == "Core":
            capex_bridge_id = "B:SANIL:CAPEX"
            capex_path = f"{CAPACITY_PATH_ROOT}:capex"
            capex_evidence_ids = (
                _evidence_id("expansion_capex_committed"),
                _evidence_id(capex_metric),
            )
            capex_hypothesis = "H:SANIL:CAPACITY"
        drafts.append(
            BridgeDraft(
                assumption_key="expansion_capex",
                scenario_id=scenario,
                bridge=_bridge(
                    bridge_id=capex_bridge_id,
                    evidence_ids=capex_evidence_ids,
                    hypothesis_id=capex_hypothesis,
                    affected_variable=AffectedVariable.QUANTITY,
                    direction=Direction.UP,
                    old_value=0.0,
                    new_value=capex_value,
                    unit="KRW_billion",
                    economic_path_id=capex_path,
                    rationale=(
                        "committed expansion CAPEX is a separate explicit cash outflow, "
                        "not a label attached to an FCFF assumption"
                    ),
                ),
                canonical_unit="KRW_billion",
                transform_id="identity_observation",
                input_evidence_ids=(_evidence_id(capex_metric),),
                min_value="0",
            )
        )

        for key in ("terminal_growth", "terminal_roic"):
''',
    )

    replace_once(
        path,
        '''def build_sanil_live_primary_config(
    state_root: str | Path,
    *,
    run_id: str = "SANIL-062040-20260825",
    snapshot_path: str | Path = _DEFAULT_SNAPSHOT_PATH,
) -> LivePrimaryRuntimeConfig:
    snapshot = load_sanil_snapshot(snapshot_path)
''',
        '''def build_sanil_live_primary_config(
    state_root: str | Path,
    *,
    run_id: str = "SANIL-062040-20260825",
    snapshot_path: str | Path | None = None,
    market_snapshot_path: str | Path | None = None,
) -> LivePrimaryRuntimeConfig:
    snapshot = load_sanil_snapshot(snapshot_path)
''',
    )

    replace_once(
        path,
        '''    providers = LivePrimaryProviders(
''',
        '''    def market_loader() -> MarketObservation:
        market = load_sanil_market_snapshot(market_snapshot_path)
        return MarketObservation(
            market.price,
            market.as_of,
            market.source_ref,
        )

    providers = LivePrimaryProviders(
''',
    )
    replace_once(
        path,
        '''        evaluator_registry_loader=live_fcff_dcf_registry_loader(
            registrations=(LiveDCFRegistration("capacity_manufacturing", "driver_dcf", "1", FORECAST_YEARS),),
            include_default_normalized_multiples=True,
        ),
''',
        '''        evaluator_registry_loader=live_fcff_dcf_registry_loader(
            registrations=(
                LiveDCFRegistration(
                    "capacity_manufacturing",
                    "driver_dcf",
                    "1",
                    FORECAST_YEARS,
                    expansion_capex_key="expansion_capex",
                    expansion_capex_year=2,
                ),
            ),
            include_default_normalized_multiples=True,
        ),
''',
    )
    replace_once(
        path,
        '''        market_loader=lambda: MarketObservation(float(snapshot.market["price"]), str(snapshot.market["as_of"]), str(snapshot.market["source_ref"])),
''',
        '''        market_loader=market_loader,
''',
    )
    replace_once(
        path,
        '''                *(f"fcff_year_{year}" for year in range(1, FORECAST_YEARS + 1)),
                "terminal_growth",
''',
        '''                *(f"fcff_year_{year}" for year in range(1, FORECAST_YEARS + 1)),
                "expansion_capex",
                "terminal_growth",
''',
    )
    replace_once(
        path,
        '''def run_sanil_live_primary(
    state_root: str | Path,
    *,
    run_id: str = "SANIL-062040-20260825",
    snapshot_path: str | Path = _DEFAULT_SNAPSHOT_PATH,
):
''',
        '''def run_sanil_live_primary(
    state_root: str | Path,
    *,
    run_id: str = "SANIL-062040-20260825",
    snapshot_path: str | Path | None = None,
    market_snapshot_path: str | Path | None = None,
):
''',
    )
    replace_once(
        path,
        '''            snapshot_path=snapshot_path,
        )
''',
        '''            snapshot_path=snapshot_path,
            market_snapshot_path=market_snapshot_path,
        )
''',
    )


def patch_snapshot() -> None:
    path = ROOT / "config" / "sanil_live_snapshot.yaml"
    text = path.read_text(encoding="utf-8")
    marker = "\nmarket:\n  price: 169300\n  as_of: '2026-08-25'\n  source_ref: https://data.krx.co.kr/\n"
    if marker not in text:
        raise RuntimeError("Sanil pre-Freeze snapshot market block not found")
    path.write_text(text.replace(marker, "\n", 1), encoding="utf-8")


def patch_report_runner() -> None:
    path = ROOT / "scripts" / "run_sanil_live_primary.py"
    replace_once(
        path,
        '''    load_sanil_snapshot,
    run_sanil_live_primary,
''',
        '''    load_sanil_market_snapshot,
    load_sanil_snapshot,
    run_sanil_live_primary,
''',
    )
    replace_once(
        path,
        '''    assessment = result.data["capacity_commitment_assessment"]
    market = result.data.get("market_comparison")
''',
        '''    assessment = result.data["capacity_commitment_assessment"]
    market_snapshot = load_sanil_market_snapshot()
    market = result.data.get("market_comparison")
''',
    )
    replace_once(
        path,
        '''        else snapshot.market["price"]
''',
        '''        else market_snapshot.price
''',
    )
    replace_once(
        path,
        '''- 현재가: {snapshot.market['source_ref']}
''',
        '''- 현재가: {market_snapshot.source_ref}
''',
    )


def patch_installed_wheel_validation() -> None:
    path = ROOT / "scripts" / "validate_installed_wheel.py"
    replace_once(
        path,
        '''from valuation_engine.method_capabilities import load_default_method_capability_registry
''',
        '''from valuation_engine.method_capabilities import load_default_method_capability_registry
from valuation_engine.sanil_live_primary import (
    load_sanil_market_snapshot,
    load_sanil_snapshot,
)
''',
    )
    replace_once(
        path,
        '''    "probability_calibration_policy.yaml",
)
''',
        '''    "probability_calibration_policy.yaml",
    "sanil_live_snapshot.yaml",
    "sanil_market_snapshot.yaml",
)
''',
    )
    replace_once(
        path,
        '''    if not unit_registry.units:
        raise SystemExit("installed wheel Unit Contract registry is empty")

    print(
''',
        '''    if not unit_registry.units:
        raise SystemExit("installed wheel Unit Contract registry is empty")

    sanil = load_sanil_snapshot()
    market = load_sanil_market_snapshot()
    if sanil.company["ticker"] != "062040" or market.ticker != "062040":
        raise SystemExit("installed wheel Sanil runtime resources are invalid")

    print(
''',
    )


def patch_tests() -> None:
    path = ROOT / "tests" / "test_sanil_live_primary.py"
    replace_once(
        path,
        '''    build_sanil_live_primary_config,
    load_sanil_snapshot,
''',
        '''    build_sanil_live_primary_config,
    load_sanil_market_snapshot,
    load_sanil_snapshot,
''',
    )
    replace_once(
        path,
        '''    assert tuple(snapshot.scenarios) == ("Down", "Core", "Bull")
''',
        '''    assert tuple(snapshot.scenarios) == ("Down", "Core", "Bull")
    assert "market" not in snapshot.payload
    market = load_sanil_market_snapshot()
    assert market.price == 169300
    assert market.currency == "KRW"
''',
    )
    replace_once(
        path,
        '''    assert (
        ledger.get("E:SANIL:model_core_fcff_year_1").source_layer
        is EvidenceSourceLayer.ANALYST_UNDERWRITING
    )
''',
        '''    assert (
        ledger.get("E:SANIL:model_core_fcff_year_1").source_layer
        is EvidenceSourceLayer.ANALYST_UNDERWRITING
    )
    assert (
        ledger.get("E:SANIL:model_core_expansion_capex").source_layer
        is EvidenceSourceLayer.ANALYST_UNDERWRITING
    )
''',
    )
    replace_once(
        path,
        '''    assert valuation.expected_value_per_share is None

    assessment = result.data["capacity_commitment_assessment"]
''',
        '''    assert valuation.expected_value_per_share is None
    core = next(item for item in valuation.scenarios if item.scenario_id == "Core")
    assert f"capacity_project:SANIL_SECOND_FACTORY_RAMP:capex" in core.economic_path_ids
    compiled = result.data["compiled_assumption_set"]
    assert compiled.get("expansion_capex", "Core").measure.amount == 42

    findings = {
        item.scanner_id: item for item in result.data["scanner_findings"]
    }
    assert findings["BACKLOG_QUALITY"].evidence_ids != findings["UTILIZATION"].evidence_ids
    assert findings["CAPEX_EXECUTION"].economic_path_ids == (
        "capacity_project:SANIL_SECOND_FACTORY_RAMP:capex",
    )
    assert findings["CANCELLATION_TERMS"].status is not None

    assessment = result.data["capacity_commitment_assessment"]
''',
    )
    path.write_text(
        path.read_text(encoding="utf-8")
        + '''\n\ndef test_sanil_collector_returns_only_requested_metrics():
    from valuation_engine.evidence_collection import EvidenceCollectionRequest
    from valuation_engine.sanil_live_primary import _primary_collector

    snapshot = load_sanil_snapshot(SNAPSHOT)
    batch = _primary_collector(snapshot)(
        EvidenceCollectionRequest(TARGET_ID, ("backlog", "utilization"))
    )
    assert tuple(item.metric for item in batch.records) == (
        "backlog",
        "utilization",
    )
''',
        encoding="utf-8",
    )


def main() -> int:
    patch_provider()
    patch_snapshot()
    patch_report_runner()
    patch_installed_wheel_validation()
    patch_tests()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
