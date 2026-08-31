"""W5/W4 gold (play pass): the 12 audit rules in the rules engine's
code — sense (1) of the "audit rules" concept. Counted from the CKG,
never remembered; the names anchor W4's association tripwire."""


def gold(world):
    rules = sorted(
        node.qualified_name.rsplit(".", 1)[1]
        for node in world.ckg.node_by_qualified_name.values()
        if node.kind == "function"
        and node.qualified_name.startswith(
            "invoiceguard.spine.rules_engine.rule_"
        )
    )
    return {"value": len(rules), "rule_names": rules}
