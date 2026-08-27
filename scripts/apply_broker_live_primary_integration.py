from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


def insert_before(path: str, marker: str, insertion: str) -> None:
    replace_once(path, marker, insertion + marker)


def patch_broker_runtime() -> None:
    path = "src/valuation_engine/broker_runtime.py"
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if "class BrokerResearchLLMContext" not in text:
        marker = "@dataclass(frozen=True)\nclass BrokerResearchAuditResult:"
        insertion = '''@dataclass(frozen=True)\nclass BrokerResearchLLMContext:\n    context_claims: tuple[BrokerClaim, ...]\n    primary_verification_claims: tuple[BrokerClaim, ...]\n    verification_requests: tuple[str, ...]\n    primary_source_hints: tuple[str, ...]\n    source_refs: tuple[str, ...]\n    snapshot_hash: str\n\n    def __post_init__(self) -> None:\n        if not self.source_refs or not self.snapshot_hash:\n            raise ValueError("BrokerResearchLLMContext requires source refs and hash")\n        if any(\n            pre_freeze_use(item) is not BrokerPreFreezeUse.CONTEXT\n            for item in self.context_claims\n        ):\n            raise ValueError("LLM broker context contains a non-context claim")\n        if any(\n            pre_freeze_use(item) is not BrokerPreFreezeUse.PRIMARY_VERIFICATION_ONLY\n            for item in self.primary_verification_claims\n        ):\n            raise ValueError("LLM broker context contains a non-verification claim")\n\n\n'''
        if marker not in text:
            raise RuntimeError("broker runtime LLM-context insertion marker missing")
        text = text.replace(marker, insertion + marker, 1)
    old = '''            "broker_primary_source_hints": result.primary_source_hints,\n            "broker_additional_required_evidence": result.additional_required_evidence,\n            **plan_stage.outputs,\n'''
    new = '''            "broker_primary_source_hints": result.primary_source_hints,\n            "broker_additional_required_evidence": result.additional_required_evidence,\n            "broker_research_llm_context": BrokerResearchLLMContext(\n                context_claims=result.context_claims,\n                primary_verification_claims=result.primary_verification_claims,\n                verification_requests=result.verification_requests,\n                primary_source_hints=result.primary_source_hints,\n                source_refs=result.source_refs,\n                snapshot_hash=result.snapshot_hash,\n            ),\n            **plan_stage.outputs,\n'''
    if old in text:
        text = text.replace(old, new, 1)
    elif '"broker_research_llm_context": BrokerResearchLLMContext(' not in text:
        raise RuntimeError("broker runtime plan output marker missing")
    old = '''        no_direct_broker_evidence = not claim_ids.intersection(\n            hypothesis_evidence_ids | bridge_evidence_ids\n        )\n\n        findings = (\n'''
    new = '''        no_direct_broker_evidence = not claim_ids.intersection(\n            hypothesis_evidence_ids | bridge_evidence_ids\n        )\n        broker_source_refs = set(result.source_refs)\n        no_broker_sources_in_ledger = not broker_source_refs.intersection(\n            item.source_ref for item in ledger.active()\n        )\n\n        findings = (\n'''
    if old in text:
        text = text.replace(old, new, 1)
    elif "no_broker_sources_in_ledger" not in text:
        raise RuntimeError("broker runtime audit source marker missing")
    old = '''            AuditFinding(\n                "broker_claims_not_direct_assumption_evidence",\n                no_direct_broker_evidence,\n                True,\n                "broker claim IDs never became Hypothesis or Bridge Evidence IDs",\n            ),\n        )\n'''
    new = '''            AuditFinding(\n                "broker_claims_not_direct_assumption_evidence",\n                no_direct_broker_evidence,\n                True,\n                "broker claim IDs never became Hypothesis or Bridge Evidence IDs",\n            ),\n            AuditFinding(\n                "broker_sources_not_in_primary_ledger",\n                no_broker_sources_in_ledger,\n                True,\n                "broker report sources never entered the primary EvidenceLedger",\n            ),\n        )\n'''
    if old in text:
        text = text.replace(old, new, 1)
    elif '"broker_sources_not_in_primary_ledger"' not in text:
        raise RuntimeError("broker runtime audit finding marker missing")
    target.write_text(text, encoding="utf-8")


def patch_live_runtime() -> None:
    path = "src/valuation_engine/live_runtime.py"
    replace_once(
        path,
        "from .audit_adapter import generic_audit_adapter\n",
        '''from .audit_adapter import generic_audit_adapter\nfrom .broker_runtime import (\n    BrokerResearchLoader,\n    broker_aware_module_requirement_plan_adapter,\n    broker_research_audit_adapter,\n)\n''',
    )
    replace_once(
        path,
        "from .module_plan_adapter import module_requirement_plan_adapter\n",
        "",
    )
    replace_once(
        path,
        '''    valuation_plan_inputs_loader: ValuationPlanInputsLoader\n    capacity_commitment_loader: CapacityCommitmentLoader | None = None\n''',
        '''    valuation_plan_inputs_loader: ValuationPlanInputsLoader\n    broker_research_loader: BrokerResearchLoader | None = None\n    capacity_commitment_loader: CapacityCommitmentLoader | None = None\n''',
    )
    replace_once(
        path,
        '''    additional_required_evidence: Mapping[str, tuple[str, ...]] = field(\n        default_factory=dict\n    )\n    capacity_core_scenario_id: str | None = None\n''',
        '''    additional_required_evidence: Mapping[str, tuple[str, ...]] = field(\n        default_factory=dict\n    )\n    require_broker_research: bool = False\n    capacity_core_scenario_id: str | None = None\n''',
    )
    replace_once(
        path,
        '''        if self.providers.market_loader is not None and not self.market_currency:\n            raise ValueError("LIVE_PRIMARY market_loader requires market_currency")\n''',
        '''        if not isinstance(self.require_broker_research, bool):\n            raise TypeError("require_broker_research must be bool")\n        if self.require_broker_research and self.providers.broker_research_loader is None:\n            raise ValueError(\n                "require_broker_research=True requires broker_research_loader"\n            )\n        if self.providers.market_loader is not None and not self.market_currency:\n            raise ValueError("LIVE_PRIMARY market_loader requires market_currency")\n''',
    )
    replace_once(
        path,
        '''        "MODULE_REQUIREMENT_PLAN": module_requirement_plan_adapter(\n            registry_path=config.archetype_registry_path,\n            control_requirements_path=config.archetype_control_requirements_path,\n            additional_required_evidence=config.additional_required_evidence,\n        ),\n''',
        '''        "MODULE_REQUIREMENT_PLAN": broker_aware_module_requirement_plan_adapter(\n            registry_path=config.archetype_registry_path,\n            control_requirements_path=config.archetype_control_requirements_path,\n            loader=providers.broker_research_loader,\n            require_broker_research=config.require_broker_research,\n            additional_required_evidence=config.additional_required_evidence,\n        ),\n''',
    )
    replace_once(
        path,
        '''        "AUDIT_GATE": chain_stage_adapters(\n            capacity_audit_adapter(),\n            generic_audit_adapter(\n''',
        '''        "AUDIT_GATE": chain_stage_adapters(\n            broker_research_audit_adapter(required=config.require_broker_research),\n            capacity_audit_adapter(),\n            generic_audit_adapter(\n''',
    )


def patch_llm_context() -> None:
    replace_once(
        "src/valuation_engine/llm_staff.py",
        "from .capacity_commitment import CapacityCommitmentAssessment\n",
        '''from .broker_runtime import BrokerResearchLLMContext\nfrom .capacity_commitment import CapacityCommitmentAssessment\n''',
    )
    replace_once(
        "src/valuation_engine/llm_staff.py",
        '''    funding_scan_result: object | None = None\n    capacity_commitment_assessment: CapacityCommitmentAssessment | None = None\n''',
        '''    funding_scan_result: object | None = None\n    broker_research_context: BrokerResearchLLMContext | None = None\n    capacity_commitment_assessment: CapacityCommitmentAssessment | None = None\n''',
    )
    replace_once(
        "src/valuation_engine/llm_adapters.py",
        "from .capacity_commitment import CapacityCommitmentAssessment\n",
        '''from .broker_runtime import BrokerResearchLLMContext\nfrom .capacity_commitment import CapacityCommitmentAssessment\n''',
    )
    replace_once(
        "src/valuation_engine/llm_adapters.py",
        '''    capacity = context.data.get("capacity_commitment_assessment")\n    if capacity is not None and not isinstance(\n''',
        '''    broker_context = context.data.get("broker_research_llm_context")\n    if broker_context is not None and not isinstance(\n        broker_context, BrokerResearchLLMContext\n    ):\n        raise ValueError("broker_research_llm_context must be typed when present")\n    capacity = context.data.get("capacity_commitment_assessment")\n    if capacity is not None and not isinstance(\n''',
    )
    replace_once(
        "src/valuation_engine/llm_adapters.py",
        '''        scanner_findings=scanner_findings,\n        funding_scan_result=context.data.get("funding_scan_result"),\n        capacity_commitment_assessment=capacity,\n''',
        '''        scanner_findings=scanner_findings,\n        funding_scan_result=context.data.get("funding_scan_result"),\n        broker_research_context=broker_context,\n        capacity_commitment_assessment=capacity,\n''',
    )


def patch_audit_binding() -> None:
    path = "src/valuation_engine/audit_adapter.py"
    replace_once(
        path,
        '''        capacity_report = (\n            capacity_report_raw\n            if isinstance(capacity_report_raw, AuditReport)\n            else None\n        )\n        capacity_hash = (\n            capacity_hash_raw\n            if isinstance(capacity_hash_raw, str) and capacity_hash_raw\n            else None\n        )\n\n        try:\n''',
        '''        capacity_report = (\n            capacity_report_raw\n            if isinstance(capacity_report_raw, AuditReport)\n            else None\n        )\n        capacity_hash = (\n            capacity_hash_raw\n            if isinstance(capacity_hash_raw, str) and capacity_hash_raw\n            else None\n        )\n        broker_required = bool(context.data.get("broker_research_required", False))\n        broker_result_present = context.data.get("broker_research_prefreeze_result") is not None\n        broker_report_raw = context.data.get("broker_research_audit_report")\n        broker_hash_raw = context.data.get("broker_research_audit_hash")\n        if (broker_required or broker_result_present) and (\n            not isinstance(broker_report_raw, AuditReport)\n            or not isinstance(broker_hash_raw, str)\n            or not broker_hash_raw\n            or not bool(context.data.get("broker_research_audit_passed"))\n        ):\n            return StageExecutionResult(\n                StageStatus.RECOVERY_REQUIRED,\n                "Broker Research audit artifact/hash is required before generic audit",\n                blocking=True,\n            )\n        broker_report = (\n            broker_report_raw if isinstance(broker_report_raw, AuditReport) else None\n        )\n        broker_hash = (\n            broker_hash_raw\n            if isinstance(broker_hash_raw, str) and broker_hash_raw\n            else None\n        )\n\n        try:\n''',
    )
    replace_once(
        path,
        '''            external_guardrail_findings=(\n                capacity_report.findings if capacity_report is not None else ()\n            ),\n            external_guardrail_hashes=(\n                (capacity_hash,) if capacity_hash is not None else ()\n            ),\n''',
        '''            external_guardrail_findings=(\n                *(capacity_report.findings if capacity_report is not None else ()),\n                *(broker_report.findings if broker_report is not None else ()),\n            ),\n            external_guardrail_hashes=(\n                *((capacity_hash,) if capacity_hash is not None else ()),\n                *((broker_hash,) if broker_hash is not None else ()),\n            ),\n''',
    )


def patch_report_form() -> None:
    path = "src/valuation_engine/report_form.py"
    replace_once(
        path,
        "from .capacity_commitment import CapacityCommitmentAssessment\n",
        '''from .broker_runtime import BrokerResearchPreFreezeResult\nfrom .capacity_commitment import CapacityCommitmentAssessment\n''',
    )
    marker = '''    capacity = data.get("capacity_commitment_assessment")\n'''
    insertion = '''    broker_required = bool(data.get("broker_research_required", False))\n    broker_result = data.get("broker_research_prefreeze_result")\n    broker_configured = broker_required or broker_result is not None\n    if broker_configured:\n        broker_runtime_ok = (\n            isinstance(broker_result, BrokerResearchPreFreezeResult)\n            and _string_hash(data, "broker_research_snapshot_hash")\n            == broker_result.snapshot_hash\n            and bool(data.get("broker_research_audit_passed"))\n            and _string_hash(data, "broker_research_audit_hash") is not None\n        )\n        checks.append(\n            _check(\n                "broker_research_primary_verification_chain",\n                broker_runtime_ok,\n                "pre-freeze Broker Research was partitioned, primary-verified and audit-bound",\n                "Broker Research discovery, primary verification or audit binding is missing",\n            )\n        )\n\n'''
    insert_before(path, marker, insertion)
    replace_once(
        path,
        '''        "hashes": {\n            key: data.get(key)\n            for key in (\n                "ledger_snapshot_hash",\n''',
        '''        "hashes": {\n            key: data.get(key)\n            for key in (\n                "ledger_snapshot_hash",\n''',
    )
    # Conditionally bind broker hashes without changing non-broker attestation payloads.
    replace_once(
        path,
        '''        "freeze_token_hash": getattr(token, "token_hash", None),\n    }\n    return RunAttestation(result.run_id, tuple(checks), _stable_hash(payload))\n''',
        '''        "freeze_token_hash": getattr(token, "token_hash", None),\n    }\n    if broker_configured:\n        payload["broker_research"] = {\n            "snapshot_hash": data.get("broker_research_snapshot_hash"),\n            "audit_hash": data.get("broker_research_audit_hash"),\n        }\n    return RunAttestation(result.run_id, tuple(checks), _stable_hash(payload))\n''',
    )
    replace_once(
        path,
        '''        ("Capacity audit", "capacity_audit_hash"),\n        ("Valuation", "valuation_hash"),\n''',
        '''        ("Capacity audit", "capacity_audit_hash"),\n        *(\n            (\n                ("Broker pre-freeze", "broker_research_snapshot_hash"),\n                ("Broker audit", "broker_research_audit_hash"),\n            )\n            if broker_configured\n            else ()\n        ),\n        ("Valuation", "valuation_hash"),\n''',
    )
    replace_once(
        path,
        '''| `capacity_core_consumption_chain` | `{{ PASS_OR_FAIL_OR_NOT_APPLICABLE }}` | `{{ detail }}` |\n| `freeze_hash_binding` | `{{ PASS_OR_FAIL }}` | `{{ detail }}` |\n''',
        '''| `capacity_core_consumption_chain` | `{{ PASS_OR_FAIL_OR_NOT_APPLICABLE }}` | `{{ detail }}` |\n| `broker_research_primary_verification_chain` | `{{ PASS_OR_FAIL_OR_NOT_APPLICABLE }}` | `{{ detail }}` |\n| `freeze_hash_binding` | `{{ PASS_OR_FAIL }}` | `{{ detail }}` |\n''',
    )
    replace_once(
        path,
        '''| Capacity audit | `{{ capacity_audit_hash }}` |\n| Valuation | `{{ valuation_hash }}` |\n''',
        '''| Capacity audit | `{{ capacity_audit_hash }}` |\n| Broker pre-freeze | `{{ broker_research_snapshot_hash_or_not_applicable }}` |\n| Broker audit | `{{ broker_research_audit_hash_or_not_applicable }}` |\n| Valuation | `{{ valuation_hash }}` |\n''',
    )


def patch_sanil() -> None:
    path = "src/valuation_engine/sanil_live_primary.py"
    replace_once(
        path,
        "from .capacity_commitment import (\n",
        '''from .broker_research import (\n    BrokerClaim,\n    BrokerFieldClass,\n    BrokerReportType,\n)\nfrom .broker_runtime import (\n    BrokerResearchBatch,\n    BrokerResearchObservation,\n)\nfrom .capacity_commitment import (\n''',
    )
    replace_once(
        path,
        '''_DEFAULT_MARKET_SNAPSHOT_FILENAME = "sanil_market_snapshot.yaml"\n\nTARGET_ID = "KR:DART:00366438"\n''',
        '''_DEFAULT_MARKET_SNAPSHOT_FILENAME = "sanil_market_snapshot.yaml"\n_MIRAE_2Q26_REPORT_URL = (\n    "https://securities.miraeasset.com/bbs/board/message/view.do"\n    "?categoryId=1800&messageId=2341906"\n)\n\nTARGET_ID = "KR:DART:00366438"\n''',
    )
    marker = "def _street_reports() -> tuple[StreetResearchReport, ...]:\n"
    insertion = '''def _broker_research_loader(snapshot: SanilSnapshot):\n    def load(_context: OrchestratorContext) -> BrokerResearchBatch:\n        return BrokerResearchBatch(\n            checked_at=snapshot.cutoff,\n            observations=(\n                BrokerResearchObservation(\n                    claim=BrokerClaim(\n                        claim_id="B:SANIL:MIRAE:2Q26_PRIMARY_LEADS",\n                        source_id="KR_MIRAE_INDUSTRY_RESEARCH",\n                        broker_family="MiraeAssetSecurities",\n                        report_type=BrokerReportType.EARNINGS_REVIEW,\n                        field_class=BrokerFieldClass.UNDERLYING_DATA_REFERENCE,\n                        industry_node="power_transformers",\n                        statement=(\n                            "Mirae identifies order/backlog, specialty-transformer mix "\n                            "and capacity utilization as key Sanil operating signals; "\n                            "the runtime must verify them in company primary sources."\n                        ),\n                        target_company_specific=True,\n                        underlying_data_families=("company_ir", "company_filing"),\n                        report_date="2026-08-07",\n                    ),\n                    segment_id=SEGMENT_ID,\n                    source_ref=_MIRAE_2Q26_REPORT_URL,\n                    verification_metrics=("orders", "backlog", "mix", "utilization"),\n                    verification_requests=(\n                        "verify orders, backlog, mix and utilization in official filing/IR",\n                    ),\n                    primary_source_hints=("2025 annual report", "2Q26 company IR"),\n                ),\n                BrokerResearchObservation(\n                    claim=BrokerClaim(\n                        claim_id="B:SANIL:MIRAE:UHV_PRIMARY_LEADS",\n                        source_id="KR_MIRAE_INDUSTRY_RESEARCH",\n                        broker_family="MiraeAssetSecurities",\n                        report_type=BrokerReportType.COMPANY_UPDATE,\n                        field_class=BrokerFieldClass.UNDERLYING_DATA_REFERENCE,\n                        industry_node="power_transformers",\n                        statement=(\n                            "Mirae flags a separate UHV expansion path; exact future "\n                            "capacity and timing are not accepted until company primary "\n                            "evidence establishes land control, committed spend and ramp boundaries."\n                        ),\n                        target_company_specific=True,\n                        underlying_data_families=("company_filing",),\n                        report_date="2026-08-07",\n                    ),\n                    segment_id=SEGMENT_ID,\n                    source_ref=_MIRAE_2Q26_REPORT_URL,\n                    verification_metrics=(\n                        "expansion_land_control",\n                        "expansion_site_area",\n                        "expansion_capex_committed",\n                        "expansion_ramp_date",\n                    ),\n                    verification_requests=(\n                        "verify UHV land control, disclosed consideration and ramp boundary in company filing",\n                    ),\n                    primary_source_hints=("company property-acquisition filing",),\n                ),\n                BrokerResearchObservation(\n                    claim=BrokerClaim(\n                        claim_id="B:SANIL:MIRAE:FORWARD_FORECAST",\n                        source_id="KR_MIRAE_INDUSTRY_RESEARCH",\n                        broker_family="MiraeAssetSecurities",\n                        report_type=BrokerReportType.EARNINGS_REVIEW,\n                        field_class=BrokerFieldClass.TARGET_COMPANY_FORECAST,\n                        industry_node="power_transformers",\n                        statement="Mirae publishes a target-company forward earnings path.",\n                        target_company_specific=True,\n                        report_date="2026-08-07",\n                    ),\n                    segment_id=SEGMENT_ID,\n                    source_ref=_MIRAE_2Q26_REPORT_URL,\n                ),\n                BrokerResearchObservation(\n                    claim=BrokerClaim(\n                        claim_id="B:SANIL:MIRAE:TARGET_PRICE",\n                        source_id="KR_MIRAE_INDUSTRY_RESEARCH",\n                        broker_family="MiraeAssetSecurities",\n                        report_type=BrokerReportType.VALUATION_CHANGE,\n                        field_class=BrokerFieldClass.TARGET_PRICE,\n                        industry_node="power_transformers",\n                        statement="Mirae target price is KRW 250,000.",\n                        target_company_specific=True,\n                        report_date="2026-08-07",\n                    ),\n                    segment_id=SEGMENT_ID,\n                    source_ref=_MIRAE_2Q26_REPORT_URL,\n                ),\n            ),\n            source_refs=(_MIRAE_2Q26_REPORT_URL,),\n        )\n\n    return load\n\n\n'''
    insert_before(path, marker, insertion)
    replace_once(
        path,
        '''def _street_reports() -> tuple[StreetResearchReport, ...]:\n    return (\n        StreetResearchReport(\n            broker="Shinhan Securities",\n''',
        '''def _street_reports() -> tuple[StreetResearchReport, ...]:\n    return (\n        StreetResearchReport(\n            broker="Mirae Asset Securities",\n            analyst="Kim Tae-hyung",\n            published_date="2026-08-07",\n            target_price=250000.0,\n            target_price_currency="KRW",\n            valuation_method="PER-based target framework",\n            base_year="2028",\n            estimates=(),\n            source_ref=_MIRAE_2Q26_REPORT_URL,\n        ),\n        StreetResearchReport(\n            broker="Shinhan Securities",\n''',
    )
    replace_once(
        path,
        '''def _intelligence_officer(context) -> IntelligenceProposal:\n    hypotheses = tuple(\n''',
        '''def _intelligence_officer(context) -> IntelligenceProposal:\n    broker_context = context.broker_research_context\n    if broker_context is None:\n        raise ValueError(\n            "Sanil LIVE_PRIMARY requires the pre-freeze Broker Research context"\n        )\n    hypotheses = tuple(\n''',
    )
    replace_once(
        path,
        '''    return IntelligenceProposal(\n        hypotheses=hypotheses,\n        rationale=(\n            "Sanil is routed as contracted-backlog plus capacity-manufacturing; "\n''',
        '''    return IntelligenceProposal(\n        hypotheses=hypotheses,\n        requested_evidence=broker_context.verification_requests,\n        rationale=(\n            "Broker Research factual leads were converted to primary-source verification "\n            "and target forecasts/targets were quarantined before intrinsic valuation. "\n            "Sanil is routed as contracted-backlog plus capacity-manufacturing; "\n''',
    )
    replace_once(
        path,
        '''        valuation_plan_inputs_loader=_valuation_plan_inputs,\n        capacity_commitment_loader=_capacity_loader,\n''',
        '''        valuation_plan_inputs_loader=_valuation_plan_inputs,\n        broker_research_loader=_broker_research_loader(snapshot),\n        capacity_commitment_loader=_capacity_loader,\n''',
    )
    replace_once(
        path,
        '''        additional_required_evidence={\n            SEGMENT_ID: tuple(item.metric for item in records)\n        },\n        method_choices=(SegmentMethodChoice(SEGMENT_ID, "capacity_manufacturing", "driver_dcf", "1"),),\n''',
        '''        additional_required_evidence={\n            SEGMENT_ID: tuple(item.metric for item in records)\n        },\n        require_broker_research=True,\n        method_choices=(SegmentMethodChoice(SEGMENT_ID, "capacity_manufacturing", "driver_dcf", "1"),),\n''',
    )


def patch_sanil_tests() -> None:
    path = "tests/test_sanil_live_primary.py"
    replace_once(
        path,
        '''        "capacity_audit_hash",\n        "valuation_hash",\n''',
        '''        "capacity_audit_hash",\n        "broker_research_snapshot_hash",\n        "broker_research_audit_hash",\n        "valuation_hash",\n''',
    )
    replace_once(
        path,
        '''    assert result.data["capacity_audit_passed"]\n\n    trace_index = {\n''',
        '''    assert result.data["capacity_audit_passed"]\n    assert result.data["broker_research_audit_passed"]\n    broker_result = result.data["broker_research_prefreeze_result"]\n    assert tuple(\n        item.claim_id for item in broker_result.primary_verification_claims\n    ) == (\n        "B:SANIL:MIRAE:2Q26_PRIMARY_LEADS",\n        "B:SANIL:MIRAE:UHV_PRIMARY_LEADS",\n    )\n    assert tuple(item.claim_id for item in broker_result.quarantined_claims) == (\n        "B:SANIL:MIRAE:FORWARD_FORECAST",\n        "B:SANIL:MIRAE:TARGET_PRICE",\n    )\n    assert not any(\n        "securities.miraeasset.com" in item.source_ref\n        for item in ledger.active()\n    )\n    assert result.data["intelligence_proposal"].requested_evidence\n\n    trace_index = {\n''',
    )
    replace_once(
        path,
        '''    assert result.data["street_comparison"].consensus.report_count == 1\n''',
        '''    assert result.data["street_comparison"].consensus.report_count == 2\n''',
    )
    replace_once(
        path,
        '''    assert config.providers.beta_loader is not None\n    assert config.providers.wacc_loader is not None\n''',
        '''    assert config.require_broker_research\n    assert config.providers.broker_research_loader is not None\n    assert config.providers.beta_loader is not None\n    assert config.providers.wacc_loader is not None\n''',
    )


def patch_report_tests() -> None:
    path = "tests/test_report_form.py"
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if "broker_research_primary_verification_chain" not in text:
        text += '''\n\ndef test_report_template_exposes_broker_research_audit_identity():\n    template = render_report_form_template()\n\n    assert "broker_research_primary_verification_chain" in template\n    assert "broker_research_snapshot_hash" in template\n    assert "broker_research_audit_hash" in template\n'''
        target.write_text(text, encoding="utf-8")


def patch_workflows() -> None:
    path = ".github/workflows/sanil-live-primary.yml"
    replace_once(
        path,
        '''      - 'src/valuation_engine/sanil_live_primary.py'\n      - 'src/valuation_engine/dcf_evaluators.py'\n''',
        '''      - 'src/valuation_engine/sanil_live_primary.py'\n      - 'src/valuation_engine/broker_runtime.py'\n      - 'tests/test_broker_runtime.py'\n      - 'src/valuation_engine/dcf_evaluators.py'\n''',
    )
    replace_once(
        path,
        '''      - 'src/valuation_engine/sanil_live_primary.py'\n      - 'src/valuation_engine/dcf_evaluators.py'\n''',
        '''      - 'src/valuation_engine/sanil_live_primary.py'\n      - 'src/valuation_engine/broker_runtime.py'\n      - 'tests/test_broker_runtime.py'\n      - 'src/valuation_engine/dcf_evaluators.py'\n''',
    )
    replace_once(
        path,
        '''          tests/test_sanil_live_primary.py\n          tests/test_incremental_capacity_dcf.py\n''',
        '''          tests/test_sanil_live_primary.py\n          tests/test_broker_runtime.py\n          tests/test_incremental_capacity_dcf.py\n''',
    )


def main() -> int:
    patch_broker_runtime()
    patch_live_runtime()
    patch_llm_context()
    patch_audit_binding()
    patch_report_form()
    patch_sanil()
    patch_sanil_tests()
    patch_report_tests()
    patch_workflows()
    print("Broker Research LIVE_PRIMARY integration patch applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
