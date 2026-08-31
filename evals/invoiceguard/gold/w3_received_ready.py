"""W3 gold (play pass): average RECEIVED → READY hours, pairing the
transition that ENTERS the status with the one that LEAVES it — the
time_in_status gotcha's shape. The play answer read both timestamps
from the single READY row and verified 0:00:00."""


def gold(world):
    (row,) = world.sql(
        "SELECT AVG((EPOCH(lv.at) - EPOCH(en.at)) / 3600.0) AS avg_hours "
        "FROM invoice_history en "
        "JOIN invoice_history lv ON lv.invoice_id = en.invoice_id "
        " AND lv.from_status = 'RECEIVED' AND lv.to_status = 'READY' "
        "WHERE en.to_status = 'RECEIVED'"
    )
    return {"avg_hours": row["avg_hours"]}
