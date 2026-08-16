"""The canonical-serialization chokepoint keeps its byte promises."""

from engine.substrates.jsonl import dumps_row, read_rows, write_rows
from engine.substrates.models import DictionaryRow, Provenance

import pytest

PROV = Provenance(
    source="machine", confidence=1.0, needs_validation=True, manifest_id="m1"
)


def row(table: str, column: str = "") -> DictionaryRow:
    return DictionaryRow(table_name=table, column_name=column, provenance=PROV)


def test_keys_sorted_and_compact():
    line = dumps_row(row("invoices", "status"))
    assert line.startswith('{"column_name":"status"')
    assert ": " not in line and ", " not in line


def test_rows_sorted_lf_and_trailing_newline(tmp_path):
    path = tmp_path / "dictionary.jsonl"
    rows = [row("users"), row("invoices", "status"), row("invoices")]
    write_rows(path, rows, sort_key=lambda r: (r.table_name, r.column_name))
    data = path.read_bytes()
    assert b"\r" not in data
    assert data.endswith(b"\n")
    lines = data.decode().splitlines()
    assert [l.split('"table_name":"')[1].split('"')[0] for l in lines] == [
        "invoices",
        "invoices",
        "users",
    ]


def test_round_trip_byte_identity(tmp_path):
    path = tmp_path / "a.jsonl"
    rows = [row("invoices", "id"), row("invoices", "status")]
    write_rows(path, rows, sort_key=lambda r: (r.table_name, r.column_name))
    first = path.read_bytes()
    reread = read_rows(path, DictionaryRow)
    write_rows(path, reread, sort_key=lambda r: (r.table_name, r.column_name))
    assert path.read_bytes() == first


def test_blank_line_rejected(tmp_path):
    path = tmp_path / "bad.jsonl"
    path.write_text(dumps_row(row("invoices")) + "\n\n", encoding="utf-8")
    with pytest.raises(ValueError, match="blank line"):
        read_rows(path, DictionaryRow)
