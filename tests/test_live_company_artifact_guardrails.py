from hashlib import sha256

from valuation_engine.live_company_artifact import _audit_hash_preimage
from valuation_engine.records import AuditFinding, AuditReport


def test_live_artifact_audit_proof_replays_capacity_guardrail_hash():
    capacity_hash = "a" * 64
    report = AuditReport(
        (
            AuditFinding(
                "capacity_double_count",
                True,
                True,
                "capacity guardrail passed",
            ),
        )
    )
    preimage = _audit_hash_preimage(
        run_id="RUN",
        ledger_snapshot_hash="L" * 64,
        assumption_set_hash="A" * 64,
        scenario_set_hash="S" * 64,
        valuation_hash="V" * 64,
        external_guardrail_hashes=(capacity_hash,),
        report=report,
    )

    expected = "\n".join(
        (
            "RUN",
            "L" * 64,
            "A" * 64,
            "S" * 64,
            "V" * 64,
            capacity_hash,
            "capacity_double_count|True|True|capacity guardrail passed",
        )
    )
    assert preimage == expected
    assert sha256(preimage.encode("utf-8")).hexdigest() != sha256(
        preimage.replace(f"\n{capacity_hash}\n", "\n").encode("utf-8")
    ).hexdigest()
