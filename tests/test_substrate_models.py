"""Contract tests for the §4 substrate schemas."""

import pytest
from pydantic import ValidationError

from engine.substrates.models import (
    CkgEdge,
    DictionaryRow,
    Provenance,
    StatsRow,
)


def machine_provenance(manifest_id: str = "abc123") -> Provenance:
    return Provenance(
        source="machine",
        confidence=1.0,
        needs_validation=True,
        manifest_id=manifest_id,
    )


def human_provenance() -> Provenance:
    return Provenance(
        source="human",
        confidence=1.0,
        last_confirmed_by="sme",
        needs_validation=False,
    )


def test_machine_rows_require_manifest_id():
    with pytest.raises(ValidationError, match="manifest_id"):
        Provenance(source="machine", confidence=1.0, needs_validation=True)


def test_human_rows_reject_manifest_id():
    with pytest.raises(ValidationError, match="human"):
        Provenance(
            source="human",
            confidence=1.0,
            needs_validation=False,
            manifest_id="abc123",
        )


def test_table_edges_target_tables_only():
    with pytest.raises(ValidationError, match="table"):
        CkgEdge(
            id="e1",
            source_id="n1",
            kind="reads_table",
            target_node_id="n2",
            line=10,
            provenance=machine_provenance(),
        )


def test_node_edges_target_nodes_only():
    with pytest.raises(ValidationError, match="node"):
        CkgEdge(
            id="e1",
            source_id="n1",
            kind="calls",
            target_table="invoices",
            line=10,
            provenance=machine_provenance(),
        )


def test_valid_table_edge():
    edge = CkgEdge(
        id="e1",
        source_id="n1",
        kind="writes_table",
        target_table="invoice_history",
        line=5,
        provenance=machine_provenance(),
    )
    assert edge.target_table == "invoice_history"


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        DictionaryRow(
            table_name="invoices",
            column_name="status",
            surprise_field="nope",
            provenance=machine_provenance(),
        )


def test_stats_row_minimal_shape():
    row = StatsRow(
        table_name="invoices",
        column_name="status",
        data_type="VARCHAR",
        row_count=50,
        null_rate=0.0,
        distinct_count=4,
        provenance=machine_provenance(),
    )
    assert row.top_values == []
