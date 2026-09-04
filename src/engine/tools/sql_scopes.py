"""The SQL text layer every lint and parse reads: scopes, literals,
table references and select-list items, from the statement's own text.

Regex-level on purpose (the house precedent, generators/ckg/sql_tables
.py): no parser dependency the work machine cannot install. This module
imports nothing from the package, so the fan-out lint, the select-list
resolver, the enum lint and the Verifier's statement checks read one
tokenisation and none of them can form a cycle (the Close Pass: the
lint needed the scope registry the resolver had, and the resolver
imported the lint).

scope_tree is the one walk: every SELECT scope in the statement — the
outer statement, each CTE body, each derived table, each scalar or
EXISTS subquery — as its own text with nested subqueries replaced by a
placeholder, function-call parentheses kept inline so COUNT(DISTINCT x)
stays visible to an aggregate scan, string literals blanked to '' and
recorded beside the text in order (the k-th '' in the text is
literals[k]), and the named scopes a scope can see: the CTEs declared
before it in the same WITH, the ones enclosing it, and its own derived
tables. Nothing here knows a dictionary or a map; that is the lints'
business. The analysed text is never executed: unquote_identifiers
reads a quoted name as a bare one for analysis only.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_LINE_COMMENT = re.compile(r"--[^\n]*")
# A double-quoted identifier, or a single-quoted literal to step over
# so a string containing "quotes" is never edited.
_QUOTED_OR_LITERAL = re.compile(r"'(?:[^']|'')*'|\"([A-Za-z_]\w*)\"")
# A blanked literal while the walk runs: quotes around an index no SQL
# can contain, so the parenthesis walk and the select/with test read
# the text exactly as they read '' — and each scope learns which
# literals are its own before the index becomes ''.
_PLACEHOLDER = re.compile("'\x00(\\d+)\x00'")
SUBQUERY = "(__subquery__)"
_SUBQUERY_START = re.compile(r"\s*(select|with)\b", re.IGNORECASE)
# `name AS (` — with an optional column list — right before a subquery
# is a CTE head; `FROM (` / `JOIN (` right before one is a derived table.
_CTE_HEAD = re.compile(
    r"\b([A-Za-z_]\w*)\s*(?:\([^()]*\)\s*)?\bas\s*$", re.IGNORECASE
)
_DERIVED_HEAD = re.compile(r"\b(?:from|join)\s*$", re.IGNORECASE)
_ALIAS_AFTER = re.compile(r"\s*(?:as\s+)?([A-Za-z_]\w*)", re.IGNORECASE)

KEYWORDS = {
    "on", "where", "join", "left", "right", "inner", "outer", "full",
    "cross", "group", "order", "limit", "having", "union", "qualify",
    "using", "as", "select", "with", "natural", "lateral",
}
# A keyword after a table name is not its alias: without the lookahead,
# `FROM findings JOIN compliance_reports` read JOIN as findings' alias
# and the scan never saw compliance_reports (guard pass; latent — the
# pinned model aliases every table, so no live statement hit it).
_TABLE_REF = re.compile(
    r"\b(from|join)\s+([A-Za-z_]\w*)"
    rf"(?:\s+(?:as\s+)?(?!(?:{'|'.join(sorted(KEYWORDS))})\b)([A-Za-z_]\w*))?",
    re.IGNORECASE,
)
_SELECT_LIST = re.compile(r"\bselect\b(.*?)\bfrom\b", re.IGNORECASE | re.DOTALL)
# One select item: `expr AS alias`, or a bare column whose alias is its
# own name. Shared by the Verifier's view, the display view and the
# fan-out lint's reading of a CTE's projection.
PLAIN_ITEM = re.compile(
    r"^\s*(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)"
    r"(?:\s+as\s+([A-Za-z_]\w*))?\s*$",
    re.IGNORECASE,
)
NON_COLUMN_WORDS = {"distinct", "null", "true", "false"}
_AS_ALIAS = re.compile(r"\s+as\s+([A-Za-z_]\w*)\s*$", re.IGNORECASE)


@dataclass
class QueryScope:
    """One SELECT scope of a statement, as the lints read it."""

    # Literals blanked to '', nested subqueries as (__subquery__),
    # function-call parentheses inline.
    text: str
    # The literals the text blanked, in order: the k-th '' is literals[k]
    # (quotes stripped, '' unescaped to ').
    literals: tuple[str, ...]
    # A CTE's name or a derived table's alias, lowercased; None for the
    # statement itself, an unaliased derived table, or a subquery.
    name: str | None
    kind: Literal["statement", "cte", "derived", "subquery"]
    # The named scopes this one can reference: the CTEs declared before
    # it in the same WITH, the ones enclosing it, its own derived tables.
    named: dict[str, "QueryScope"] = field(default_factory=dict)


def unquote_identifiers(sql: str) -> str:
    """`"invoices"."status"` read as `invoices.status` — for analysis
    only. Every lint and parse in this package reads identifiers with a
    bare-name regex, so a quoted statement bypassed all of them (guard
    pass; 0 of 202 live statements quoted under the current pin). The
    executed statement is never rewritten: a quoted reserved word must
    stay quoted for the database."""
    return _QUOTED_OR_LITERAL.sub(
        lambda match: match.group(0) if match.group(1) is None else match.group(1),
        sql,
    )


def _blank_literals(text: str) -> tuple[str, list[str]]:
    literals: list[str] = []

    def placeholder(match: re.Match) -> str:
        literals.append(match.group(0)[1:-1].replace("''", "'"))
        return f"'\x00{len(literals) - 1}\x00'"

    return _STRING_LITERAL.sub(placeholder, text), literals


def _closing_paren(text: str, open_index: int) -> int:
    """The index of the parenthesis closing the one at open_index, or
    len(text) when nothing closes it (the walk then reads to the end,
    as it always has)."""
    depth = 0
    end = open_index
    while end < len(text):
        if text[end] == "(":
            depth += 1
        elif text[end] == ")":
            depth -= 1
            if depth == 0:
                break
        end += 1
    return end


def scope_tree(sql: str) -> list[QueryScope]:
    """Every scope of the statement in post-order — each CTE body, each
    derived table and each subquery before the scope that holds it, the
    statement last — so a consumer reading scopes[-1] has the outer
    statement and a consumer walking the list meets a body before the
    join that references it."""
    text, literals = _blank_literals(
        _LINE_COMMENT.sub("", unquote_identifiers(sql))
    )
    scopes: list[QueryScope] = []

    def finish(
        flattened: str,
        kind: Literal["statement", "cte", "derived", "subquery"],
        name: str | None,
        named: dict[str, QueryScope],
    ) -> QueryScope:
        own = tuple(
            literals[int(match.group(1))]
            for match in _PLACEHOLDER.finditer(flattened)
        )
        scope = QueryScope(
            text=_PLACEHOLDER.sub("''", flattened),
            literals=own,
            name=name,
            kind=kind,
            named=named,
        )
        scopes.append(scope)
        return scope

    def walk(
        text: str,
        kind: Literal["statement", "cte", "derived", "subquery"],
        name: str | None,
        visible: dict[str, QueryScope],
    ) -> QueryScope:
        named = dict(visible)
        out: list[str] = []
        index = 0
        while index < len(text):
            char = text[index]
            if char != "(":
                out.append(char)
                index += 1
                continue
            end = _closing_paren(text, index)
            inner = text[index + 1 : end]
            if _SUBQUERY_START.match(inner):
                before = "".join(out)
                head = _CTE_HEAD.search(before)
                if head is not None:
                    child = walk(inner, "cte", head.group(1).lower(), named)
                    named[head.group(1).lower()] = child
                elif _DERIVED_HEAD.search(before):
                    alias_match = _ALIAS_AFTER.match(text, end + 1)
                    alias = (
                        alias_match.group(1).lower()
                        if alias_match
                        and alias_match.group(1).lower() not in KEYWORDS
                        else None
                    )
                    child = walk(inner, "derived", alias, named)
                    if alias is not None:
                        named[alias] = child
                else:
                    walk(inner, "subquery", None, named)
                out.append(SUBQUERY)
            else:
                out.append("(" + walk_inline(inner, named) + ")")
            index = end + 1
        return finish("".join(out), kind, name, named)

    def walk_inline(text: str, named: dict[str, QueryScope]) -> str:
        # Function arguments may themselves hold a subquery
        # (SUM(CASE WHEN x IN (SELECT ...))): recurse, keep the rest.
        out: list[str] = []
        index = 0
        while index < len(text):
            if text[index] != "(":
                out.append(text[index])
                index += 1
                continue
            end = _closing_paren(text, index)
            inner = text[index + 1 : end]
            if _SUBQUERY_START.match(inner):
                walk(inner, "subquery", None, named)
                out.append(SUBQUERY)
            else:
                out.append("(" + walk_inline(inner, named) + ")")
            index = end + 1
        return "".join(out)

    walk(text, "statement", None, {})
    return scopes


def split_scopes(sql: str) -> list[str]:
    """Every SELECT scope in the statement, each with its nested
    subqueries replaced by a placeholder: the outer statement, each
    CTE body, each subquery. Function-call parentheses stay inline so
    COUNT(DISTINCT x) remains visible to the aggregate scan."""
    return [scope.text for scope in scope_tree(sql)]


def literals_between(scope: QueryScope, start: int, end: int) -> list[str]:
    """The literals blanked inside scope.text[start:end], in order —
    what a predicate found in the cleaned text actually compared."""
    first = scope.text.count("''", 0, start)
    count = scope.text.count("''", start, end)
    return list(scope.literals[first : first + count])


def original_fragment(sql: str, cleaned: str) -> str:
    """The model's own text for a fragment of a cleaned scope. The walk
    blanks every string literal to '' and hoists subqueries, so a
    challenge that quotes the cleaned text would show
    CONCAT('', cr.rule_code) for the CONCAT('compliance_', cr.rule_code)
    the model wrote (Block 2 rider). Rebuild the fragment as a pattern
    — literals and hoisted subqueries as wildcards, whitespace as any
    whitespace — and find it in the original; fall back to the cleaned
    text, whitespace-collapsed, when nothing matches (a comment inside
    the fragment, say)."""
    parts: list[str] = []
    for token in re.split(r"(''|\(__subquery__\)|\s+)", cleaned):
        if not token:
            continue
        if token == "''":
            parts.append(r"'(?:[^']|'')*'")
        elif token == SUBQUERY:
            parts.append(r"\((?:[^()]|\([^()]*\))*\)")
        elif token.isspace():
            parts.append(r"\s+")
        else:
            parts.append(re.escape(token))
    match = re.search("".join(parts), sql, re.IGNORECASE | re.DOTALL)
    text = match.group(0) if match else cleaned
    return " ".join(text.split())


def table_references(scope: str) -> list[tuple[str, str | None]]:
    """(table, alias) for every FROM/JOIN reference in the scope's
    text, in order, lowercased; alias is None when the table stands
    bare. A name after FROM or JOIN is a real table or a CTE — the
    consumer tells them apart through the scope's named map."""
    references: list[tuple[str, str | None]] = []
    for _, table, alias in _TABLE_REF.findall(scope):
        alias_name = alias.lower() if alias and alias.lower() not in KEYWORDS else None
        references.append((table.lower(), alias_name))
    return references


def table_aliases(scope: str) -> dict[str, str]:
    """alias (or bare table name) -> table name, lowercased, from the
    scope's FROM/JOIN references. Shared with the select-list
    resolution both the verifier and the display layer read
    (tools/sql_select.py)."""
    aliases: dict[str, str] = {}
    for table, alias in table_references(scope):
        aliases[table] = table
        if alias is not None:
            aliases[alias] = table
    return aliases


def from_table_of(scope: str) -> str | None:
    """The scope's first FROM/JOIN reference — its row grain, the
    table a COUNT(*) is attributed to."""
    references = table_references(scope)
    return references[0][0] if references else None


def select_list_of(scope: str) -> str:
    """The text between the scope's first SELECT and its FROM — where
    the aggregates and result aliases live. A scope without FROM is
    not a query."""
    match = _SELECT_LIST.search(scope)
    return match.group(1) if match else ""


def split_items(select_list: str) -> list[str]:
    """Top-level comma split, paren-aware (function calls survive)."""
    items: list[str] = []
    depth = 0
    start = 0
    for index, char in enumerate(select_list):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            items.append(select_list[start:index])
            start = index + 1
    items.append(select_list[start:])
    return items


def split_alias(item: str) -> tuple[str | None, str]:
    """(alias, expression text) for one select item: `expr AS alias`,
    or a bare column whose alias is its own name; (None, item) when
    the item names no result column."""
    match = _AS_ALIAS.search(item)
    if match:
        return match.group(1), item[: match.start()]
    plain = PLAIN_ITEM.match(item)
    if plain and plain.group(3) is None:
        column = plain.group(2)
        if column.lower() in NON_COLUMN_WORDS:
            return None, item
        return column, item
    return None, item
