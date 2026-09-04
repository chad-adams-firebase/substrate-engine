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


# --- Close Pass: a join path one-to-one under a declared filter ---------


def test_a_join_path_is_one_to_one_always_or_under_a_filter_not_both():
    from engine.substrates.models import CardinalityCondition, JoinPath, JoinStep

    step = JoinStep(
        from_table="invoices", from_column="id",
        to_table="invoice_history", to_column="invoice_id",
    )
    condition = CardinalityCondition(
        column="invoice_history.to_status", values=["CLOSED"]
    )
    assert JoinPath(name="p", steps=[step], one_to_one_when=[condition]).cardinality is None
    with pytest.raises(ValidationError, match="both cardinality and one_to_one_when"):
        JoinPath(
            name="p", steps=[step], cardinality="one_to_one",
            one_to_one_when=[condition],
        )


def test_a_cardinality_condition_names_a_qualified_column_and_some_values():
    from engine.substrates.models import CardinalityCondition

    condition = CardinalityCondition(
        column="invoice_history.to_status", values=["CLOSED", "NO_REVIEW_NEEDED"]
    )
    assert (condition.table, condition.column_name) == ("invoice_history", "to_status")
    with pytest.raises(ValidationError, match="table.column"):
        CardinalityCondition(column="to_status", values=["CLOSED"])
    with pytest.raises(ValidationError, match="table.column"):
        CardinalityCondition(column="a.b.c", values=["CLOSED"])
    with pytest.raises(ValidationError, match="at least one value"):
        CardinalityCondition(column="invoice_history.to_status", values=[])
