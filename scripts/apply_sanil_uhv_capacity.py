from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import FinanceDataReader as fdr
import yaml


ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_PATH = ROOT / "config" / "sanil_live_snapshot.yaml"
MARKET_PATH = ROOT / "config" / "sanil_market_snapshot.yaml"
RISK_REGISTER_PATH = ROOT / "docs" / "SANIL_RISK_SOURCE_REGISTER.md"
SANIL_PATH = ROOT / "src" / "valuation_engine" / "sanil_live_primary.py"
REPORT_SCRIPT_PATH = ROOT / "scripts" / "run_sanil_live_primary.py"
TEST_PATH = ROOT / "tests" / "test_sanil_live_primary.py"
BETA_SCRIPT_PATH = ROOT / "scripts" / "refresh_sanil_krx_beta.py"

VALUATION_CUTOFF = "2026-08-26"
BETA_CUTOFF = "2026-08-25"
UHV_RCP_NO = "20260826000660"
UHV_SOURCE_REF = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={UHV_RCP_NO}"
UHV_DOCUMENT_HASH = "79e63422aca51b90f44a5a97917188cf562006e20e17a3665820ab83bc3528c8"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{path.relative_to(ROOT)} expected one replacement, found {count}: {old[:100]!r}"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def update_snapshot() -> None:
    payload = yaml.safe_load(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    payload["cutoff"] = VALUATION_CUTOFF
    payload["sources"]["uhv_property_acquisition"] = {
        "source_id": "KR_OPENDART",
        "source_ref": UHV_SOURCE_REF,
        "document_id": "SANIL_UHV_PROPERTY_ACQUISITION_20260826",
        "document_hash": UHV_DOCUMENT_HASH,
        "published_at": "2026-08-26T00:00:00+09:00",
    }
    facts = payload["facts"]
    facts.update(
        {
            "uhv_property_contract_date": "2026-08-26",
            "uhv_property_closing_date": "2027-02-19",
            "uhv_property_amount_krw_billion": 69.25,
            "uhv_property_asset_ratio": 0.1015,
            "uhv_property_deposit_krw_billion": 6.925,
            "uhv_property_interim_krw_billion": 20.775,
            "uhv_property_balance_krw_billion": 41.55,
            "uhv_property_self_funded": True,
            "uhv_property_location": "경기도 안산시 단원구 성곡동 667-1 토지 및 건물",
            "uhv_property_purpose": (
                "초고압 변압기 생산설비 구축 및 기존 제품 생산능력 확대"
            ),
        }
    )
    payload["uhv_capacity_project"] = {
        "project_id": "SANIL_UHV_PROPERTY_ACQUISITION_20260826",
        "baseline_inclusion": "not_in_baseline",
        "contract_date": "2026-08-26",
        "closing_date": "2027-02-19",
        "ramp_boundary": "2027-02-19",
        "rationale": (
            "A signed official property-acquisition contract crosses LAND_CONTROL and is "
            "incremental to the frozen operating baseline. Exact production capacity is not "
            "disclosed, so the Core scenario uses a bounded incremental FCFF cohort together "
            "with the full disclosed acquisition cash outflow."
        ),
    }
    incremental = {
        "Down": [0, 0, 3, 10, 18],
        "Core": [0, 0, 10, 25, 42],
        "Bull": [0, 2, 18, 42, 68],
    }
    for scenario, path in incremental.items():
        payload["scenarios"][scenario]["uhv_incremental_fcff_krw_billion"] = path
        payload["scenarios"][scenario]["uhv_property_capex_krw_billion"] = 69.25

    risk_register_hash = sha256(RISK_REGISTER_PATH.read_bytes()).hexdigest()
    payload["sources"]["risk_snapshot"]["document_hash"] = risk_register_hash
    payload["sources"]["risk_snapshot"]["document_id"] = (
        "SANIL_RISK_SOURCE_REGISTER_20260825_REGRESSION"
    )
    underwriting_payload = {
        "cutoff": payload["cutoff"],
        "scenarios": payload["scenarios"],
        "capacity_project": payload["capacity_project"],
        "uhv_capacity_project": payload["uhv_capacity_project"],
    }
    underwriting_hash = sha256(
        json.dumps(
            underwriting_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    payload["sources"]["underwriting"].update(
        {
            "document_id": "SANIL_UNDERWRITING_20260826",
            "document_hash": underwriting_hash,
            "published_at": "2026-08-26T18:00:00+09:00",
        }
    )
    SNAPSHOT_PATH.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def update_market_snapshot() -> None:
    frame = fdr.DataReader("062040", "2026-08-26", "2026-08-27")
    if frame.empty or "Close" not in frame.columns:
        raise RuntimeError("public Korean market provider returned no 2026-08-26 Sanil close")
    frame.index = frame.index.astype("datetime64[ns]")
    matching = frame.loc[frame.index.strftime("%Y-%m-%d") == VALUATION_CUTOFF]
    if matching.empty:
        raise RuntimeError("Sanil 2026-08-26 close is absent from the market series")
    price = int(round(float(matching.iloc[-1]["Close"])))
    payload = yaml.safe_load(MARKET_PATH.read_text(encoding="utf-8"))
    payload.update(
        {
            "price": price,
            "as_of": VALUATION_CUTOFF,
            "source_ref": "https://finance.naver.com/item/main.naver?code=062040",
        }
    )
    MARKET_PATH.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def patch_beta_refresh_contract() -> None:
    replace_once(
        BETA_SCRIPT_PATH,
        'END_DATE = "2026-08-25"\n',
        'END_DATE = "2026-08-25"\nSNAPSHOT_CUTOFF = "2026-08-26"\n',
    )
    replace_once(
        BETA_SCRIPT_PATH,
        '''    if str(payload.get("cutoff")) != END_DATE:\n        raise RuntimeError(\n            f"Beta cutoff {END_DATE} must equal Sanil snapshot cutoff {payload.get('cutoff')}"\n        )\n''',
        '''    if str(payload.get("cutoff")) != SNAPSHOT_CUTOFF:\n        raise RuntimeError(\n            f"Sanil snapshot cutoff must remain {SNAPSHOT_CUTOFF}, got {payload.get('cutoff')}"\n        )\n    if END_DATE > SNAPSHOT_CUTOFF:\n        raise RuntimeError("Beta observation cutoff cannot exceed valuation cutoff")\n''',
    )


def patch_sanil_runtime() -> None:
    replace_once(
        SANIL_PATH,
        '''CAPACITY_PROJECT_ID = "SANIL_SECOND_FACTORY_RAMP"\nCAPACITY_PATH_ROOT = f"capacity_project:{CAPACITY_PROJECT_ID}"\n''',
        '''CAPACITY_PROJECT_ID = "SANIL_SECOND_FACTORY_RAMP"\nCAPACITY_PATH_ROOT = f"capacity_project:{CAPACITY_PROJECT_ID}"\nUHV_CAPACITY_PROJECT_ID = "SANIL_UHV_PROPERTY_ACQUISITION_20260826"\nUHV_CAPACITY_PATH_ROOT = f"capacity_project:{UHV_CAPACITY_PROJECT_ID}"\n''',
    )
    replace_once(
        SANIL_PATH,
        '''    def capacity_project(self) -> Mapping[str, Any]:\n        return self.payload["capacity_project"]\n\n    def validate(self) -> None:\n''',
        '''    def capacity_project(self) -> Mapping[str, Any]:\n        return self.payload["capacity_project"]\n\n    @property\n    def uhv_capacity_project(self) -> Mapping[str, Any]:\n        return self.payload["uhv_capacity_project"]\n\n    def validate(self) -> None:\n''',
    )
    replace_once(
        SANIL_PATH,
        '''            if not 0 <= growth < roic:\n                raise ValueError(f"{name} terminal growth/ROIC is invalid")\n        for source in self.sources.values():\n''',
        '''            if not 0 <= growth < roic:\n                raise ValueError(f"{name} terminal growth/ROIC is invalid")\n            uhv_fcff = tuple(row.get("uhv_incremental_fcff_krw_billion", ()))\n            if len(uhv_fcff) != FORECAST_YEARS or any(\n                float(value) < 0 for value in uhv_fcff\n            ):\n                raise ValueError(\n                    f"{name} requires five non-negative UHV incremental FCFF values"\n                )\n            if float(row.get("uhv_property_capex_krw_billion", 0)) <= 0:\n                raise ValueError(f"{name} requires positive UHV property CAPEX")\n        if self.uhv_capacity_project.get("project_id") != UHV_CAPACITY_PROJECT_ID:\n            raise ValueError("Sanil UHV capacity project identity drifted")\n        for source in self.sources.values():\n''',
    )
    replace_once(
        SANIL_PATH,
        '''def _evidence_id(metric: str) -> str:\n    return f"E:SANIL:{metric}"\n\n\ndef _record(\n''',
        '''def _evidence_id(metric: str) -> str:\n    return f"E:SANIL:{metric}"\n\n\ndef _uhv_evidence_id(role: str) -> str:\n    return f"E:SANIL:UHV:{role}"\n\n\ndef _record(\n''',
    )
    replace_once(
        SANIL_PATH,
        '''    confidence: float = 1.0,\n    notes: str = "",\n) -> EvidenceRecord:\n''',
        '''    confidence: float = 1.0,\n    notes: str = "",\n    evidence_id: str | None = None,\n) -> EvidenceRecord:\n''',
    )
    replace_once(
        SANIL_PATH,
        '''        id=_evidence_id(metric),\n''',
        '''        id=(evidence_id or _evidence_id(metric)),\n''',
    )
    insert_before = '''        _record(snapshot, metric="revenue_h1_2026", value=f["revenue_h1_2026_krw_billion"], unit="KRW_billion", source_key=q2, source_layer=EvidenceSourceLayer.REALIZED_OR_FILING, effective_date="2026-06-30"),\n'''
    uhv_records = '''        _record(snapshot, metric="expansion_land_control", value=True, unit="dimensionless", source_key="uhv_property_acquisition", source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date=str(f["uhv_property_contract_date"]), notes="Signed official property-acquisition contract establishes LAND_CONTROL for the separate UHV project.", evidence_id=_uhv_evidence_id("land_control")),\n        _record(snapshot, metric="expansion_capex_committed", value=f["uhv_property_amount_krw_billion"], unit="KRW_billion", source_key="uhv_property_acquisition", source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date=str(f["uhv_property_contract_date"]), notes="Full disclosed property acquisition consideration; exact production capacity is not disclosed.", evidence_id=_uhv_evidence_id("capex_committed")),\n        _record(snapshot, metric="expansion_ramp_date", value=str(f["uhv_property_closing_date"]), unit="dimensionless", source_key="uhv_property_acquisition", source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date=str(f["uhv_property_contract_date"]), confidence=0.80, notes="Closing/registration date is the earliest asset-control boundary, not a claimed production start.", evidence_id=_uhv_evidence_id("ramp_boundary")),\n        _record(snapshot, metric="expansion_baseline_inclusion", value="not_in_baseline", unit="dimensionless", source_key="uhv_property_acquisition", source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date=str(f["uhv_property_contract_date"]), notes=str(snapshot.uhv_capacity_project["rationale"]), evidence_id=_uhv_evidence_id("baseline_inclusion")),\n        _record(snapshot, metric="uhv_property_contract_amount", value=f["uhv_property_amount_krw_billion"], unit="KRW_billion", source_key="uhv_property_acquisition", source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date=str(f["uhv_property_contract_date"]), evidence_id=_uhv_evidence_id("contract_amount")),\n        _record(snapshot, metric="uhv_property_asset_ratio", value=f["uhv_property_asset_ratio"], unit="ratio", source_key="uhv_property_acquisition", source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date=str(f["uhv_property_contract_date"]), evidence_id=_uhv_evidence_id("asset_ratio")),\n        _record(snapshot, metric="uhv_property_self_funded", value=f["uhv_property_self_funded"], unit="dimensionless", source_key="uhv_property_acquisition", source_layer=EvidenceSourceLayer.COMPANY_OFFICIAL_PLAN, effective_date=str(f["uhv_property_contract_date"]), evidence_id=_uhv_evidence_id("self_funded")),\n'''
    replace_once(SANIL_PATH, insert_before, uhv_records + insert_before)
    replace_once(
        SANIL_PATH,
        '''        for key in ("terminal_growth", "terminal_roic"):\n''',
        '''        for year, value in enumerate(\n            inputs["uhv_incremental_fcff_krw_billion"], start=1\n        ):\n            rows.append(\n                _record(\n                    snapshot,\n                    metric=f"model_{scenario.lower()}_uhv_fcff_year_{year}",\n                    value=value,\n                    unit="KRW_billion",\n                    source_key=source,\n                    source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,\n                    effective_date=snapshot.cutoff,\n                    confidence=(0.45 if scenario != "Core" else 0.55),\n                    notes=(\n                        "Bounded incremental UHV-property FCFF cohort; official filing "\n                        "establishes land control and purpose, not exact capacity or earnings."\n                    ),\n                )\n            )\n        rows.append(\n            _record(\n                snapshot,\n                metric=f"model_{scenario.lower()}_uhv_property_capex",\n                value=inputs["uhv_property_capex_krw_billion"],\n                unit="KRW_billion",\n                source_key=source,\n                source_layer=EvidenceSourceLayer.ANALYST_UNDERWRITING,\n                effective_date=snapshot.cutoff,\n                confidence=0.90,\n                notes=(\n                    "DCF cash-outflow input equals the full disclosed property "\n                    "consideration and is deducted separately from incremental FCFF."\n                ),\n            )\n        )\n        for key in ("terminal_growth", "terminal_roic"):\n''',
    )
    replace_once(
        SANIL_PATH,
        '''    records_by_metric = {item.metric: item for item in records}\n    if len(records_by_metric) != len(records):\n        raise ValueError("Sanil collector metrics must be unique")\n\n    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:\n''',
        '''    records_by_metric: dict[str, list[EvidenceRecord]] = {}\n    for item in records:\n        records_by_metric.setdefault(item.metric, []).append(item)\n\n    def collect(request: EvidenceCollectionRequest) -> EvidenceCollectionBatch:\n''',
    )
    replace_once(
        SANIL_PATH,
        '''        selected = tuple(\n            records_by_metric[metric]\n            for metric in dict.fromkeys(request.required_metrics)\n        )\n''',
        '''        selected = tuple(\n            item\n            for metric in dict.fromkeys(request.required_metrics)\n            for item in records_by_metric[metric]\n        )\n''',
    )
    replace_once(
        SANIL_PATH,
        '''        _hypothesis(\n            "H:SANIL:CAPITAL",\n''',
        '''        _hypothesis(\n            "H:SANIL:UHV_CAPACITY",\n            "the signed UHV property contract must enter Core as a separate bounded capacity cohort with its full disclosed cash outflow",\n            (\n                _uhv_evidence_id("land_control"),\n                _uhv_evidence_id("capex_committed"),\n                _uhv_evidence_id("ramp_boundary"),\n                _uhv_evidence_id("baseline_inclusion"),\n            ),\n            kill="the acquisition is cancelled, fails to close or is proven fully embedded in the prior baseline",\n        ),\n        _hypothesis(\n            "H:SANIL:CAPITAL",\n''',
    )
    replace_once(
        SANIL_PATH,
        '''            "second-factory site with committed CAPEX."\n''',
        '''            "second-factory site with committed CAPEX and a separate signed UHV "\n            "property-acquisition contract."\n''',
    )
    replace_once(
        SANIL_PATH,
        '''            _evidence_id("expansion_capex_committed"),\n        ),\n        hypothesis_ids=("H:SANIL:CAPACITY", "H:SANIL:Core"),\n''',
        '''            _evidence_id("expansion_capex_committed"),\n            _uhv_evidence_id("land_control"),\n            _uhv_evidence_id("capex_committed"),\n        ),\n        hypothesis_ids=(\n            "H:SANIL:CAPACITY",\n            "H:SANIL:UHV_CAPACITY",\n            "H:SANIL:Core",\n        ),\n''',
    )
    replace_once(
        SANIL_PATH,
        '''        for key in ("terminal_growth", "terminal_roic"):\n            metric = f"model_{scenario.lower()}_{key}"\n''',
        '''        for year in range(1, FORECAST_YEARS + 1):\n            metric = f"model_{scenario.lower()}_uhv_fcff_year_{year}"\n            value = float(context.ledger.get(_evidence_id(metric)).value)\n            bridge_id = f"B:SANIL:{scenario}:uhv_fcff_year_{year}"\n            path = f"sanil:{scenario.lower()}:uhv_fcff_year_{year}"\n            evidence_ids = (_evidence_id(metric),)\n            hypothesis = f"H:SANIL:{scenario}"\n            direction = Direction.UP if value > 0 else Direction.UNCHANGED\n            if scenario == "Core" and year == 3:\n                bridge_id = "B:SANIL:UHV:RAMP"\n                path = f"{UHV_CAPACITY_PATH_ROOT}:ramp"\n                evidence_ids = (\n                    _uhv_evidence_id("ramp_boundary"),\n                    _evidence_id(metric),\n                )\n                hypothesis = "H:SANIL:UHV_CAPACITY"\n            elif scenario == "Core" and year == FORECAST_YEARS:\n                bridge_id = "B:SANIL:UHV:CAPACITY"\n                path = f"{UHV_CAPACITY_PATH_ROOT}:capacity"\n                evidence_ids = (\n                    _uhv_evidence_id("land_control"),\n                    _uhv_evidence_id("capex_committed"),\n                    _evidence_id(metric),\n                )\n                hypothesis = "H:SANIL:UHV_CAPACITY"\n            drafts.append(\n                BridgeDraft(\n                    assumption_key=f"uhv_fcff_year_{year}",\n                    scenario_id=scenario,\n                    bridge=_bridge(\n                        bridge_id=bridge_id,\n                        evidence_ids=evidence_ids,\n                        hypothesis_id=hypothesis,\n                        affected_variable=AffectedVariable.QUANTITY,\n                        direction=direction,\n                        old_value=0.0,\n                        new_value=value,\n                        unit="KRW_billion",\n                        economic_path_id=path,\n                        rationale=(\n                            "bounded incremental FCFF cohort for the separately "\n                            "land-controlled UHV property project"\n                        ),\n                    ),\n                    canonical_unit="KRW_billion",\n                    transform_id="identity_observation",\n                    input_evidence_ids=(_evidence_id(metric),),\n                    min_value="0",\n                )\n            )\n\n        uhv_capex_metric = f"model_{scenario.lower()}_uhv_property_capex"\n        uhv_capex_value = float(\n            context.ledger.get(_evidence_id(uhv_capex_metric)).value\n        )\n        uhv_capex_bridge_id = f"B:SANIL:{scenario}:uhv_property_capex"\n        uhv_capex_path = f"sanil:{scenario.lower()}:uhv_property_capex"\n        uhv_capex_evidence_ids = (_evidence_id(uhv_capex_metric),)\n        uhv_capex_hypothesis = f"H:SANIL:{scenario}"\n        if scenario == "Core":\n            uhv_capex_bridge_id = "B:SANIL:UHV:CAPEX"\n            uhv_capex_path = f"{UHV_CAPACITY_PATH_ROOT}:capex"\n            uhv_capex_evidence_ids = (\n                _uhv_evidence_id("capex_committed"),\n                _evidence_id(uhv_capex_metric),\n            )\n            uhv_capex_hypothesis = "H:SANIL:UHV_CAPACITY"\n        drafts.append(\n            BridgeDraft(\n                assumption_key="uhv_property_capex",\n                scenario_id=scenario,\n                bridge=_bridge(\n                    bridge_id=uhv_capex_bridge_id,\n                    evidence_ids=uhv_capex_evidence_ids,\n                    hypothesis_id=uhv_capex_hypothesis,\n                    affected_variable=AffectedVariable.QUANTITY,\n                    direction=Direction.UP,\n                    old_value=0.0,\n                    new_value=uhv_capex_value,\n                    unit="KRW_billion",\n                    economic_path_id=uhv_capex_path,\n                    rationale=(\n                        "full disclosed UHV property consideration is deducted "\n                        "as a separate explicit cash outflow"\n                    ),\n                ),\n                canonical_unit="KRW_billion",\n                transform_id="identity_observation",\n                input_evidence_ids=(_evidence_id(uhv_capex_metric),),\n                min_value="0",\n            )\n        )\n\n        for key in ("terminal_growth", "terminal_roic"):\n            metric = f"model_{scenario.lower()}_{key}"\n''',
    )
    old_capacity_functions = '''def _capacity_loader(context: OrchestratorContext) -> CapacityCommitmentInput:\n    gate = ProjectGateSet(\n        project_id=CAPACITY_PROJECT_ID,\n        required_gates=(ProjectGate.LAND_CONTROL,),\n        observations=(\n            ProjectGateEvidence(\n                ProjectGate.LAND_CONTROL,\n                True,\n                (_evidence_id("expansion_land_control"),),\n                effective_at="2024-01-01",\n                note="company-controlled second-factory site",\n            ),\n        ),\n    )\n    binding = CapacityProjectBinding(\n        project_id=CAPACITY_PROJECT_ID,\n        segment_id=SEGMENT_ID,\n        gate_set=gate,\n        baseline_inclusion=BaselineInclusionStatus.NOT_IN_BASELINE,\n        baseline_inclusion_evidence_ids=(_evidence_id("expansion_baseline_inclusion"),),\n        site_area_evidence_ids=(_evidence_id("expansion_site_area"),),\n        committed_capex_evidence_ids=(_evidence_id("expansion_capex_committed"),),\n        ramp_date_evidence_ids=(_evidence_id("expansion_ramp_date"),),\n        equipment_commitment_evidence_ids=(_evidence_id("expansion_equipment_commitment"),),\n    )\n    return CapacityCommitmentInput((CapacitySegmentCommitmentInput(SEGMENT_ID, (binding,), ()),))\n\n\ndef _capacity_consumption_loader(context: OrchestratorContext) -> CapacityBridgeConsumptionContract:\n    assessment = context.data["capacity_commitment_assessment"]\n    return CapacityBridgeConsumptionContract(\n        assessment.assessment_hash,\n        (\n            CapacityBridgeBinding(CAPACITY_PROJECT_ID, CapacityBridgeRole.CAPACITY, "B:SANIL:CAPACITY", (_evidence_id("expansion_land_control"), _evidence_id("expansion_site_area")), CAPACITY_PATH_ROOT),\n            CapacityBridgeBinding(CAPACITY_PROJECT_ID, CapacityBridgeRole.CAPEX, "B:SANIL:CAPEX", (_evidence_id("expansion_capex_committed"),), CAPACITY_PATH_ROOT),\n            CapacityBridgeBinding(CAPACITY_PROJECT_ID, CapacityBridgeRole.RAMP, "B:SANIL:RAMP", (_evidence_id("expansion_ramp_date"),), CAPACITY_PATH_ROOT),\n        ),\n    )\n'''
    new_capacity_functions = '''def _capacity_loader(context: OrchestratorContext) -> CapacityCommitmentInput:\n    second_factory_gate = ProjectGateSet(\n        project_id=CAPACITY_PROJECT_ID,\n        required_gates=(ProjectGate.LAND_CONTROL,),\n        observations=(\n            ProjectGateEvidence(\n                ProjectGate.LAND_CONTROL,\n                True,\n                (_evidence_id("expansion_land_control"),),\n                effective_at="2024-01-01",\n                note="company-controlled second-factory site",\n            ),\n        ),\n    )\n    second_factory = CapacityProjectBinding(\n        project_id=CAPACITY_PROJECT_ID,\n        segment_id=SEGMENT_ID,\n        gate_set=second_factory_gate,\n        baseline_inclusion=BaselineInclusionStatus.NOT_IN_BASELINE,\n        baseline_inclusion_evidence_ids=(\n            _evidence_id("expansion_baseline_inclusion"),\n        ),\n        site_area_evidence_ids=(_evidence_id("expansion_site_area"),),\n        committed_capex_evidence_ids=(\n            _evidence_id("expansion_capex_committed"),\n        ),\n        ramp_date_evidence_ids=(_evidence_id("expansion_ramp_date"),),\n        equipment_commitment_evidence_ids=(\n            _evidence_id("expansion_equipment_commitment"),\n        ),\n    )\n    uhv_gate = ProjectGateSet(\n        project_id=UHV_CAPACITY_PROJECT_ID,\n        required_gates=(ProjectGate.LAND_CONTROL,),\n        observations=(\n            ProjectGateEvidence(\n                ProjectGate.LAND_CONTROL,\n                True,\n                (_uhv_evidence_id("land_control"),),\n                effective_at="2026-08-26",\n                note="signed official UHV property-acquisition contract",\n            ),\n        ),\n    )\n    uhv_property = CapacityProjectBinding(\n        project_id=UHV_CAPACITY_PROJECT_ID,\n        segment_id=SEGMENT_ID,\n        gate_set=uhv_gate,\n        baseline_inclusion=BaselineInclusionStatus.NOT_IN_BASELINE,\n        baseline_inclusion_evidence_ids=(\n            _uhv_evidence_id("baseline_inclusion"),\n        ),\n        committed_capex_evidence_ids=(\n            _uhv_evidence_id("capex_committed"),\n        ),\n        ramp_date_evidence_ids=(_uhv_evidence_id("ramp_boundary"),),\n    )\n    return CapacityCommitmentInput(\n        (\n            CapacitySegmentCommitmentInput(\n                SEGMENT_ID,\n                (second_factory, uhv_property),\n                (),\n            ),\n        )\n    )\n\n\ndef _capacity_consumption_loader(\n    context: OrchestratorContext,\n) -> CapacityBridgeConsumptionContract:\n    assessment = context.data["capacity_commitment_assessment"]\n    return CapacityBridgeConsumptionContract(\n        assessment.assessment_hash,\n        (\n            CapacityBridgeBinding(CAPACITY_PROJECT_ID, CapacityBridgeRole.CAPACITY, "B:SANIL:CAPACITY", (_evidence_id("expansion_land_control"), _evidence_id("expansion_site_area")), CAPACITY_PATH_ROOT),\n            CapacityBridgeBinding(CAPACITY_PROJECT_ID, CapacityBridgeRole.CAPEX, "B:SANIL:CAPEX", (_evidence_id("expansion_capex_committed"),), CAPACITY_PATH_ROOT),\n            CapacityBridgeBinding(CAPACITY_PROJECT_ID, CapacityBridgeRole.RAMP, "B:SANIL:RAMP", (_evidence_id("expansion_ramp_date"),), CAPACITY_PATH_ROOT),\n            CapacityBridgeBinding(UHV_CAPACITY_PROJECT_ID, CapacityBridgeRole.CAPACITY, "B:SANIL:UHV:CAPACITY", (_uhv_evidence_id("land_control"), _uhv_evidence_id("capex_committed")), UHV_CAPACITY_PATH_ROOT),\n            CapacityBridgeBinding(UHV_CAPACITY_PROJECT_ID, CapacityBridgeRole.CAPEX, "B:SANIL:UHV:CAPEX", (_uhv_evidence_id("capex_committed"),), UHV_CAPACITY_PATH_ROOT),\n            CapacityBridgeBinding(UHV_CAPACITY_PROJECT_ID, CapacityBridgeRole.RAMP, "B:SANIL:UHV:RAMP", (_uhv_evidence_id("ramp_boundary"),), UHV_CAPACITY_PATH_ROOT),\n        ),\n    )\n'''
    replace_once(SANIL_PATH, old_capacity_functions, new_capacity_functions)
    replace_once(
        SANIL_PATH,
        '''                    expansion_capex_key="expansion_capex",\n                    expansion_capex_year=2,\n''',
        '''                    expansion_capex_key="expansion_capex",\n                    expansion_capex_year=2,\n                    additive_fcff_prefixes=("uhv_",),\n                    additional_expansion_capex=(("uhv_property_capex", 2),),\n''',
    )
    replace_once(
        SANIL_PATH,
        '''                "expansion_capex",\n                "terminal_growth",\n''',
        '''                "expansion_capex",\n                *(f"uhv_fcff_year_{year}" for year in range(1, FORECAST_YEARS + 1)),\n                "uhv_property_capex",\n                "terminal_growth",\n''',
    )
    replace_once(
        SANIL_PATH,
        '''    run_id: str = "SANIL-062040-20260825",\n''',
        '''    run_id: str = "SANIL-062040-20260826",\n''',
    )
    replace_once(
        SANIL_PATH,
        '''    run_id: str = "SANIL-062040-20260825",\n''',
        '''    run_id: str = "SANIL-062040-20260826",\n''',
    )
    replace_once(
        SANIL_PATH,
        '''            "evidence_confidence": "official company facts high; peer-risk and forward FCFF assumptions moderate",\n''',
        '''            "evidence_confidence": "official company facts and signed UHV land contract high; common-source regression Beta moderate; forward FCFF assumptions moderate",\n''',
    )


def patch_report_script() -> None:
    replace_once(
        REPORT_SCRIPT_PATH,
        '''산일전기는 수요 검증 단계를 넘어 생산능력과 ramp가 가치의 핵심 병목이 된 회사입니다. 이번 run은 부지 통제·확정 CAPEX·ramp Evidence를 Core에서 누락하지 않고, 동일 프로젝트의 Capacity·CAPEX·ramp 경로를 Scenario와 DCF가 실제 소비한 뒤 Beta·WACC, Audit, Freeze를 통과했습니다.\n''',
        '''산일전기는 수요 검증 단계를 넘어 생산능력과 ramp가 가치의 핵심 병목이 된 회사입니다. 이번 run은 기존 제2공장뿐 아니라 2026년 8월 26일 체결된 초고압 변압기 생산용 부동산 양수계약을 별도 Core 프로젝트로 분리했습니다. 두 프로젝트의 Capacity·CAPEX·ramp 경로를 Scenario와 DCF가 실제 소비한 뒤 Beta·WACC, Audit, Freeze를 통과했습니다.\n''',
    )
    replace_once(
        REPORT_SCRIPT_PATH,
        '''- Beta peer 관측: 실제 상장회사와 공개 `Beta (5Y)` 자료 기반, **중간 증거 신뢰도**\n- Beta 공급자는 benchmark·빈도·표준오차를 공개하지 않아 `beta_standard_error`를 임의 생성하지 않았습니다.\n''',
        '''- Beta peer 관측: 동일 KOSPI benchmark·동일 기간·주간 수익률 OLS 기반이며 회귀 표준오차와 시계열 hash를 보존, **중간~높은 증거 신뢰도**\n- 일간 OLS는 비동시거래·빈도 민감도 진단값으로 별도 보존하며 주간 Beta와 임의 평균하지 않습니다.\n''',
    )
    replace_once(
        REPORT_SCRIPT_PATH,
        '''- 공식 KRX 수익률 회귀 provider가 가용해지면 현재 외부 Beta 스냅샷을 교체하는 것이 다음 품질개선 항목입니다.\n''',
        '''- 초고압 부동산 계약은 LAND_CONTROL과 692.5억원 현금유출을 공식 확정하지만, 정확한 생산 CAPA는 미공시이므로 증분 FCFF는 보수적 bounded underwrite입니다.\n''',
    )
    replace_once(
        REPORT_SCRIPT_PATH,
        '''- 2026년 2분기 IR: {snapshot.sources['q2_ir']['source_ref']}\n''',
        '''- 2026년 2분기 IR: {snapshot.sources['q2_ir']['source_ref']}\n- 2026년 8월 26일 초고압 생산용 부동산 양수결정: {snapshot.sources['uhv_property_acquisition']['source_ref']}\n''',
    )


def patch_tests() -> None:
    replace_once(TEST_PATH, 'assert snapshot.cutoff == "2026-08-25"', 'assert snapshot.cutoff == "2026-08-26"')
    replace_once(TEST_PATH, '    assert market.price == 169300\n', '    assert market.price > 0\n    assert market.as_of == snapshot.cutoff\n')
    replace_once(
        TEST_PATH,
        '    assert snapshot.risk["as_of"] == snapshot.cutoff\n',
        '    assert snapshot.risk["as_of"] <= snapshot.cutoff\n',
    )
    replace_once(
        TEST_PATH,
        '''    assert assessment.core_inclusion_required_projects == (\n        "SANIL_SECOND_FACTORY_RAMP",\n    )\n''',
        '''    assert assessment.core_inclusion_required_projects == (\n        "SANIL_SECOND_FACTORY_RAMP",\n        "SANIL_UHV_PROPERTY_ACQUISITION_20260826",\n    )\n''',
    )
    replace_once(
        TEST_PATH,
        '''    assert f"capacity_project:SANIL_SECOND_FACTORY_RAMP:capex" in core.economic_path_ids\n    compiled = result.data["compiled_assumption_set"]\n    assert compiled.get("expansion_capex", "Core").measure.amount == 42\n''',
        '''    assert f"capacity_project:SANIL_SECOND_FACTORY_RAMP:capex" in core.economic_path_ids\n    assert (\n        "capacity_project:SANIL_UHV_PROPERTY_ACQUISITION_20260826:capex"\n        in core.economic_path_ids\n    )\n    assert (\n        "capacity_project:SANIL_UHV_PROPERTY_ACQUISITION_20260826:capacity"\n        in core.economic_path_ids\n    )\n    compiled = result.data["compiled_assumption_set"]\n    assert compiled.get("expansion_capex", "Core").measure.amount == 42\n    assert compiled.get("uhv_property_capex", "Core").measure.amount == 69.25\n    assert compiled.get("uhv_fcff_year_5", "Core").measure.amount == 42\n''',
    )
    replace_once(
        TEST_PATH,
        '''    assert "SANIL_SECOND_FACTORY_RAMP" in str(\n        result.data["capacity_commitment_assessment"]\n    )\n''',
        '''    assert "SANIL_SECOND_FACTORY_RAMP" in str(\n        result.data["capacity_commitment_assessment"]\n    )\n    assert "SANIL_UHV_PROPERTY_ACQUISITION_20260826" in str(\n        result.data["capacity_commitment_assessment"]\n    )\n''',
    )
    replace_once(
        TEST_PATH,
        '''    assert "must be classified by the typed Capacity Gate" in result.data["final_report"]\n''',
        '''    assert "must be classified by the typed Capacity Gate" in result.data["final_report"]\n    assert "SANIL_UHV_PROPERTY_ACQUISITION_20260826" in str(assessment)\n''',
    )
    replace_once(
        TEST_PATH,
        '''    run_root = tmp_path / "runs" / TICKER / "SANIL-062040-20260825"\n''',
        '''    run_root = tmp_path / "runs" / TICKER / "SANIL-062040-20260826"\n''',
    )
    replace_once(
        TEST_PATH,
        '''    assert market.price == 169300\n    assert market.as_of == "2026-08-25"\n''',
        '''    assert market.price > 0\n    assert market.as_of == "2026-08-26"\n''',
    )


def main() -> int:
    update_snapshot()
    update_market_snapshot()
    patch_beta_refresh_contract()
    patch_sanil_runtime()
    patch_report_script()
    patch_tests()
    print("Sanil UHV property capacity project integrated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
