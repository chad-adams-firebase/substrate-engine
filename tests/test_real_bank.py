"""The committed InvoiceGuard bank must load under pytest. Before the
pin pass no test touched the real bank — every synthetic-bank test
passed while a malformed row edit would surface only at grade time on
the Mac. This module also pins the pass's row shapes and the one
CLAUDE.md line the pass wrote into law."""

from pathlib import Path

from engine.eval.bank import load_bank

ROOT = Path(__file__).resolve().parents[1]


def test_real_invoiceguard_bank_loads():
    bank = load_bank(ROOT / "evals" / "invoiceguard")
    assert len(bank.rows) > 30
    assert len(bank.bank_hash) == 16


def test_rec_ckg_carries_the_clarify_arm():
    bank = load_bank(ROOT / "evals" / "invoiceguard")
    row = next(r for r in bank.rows if r.id == "REC-CKG")
    assert row.expect.exit == [0, 2, 4]
    clarify_arms = [a for a in row.expect.assertions if a.at_exit == [4]]
    assert len(clarify_arms) == 1
    assert clarify_arms[0].kind == "contains"
    retry_arms = [a for a in row.expect.assertions if a.kind == "retry_count"]
    assert retry_arms[0].at_exit == [0, 2]


def test_play_route_rows_split_outcome_from_mechanism():
    bank = load_bank(ROOT / "evals" / "invoiceguard")
    rows = {r.id: r for r in bank.rows}
    (r1_route,) = [
        a for a in rows["PLAY-R1"].expect.assertions if a.kind == "route"
    ]
    assert r1_route.mode == "must_include_any_of"
    (r3_route,) = [
        a for a in rows["PLAY-R3"].expect.assertions if a.kind == "route"
    ]
    assert r3_route.mode == "must_include"  # the mechanism probe stays pure
