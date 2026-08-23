"""Recovery gold: 'health' is a genuinely ambiguous bare suffix —
the module and its function — so the tool must steer, and the
router gets exactly one licensed retry."""


def gold(world):
    candidates = world.ckg.resolve_suffix("health")
    return {
        "candidate_count": len(candidates),
        "candidates": [node.qualified_name for node in candidates],
    }
