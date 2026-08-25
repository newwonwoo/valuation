from pathlib import Path

from valuation_engine.live_company_acceptance import validate_live_company_acceptance


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    summary = validate_live_company_acceptance(
        ROOT / "config" / "live_company_acceptance.yaml",
        repo_root=ROOT,
    )
    print(
        "LIVE company acceptance: "
        f"ready={len(summary.ready)} blocked={len(summary.blocked)} "
        f"blocked_companies={','.join(summary.blocked) or '-'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
