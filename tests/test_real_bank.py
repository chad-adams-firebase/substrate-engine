"""The committed InvoiceGuard bank must load under pytest. Before the
pin pass no test touched the real bank — every synthetic-bank test
passed while a malformed row edit would surface only at grade time on
the Mac. This module also pins the pass's row shapes and the one
CLAUDE.md line the pass wrote into law."""

import re
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


def test_w3_grades_its_duration_in_seconds():
    """Duration pass: W3's gold is hours and the assertion says so, so a
    rendered "60 minutes" (post-coverage rep 5) is the gold and "0
    seconds" (rep 4) is not — the unit-blind arm never sees the row."""
    bank = load_bank(ROOT / "evals" / "invoiceguard")
    row = next(r for r in bank.rows if r.id == "W3")
    (numeric,) = [a for a in row.expect.assertions if a.kind == "numeric_from_gold"]
    assert numeric.unit == "hours"
    assert row.expected_gold == {"avg_hours": 1.0}


def test_s2_grades_the_number_it_computes():
    """Post-coverage S2 (0/5): every rep computed 0.9545 and the table
    reps failed a prose assertion (`\\byes\\b`) a table never says. The
    numeric is the check; the item-code contains pins the SQL's filter
    through the caption."""
    bank = load_bank(ROOT / "evals" / "invoiceguard")
    row = next(r for r in bank.rows if r.id == "S2")
    kinds = [(a.kind, getattr(a, "pattern", None)) for a in row.expect.assertions]
    assert ("contains", "SVC-4410") in kinds
    assert not any(pattern and "yes" in pattern for _, pattern in kinds)
    (numeric,) = [a for a in row.expect.assertions if a.kind == "numeric_from_gold"]
    assert numeric.field == "rate"


def test_assoc_rows_are_deliberate_keeps():
    """Duration pass: W4 and W2 keep their ASSOC blocks until association
    verification lands; the grader reports a keep, never a deletion
    prompt, on their XPASSes."""
    bank = load_bank(ROOT / "evals" / "invoiceguard")
    rows = {r.id: r for r in bank.rows}
    for row_id in ("W4", "W2"):
        assert rows[row_id].xfail.keep_until == "association verification", row_id


def test_guard_pass_ledger_state():
    """Guard pass rulings on the bank: REC-SQL counts a rephrase before
    the bounce as recovery (0 or 1 bounce), and AMB2's exit-0 content
    check is the breach-carrying numeric alone — no caption regex on
    invoice_history, because a correct answer may legitimately read it
    (the latest transition per invoice is current status) and land at
    78; the mechanism is diagnosed in the verdict by
    run_sql.entity_count_exceeds_table instead."""
    bank = load_bank(ROOT / "evals" / "invoiceguard")
    rows = {r.id: r for r in bank.rows}
    (retry,) = [a for a in rows["REC-SQL"].expect.assertions if a.kind == "retry_count"]
    assert retry.errors == [0, 1]
    exit_zero = [a for a in rows["AMB2"].expect.assertions if a.at_exit == [0]]
    assert [a.kind for a in exit_zero] == ["contains", "numeric_from_gold"]
    (numeric,) = [a for a in exit_zero if a.kind == "numeric_from_gold"]
    assert numeric.field == ["ready", "not_closed"] and numeric.breach is True


def test_mt4_exercises_the_summary_live():
    """Phase 5 Block 4: the one row whose window folds the summary
    within its turns, so the summarizer prompt is under the bank."""
    bank = load_bank(ROOT / "evals" / "invoiceguard")
    row = next(r for r in bank.rows if r.id == "MT4")
    assert row.context is not None
    assert row.context.last_n_turns == 1 and row.context.summary_refresh_after_turns == 1
    assert len(row.turns) == 4
    kinds = [a.kind for a in row.turns[3].expect.assertions]
    assert kinds.count("summary_contains") == 2
    assert "summary_excludes_figures" in kinds and "route" in kinds
    assert all(r.context is None for r in bank.rows if r.id != "MT4")


def test_backlog_pass_rows_reconstruct_the_sessions_two_findings():
    """Backlog Pass: MT-KEY and MT-ANCHOR carry the 30-turn session's
    wrong-but-verified shapes with executed gold and breach semantics
    stated in their notes; the bank is 68 rows since the Fix Pass."""
    bank = load_bank(ROOT / "evals" / "invoiceguard")
    assert len(bank.rows) == 68
    key = next(r for r in bank.rows if r.id == "MT-KEY")
    anchor = next(r for r in bank.rows if r.id == "MT-ANCHOR")
    for row in (key, anchor):
        assert row.provenance == "user-sourced" and row.threshold == 0.8
        assert row.context is None
        assert "Breach semantics" in row.note
    assert [t.question for t in key.turns] == [
        "Which invoice has the highest invoice total?",
        "What was that invoice's history?",
    ]
    sentinel = next(a for a in key.turns[1].expect.assertions if a.kind == "not_contains")
    assert sentinel.regex and sentinel.breach and "03-20" in sentinel.pattern
    tolerant = [a for a in key.turns[1].expect.assertions if not a.breach]
    assert sorted(a.kind for a in tolerant) == ["name_from_gold", "pattern_count"]
    # Rider Pass: either identifier names the invoice, both executed gold.
    identity = next(a for a in key.turns[0].expect.assertions if a.kind == "name_from_gold")
    assert identity.fields() == ["invoice_number", "invoice_id"] and not identity.forbid_bare_ids
    assert isinstance(key.expected_gold["invoice_id"], str) and "false alarm" in key.note
    assert [t.question for t in anchor.turns] == [
        "Which rule fires most often?",
        "Tell me more about that rule.",
        "How many findings has it produced?",
    ]
    assert anchor.turns[1].expect.exit == [0, 2, 3]
    about = next(a for a in anchor.turns[1].expect.assertions if a.kind == "contains")
    assert about.regex and about.pattern.endswith("line_note\\.") and about.at_exit == [0, 2]
    assert re.search(about.pattern, "About: line_note.") and re.search(about.pattern, "About: rule line_note.")
    assert not re.search(about.pattern, "About: new_supplier.")
    drift = next(a for a in anchor.turns[1].expect.assertions if a.kind == "not_contains")
    assert drift.pattern == "new_supplier" and drift.breach
    # Rider Pass: the assertions admit the designed outcomes — they stand
    # down only where the anchor check spoke, and turn 3 accepts the
    # caught drift at exit 2 while 197 at exit 0 stays the breach.
    assert about.unless_finding == drift.unless_finding == "anchor.entity_mismatch"
    assert anchor.turns[2].expect.exit == [0, 2]
    count = next(a for a in anchor.turns[2].expect.assertions if a.kind == "numeric_from_gold")
    assert count.field == "fire_count" and count.breach and count.unless_finding == "anchor.entity_mismatch"
    assert "wrong-count-caught passes" in anchor.note


def test_fix_pass_row_shows_the_anchored_follow_ups_positive_path():
    """Fix Pass R4: MT-ABOUT anchors a rule with evidence to describe,
    so turn 2 answering — About line, content — is the modal outcome
    and a refusal is a miss; turn 3's "it" runs on the clean path."""
    bank = load_bank(ROOT / "evals" / "invoiceguard")
    row = next(r for r in bank.rows if r.id == "MT-ABOUT")
    assert row.provenance == "scripted" and row.threshold == 0.8 and row.context is None
    assert [t.question for t in row.turns] == [
        "How many findings has the rate_variance rule produced?",
        "Tell me more about that rule.",
        "Which supplier does it flag most often?",
    ]
    assert row.turns[1].expect.exit == [0, 2]
    about = next(a for a in row.turns[1].expect.assertions if a.kind == "contains" and a.regex)
    assert re.search(about.pattern, "About: rate_variance.") and re.search(about.pattern, "About: the rule rate_variance.")
    drift = next(a for a in row.turns[1].expect.assertions if a.kind == "not_contains")
    assert drift.regex and drift.breach and drift.at_exit == [0, 2]
    assert re.search(drift.pattern, "About: new_supplier.") and not re.search(drift.pattern, "About: rule rate_variance.")
    count = next(a for a in row.turns[2].expect.assertions if a.kind == "numeric_from_gold")
    assert not count.breach
    name = next(a for a in row.turns[2].expect.assertions if a.kind == "name_from_gold")
    assert name.breach
    assert "miss" in row.note and "breach" in row.note
