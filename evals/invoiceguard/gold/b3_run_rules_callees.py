"""L2 gold: run_rules callees in call (line) order, from the CKG."""

QUALIFIED = "invoiceguard.spine.rules_engine.run_rules"


def gold(world):
    node = world.ckg.node_by_qualified_name[QUALIFIED]
    edges = world.ckg.callees(node.id)
    names = [
        world.ckg.node_by_id[edge.target_node_id].qualified_name.split(".")[-1]
        if edge.target_node_id is not None
        else edge.target_table
        for edge in edges
    ]
    return {
        "callee_count": len(names),
        "callees": names,
        "first_line": edges[0].line,
        "last_line": edges[-1].line,
    }
