"""MT-ABOUT gold (Fix Pass, R4): the anchored follow-up's positive
path — a rule that HAS evidence to describe. rate_variance carries the
threshold memo ("Rate variance: 15% over contract") and a CKG rule
function; line_note, MT-ANCHOR's anchor, carries neither, which is why
that row's turn 2 passed only through refusals. Turn 1's anchor is the
rule's finding count (a filter-sourced anchor); turn 3's "it" is the
supplier the rule flags most, C5's shape."""


def gold(world):
    (count,) = world.sql(
        "SELECT COUNT(*) AS n FROM findings WHERE rule_name = 'rate_variance'"
    )
    rows = world.sql(
        "SELECT s.code, s.name, COUNT(*) AS n FROM findings f "
        "JOIN invoices i ON i.id = f.invoice_id "
        "JOIN suppliers s ON s.id = i.supplier_id "
        "WHERE f.rule_name = 'rate_variance' "
        "GROUP BY s.code, s.name ORDER BY n DESC LIMIT 2"
    )
    (node,) = world.ckg.resolve_suffix("rule_rate_variance")
    return {
        "rule": "rate_variance",
        "fire_count": count["n"],
        "supplier": rows[0]["code"],
        "supplier_name": rows[0]["name"],
        "supplier_count": rows[0]["n"],
        "runner_up": rows[1]["code"],
        "rule_function": node.qualified_name,
    }
