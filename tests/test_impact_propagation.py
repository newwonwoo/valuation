from valuation_engine.impact_propagation import ImpactEdge, ImpactGraph, build_revalidation_request


def test_dirty_source_propagates_only_to_connected_assumptions():
    graph = ImpactGraph((
        ImpactEdge("power.grid", "mechanism:grid_scarcity", "supports"),
        ImpactEdge("mechanism:grid_scarcity", "assumption:transformer_backlog", "drives"),
        ImpactEdge("assumption:transformer_backlog", "company_segment:SANIL_transformer", "applies_to"),
        ImpactEdge("software.saas", "assumption:nrr", "drives"),
    ))
    req = build_revalidation_request("IEA_MES", ("power.grid",), graph)
    assert "mechanism:grid_scarcity" in req.affected_mechanisms
    assert "assumption:transformer_backlog" in req.affected_assumptions
    assert "company_segment:SANIL_transformer" in req.affected_company_segments
    assert "assumption:nrr" not in req.affected_assumptions
    assert req.requires_new_intrinsic_run
