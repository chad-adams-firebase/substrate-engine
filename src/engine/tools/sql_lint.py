"""Deterministic post-generation SQL lint: the fan-out check.

The 4b baseline's MT2 breach: COUNT(*) over findings JOIN
compliance_reports JOIN compliance_rules cross-multiplied every
finding against every critical rule on the same invoice — 1,065
verified, truth 254. The join-path note warning about exactly this sat
verbatim in the grounding prompt and was ignored. Grounding-by-
inclusion is not grounding-by-enforcement; this is the mechanical
check.

Regex-level on purpose (the house precedent, generators/ckg/sql_tables
.py): no parser dependency the work machine cannot install. Scopes
are split on parentheses so a correlated or scalar subquery is linted
as its own SELECT, never mistaken for a join in its parent.

What fires: a scope with a non-DISTINCT COUNT/SUM in its select list
and at least one JOIN ... ON whose equality can multiply the rows of
the tables already in scope — a join to the many side of a foreign
key, a join of two foreign keys (MT2's shape), or a join no foreign
key or declared one-to-one path vouches for. What is exempt: lookups
along a foreign key from the from-side (findings -> invoices ->
suppliers), joins the Dictionary Map declares one_to_one, and
COUNT(DISTINCT ...). Comma-separated FROM lists escape the check
(the model does not write them).

The lint's word is a repair round, not a verdict: run_sql fires it at
most once per call and licenses the model to resend the statement
unchanged when the join cannot multiply rows.
"""

import re
from dataclasses import dataclass

from engine.substrates.models import DictionaryMap, DictionaryRow

_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_LINE_COMMENT = re.compile(r"--[^\n]*")
_KEYWORDS = {
    "on", "where", "join", "left", "right", "inner", "outer", "full",
    "cross", "group", "order", "limit", "having", "union", "qualify",
    "using", "as", "select", "with", "natural", "lateral",
}
_TABLE_REF = re.compile(
    r"\b(from|join)\s+([A-Za-z_]\w*)(?:\s+(?:as\s+)?([A-Za-z_]\w*))?",
    re.IGNORECASE,
)
_JOIN_ON = re.compile(
    r"\bjoin\s+([A-Za-z_]\w*)(?:\s+(?:as\s+)?([A-Za-z_]\w*))?\s+on\s+(.*?)"
    r"(?=\b(?:left|right|inner|outer|full|cross|join|where|group|order"
    r"|limit|having|union|qualify)\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_EQUALITY = re.compile(r"([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\.([A-Za-z_]\w*)")
_PLAIN_AGGREGATE = re.compile(r"\b(?:count|sum)\s*\(\s*(?!distinct\b)", re.IGNORECASE)
_SUBQUERY = "(__subquery__)"


@dataclass
class _RiskyJoin:
    joined: str
    left: str
    right: str
    reason: str


def _clean(sql: str) -> str:
    return _STRING_LITERAL.sub("''", _LINE_COMMENT.sub("", sql))


def split_scopes(sql: str) -> list[str]:
    """Every SELECT scope in the statement, each with its nested
    subqueries replaced by a placeholder: the outer statement, each
    CTE body, each subquery. Function-call parentheses stay inline so
    COUNT(DISTINCT x) remains visible to the aggregate scan."""
    scopes: list[str] = []

    def walk(text: str) -> str:
        out: list[str] = []
        index = 0
        while index < len(text):
            char = text[index]
            if char != "(":
                out.append(char)
                index += 1
                continue
            depth = 0
            end = index
            while end < len(text):
                if text[end] == "(":
                    depth += 1
                elif text[end] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                end += 1
            inner = text[index + 1 : end]
            if re.match(r"\s*(select|with)\b", inner, re.IGNORECASE):
                walk(inner)
                out.append(_SUBQUERY)
            else:
                out.append("(" + walk_inline(inner) + ")")
            index = end + 1
        flattened = "".join(out)
        scopes.append(flattened)
        return flattened

    def walk_inline(text: str) -> str:
        # Function arguments may themselves hold a subquery
        # (SUM(CASE WHEN x IN (SELECT ...))): recurse, keep the rest.
        out: list[str] = []
        index = 0
        while index < len(text):
            if text[index] != "(":
                out.append(text[index])
                index += 1
                continue
            depth = 0
            end = index
            while end < len(text):
                if text[end] == "(":
                    depth += 1
                elif text[end] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                end += 1
            inner = text[index + 1 : end]
            if re.match(r"\s*(select|with)\b", inner, re.IGNORECASE):
                walk(inner)
                out.append(_SUBQUERY)
            else:
                out.append("(" + walk_inline(inner) + ")")
            index = end + 1
        return "".join(out)

    walk(_clean(sql))
    return scopes


def lint_fan_out(
    sql: str, dictionary: list[DictionaryRow], dictionary_map: DictionaryMap
) -> str | None:
    """The reason the statement risks a join fan-out, or None."""
    fk_of: dict[tuple[str, str], str] = {
        (row.table_name.lower(), row.column_name.lower()): row.fk_target.lower()
        for row in dictionary
        if row.fk_target and row.column_name
    }
    one_to_one: set[frozenset[tuple[str, str]]] = set()
    for path in dictionary_map.join_paths:
        if path.cardinality == "one_to_one":
            for step in path.steps:
                one_to_one.add(
                    frozenset(
                        {
                            (step.from_table.lower(), step.from_column.lower()),
                            (step.to_table.lower(), step.to_column.lower()),
                        }
                    )
                )

    risky: list[_RiskyJoin] = []
    for scope in split_scopes(sql):
        if not _PLAIN_AGGREGATE.search(_select_list(scope)):
            continue
        aliases: dict[str, str] = {}
        for _, table, alias in _TABLE_REF.findall(scope):
            aliases[table.lower()] = table.lower()
            if alias and alias.lower() not in _KEYWORDS:
                aliases[alias.lower()] = table.lower()
        for joined, _, condition in _JOIN_ON.findall(scope):
            joined = joined.lower()
            for a, c1, b, c2 in _EQUALITY.findall(condition):
                ta, tb = aliases.get(a.lower(), a.lower()), aliases.get(b.lower(), b.lower())
                c1, c2 = c1.lower(), c2.lower()
                left, right = f"{ta}.{c1}", f"{tb}.{c2}"
                if frozenset({(ta, c1), (tb, c2)}) in one_to_one:
                    continue
                a_fk = fk_of.get((ta, c1)) == right
                b_fk = fk_of.get((tb, c2)) == left
                if a_fk and b_fk:
                    reason = "each side is a foreign key to the other"
                elif a_fk or b_fk:
                    many = ta if a_fk else tb
                    if many != joined:
                        continue  # a lookup from the from-side: many-to-one
                    reason = (
                        f"{many}.{c1 if a_fk else c2} is a foreign key — "
                        f"several {many} rows can share one "
                        f"{tb if a_fk else ta} row"
                    )
                else:
                    shared = fk_of.get((ta, c1))
                    if shared and shared == fk_of.get((tb, c2)):
                        reason = (
                            f"both columns are foreign keys to {shared} — "
                            f"every {ta} row pairs with every {tb} row of "
                            f"the same {shared.split('.')[0]}"
                        )
                    else:
                        reason = "no foreign key relates these columns"
                risky.append(_RiskyJoin(joined, left, right, reason))

    if not risky:
        return None
    involved = {r.left.split(".")[0] for r in risky} | {
        r.right.split(".")[0] for r in risky
    }
    paths = [
        path.name
        for path in dictionary_map.join_paths
        if any(
            step.from_table.lower() in involved or step.to_table.lower() in involved
            for step in path.steps
        )
    ]
    listed = "; ".join(f"{r.left} = {r.right} ({r.reason})" for r in risky)
    hint = f" Canonical join paths for these tables: {', '.join(paths)}." if paths else ""
    return (
        "Fan-out check: COUNT/SUM over a multi-table join counts join "
        "combinations, not entities. Join condition(s) that can multiply "
        f"rows: {listed}. Count the entity the question is about with "
        "COUNT(DISTINCT <table>.id), or aggregate from the table that "
        f"carries the filtered column.{hint} If this join cannot multiply "
        "rows, resend the statement unchanged."
    )


def _select_list(scope: str) -> str:
    """The text between the scope's first SELECT and its FROM — where
    the aggregates live. A scope without FROM is not a query."""
    match = re.search(r"\bselect\b(.*?)\bfrom\b", scope, re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else ""
