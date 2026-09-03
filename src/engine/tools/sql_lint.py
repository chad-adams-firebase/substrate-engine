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

What fires: a scope with a non-DISTINCT COUNT/SUM/AVG in its select
list and at least one JOIN ... ON whose equality can multiply the
rows of the tables already in scope — a join to the many side of a
foreign key, a join of two foreign keys (MT2's shape), or a join no
foreign key or declared one-to-one path vouches for. SUM(DISTINCT)
and AVG(DISTINCT) also fire in such a scope: DISTINCT inside SUM/AVG
silently drops repeated values (the play pass's W7 band-aid), so it
is a challenged pattern, not a repair. What is exempt: lookups along
a foreign key from the from-side (findings -> invoices -> suppliers),
joins the Dictionary Map declares one_to_one, and COUNT(DISTINCT ...).
Comma-separated FROM lists escape the check (the model does not write
them).

The pin pass added three join-shape checks (the post-play-pass
breach's three mechanisms):
- An ON condition with no plain column equality at all whose text
  derives a key with an expression (a function call, ||, arithmetic)
  is risky regardless of key knowledge — expression joins defeat
  foreign-key reasoning, and MT2's CONCAT join fanned 17x. A plain
  FK-vouched equality in the same condition exempts it: AND-ed
  predicates only filter further.
- A LEFT JOIN whose table is referenced only inside its own ON
  condition, in a scope with any aggregate (COUNT(DISTINCT ...)
  included), cannot filter rows or contribute columns — B5's shape,
  where "invoices that HAD findings" kept every invoice.
- AVG over a column from the nullable side of a LEFT JOIN (declared
  one_to_one joins exempt): AVG skips NULLs, so unmatched rows vanish
  from the denominator and the average saturates — S2's 1.0. This one
  sees LEFT JOINs to subqueries too; the others read plain tables
  only (a subquery alias carries no foreign-key knowledge).

The lint's word is a repair round, not a verdict: run_sql blocks on
it at most once per call and licenses the model to resend the
statement unchanged when the join cannot multiply rows. Overriding is
no longer invisible, though: run_sql re-lints the resend in
detection-only mode and records a still-tripping reason on the
executed attempt, which the Verifier turns into a plausibility warn.
"""

import re

from engine.substrates.models import DictionaryMap, DictionaryRow

_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_LINE_COMMENT = re.compile(r"--[^\n]*")
_KEYWORDS = {
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
    rf"(?:\s+(?:as\s+)?(?!(?:{'|'.join(sorted(_KEYWORDS))})\b)([A-Za-z_]\w*))?",
    re.IGNORECASE,
)
# A double-quoted identifier, or a single-quoted literal to step over
# so a string containing "quotes" is never edited.
_QUOTED_OR_LITERAL = re.compile(r"'(?:[^']|'')*'|\"([A-Za-z_]\w*)\"")
_JOIN_ON = re.compile(
    r"\bjoin\s+([A-Za-z_]\w*)(?:\s+(?:as\s+)?([A-Za-z_]\w*))?\s+on\s+(.*?)"
    r"(?=\b(?:left|right|inner|outer|full|cross|join|where|group|order"
    r"|limit|having|union|qualify)\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_EQUALITY = re.compile(r"([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*=\s*([A-Za-z_]\w*)\.([A-Za-z_]\w*)")
_PLAIN_AGGREGATE = re.compile(
    r"\b(?:count|sum|avg)\s*\(\s*(?!distinct\b)", re.IGNORECASE
)
# SUM(DISTINCT x)/AVG(DISTINCT x) drops repeated values instead of
# de-fanning the join — the observed band-aid, challenged like a plain
# aggregate. COUNT(DISTINCT x) stays the sanctioned repair.
_DISTINCT_AGG_BANDAID = re.compile(
    r"\b(?:sum|avg)\s*\(\s*distinct\b", re.IGNORECASE
)
_SUBQUERY = "(__subquery__)"
# Any aggregate, DISTINCT included — the gate for the dead-LEFT-JOIN
# check, which COUNT(DISTINCT ...) must not slip past (B5 did).
_ANY_AGGREGATE = re.compile(r"\b(?:count|sum|avg)\s*\(", re.IGNORECASE)
# LEFT JOIN to a plain table or a hoisted subquery, with its ON
# condition. Kept separate from _JOIN_ON so the foreign-key equality
# loop never sees subquery joins (a subquery alias has no FK row, and
# feeding it in would challenge masses of legitimate SQL).
_LEFT_JOIN_ON = re.compile(
    r"\bleft\s+(?:outer\s+)?join\s+"
    r"(?:\(__subquery__\)\s+(?:as\s+)?([A-Za-z_]\w*)"
    r"|([A-Za-z_]\w*)(?:\s+(?:as\s+)?([A-Za-z_]\w*))?)"
    r"\s+on\s+(.*?)"
    r"(?=\b(?:left|right|inner|outer|full|cross|join|where|group|order"
    r"|limit|having|union|qualify)\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_AVG_QUALIFIED = re.compile(
    r"\bavg\s*\(\s*([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)\s*\)", re.IGNORECASE
)
_FUNC_CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
# Logical connectors that precede a parenthesis without deriving a
# value — everything else before "(" is a function call.
_NOT_FUNCTIONS = {"in", "exists", "any", "all", "some", "not", "and", "or"}
_ARITHMETIC_OR_CONCAT = re.compile(r"[+\-*/]|\|\|")


def _derives_a_key(condition: str) -> bool:
    """True when the ON condition computes its join key — a function
    call, concatenation, or arithmetic (string literals are already
    blanked, so a quoted date can't false-positive)."""
    if _ARITHMETIC_OR_CONCAT.search(condition):
        return True
    return any(
        name.lower() not in _NOT_FUNCTIONS
        for name in _FUNC_CALL.findall(condition)
    )


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


def _clean(sql: str) -> str:
    return _STRING_LITERAL.sub(
        "''", _LINE_COMMENT.sub("", unquote_identifiers(sql))
    )


def original_fragment(sql: str, cleaned: str) -> str:
    """The model's own text for a fragment of a cleaned scope. _clean
    blanks every string literal to '' and split_scopes hoists
    subqueries, so a challenge that quotes the cleaned text would show
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
        elif token == _SUBQUERY:
            parts.append(r"\((?:[^()]|\([^()]*\))*\)")
        elif token.isspace():
            parts.append(r"\s+")
        else:
            parts.append(re.escape(token))
    match = re.search("".join(parts), sql, re.IGNORECASE | re.DOTALL)
    text = match.group(0) if match else cleaned
    return " ".join(text.split())


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


def table_aliases(scope: str) -> dict[str, str]:
    """alias (or bare table name) -> table name, lowercased, from the
    scope's FROM/JOIN references. Shared with the select-list
    resolution both the verifier and the display layer read
    (tools/sql_select.py)."""
    aliases: dict[str, str] = {}
    for _, table, alias in _TABLE_REF.findall(scope):
        aliases[table.lower()] = table.lower()
        if alias and alias.lower() not in _KEYWORDS:
            aliases[alias.lower()] = table.lower()
    return aliases


def select_list_of(scope: str) -> str:
    """The text between the scope's first SELECT and its FROM — where
    the aggregates and result aliases live."""
    return _select_list(scope)


def lint_fan_out(
    sql: str, dictionary: list[DictionaryRow], dictionary_map: DictionaryMap
) -> str | None:
    """The reason the statement risks a join fan-out (or a LEFT JOIN
    shape that answers the wrong question), or None."""
    fk_of: dict[tuple[str, str], str] = {
        (row.table_name.lower(), row.column_name.lower()): row.fk_target.lower()
        for row in dictionary
        if row.fk_target and row.column_name
    }
    columns_of: dict[str, set[str]] = {}
    for row in dictionary:
        if row.column_name:
            columns_of.setdefault(row.table_name.lower(), set()).add(
                row.column_name.lower()
            )
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

    fan: list[str] = []
    dead: list[str] = []
    avg_null: list[str] = []
    involved: set[str] = set()
    bandaid_seen = False
    for scope in split_scopes(sql):
        select_list = _select_list(scope)
        bandaid = _DISTINCT_AGG_BANDAID.search(select_list) is not None
        plain_agg = _PLAIN_AGGREGATE.search(select_list) is not None
        any_agg = _ANY_AGGREGATE.search(select_list) is not None
        if not (plain_agg or bandaid or any_agg):
            continue
        aliases = table_aliases(scope)

        if plain_agg or bandaid:
            fan_before = len(fan)
            for joined, _, condition in _JOIN_ON.findall(scope):
                joined = joined.lower()
                equalities = _EQUALITY.findall(condition)
                for a, c1, b, c2 in equalities:
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
                    fan.append(f"{left} = {right} ({reason})")
                    involved.update({ta, tb})
                # A condition with no plain column equality at all that
                # derives its key with an expression (MT2's CONCAT join):
                # no foreign key can vouch for a computed key, and a
                # non-unique derived key fans. Any plain equality above
                # either fired, or vouched the join's grain (AND-ed
                # predicates only filter further) — so this arm is
                # reached only when FK reasoning had nothing to read.
                if not equalities and _derives_a_key(condition):
                    snippet = original_fragment(sql, condition)
                    fan.append(
                        f"join to {joined} on {snippet} (the join "
                        "condition derives its key with an expression — "
                        "expression joins defeat foreign-key reasoning, "
                        "and a derived key that is non-unique fans out; "
                        "the Dictionary Map's canonical join paths use "
                        "real key columns)"
                    )
                    involved.add(joined)
                    involved.update(
                        aliases.get(q.lower(), q.lower())
                        for q in re.findall(r"([A-Za-z_]\w*)\s*\.", condition)
                    )
            if bandaid and len(fan) > fan_before:
                bandaid_seen = True

        for match in _LEFT_JOIN_ON.finditer(scope):
            sub_alias, table, alias, condition = match.groups()
            declared_one_to_one = any(
                frozenset(
                    {
                        (aliases.get(a.lower(), a.lower()), c1.lower()),
                        (aliases.get(b.lower(), b.lower()), c2.lower()),
                    }
                )
                in one_to_one
                for a, c1, b, c2 in _EQUALITY.findall(condition)
            )
            if declared_one_to_one:
                continue  # the map vouches for the join's shape
            if sub_alias:
                qualifiers = {sub_alias.lower()}
            else:
                qualifiers = {table.lower()}
                if alias and alias.lower() not in _KEYWORDS:
                    qualifiers.add(alias.lower())
            for qual, col in _AVG_QUALIFIED.findall(select_list):
                if qual.lower() in qualifiers:
                    avg_null.append(f"AVG({qual}.{col})")
                    if not sub_alias:
                        involved.add(table.lower())
            # Dead LEFT JOIN (B5): plain tables only — a subquery's
            # columns are unknowable, so "referenced" cannot be judged.
            if sub_alias or not any_agg:
                continue
            remainder = scope[: match.start()] + scope[match.end() :]
            used_qualified = re.search(
                rf"\b(?:{'|'.join(qualifiers)})\s*\.", remainder, re.IGNORECASE
            )
            bare_columns = columns_of.get(table.lower(), set())
            used_bare = bare_columns and re.search(
                rf"(?<![.\w])(?:{'|'.join(bare_columns)})\b",
                remainder,
                re.IGNORECASE,
            )
            if not (used_qualified or used_bare):
                dead.append(table.lower())
                involved.add(table.lower())

    if not (fan or dead or avg_null):
        return None
    paths = [
        path.name
        for path in dictionary_map.join_paths
        if any(
            step.from_table.lower() in involved or step.to_table.lower() in involved
            for step in path.steps
        )
    ]
    hint = f" Canonical join paths for these tables: {', '.join(paths)}." if paths else ""
    parts: list[str] = []
    if fan:
        listed = "; ".join(fan)
        bandaid_note = (
            " SUM(DISTINCT ...) / AVG(DISTINCT ...) is not a fan-out repair:"
            " it silently drops repeated values."
            if bandaid_seen
            else ""
        )
        parts.append(
            "Fan-out check: COUNT/SUM/AVG over a multi-table join "
            "aggregates join combinations, not entities. Join condition(s) "
            f"that can multiply rows: {listed}. Count the entity the "
            "question is about with COUNT(DISTINCT <table>.id); for SUM or "
            "AVG, aggregate the fanning table in a subquery joined back "
            "per entity, or aggregate from the table that carries the "
            f"filtered column.{bandaid_note}{hint} If this join cannot "
            "multiply rows, resend the statement unchanged."
        )
    if dead:
        names = ", ".join(dict.fromkeys(dead))
        parts.append(
            f"Join-shape check: LEFT JOIN {names} is referenced only "
            "inside its own ON condition — a LEFT JOIN keeps every row of "
            "the left table, so this join cannot filter rows or contribute "
            "columns and does not answer which rows HAVE a match. If the "
            "question means rows with a match, use an inner join or "
            "require a joined column IS NOT NULL. If every row belongs in "
            "the result regardless of a match, resend the statement "
            "unchanged."
        )
    if avg_null:
        listed_avg = ", ".join(dict.fromkeys(avg_null))
        parts.append(
            f"NULL-semantics check: {listed_avg} reads the nullable side "
            "of a LEFT JOIN, and AVG skips NULLs — rows without a match "
            "contribute nothing, so the average saturates toward the "
            "matched side. If unmatched rows belong in the denominator, "
            "wrap the column: AVG(COALESCE(<column>, 0)) or a CASE. If "
            "NULLs are impossible or deliberately excluded, resend the "
            "statement unchanged."
        )
    return " ".join(parts)


def _select_list(scope: str) -> str:
    """The text between the scope's first SELECT and its FROM — where
    the aggregates live. A scope without FROM is not a query."""
    match = re.search(r"\bselect\b(.*?)\bfrom\b", scope, re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else ""
