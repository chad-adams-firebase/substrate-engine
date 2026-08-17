"""The lookup-shaped tools: stats, dictionary (+map layer), primer,
business-doc search, known items."""

from tests.conftest import build_tool_registry


def test_stats_by_table_and_column(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    by_table = registry.invoke("query_univariate_stats", {"table": "invoices"})
    assert by_table.status == "ok"
    assert {row.table_name for row in by_table.output.rows} == {"invoices"}
    assert len(by_table.output.rows) > 10
    assert by_table.manifest_ids

    by_column = registry.invoke(
        "query_univariate_stats", {"table": "invoices", "column": "status"}
    )
    [row] = by_column.output.rows
    assert row.row_count == 50
    assert row.top_values  # enum-ish column carries its value counts


def test_stats_unknown_table_lists_known(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke("query_univariate_stats", {"table": "widgets"})
    assert invocation.status == "error"
    assert "invoices" in invocation.error


def test_dictionary_lookup_by_table_brings_the_map_layer(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke("lookup_data_dictionary", {"table": "invoices"})
    assert invocation.status == "ok"
    assert all(row.table_name == "invoices" for row in invocation.output.rows)
    # The fixture map's invoice-touching entries ride along.
    assert [m.name for m in invocation.output.metrics] == ["flag_rate"]
    assert [g.name for g in invocation.output.gotchas] == ["adjustment_totals"]
    assert [c.name for c in invocation.output.concepts] == ["invoice"]


def test_dictionary_lookup_by_term(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke("lookup_data_dictionary", {"term": "adjustment"})
    assert invocation.status == "ok"
    assert any(
        row.column_name == "adjustment_flag" for row in invocation.output.rows
    )
    assert [g.name for g in invocation.output.gotchas] == ["adjustment_totals"]


def test_dictionary_lookup_requires_a_handle(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke("lookup_data_dictionary", {})
    assert invocation.status == "error"
    assert "table" in invocation.error and "term" in invocation.error


def test_primer_returns_l0_and_l1_only(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke("app_primer", {})
    assert invocation.status == "ok"
    assert "Snapshot primer" in invocation.output.primer
    assert {c.id for c in invocation.output.components} >= {"ig.spine.rules-engine"}
    assert invocation.evidence is None


def test_doc_search_finds_the_relevant_section_first(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke(
        "search_business_docs", {"query": "why fifteen percent rate variance"}
    )
    assert invocation.status == "ok"
    hits = invocation.output.hits
    assert hits, "expected at least one hit"
    assert hits[0].slug == "rate-variance-memo"
    assert hits[0].heading == "Why fifteen percent"
    # Evidence carries the full section text behind the snippet.
    assert invocation.evidence.sections[0].text.startswith("The rate variance")


def test_doc_search_empty_query_is_an_error(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    assert registry.invoke("search_business_docs", {"query": "  !  "}).status == "error"


def test_doc_search_no_match_returns_empty_ok(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke(
        "search_business_docs", {"query": "zeppelin telemetry"}
    )
    assert invocation.status == "ok"
    assert invocation.output.hits == []


def test_known_items_empty_until_phase_6(tool_pack):
    registry, _ = build_tool_registry(tool_pack)
    invocation = registry.invoke("answer_from_known_items", {"query": "flag rate"})
    assert invocation.status == "ok"
    assert invocation.output.matches == []
    assert invocation.substrates_read == []
