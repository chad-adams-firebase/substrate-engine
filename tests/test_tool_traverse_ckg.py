"""CKG traversal against the real fixture graph — the ordered-calls
question is the acceptance test (phasing Phase 3)."""

from tests.conftest import build_tool_registry

RUN_RULES = "invoiceguard.spine.rules_engine.run_rules"

RULES_IN_ORDER = [
    "rule_rate_variance",
    "rule_unapproved_item",
    "rule_quantity_spike",
    "rule_duplicate_line",
    "rule_rush_fee_unjustified",
    "rule_markup_over_list",
    "rule_service_hours_excessive",
    "rule_contract_lapsed_rate",
    "rule_new_supplier",
    "rule_freight_overcharge",
    "rule_total_mismatch",
    "rule_split_billing",
]

# The same twelve rules in the order the module DEFINES them, which
# run_rules does not follow — contains answers "what lives here",
# callees answers "what runs, in order".
RULES_DEFINED_IN_ORDER = [
    "rule_rate_variance",
    "rule_unapproved_item",
    "rule_quantity_spike",
    "rule_duplicate_line",
    "rule_new_supplier",
    "rule_freight_overcharge",
    "rule_rush_fee_unjustified",
    "rule_markup_over_list",
    "rule_service_hours_excessive",
    "rule_contract_lapsed_rate",
    "rule_total_mismatch",
    "rule_split_billing",
]


def test_callees_of_run_rules_come_back_in_call_order(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke(
        "traverse_code_knowledge_graph", {"entry": RUN_RULES, "hop": "callees"}
    )
    assert invocation.status == "ok", invocation.error
    called = [node.qualified_name.rsplit(".", 1)[1] for node in invocation.output.nodes]
    # Per-line rules then invoice-level rules, exactly in source order,
    # after the contracts lookup.
    assert called[0] == "_supplier_contracts"
    assert called[1:] == RULES_IN_ORDER
    lines = [edge.line for edge in invocation.output.edges]
    assert lines == sorted(lines)
    assert invocation.manifest_ids  # machine rows carried their manifest


def test_entry_by_node_id_matches_entry_by_name(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    by_name = registry.invoke(
        "traverse_code_knowledge_graph", {"entry": RUN_RULES, "hop": "node"}
    )
    node_id = by_name.output.entry_node.id
    by_id = registry.invoke(
        "traverse_code_knowledge_graph", {"entry": node_id, "hop": "node"}
    )
    assert by_id.output.entry_node == by_name.output.entry_node


def test_callers_finds_who_calls_a_rule(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke(
        "traverse_code_knowledge_graph",
        {
            "entry": "invoiceguard.spine.rules_engine.rule_rate_variance",
            "hop": "callers",
        },
    )
    assert [n.qualified_name for n in invocation.output.nodes] == [RUN_RULES]


def test_reads_tables_hop_returns_table_edges(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke(
        "traverse_code_knowledge_graph",
        {
            "entry": "invoiceguard.spine.lapse_lifecycle.run_stale_sweep",
            "hop": "reads_tables",
        },
    )
    assert invocation.status == "ok", invocation.error
    assert "invoices" in {edge.target_table for edge in invocation.output.edges}


def test_conditionals_hop_surfaces_thresholds(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke(
        "traverse_code_knowledge_graph",
        {
            "entry": "invoiceguard.spine.rules_engine.rule_rate_variance",
            "hop": "conditionals",
        },
    )
    assert invocation.status == "ok"
    assert invocation.output.conditionals, "expected branch conditions"


def test_members_hop_lists_a_components_nodes(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke(
        "traverse_code_knowledge_graph",
        {"entry": "ig.spine.rules-engine", "hop": "members"},
    )
    assert invocation.status == "ok", invocation.error
    assert invocation.output.entry_component.id == "ig.spine.rules-engine"
    # Memberships are proposed at module granularity (module structure
    # is the L1 signal); functions hang off the module via the graph.
    names = {node.qualified_name for node in invocation.output.nodes}
    assert names == {"invoiceguard.spine.rules_engine"}


def test_contains_hop_lists_a_modules_definitions_in_source_order(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke(
        "traverse_code_knowledge_graph",
        {"entry": "invoiceguard.spine.rules_engine", "hop": "contains"},
    )
    assert invocation.status == "ok", invocation.error
    starts = [node.start_line for node in invocation.output.nodes]
    assert starts == sorted(starts)
    lines = [edge.line for edge in invocation.output.edges]
    assert lines == sorted(lines)
    names = [n.qualified_name.rsplit(".", 1)[1] for n in invocation.output.nodes]
    # Every rule function is a direct child of the module, in DEFINITION
    # order — which is not the call order run_rules uses (that is what
    # the callees hop answers; the two orders differing is the point).
    in_module_order = [n for n in names if n in RULES_IN_ORDER]
    assert in_module_order == RULES_DEFINED_IN_ORDER
    assert set(in_module_order) == set(RULES_IN_ORDER)
    assert "run_rules" in names
    assert invocation.manifest_ids


def test_component_to_functions_is_members_then_contains(tool_pack):
    # The two-hop ladder the router prompt documents: L1 memberships are
    # module-granularity, so component -> functions is members, then
    # contains from the member module.
    registry, _ = build_tool_registry(tool_pack)
    members = registry.invoke(
        "traverse_code_knowledge_graph",
        {"entry": "ig.spine.rules-engine", "hop": "members"},
    )
    assert members.status == "ok", members.error
    (module,) = members.output.nodes
    contained = registry.invoke(
        "traverse_code_knowledge_graph", {"entry": module.id, "hop": "contains"}
    )
    assert contained.status == "ok", contained.error
    names = {n.qualified_name.rsplit(".", 1)[1] for n in contained.output.nodes}
    assert set(RULES_IN_ORDER) <= names


def test_unknown_entry_is_an_error_with_a_component_hint(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    unknown = registry.invoke(
        "traverse_code_knowledge_graph", {"entry": "nope.nope", "hop": "callees"}
    )
    assert unknown.status == "error"

    component_as_node = registry.invoke(
        "traverse_code_knowledge_graph",
        {"entry": "ig.spine.rules-engine", "hop": "callees"},
    )
    assert component_as_node.status == "error"
    assert "members" in component_as_node.error


def test_members_with_a_function_name_steers_to_node_hop(tool_pack):
    # The acceptance L3 flail: hop='members' with a function name got
    # a dump of all component ids — steering the wrong way. The error
    # now names the node and the hop that works.
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke(
        "traverse_code_knowledge_graph",
        {"entry": RUN_RULES, "hop": "members"},
    )
    assert invocation.status == "error"
    assert "hop='node'" in invocation.error
    assert RUN_RULES in invocation.error
    assert "Components:" not in invocation.error  # no component dump
