from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
reg = yaml.safe_load((ROOT / "config/industry_source_registry.yaml").read_text(encoding="utf-8"))
foundation_reg = yaml.safe_load((ROOT / "config/foundation_source_registry.yaml").read_text(encoding="utf-8"))
docs = yaml.safe_load((ROOT / "data/industry_seed_documents.yaml").read_text(encoding="utf-8"))["documents"]
claims = yaml.safe_load((ROOT / "data/industry_seed_claims.yaml").read_text(encoding="utf-8"))["claims"]
mechs = yaml.safe_load((ROOT / "data/mechanism_candidates.yaml").read_text(encoding="utf-8"))["mechanisms"]
watch = yaml.safe_load((ROOT / "config/source_watch_registry.yaml").read_text(encoding="utf-8"))

sources = {s["id"]: s for s in reg["sources"]}
foundation_sources = {s["id"]: s for s in foundation_reg["sources"]}
all_source_ids = set(sources) | set(foundation_sources)
doc_map = {d["document_id"]: d for d in docs}
claim_map = {c["claim_id"]: c for c in claims}
errors=[]

if len(sources) != len(reg["sources"]): errors.append("duplicate source IDs")
if len(foundation_sources) != len(foundation_reg["sources"]): errors.append("duplicate foundation source IDs")
if set(sources) & set(foundation_sources): errors.append("source IDs collide across industry/foundation registries")
if len(doc_map) != len(docs): errors.append("duplicate document IDs")
if len(claim_map) != len(claims): errors.append("duplicate claim IDs")
if len({w['series_id'] for w in watch['sources']}) != len(watch['sources']): errors.append("duplicate watch series IDs")

for sid,s in sources.items():
    if s.get('access') == 'licensed' and s.get('public_fulltext_allowed'):
        errors.append(f"licensed source marked public_fulltext_allowed {sid}")

for d in docs:
    sid=d["source_id"]
    if sid not in sources:
        errors.append(f"unknown document source {sid}")
        continue
    src=sources[sid]
    if d.get('source_family') != src.get('family'):
        errors.append(f"document source family mismatch {d['document_id']}")
    for role in d.get('roles',[]):
        if role not in src.get('roles',[]): errors.append(f"document role {role} not allowed by source {sid}")

for c in claims:
    sid=c["source_id"]
    if sid not in sources:
        errors.append(f"unknown claim source {sid}")
        continue
    src=sources[sid]
    if c["document_id"] not in doc_map: errors.append(f"unknown claim document {c['document_id']}")
    if c.get('source_family') != src.get('family'): errors.append(f"claim source family mismatch {c['claim_id']}")
    if c.get('role') not in src.get('roles',[]): errors.append(f"claim role {c.get('role')} not allowed by source {sid}")
    if c["kind"] == "forecast" and c["role"] == "observed_state": errors.append(f"forecast leaked to observed state {c['claim_id']}")
    if c["kind"] == "policy_intent" and c["role"] == "observed_state": errors.append(f"policy intent leaked to observed state {c['claim_id']}")

for m in mechs:
    for cid in m["evidence_claim_ids"]:
        if cid not in claim_map: errors.append(f"unknown mechanism claim {cid}")
for w in watch["sources"]:
    if w["source_id"] not in all_source_ids: errors.append(f"unknown watch source {w['source_id']}")

if errors:
    raise SystemExit("\n".join(errors))
print(f"PASS industry_sources={len(sources)} foundation_sources={len(foundation_sources)} documents={len(docs)} claims={len(claims)} mechanisms={len(mechs)} watch_series={len(watch['sources'])}")
