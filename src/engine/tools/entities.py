"""Entity kinds at work (Backlog Pass, gate verdict §7 items 1–2): the
pack declares what a conversation refers back to — "that invoice",
"this rule" — and this module reads those declarations against SQL
and result tables. Three consumers, one vocabulary:

- the catalog: which columns identify (keys) or name (names) an entity
  of each kind, foreign keys resolved to the key they reference, and
  the id-like set the ungrounded-key lint challenges literals on;
- the harvest: the entities a finished turn's evidence established
  (a single-valued key/name column, or a filter literal) and every key
  value it carried — kept on the history for the router's transcript,
  the run_sql grounding, the key lint, and the Verifier's anchor check;
- the question scan: which kind, if any, a question refers back to.

Imports the SQL text layer and the substrate models; nothing from the
harness or the verifier, so both can import it.
"""

import re
from dataclasses import dataclass

from engine.config.models import ToolName
from engine.substrates.models import DictionaryMap, DictionaryRow
from engine.tools.envelope import (
    Anchor,
    KnownKey,
    RunSqlOutput,
    ToolInvocation,
    TurnAnchors,
)
from engine.tools.key_lint import split_comments
from engine.tools.sql_scopes import (
    EQUALS_LITERAL,
    IN_LITERALS,
    literals_between,
    scope_tree,
    split_scopes,
    table_aliases,
    table_references,
    unquote_identifiers,
)
from engine.tools.sql_select import Column, resolve_select_items, source_column

# A column restricted to an integer literal (string literals are
# blanked by the scope walk; numbers are not). The same shape as
# EQUALS_LITERAL / IN_LITERALS, so <=, >=, != and <> never match.
_EQUALS_NUMBER = re.compile(
    r"(?:([A-Za-z_]\w*)\s*\.\s*)?([A-Za-z_]\w*)\s*=\s*(\d+)(?![\w.])"
)
_IN_NUMBERS = re.compile(
    r"(?:([A-Za-z_]\w*)\s*\.\s*)?([A-Za-z_]\w*)\s+in\s*\(\s*(\d+(?:\s*,\s*\d+)*)\s*\)(?![\w.])",
    re.IGNORECASE,
)
# A right side that keeps going is an expression, not a value:
# `rule_name = 'compliance_' || cr.rule_code` binds no literal.
_CONTINUES = re.compile(r"\s*(?:\|\||[-+*/%])")


@dataclass(frozen=True)
class EntityCatalog:
    """The pack's entity declarations, resolved against the dictionary.
    column_kind and canonical are keyed by "table.column"; a foreign
    key to a declared key column resolves to that key's kind and
    canonical name (findings.invoice_id → invoices.id, kind invoice)."""

    kinds: tuple[str, ...]
    column_kind: dict[str, str]
    canonical: dict[str, str]
    id_like: frozenset[str]
    synonyms: dict[str, str]  # noun (lowercase) -> kind
    columns_of: dict[str, frozenset[str]]  # table -> its column names

    @classmethod
    def from_substrates(
        cls, dictionary: list[DictionaryRow], dictionary_map: DictionaryMap
    ) -> "EntityCatalog":
        columns_of: dict[str, set[str]] = {}
        fk_of: dict[str, str] = {}
        id_like: set[str] = set()
        for row in dictionary:
            if not row.column_name:
                continue
            qualified = f"{row.table_name.lower()}.{row.column_name.lower()}"
            columns_of.setdefault(row.table_name.lower(), set()).add(
                row.column_name.lower()
            )
            if row.is_primary_key:
                id_like.add(qualified)
            if row.fk_target:
                fk_of[qualified] = row.fk_target.lower()
                id_like.add(qualified)
        column_kind: dict[str, str] = {}
        canonical: dict[str, str] = {}
        synonyms: dict[str, str] = {}
        kinds: list[str] = []
        for entity in dictionary_map.entities:
            kinds.append(entity.kind)
            for qualified in entity.columns:
                column_kind.setdefault(qualified.lower(), entity.kind)
                canonical.setdefault(qualified.lower(), qualified.lower())
            for qualified in entity.key_columns:
                id_like.add(qualified.lower())
            for noun in [entity.kind, *entity.synonyms]:
                synonyms.setdefault(noun.lower(), entity.kind)
        # A foreign key to a declared column is that column's kind.
        for qualified, target in fk_of.items():
            if target in column_kind and qualified not in column_kind:
                column_kind[qualified] = column_kind[target]
                canonical[qualified] = canonical[target]
        return cls(
            kinds=tuple(kinds),
            column_kind=column_kind,
            canonical=canonical,
            id_like=frozenset(id_like),
            synonyms=synonyms,
            columns_of={t: frozenset(c) for t, c in columns_of.items()},
        )

    def kind_of(self, qualified: str) -> str | None:
        return self.column_kind.get(qualified.lower())

    def canonical_of(self, qualified: str) -> str:
        return self.canonical.get(qualified.lower(), qualified.lower())

    def is_id_like(self, qualified: str) -> bool:
        return qualified.lower() in self.id_like

    def entity_column_by_name(self, name: str) -> str | None:
        """The one declared entity column spelled `name` (the alias
        fallback for a result column the parse could not place), or
        None when zero or several are."""
        owners = {
            qualified
            for qualified in self.column_kind
            if qualified.partition(".")[2] == name.lower()
        }
        return next(iter(owners)) if len(owners) == 1 else None


@dataclass(frozen=True)
class KeyLiteral:
    """One equality (or IN) predicate binding a real table's column to
    literal values, as a statement wrote it."""

    table: str
    column: str
    values: tuple[str, ...]
    canonical: str
    kind: str | None
    id_like: bool


def _resolve(
    qualifier: str | None,
    column: str,
    aliases: dict[str, str],
    catalog: EntityCatalog,
) -> str | None:
    """The real table a predicate's column belongs to: the alias's
    table, or the sole in-scope owner of an unqualified column. None
    for a CTE alias or an ambiguous bare column."""
    if qualifier is not None:
        table = aliases.get(qualifier.lower())
        return table if table in catalog.columns_of else None
    owners = {
        table
        for table in set(aliases.values())
        if column in catalog.columns_of.get(table, frozenset())
    }
    return next(iter(owners)) if len(owners) == 1 else None


def equality_literals(sql: str, catalog: EntityCatalog) -> list[KeyLiteral]:
    """Every `col = literal` / `col IN (literals)` predicate in the
    statement, per scope, its column resolved to a real table — string
    literals read back by index from the scope, integers read from the
    text. Block comments are stripped first (the scope walk strips only
    line comments, and an apostrophe inside one would desync the
    literal index). A right side that continues into an expression is
    not a literal."""
    stripped, _ = split_comments(sql)
    found: list[KeyLiteral] = []
    for scope in scope_tree(unquote_identifiers(stripped)):
        aliases = {
            alias: table
            for alias, table in table_aliases(scope.text).items()
            if table in catalog.columns_of
        }
        text = scope.text
        for pattern, numeric in (
            (EQUALS_LITERAL, False),
            (IN_LITERALS, False),
            (_EQUALS_NUMBER, True),
            (_IN_NUMBERS, True),
        ):
            for match in pattern.finditer(text):
                if _CONTINUES.match(text, match.end()):
                    continue
                qualifier, column = match.group(1), match.group(2).lower()
                table = _resolve(qualifier, column, aliases, catalog)
                if table is None or column not in catalog.columns_of[table]:
                    continue
                if numeric:
                    values = tuple(v.strip() for v in match.group(3).split(","))
                else:
                    values = tuple(
                        literals_between(scope, match.start(), match.end())
                    )
                if not values:
                    continue
                qualified = f"{table}.{column}"
                found.append(
                    KeyLiteral(
                        table=table,
                        column=column,
                        values=values,
                        canonical=catalog.canonical_of(qualified),
                        kind=catalog.kind_of(qualified),
                        id_like=catalog.is_id_like(qualified),
                    )
                )
    return found


def _result_columns(sql: str, columns: list[str], catalog: EntityCatalog) -> dict[str, str]:
    """Result column -> the real "table.column" it reads, for every
    column the parse can place: a plain column or a plain aggregate of
    one, followed through CTEs and derived tables; a bare column
    resolved to its sole owner among the statement's tables; else the
    one declared entity column that spells its name."""
    try:
        items = resolve_select_items(sql)
    except Exception:  # the parse declines; the alias fallback remains
        items = {}
    referenced = {
        table
        for scope in split_scopes(unquote_identifiers(sql))
        for table, _ in table_references(scope)
        if table in catalog.columns_of
    }
    placed: dict[str, str] = {}
    for alias in columns:
        expr = items.get(alias)
        column = source_column(expr) if expr is not None else None
        resolved: str | None = None
        if isinstance(column, Column):
            if column.table is not None and column.table in catalog.columns_of:
                resolved = f"{column.table}.{column.column}"
            else:
                owners = {
                    table
                    for table in referenced
                    if column.column in catalog.columns_of[table]
                }
                if len(owners) == 1:
                    resolved = f"{next(iter(owners))}.{column.column}"
        if resolved is None:
            resolved = catalog.entity_column_by_name(alias)
        if resolved is not None:
            placed[alias] = resolved
    return placed


def _cell(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def harvest_turn_anchors(
    evidence: list[ToolInvocation],
    catalog: EntityCatalog,
    *,
    about: str | None = None,
    question_kind: str | None = None,
    turn: int = 0,
) -> TurnAnchors:
    """What the turn's run_sql evidence established. A kind is
    determinate when its entity is single: every key/name column of
    that kind the results carry has one distinct value, and every
    filter on one agrees — then one Anchor per column, all describing
    that entity. A multi-row column, or two invocations disagreeing,
    is ambiguous and yields nothing for that kind. Keys are every
    distinct value of every id-like result column plus every id-like
    filter literal. The router's declared about rides along as an
    Anchor of source "declared"."""
    cells: dict[str, dict[str, set[str]]] = {}  # kind -> canonical column -> values
    filters: dict[str, dict[str, set[str]]] = {}
    keys: dict[tuple[str, str], None] = {}
    for invocation in evidence:
        if invocation.tool != ToolName.RUN_SQL or invocation.status != "ok":
            continue
        output = invocation.output
        if not isinstance(output, RunSqlOutput):
            continue
        placed = _result_columns(output.sql, output.table.columns, catalog)
        for alias, qualified in placed.items():
            canonical = catalog.canonical_of(qualified)
            values = {
                text
                for row in output.table.rows
                if (text := _cell(row.get(alias))) is not None
            }
            if not values:
                continue
            kind = catalog.kind_of(qualified)
            if kind is not None:
                cells.setdefault(kind, {}).setdefault(canonical, set()).update(values)
            if catalog.is_id_like(qualified):
                for value in values:
                    keys.setdefault((canonical, value), None)
        for literal in equality_literals(output.sql, catalog):
            if literal.kind is not None:
                filters.setdefault(literal.kind, {}).setdefault(
                    literal.canonical, set()
                ).update(literal.values)
            if literal.id_like:
                for value in literal.values:
                    keys.setdefault((literal.canonical, value), None)

    entities: list[Anchor] = []
    for kind in catalog.kinds:
        by_column: dict[str, tuple[str, set[str]]] = {}
        for column, values in cells.get(kind, {}).items():
            by_column[column] = ("cell", set(values))
        for column, values in filters.get(kind, {}).items():
            if column in by_column:
                by_column[column] = ("cell", by_column[column][1] | set(values))
            else:
                by_column[column] = ("filter", set(values))
        if not by_column:
            continue
        if any(len(values) != 1 for _, values in by_column.values()):
            continue  # ambiguous: more than one entity of this kind
        for column, (source, values) in sorted(by_column.items()):
            entities.append(
                Anchor(kind=kind, column=column, value=next(iter(values)), source=source)
            )
    if about:
        # Stored without its kind noun, so a later turn's declared
        # fallback compares like with like (Fix Pass, R2).
        entities.append(
            Anchor(
                kind=question_kind or "",
                column="",
                value=strip_kind_noun(about, question_kind, catalog) or about,
                source="declared",
            )
        )
    return TurnAnchors(
        turn=turn,
        entities=entities,
        keys=[KnownKey(column=column, value=value) for column, value in keys],
    )


def anaphor_kind(question: str, catalog: EntityCatalog) -> str | None:
    """The kind a question refers back to, or None. A singular
    demonstrative before a kind noun ("that rule", "this invoice's",
    "the same supplier"), or a kind noun followed by a back-reference
    ("the supplier from earlier", "the invoice above"). Kind-less
    pronouns ("it", "its"), plurals ("those invoices"), definite
    articles ("the rule that flags it") and positionals ("the first
    supplier") are None: their referent is not a single kind's single
    entity, and a comparison there would flag clean turns."""
    if not catalog.synonyms:
        return None
    nouns = sorted(catalog.synonyms, key=len, reverse=True)
    alternation = "|".join(re.escape(noun) for noun in nouns)
    text = question.casefold()
    demonstrative = re.compile(
        rf"\b(?:that|this|the same|said)\s+(?:\w+\s+)?(?P<noun>{alternation})(?:'s)?\b"
    )
    back_reference = re.compile(
        rf"\b(?:the\s+)?(?P<noun>{alternation})\s+"
        r"(?:above|earlier|from earlier|from before|we discussed|mentioned|in question)\b"
    )
    match = demonstrative.search(text) or back_reference.search(text)
    if match is None:
        return None
    return catalog.synonyms[match.group("noun")]


_ARTICLE = re.compile(r"^(?:the|a|an)\s+")


def strip_kind_noun(about: str, kind: str | None, catalog: EntityCatalog) -> str:
    """A declared about without the kind noun the router may have put
    in front of it (Fix Pass, R2): one optional leading article, then
    one leading synonym of `kind` from the pack's declarations — so
    "invoice 440" and "the invoice INV-00426" read 440 and INV-00426,
    while "440 and 441" (a list) and "supplier 440" (another kind's
    noun) read unchanged. Strip-and-match, never containment: a list
    gets no partial credit. The remainder keeps the value's own
    spelling; the caller normalizes for comparison."""
    text = about.strip().strip("`'\"")
    lowered = text.lower().replace("_", " ")
    offset = 0
    article = _ARTICLE.match(lowered)
    if article:
        offset = article.end()
    if kind:
        nouns = sorted(
            (noun for noun, of_kind in catalog.synonyms.items() if of_kind == kind),
            key=len,
            reverse=True,
        )
        for noun in nouns:
            if lowered.startswith(noun + " ", offset):
                offset += len(noun) + 1
                break
    return text[offset:].strip()


_TOKEN = re.compile(r"[A-Za-z0-9_][\w.\-]*")


def tokens_of(text: str) -> set[str]:
    """Casefolded whole tokens: letters, digits, and the -, _ and .
    a code carries inside it (INV-00426, SVC-4410, 30.6); trailing
    punctuation stripped. 8,123.45 yields 8 and 123.45, never 123."""
    return {
        token.rstrip(".-_").casefold()
        for token in _TOKEN.findall(text)
        if token.rstrip(".-_")
    }


def known_values(
    texts: list[str], keys: list[KnownKey], grounding_text: str = ""
) -> set[str]:
    """Every value the conversation has put in front of the model: the
    user's own words, every key a result or filter carried, and the
    grounding text (its templates and value lists are the engine's
    words, not the model's)."""
    known: set[str] = set()
    for text in [*texts, grounding_text]:
        known |= tokens_of(text)
    known |= {key.value.casefold() for key in keys}
    return known
