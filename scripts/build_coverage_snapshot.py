from __future__ import annotations
from pathlib import Path
import yaml

from valuation_engine.coverage_qa import CoverageEvidence, score_coverage

ROOT = Path(__file__).resolve().parents[1]
claims = yaml.safe_load((ROOT / "data/industry_seed_claims.yaml").read_text(encoding="utf-8"))["claims"]
watch = yaml.safe_load((ROOT / "config/source_watch_registry.yaml").read_text(encoding="utf-8"))["sources"]
mechs = yaml.safe_load((ROOT / "data/mechanism_candidates.yaml").read_text(encoding="utf-8"))["mechanisms"]

watched_nodes = {n for w in watch for n in w.get("impact_nodes", [])}
claim_by_id = {c["claim_id"]: c for c in claims}

# Exact claim-node evidence plus mechanism-level source-family corroboration propagated only
# to the mechanism's declared industry_scope. This avoids fuzzy parent inheritance while
# not understating cross-source mechanisms whose supporting facts live on adjacent subnodes.
by_node: dict[str, dict[tuple[str, str], int]] = {}
mechanism_nodes: set[str] = set()
for c in claims:
    node = c["industry_node"]
    key = (c["source_family"], c["role"])
    by_node.setdefault(node, {}).setdefault(key, 0)
    by_node[node][key] += 1

for m in mechs:
    families = {
        claim_by_id[cid]["source_family"]
        for cid in m.get("evidence_claim_ids", [])
        if cid in claim_by_id
    }
    for node in m.get("industry_scope", []):
        mechanism_nodes.add(node)
        for family in families:
            by_node.setdefault(node, {}).setdefault((family, "mechanism_corroboration"), 0)

rows=[]
for node in sorted(by_node):
    ev=[]
    for (family, role), count in by_node[node].items():
        watched = node in watched_nodes or any(node.startswith(w + ".") or w.startswith(node + ".") for w in watched_nodes)
        mechanism = node in mechanism_nodes or any(node.startswith(m + ".") or m.startswith(node + ".") for m in mechanism_nodes)
        ev.append(CoverageEvidence(node, family, role, count, watched, mechanism))
    s=score_coverage(node, ev)
    rows.append({
        "industry_node": s.industry_node,
        "independent_source_families": s.independent_source_families,
        "roles": list(s.roles),
        "claim_count": s.claim_count,
        "watch_coverage": s.watch_coverage,
        "mechanism_coverage": s.mechanism_coverage,
        "score": s.score,
        "grade": s.grade,
        "gaps": list(s.gaps),
    })

out={
    "snapshot_version":"2026-08-21.3",
    "method":"deterministic heuristic; exact claim-node scoring plus declared mechanism-scope source-family corroboration; not an investment-quality rating",
    "coverage":rows,
}
(ROOT / "data/source_coverage_snapshot.yaml").write_text(yaml.safe_dump(out, allow_unicode=True, sort_keys=False), encoding="utf-8")
print(f"WROTE {len(rows)} coverage nodes")
