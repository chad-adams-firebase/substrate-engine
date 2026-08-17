"""Pack-files adapter for SubstrateStore — the local, read-only store.

Serves the pack directory's generated substrates (substrates/*.jsonl,
written by the generators) and pack-authored artifacts (components,
primer, dictionary map, business docs) as typed §4 models. Bridges the
on-disk file names (dictionary.jsonl, univariate_stats.jsonl, …) to
the port's typed getters.

Reads are cached: substrates change only when a generator or a pack
author writes them, never at answer time — a stale in-process cache
would mean the process outlived a regeneration, which is a restart,
not a refresh.

Never reads overlays/ — those are generator INPUTS; their content
reaches this store only through the merged substrates the generators
write.
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from engine.ports.substrate_store import SubstrateStoreError
from engine.substrates import pack_data
from engine.substrates.jsonl import read_rows
from engine.substrates.manifest import load_manifest
from engine.substrates.models import (
    BusinessDoc,
    CkgConditional,
    CkgEdge,
    CkgNode,
    Component,
    ComponentMembership,
    DictionaryMap,
    DictionaryRow,
    Manifest,
    StatsRow,
)


class PackFilesSettings(BaseModel):
    """No settings: the store serves the pack directory it belongs to
    (the container passes the pack root). An empty-but-validated model
    so a typo'd settings key still fails loudly."""

    model_config = ConfigDict(extra="forbid")


class PackFilesSubstrateStore:
    def __init__(self, settings: PackFilesSettings, pack_root: Path) -> None:
        self._settings = settings
        self._root = pack_root
        self._cache: dict[str, Any] = {}

    @property
    def settings(self) -> PackFilesSettings:
        return self._settings

    def _substrate_rows(self, file_name: str, model: type) -> list:
        if file_name not in self._cache:
            path = self._root / "substrates" / f"{file_name}.jsonl"
            if not path.is_file():
                raise SubstrateStoreError(
                    f"Substrate file missing: {path} — run the generators "
                    f"(`engine generate --pack {self._root}`) to produce it."
                )
            self._cache[file_name] = read_rows(path, model)
        return self._cache[file_name]

    def dictionary(self) -> list[DictionaryRow]:
        return self._substrate_rows("dictionary", DictionaryRow)

    def stats(self) -> list[StatsRow]:
        return self._substrate_rows("univariate_stats", StatsRow)

    def ckg_nodes(self) -> list[CkgNode]:
        return self._substrate_rows("ckg_nodes", CkgNode)

    def ckg_edges(self) -> list[CkgEdge]:
        return self._substrate_rows("ckg_edges", CkgEdge)

    def ckg_conditionals(self) -> list[CkgConditional]:
        return self._substrate_rows("ckg_conditionals", CkgConditional)

    def memberships(self) -> list[ComponentMembership]:
        return self._substrate_rows("component_memberships", ComponentMembership)

    def components(self) -> list[Component]:
        if "components" not in self._cache:
            try:
                self._cache["components"] = pack_data.load_components(
                    self._root / "components.yaml"
                )
            except pack_data.PackDataError as exc:
                raise SubstrateStoreError(str(exc)) from exc
        return self._cache["components"]

    def primer(self) -> str | None:
        if "primer" not in self._cache:
            self._cache["primer"] = pack_data.load_primer(self._root / "primer.md")
        return self._cache["primer"]

    def dictionary_map(self) -> DictionaryMap:
        if "dictionary_map" not in self._cache:
            try:
                self._cache["dictionary_map"] = pack_data.load_dictionary_map(
                    self._root / "dictionary_map.yaml"
                )
            except pack_data.PackDataError as exc:
                raise SubstrateStoreError(str(exc)) from exc
        return self._cache["dictionary_map"]

    def business_docs(self) -> list[BusinessDoc]:
        if "business_docs" not in self._cache:
            try:
                self._cache["business_docs"] = pack_data.load_business_docs(
                    self._root / "business_docs"
                )
            except pack_data.PackDataError as exc:
                raise SubstrateStoreError(str(exc)) from exc
        return self._cache["business_docs"]

    def manifests(self) -> list[Manifest]:
        if "manifests" not in self._cache:
            directory = self._root / "substrates" / "manifests"
            self._cache["manifests"] = [
                load_manifest(path) for path in sorted(directory.glob("*.json"))
            ]
        return self._cache["manifests"]
