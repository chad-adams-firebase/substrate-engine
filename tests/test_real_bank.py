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


def test_claude_md_carries_the_pin_hygiene_law():
    """The breach cost an evening because the pin landed inside the
    play pass's commit range; the law is one line and this pins it."""
    text = (ROOT / "CLAUDE.md").read_text()
    assert (
        "Model pin changes are isolated commits and trigger a full bank "
        "re-run before any other change lands." in text
    )


def test_block_2_ledger_state():
    """Phase 5 Block 2 and coverage-pass rulings on the bank: P-N11 is
    retired (its scenario starved by the definitional vocabulary),
    W4/W2 keep the ASSOC block (no association code has landed) and W4
    gates on reaching a drafted answer, S4's WBV-S4 block came off
    (three stable XPASS runs on one model pin attribute to the pin —
    the standard is revised), and B2's dump guard keeps its
    per-assertion O1 ref until a live run passes it. Each change is a
    deliberate bank edit that updates this test."""
    bank = load_bank(ROOT / "evals" / "invoiceguard")
    rows = {r.id: r for r in bank.rows}
    assert "P-N11" not in rows
    assert rows["W4"].xfail.ref == "ASSOC" and rows["W2"].xfail.ref == "ASSOC"
    assert rows["W4"].expect.setup.exit == [0, 2]
    assert rows["S4"].xfail is None
    (dump,) = [a for a in rows["B2"].expect.assertions if a.kind == "no_text_block_dump"]
    assert dump.xfail_ref == "O1"


def test_play_session_2_rows_carry_executed_gold():
    """The coverage pass's rows: each names a gold script and, where
    the session's wrong answer had a caption tripwire, a not_contains
    that breaches on the mechanism."""
    bank = load_bank(ROOT / "evals" / "invoiceguard")
    rows = {r.id: r for r in bank.rows}
    for row_id in ("W-A", "W-B", "W-C", "R-A", "F1"):
        assert rows[row_id].gold is not None, row_id
        assert rows[row_id].provenance == "user-sourced"
    kinds = {a.kind for a in rows["W-C"].expect.assertions}
    assert {"name_from_gold", "numeric_from_gold"} <= kinds
    assert rows["F1"].expect.exit == [0, 2]
