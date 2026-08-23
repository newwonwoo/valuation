from pathlib import Path
import yaml

from valuation_engine.industry_dna import EconomicArchetype, IndustryDNAProfile, compose_modules
from valuation_engine.method_capabilities import load_method_capability_registry

ROOT=Path(__file__).resolve().parents[1]
tax=yaml.safe_load((ROOT/'config/industry_taxonomy.yaml').read_text(encoding='utf-8'))
mods=yaml.safe_load((ROOT/'config/archetype_module_registry.yaml').read_text(encoding='utf-8'))
sectors=yaml.safe_load((ROOT/'config/sector_adapter_registry.yaml').read_text(encoding='utf-8'))
impact=yaml.safe_load((ROOT/'config/impact_graph_seed.yaml').read_text(encoding='utf-8'))
mechs=yaml.safe_load((ROOT/'data/mechanism_candidates.yaml').read_text(encoding='utf-8'))['mechanisms']
errors=[]
archetypes=set(tax['economic_archetypes'])
if set(mods['modules']) != archetypes:
    errors.append(f"archetype registry mismatch taxonomy_only={sorted(archetypes-set(mods['modules']))} module_only={sorted(set(mods['modules'])-archetypes)}")

# Code and YAML must expose the same method contract; never maintain two silent truths.
yaml_pairs=set()
for name,spec in mods['modules'].items():
    a=EconomicArchetype(name)
    profile=IndustryDNAProfile('registry', 'registry.validation', (a,), 'na','na','na','na','na','na','na','na',('REGISTRY',))
    code_methods=set(compose_modules(profile).allowed_valuation_methods)
    yaml_methods=set(spec.get('allowed_valuation_methods',[]))
    yaml_pairs.update((name, method) for method in yaml_methods)
    if code_methods != yaml_methods:
        errors.append(f"method registry drift {name}: code={sorted(code_methods)} yaml={sorted(yaml_methods)}")

# The capability registry owns execution metadata only. Exact archetype/method permission
# remains in the Industry DNA contracts, so the pair universes must be identical.
try:
    method_capabilities=load_method_capability_registry(ROOT/'config/valuation_method_capability_registry.yaml')
    method_capabilities.validate(
        archetype_registry_path=ROOT/'config/archetype_module_registry.yaml',
        repo_root=ROOT,
    )
    capability_pairs={item.identity for item in method_capabilities.capabilities}
    if capability_pairs != yaml_pairs:
        errors.append(
            'method capability drift '
            f'yaml_only={sorted(yaml_pairs-capability_pairs)} '
            f'capability_only={sorted(capability_pairs-yaml_pairs)}'
        )
except (OSError, ValueError) as exc:
    errors.append(f"method capability registry invalid: {exc}")

for name,spec in sectors['adapters'].items():
    for a in spec.get('default_archetypes',[])+spec.get('optional_archetypes',[]):
        if a not in archetypes: errors.append(f"unknown archetype {a} in {name}")

# Taxonomy adapter list and concrete registry must be exactly synchronized.
tax_adapters=set()
for family, names in tax.get('sector_adapters_phase1',{}).items():
    for name in names:
        # historical taxonomy uses `power.*`, `software.*`, etc.; preserve explicit family names.
        tax_adapters.add(f"{family}.{name}")
registry_adapters=set(sectors['adapters'])
if tax_adapters != registry_adapters:
    errors.append(f"sector adapter drift taxonomy_only={sorted(tax_adapters-registry_adapters)} registry_only={sorted(registry_adapters-tax_adapters)}")

mechanism_ids={m['mechanism_id'] for m in mechs}
for edge in impact['edges']:
    for node in edge[:2]:
        if isinstance(node,str) and node.startswith('mechanism:') and node.split(':',1)[1] not in mechanism_ids:
            errors.append(f"unknown impact mechanism {node}")
if errors: raise SystemExit('\n'.join(errors))
print(
    f"PASS archetypes={len(archetypes)} sector_adapters={len(sectors['adapters'])} "
    f"impact_edges={len(impact['edges'])} method_contracts_synced=True "
    f"method_capability_bindings={len(method_capabilities.capabilities)}"
)
