"""PackFilesSubstrateStore: typed getters over a pack directory."""

import shutil
from pathlib import Path

import pytest

from engine.adapters.substrate_store_pack import (
    PackFilesSettings,
    PackFilesSubstrateStore,
)
from engine.ports.substrate_store import SubstrateStoreError
from engine.substrates.jsonl import read_rows
from engine.substrates.models import DictionaryRow, StatsRow

SNAPSHOT = Path(__file__).parent / "fixtures" / "invoiceguard_snapshot"
EXPECTED = SNAPSHOT / "expected"
ARTIFACTS = Path(__file__).parent / "fixtures" / "pack_artifacts"

SUBSTRATE_FILES = (
    "dictionary",
    "univariate_stats",
    "ckg_nodes",
    "ckg_edges",
    "ckg_conditionals",
    "component_memberships",
)


@pytest.fixture
def pack_root(tmp_path: Path) -> Path:
    """A pack directory assembled from the vendored snapshot's expected
    generator outputs plus the synthetic authored artifacts."""
    substrates = tmp_path / "substrates"
    substrates.mkdir()
    for name in SUBSTRATE_FILES:
        shutil.copy(EXPECTED / f"{name}.jsonl", substrates / f"{name}.jsonl")
    shutil.copy(SNAPSHOT / "components.yaml", tmp_path / "components.yaml")
    shutil.copy(SNAPSHOT / "primer.md", tmp_path / "primer.md")
    shutil.copy(ARTIFACTS / "dictionary_map.yaml", tmp_path / "dictionary_map.yaml")
    shutil.copytree(ARTIFACTS / "business_docs", tmp_path / "business_docs")
    return tmp_path


def _store(root: Path) -> PackFilesSubstrateStore:
    return PackFilesSubstrateStore(PackFilesSettings(), root)


def test_generated_substrates_come_back_typed_and_complete(pack_root):
    store = _store(pack_root)
    assert store.dictionary() == read_rows(
        EXPECTED / "dictionary.jsonl", DictionaryRow
    )
    assert store.stats() == read_rows(EXPECTED / "univariate_stats.jsonl", StatsRow)
    assert len(store.ckg_nodes()) > 0
    assert len(store.ckg_edges()) > 0
    assert len(store.ckg_conditionals()) > 0
    assert len(store.memberships()) > 0


def test_human_overlay_row_is_visible_through_the_store(pack_root):
    # The snapshot overlay carries one human SME row (the adjustment
    # gotcha); the generators merged it, and the store must serve it.
    rows = [
        row
        for row in _store(pack_root).dictionary()
        if row.provenance.source == "human"
    ]
    assert rows, "expected the merged human overlay row"
    assert rows[0].table_name == "invoices"
    assert rows[0].column_name == "adjustment_flag"


def test_pack_authored_artifacts_load(pack_root):
    store = _store(pack_root)
    assert "Snapshot primer" in (store.primer() or "")
    assert [c.id for c in store.components()]
    dictionary_map = store.dictionary_map()
    assert [m.name for m in dictionary_map.metrics] == ["flag_rate"]
    assert dictionary_map.provenance.needs_validation is True
    [doc] = store.business_docs()
    assert doc.slug == "rate-variance-memo"
    assert doc.title == "Rate Variance Policy (Fixture)"
    assert doc.doc_date == "2025-01-01"
    assert "fifteen percent" in doc.body
    assert not doc.body.startswith("---")


def test_missing_substrate_file_names_the_file(pack_root):
    (pack_root / "substrates" / "univariate_stats.jsonl").unlink()
    with pytest.raises(SubstrateStoreError, match="univariate_stats.jsonl"):
        _store(pack_root).stats()


def test_missing_dictionary_map_speaks_to_the_pack_author(pack_root):
    (pack_root / "dictionary_map.yaml").unlink()
    with pytest.raises(SubstrateStoreError, match="dictionary_map.yaml"):
        _store(pack_root).dictionary_map()


def test_malformed_business_doc_front_matter_fails_loudly(pack_root):
    (pack_root / "business_docs" / "bad.md").write_text(
        "no front matter here\n", encoding="utf-8"
    )
    with pytest.raises(SubstrateStoreError, match="front matter"):
        _store(pack_root).business_docs()


def test_reads_are_cached(pack_root):
    store = _store(pack_root)
    assert store.dictionary() is store.dictionary()
