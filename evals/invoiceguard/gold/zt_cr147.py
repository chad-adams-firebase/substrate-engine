"""ZT-CR147 gold (fix pass 3 zero trap #2, on findings): compliance
findings are named compliance_<code>, so the naive rule_name =
'CR-147' returns 0. Both correct paths agree — the prefixed finding
name and the compliance_rules row count."""


def gold(world):
    (by_name,) = world.sql(
        "SELECT COUNT(*) AS n FROM findings "
        "WHERE rule_name = 'compliance_CR-147'"
    )
    (by_rule,) = world.sql(
        "SELECT COUNT(*) AS n FROM compliance_rules WHERE rule_code = 'CR-147'"
    )
    (naive,) = world.sql(
        "SELECT COUNT(*) AS n FROM findings WHERE rule_name = 'CR-147'"
    )
    assert by_name["n"] == by_rule["n"], "the two correct paths disagree"
    return {"value": by_name["n"], "naive": naive["n"]}
