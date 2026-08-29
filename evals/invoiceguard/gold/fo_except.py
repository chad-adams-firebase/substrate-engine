"""FO-EXCEPT gold (fix pass 3 fan-out trap #2, a different join pair
from MT2): invoices with at least one valid-exception finding. The
naive COUNT(*) over invoices -> findings -> finding_feedback counts
excepted findings, not invoices."""


def gold(world):
    (distinct,) = world.sql(
        "SELECT COUNT(DISTINCT i.id) AS n FROM invoices i "
        "JOIN findings f ON f.invoice_id = i.id "
        "JOIN finding_feedback ff ON ff.finding_id = f.id "
        "WHERE ff.valid_exception"
    )
    (naive,) = world.sql(
        "SELECT COUNT(*) AS n FROM invoices i "
        "JOIN findings f ON f.invoice_id = i.id "
        "JOIN finding_feedback ff ON ff.finding_id = f.id "
        "WHERE ff.valid_exception"
    )
    return {"value": distinct["n"], "naive": naive["n"]}
