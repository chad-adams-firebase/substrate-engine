"""L3 gold: where rule_rate_variance lives, from the CKG node."""


def gold(world):
    (node,) = world.ckg.resolve_suffix("rule_rate_variance")
    return {
        "qualified_name": node.qualified_name,
        "file_path": node.file_path,
        "start_line": node.start_line,
        "end_line": node.end_line,
    }
