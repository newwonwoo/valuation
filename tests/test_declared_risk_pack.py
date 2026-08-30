"""The discount rate's front door, and the ways in that must stay shut.

The declared risk pack is operator judgment entering the highest-leverage seat
in a DCF — the denominator. These tests are attack-shaped: a pack written for
another company, a pack that smuggles the target in as its own Beta peer, a
peer set with no reason, a reference with no provenance, a level missing — each
must fail closed at load or at bind, never dilute into a default.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from valuation_engine.declared_risk_pack import (
    BETA_SELECTION_METRICS,
    DeclaredRiskPackError,
    declared_risk_collector,
    load_declared_risk_pack,
)
from valuation_engine.evidence_collection import EvidenceCollectionRequest
from valuation_engine.live_primary_adapters import ResolvedCompanyIdentity
from valuation_engine.records import EvidenceSourceLayer


TARGET = "KR:DART:00888801"
AS_OF = "2026-08-27"


def _peer(peer_id: str, beta: float, debt: float, equity: float) -> dict:
    return {
        "peer_id": peer_id,
        "beta": {
            "benchmark": "코스피",
            "beta": beta,
            "observations": 250,
            "start_date": "2025-08-20",
            "end_date": "2026-08-20",
        },
        "capital": {
            "debt": debt,
            "equity_market_value": equity,
            "tax_rate": 0.24,
            "as_of": "2026-06-30",
            "source_ref": f"https://probe.invalid/capital/{peer_id}",
        },
        "beta_source_ref": f"https://probe.invalid/krx/beta/{peer_id}",
    }


def _payload() -> dict:
    return {
        "target_id": TARGET,
        "as_of": AS_OF,
        "source_ref": "https://probe.invalid/risk-pack/daeyang",
        "cash_flow_currency": "KRW",
        "risk_free_rate": {
            "time": "20260820", "value": 3.10, "unit": "연%",
            "name": "국고채 10년",
            "source_ref": "https://ecos.bok.or.kr/api/rf-10y",
        },
        "country_risk": {
            "country": "Korea", "as_of": "2026-08-01",
            "mature_market_erp": 0.0508, "country_risk_premium": 0.0057,
            "total_equity_risk_premium": 0.0565,
            "adjusted_default_spread": 0.0030,
            "corporate_tax_rate": 0.24, "rating": "AA",
        },
        "marginal_debt": {
            "series": {
                "time": "20260820", "value": 4.35, "unit": "연%",
                "name": "회사채 AA- 3년",
                "source_ref": "https://ecos.bok.or.kr/api/corp-aa-minus-3y",
            },
            "credit_rating": "AA-", "maturity": "3Y",
            "rating_source_ref": "https://probe.invalid/rating/issuer",
        },
        "beta_levels": {
            "L1_BROAD_SECTOR": {
                "selection_rationale": "KOSPI 대형 산업재 상장사 — 광의 섹터 사전확률로 사용.",
                "risk_driver_features": ["industrial cyclicality"],
                "peers": [_peer("PEER-IND-1", 1.02, 4200, 9800),
                          _peer("PEER-IND-2", 0.96, 3100, 11200)],
            },
            "L2_INDUSTRY": {
                "selection_rationale": "국내 상장 조선업 동종사 — 수주-인도 사이클 공유.",
                "risk_driver_features": ["order cycle"],
                "peers": [_peer("PEER-SHIP-1", 1.24, 5200, 7400)],
            },
            "L3_RISK_DRIVER_SUBINDUSTRY": {
                "selection_rationale": "상선 중심 야드 — 잔고 회전이 유사한 하위군.",
                "risk_driver_features": ["backlog duration"],
                "peers": [_peer("PEER-YARD-1", 1.31, 6100, 6900)],
            },
            "L4_ECONOMIC_TWINS": {
                "selection_rationale": "수주잔고 3년치 이상 — 경제적 쌍둥이 조건 일치.",
                "risk_driver_features": ["capacity intensity", "lead time"],
                "peers": [_peer("PEER-TWIN-1", 1.27, 5600, 7200)],
            },
        },
    }


def _write(tmp_path: Path, payload: dict) -> str:
    path = tmp_path / "risk_pack.yaml"
    path.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return str(path)


def _identity(target_id: str = TARGET, ticker: str = "900881") -> ResolvedCompanyIdentity:
    return ResolvedCompanyIdentity(
        target_id=target_id,
        legal_name="대양중공업",
        ticker=ticker,
        jurisdiction="KR",
        external_ids=(("corp_code", "00888801"), ("stock_code", ticker)),
        source_refs=("https://opendart.fss.or.kr/corpCode",),
    )


def test_a_valid_pack_loads_binds_and_produces_the_risk_chain(tmp_path):
    declared = load_declared_risk_pack(_write(tmp_path, _payload()))
    universe = declared.beta_universe()
    inputs = declared.wacc_inputs()
    assert len(universe.levels) == 4
    assert inputs.risk_free_rate.value == pytest.approx(0.031)
    assert inputs.marginal_pre_tax_cost_of_debt.value == pytest.approx(0.0435)
    assert declared.selection_evidence_ids() == tuple(
        f"RISK:{TARGET}:{metric}" for metric in BETA_SELECTION_METRICS
    )


def test_the_pack_is_bound_to_one_target(tmp_path):
    declared = load_declared_risk_pack(_write(tmp_path, _payload()))
    with pytest.raises(DeclaredRiskPackError, match="cross-company reuse"):
        declared.assert_target("KR:DART:99999999")


def test_the_target_may_not_be_its_own_beta_peer(tmp_path):
    """Smuggling the target into its peer set re-admits its market cap.

    The peer-normalized structure exists so the target's own market
    capitalization never shapes its discount rate; a 'peer' row whose id is the
    target's ticker or corp code is that leakage wearing a costume.
    """
    payload = _payload()
    payload["beta_levels"]["L2_INDUSTRY"]["peers"].append(
        _peer("900881", 1.20, 5100, 7800)  # the target's own stock code
    )
    declared = load_declared_risk_pack(_write(tmp_path, payload))
    with pytest.raises(DeclaredRiskPackError, match="target itself as Beta peer"):
        declared.assert_target_not_a_peer(_identity())


def test_a_peer_set_without_a_substantive_rationale_is_refused(tmp_path):
    payload = _payload()
    payload["beta_levels"]["L1_BROAD_SECTOR"]["selection_rationale"] = "peers"
    with pytest.raises(DeclaredRiskPackError, match="substantive selection_rationale"):
        load_declared_risk_pack(_write(tmp_path, payload))


def test_a_missing_level_is_a_missing_judgment_not_a_default(tmp_path):
    payload = _payload()
    del payload["beta_levels"]["L4_ECONOMIC_TWINS"]
    with pytest.raises(DeclaredRiskPackError, match="L4_ECONOMIC_TWINS"):
        load_declared_risk_pack(_write(tmp_path, payload))


def test_non_http_provenance_is_refused_everywhere(tmp_path):
    for mutate in (
        lambda p: p.update(source_ref="file:///tmp/x"),
        lambda p: p["risk_free_rate"].update(source_ref="ecos"),
        lambda p: p["marginal_debt"].update(rating_source_ref="memo"),
        lambda p: p["beta_levels"]["L1_BROAD_SECTOR"]["peers"][0].update(
            beta_source_ref="krx"
        ),
        lambda p: p["beta_levels"]["L1_BROAD_SECTOR"]["peers"][0]["capital"].update(
            source_ref="dart"
        ),
    ):
        payload = _payload()
        mutate(payload)
        with pytest.raises(DeclaredRiskPackError, match="HTTP provenance"):
            load_declared_risk_pack(_write(tmp_path, payload))


def test_a_pack_that_cannot_produce_valid_wacc_inputs_fails_at_load(tmp_path):
    payload = _payload()
    payload["risk_free_rate"]["unit"] = "unknown"  # implicit rate unit
    with pytest.raises(Exception, match="percent/ratio"):
        load_declared_risk_pack(_write(tmp_path, payload))


def test_country_risk_lambda_requires_an_exposure_source(tmp_path):
    payload = _payload()
    payload["country_risk_lambda"] = 0.5
    with pytest.raises(DeclaredRiskPackError, match="HTTP provenance"):
        load_declared_risk_pack(_write(tmp_path, payload))
    payload["country_risk_exposure_source_ref"] = (
        "https://probe.invalid/filing/foreign-revenue"
    )
    declared = load_declared_risk_pack(_write(tmp_path, payload))
    assert declared.wacc_inputs().country_risk_lambda == 0.5


def test_the_collector_serves_peer_selection_evidence_with_the_file_fingerprint(tmp_path):
    declared = load_declared_risk_pack(_write(tmp_path, _payload()))
    collector = declared_risk_collector(declared, segment_id="core")
    batch = collector(
        EvidenceCollectionRequest(
            target_id=TARGET, required_metrics=BETA_SELECTION_METRICS
        )
    )
    assert len(batch.records) == 4
    assert batch.source_fingerprint == declared.file_sha256
    for record in batch.records:
        assert record.source_layer is EvidenceSourceLayer.ANALYST_UNDERWRITING
        assert record.id.startswith(f"RISK:{TARGET}:beta_selection_")
        assert "rationale=" in record.notes
    with pytest.raises(DeclaredRiskPackError, match="cross-company reuse"):
        collector(
            EvidenceCollectionRequest(
                target_id="KR:DART:99999999",
                required_metrics=BETA_SELECTION_METRICS,
            )
        )


def test_editing_one_digit_changes_the_batch_fingerprint(tmp_path):
    original = load_declared_risk_pack(_write(tmp_path, _payload()))
    payload = copy.deepcopy(_payload())
    payload["beta_levels"]["L2_INDUSTRY"]["peers"][0]["beta"]["beta"] = 1.25
    edited = load_declared_risk_pack(_write(tmp_path, payload))
    assert original.file_sha256 != edited.file_sha256


@pytest.mark.parametrize(
    "mutate",
    (
        lambda p: p.update(as_of="2026-08-28"),
        lambda p: p["risk_free_rate"].update(time="20260828"),
        lambda p: p["country_risk"].update(as_of="2026-08-28"),
        lambda p: p["marginal_debt"]["series"].update(time="20260828"),
        lambda p: p["beta_levels"]["L1_BROAD_SECTOR"]["peers"][0]["beta"].update(
            end_date="2026-08-28"
        ),
        lambda p: p["beta_levels"]["L1_BROAD_SECTOR"]["peers"][0]["capital"].update(
            as_of="2026-08-28"
        ),
    ),
)
def test_every_future_dated_risk_observation_is_rejected(tmp_path, mutate):
    payload = _payload()
    mutate(payload)
    with pytest.raises(DeclaredRiskPackError, match="after run cutoff"):
        load_declared_risk_pack(
            _write(tmp_path, payload), run_as_of=AS_OF
        )
