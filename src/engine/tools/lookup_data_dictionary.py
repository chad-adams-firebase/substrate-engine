"""lookup_data_dictionary — definitions from the dictionary plus the
Dictionary Map's semantic layer (concepts, canonical metrics, join
paths, gotchas) matching the same lookup."""

from pydantic import BaseModel, ConfigDict, model_validator

from engine.config.models import SubstrateName, ToolName
from engine.ports.substrate_store import SubstrateStoreError, SubstrateStorePort
from engine.substrates.models import DictionaryMap
from engine.tools.base import Tool, manifest_ids_of
from engine.tools.envelope import DictionaryLookupOutput, ToolInvocation


class DictionaryLookupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str | None = None
    column: str | None = None
    term: str | None = None

    @model_validator(mode="after")
    def _something_to_look_up(self) -> "DictionaryLookupInput":
        if self.table is None and self.term is None:
            raise ValueError("provide a table (optionally a column) or a term")
        return self


def _matches_term(term: str, *texts: str) -> bool:
    lowered = term.lower()
    return any(lowered in text.lower() for text in texts if text)


class LookupDataDictionary(Tool):
    name = ToolName.LOOKUP_DATA_DICTIONARY
    description = (
        "Look up the data dictionary: table/column definitions, types, "
        "keys, and enums — plus matching business concepts, canonical "
        "metric definitions, vetted join paths, and known gotchas from "
        "the dictionary map. Query by table (optionally column) or by a "
        "free-text term."
    )
    input_model = DictionaryLookupInput

    def __init__(
        self, store: SubstrateStorePort, *, include_map: bool
    ) -> None:
        self._store = store
        # A pack may enable the dictionary without the map (the Brief's
        # minimum useful pack); the map layer then simply stays empty.
        self._include_map = include_map

    def run(self, params: DictionaryLookupInput) -> ToolInvocation:
        try:
            dictionary = self._store.dictionary()
            dictionary_map = (
                self._store.dictionary_map() if self._include_map else None
            )
        except SubstrateStoreError as exc:
            return self.fail(params, str(exc))

        rows = dictionary
        if params.table is not None:
            rows = [row for row in rows if row.table_name == params.table]
            if not rows:
                known = ", ".join(
                    sorted({row.table_name for row in dictionary})
                )
                return self.fail(
                    params,
                    f"No dictionary entry for table {params.table!r}. "
                    f"Known tables: {known}.",
                )
            if params.column is not None:
                rows = [row for row in rows if row.column_name == params.column]
                if not rows:
                    return self.fail(
                        params,
                        f"Table {params.table!r} has no dictionary entry for "
                        f"column {params.column!r}.",
                    )
        elif params.term is not None:
            rows = [
                row
                for row in rows
                if _matches_term(
                    params.term, row.table_name, row.column_name, row.description
                )
            ]

        substrates = [SubstrateName.DATA_DICTIONARY]
        output = DictionaryLookupOutput(rows=rows)
        if dictionary_map is not None:
            substrates.append(SubstrateName.DATA_DICTIONARY_MAP)
            output = output.model_copy(
                update=self._map_matches(dictionary_map, params)
            )
        return self.ok(
            params,
            output,
            substrates_read=substrates,
            manifest_ids=manifest_ids_of(rows),
        )

    @staticmethod
    def _map_matches(dictionary_map: DictionaryMap, params: DictionaryLookupInput) -> dict:
        """Map entries matching the lookup: by referenced table when a
        table was asked for, by text when a term was."""

        def selected(name: str, tables: list[str], *texts: str) -> bool:
            if params.table is not None:
                return params.table in tables
            assert params.term is not None
            return _matches_term(params.term, name, *texts)

        return {
            "concepts": [
                c
                for c in dictionary_map.concepts
                if selected(c.name, c.tables, c.definition, *c.synonyms)
            ],
            "metrics": [
                m
                for m in dictionary_map.metrics
                if selected(m.name, m.tables, m.description, m.notes)
            ],
            "join_paths": [
                j
                for j in dictionary_map.join_paths
                if selected(
                    j.name,
                    [s.from_table for s in j.steps] + [s.to_table for s in j.steps],
                    j.notes,
                )
            ],
            "gotchas": [
                g
                for g in dictionary_map.gotchas
                if selected(g.name, g.tables, g.summary, g.detail)
            ],
        }
