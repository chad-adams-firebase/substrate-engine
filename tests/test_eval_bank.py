"""Bank loading: hand-authored YAML rows fail loudly on typos, gold
references must exist, and the bank hash pins a report to the exact
rows that produced it."""

import pytest

from engine.eval.bank import BankLoadError, LoadedBank, load_bank

EVAL_YAML = "default_runs: 2\ndefault_threshold: 1.0\npack: ../pack\n"

ROW_B5 = """\
- id: B5
  provenance: scripted
  category: data
  question: "How many invoices received last week had findings?"
  gold: gold/b5.py
  expected_gold: {value: 146}
  expect:
    exit: [0]
    assertions:
      - {kind: nonempty}
      - {kind: numeric_from_gold, field: value}
      - {kind: route, mode: must_include, tools: [run_sql]}
"""

ROW_MT = """\
- id: MT1
  provenance: user-sourced
  category: multiturn
  turns:
    - question: "How many invoices arrived per month?"
      expect: {exit: [0], assertions: [{kind: nonempty}]}
    - question: "How many of those had findings?"
      expect: {exit: [0], assertions: [{kind: nonempty}]}
"""


def write_bank(root, *, rows=ROW_B5, config=EVAL_YAML, gold=("b5.py",)):
    (root / "bank").mkdir(parents=True)
    (root / "gold").mkdir()
    (root / "eval.yaml").write_text(config, encoding="utf-8")
    (root / "bank" / "rows.yaml").write_text(rows, encoding="utf-8")
    for name in gold:
        (root / "gold" / name).write_text(
            "def gold(world):\n    return {'value': 146}\n", encoding="utf-8"
        )
    return root


def test_loads_single_and_multi_turn_rows(tmp_path):
    bank = load_bank(write_bank(tmp_path, rows=ROW_B5 + ROW_MT))
    assert isinstance(bank, LoadedBank)
    assert bank.row_ids() == ["B5", "MT1"]
    assert bank.config.default_runs == 2
    single, multi = bank.rows
    assert len(single.all_turns()) == 1
    assert len(multi.all_turns()) == 2
    assert multi.all_turns()[1].question.startswith("How many of those")


def test_duplicate_ids_fail(tmp_path):
    write_bank(tmp_path, rows=ROW_B5)
    (tmp_path / "bank" / "more.yaml").write_text(ROW_B5, encoding="utf-8")
    with pytest.raises(BankLoadError, match="duplicate row id B5"):
        load_bank(tmp_path)


def test_question_and_turns_together_fail(tmp_path):
    rows = ROW_MT.replace(
        "  turns:", '  question: "also a question"\n  turns:'
    )
    write_bank(tmp_path, rows=rows)
    with pytest.raises(BankLoadError, match="exactly one of question/turns"):
        load_bank(tmp_path)


def test_gold_assertion_without_gold_script_fails(tmp_path):
    rows = ROW_B5.replace("  gold: gold/b5.py\n", "").replace(
        "  expected_gold: {value: 146}\n", ""
    )
    write_bank(tmp_path, rows=rows)
    with pytest.raises(BankLoadError, match="need a gold script"):
        load_bank(tmp_path)


def test_missing_gold_file_fails(tmp_path):
    write_bank(tmp_path, gold=())
    with pytest.raises(BankLoadError, match="gold/b5.py not found"):
        load_bank(tmp_path)


def test_expected_gold_without_script_fails(tmp_path):
    rows = ROW_B5.replace("  gold: gold/b5.py\n", "").replace(
        "- {kind: numeric_from_gold, field: value}\n      ", ""
    )
    write_bank(tmp_path, rows=rows)
    with pytest.raises(BankLoadError, match="expected_gold without a gold"):
        load_bank(tmp_path)


def test_route_pair_needs_two_members(tmp_path):
    rows = ROW_B5 + "  route_pair: metric\n"
    write_bank(tmp_path, rows=rows)
    with pytest.raises(BankLoadError, match="route_pair 'metric'"):
        load_bank(tmp_path)


def test_unknown_key_fails(tmp_path):
    write_bank(tmp_path, rows=ROW_B5 + "  thresold: 0.8\n")
    with pytest.raises(BankLoadError, match="thresold"):
        load_bank(tmp_path)


def test_unknown_tool_name_fails(tmp_path):
    write_bank(tmp_path, rows=ROW_B5.replace("run_sql", "run_sqll"))
    with pytest.raises(BankLoadError, match="row B5"):
        load_bank(tmp_path)


def test_bank_hash_pins_file_bytes(tmp_path):
    """The hash is over the bank's files as committed: rows, gold
    scripts and config all count; the same files always agree."""
    one = load_bank(write_bank(tmp_path / "one", rows=ROW_B5 + ROW_MT))
    same = load_bank(write_bank(tmp_path / "same", rows=ROW_B5 + ROW_MT))
    assert one.bank_hash == same.bank_hash

    rows_edit = write_bank(
        tmp_path / "rows", rows=(ROW_B5 + ROW_MT).replace("146", "147")
    )
    assert load_bank(rows_edit).bank_hash != one.bank_hash

    gold_edit = write_bank(tmp_path / "gold", rows=ROW_B5 + ROW_MT)
    (gold_edit / "gold" / "b5.py").write_text(
        "def gold(world):\n    return {'value': 147}\n", encoding="utf-8"
    )
    assert load_bank(gold_edit).bank_hash != one.bank_hash

    config_edit = write_bank(
        tmp_path / "config", rows=ROW_B5 + ROW_MT,
        config=EVAL_YAML.replace("default_runs: 2", "default_runs: 3"),
    )
    assert load_bank(config_edit).bank_hash != one.bank_hash


def test_bank_hash_survives_a_schema_default_addition(tmp_path, monkeypatch):
    """Adding a defaulted field to the row schema must not change the
    hash — the old parsed-form hash orphaned every historical report
    on exactly that kind of change (fix pass 3's `breach: bool`)."""
    import engine.eval.bank as bank_module
    from engine.eval.models import BankRow

    root = write_bank(tmp_path, rows=ROW_B5 + ROW_MT)
    before = load_bank(root).bank_hash

    class WiderRow(BankRow):
        fabricated_default: bool = True

    monkeypatch.setattr(bank_module, "BankRow", WiderRow)
    after = load_bank(root)
    assert all(isinstance(row, WiderRow) for row in after.rows)
    assert after.bank_hash == before


def test_select_globs_and_rejects_unmatched(tmp_path):
    bank = load_bank(write_bank(tmp_path, rows=ROW_B5 + ROW_MT))
    assert [r.id for r in bank.select(None)] == ["B5", "MT1"]
    assert [r.id for r in bank.select(["MT*"])] == ["MT1"]
    assert [r.id for r in bank.select(["B5", "MT1"])] == ["B5", "MT1"]
    with pytest.raises(BankLoadError, match="match nothing: C9"):
        bank.select(["B5", "C9"])


def test_name_from_gold_rejects_an_empty_field_list(tmp_path):
    rows = ROW_B5.replace(
        "{kind: numeric_from_gold, field: value}",
        "{kind: name_from_gold, field: []}",
    )
    write_bank(tmp_path, rows=rows)
    with pytest.raises(BankLoadError, match="at least one gold field"):
        load_bank(tmp_path)
