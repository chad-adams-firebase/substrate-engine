"""Deterministic post-generation SQL lint: the fan-out check.

The 4b baseline's MT2 breach: COUNT(*) over findings JOIN
compliance_reports JOIN compliance_rules cross-multiplied every
finding against every critical rule on the same invoice — 1,065
verified, truth 254. The join-path note warning about exactly this sat
verbatim in the grounding prompt and was ignored. Grounding-by-
inclusion is not grounding-by-enforcement; this is the mechanical
check.

Regex-level on purpose (the house precedent, generators/ckg/sql_tables
.py): no parser dependency the work machine cannot install. The text
layer — scopes split on parentheses so a correlated or scalar subquery
is linted as its own SELECT, never mistaken for a join in its parent;
table references; select lists — is tools/sql_scopes.py, shared with
every other lint and parse; this module holds the rules.

What fires (the Polish Pass's direction rule): a scope whose
COUNT/SUM/AVG reads a table its joins REPEAT. A join along a foreign
key, one.id = many.fk, repeats each one-side row once per many-side
row and never repeats the many side — so SUM(invoices.invoice_total)
across invoices JOIN invoice_lines fans, and SUM(invoice_lines
.extended_price) across the same join does not (W1's four runs at 0/5
were the direction-blind reading of exactly that pair). In a scope
whose every step is such a vouched one-to-many join, the repeated
tables are the one side of each step plus any many sides that share a
one side (siblings repeat each other); a scope with a step nothing
vouches for — both sides foreign keys to each other or to the same
target (MT2's shape), no foreign key at all, or a key derived by an
expression — repeats every table in it, the conservative stance kept
from before. An aggregate is attributed to the tables its argument
reads; one that reads no outer column (COUNT(*), a CASE over a
correlated subquery) counts the scope's row grain and is attributed to
the FROM table, which keeps a lookup chain from the from-side
(findings -> invoices -> suppliers) silent. SUM(DISTINCT) and
AVG(DISTINCT) are read like plain aggregates and, when they fire, are
named as the band-aid they are (the play pass's W7): DISTINCT inside
SUM/AVG silently drops repeated values. Exempt: joins the Dictionary
Map declares one_to_one, and COUNT(DISTINCT ...). Comma-separated FROM
lists escape the check (the model does not write them).

The challenge names the aggregate, the table it reads, and the step
that repeats it — never a destination (the guard pass's principle:
a challenge names what is wrong, and no table the statement does not
already query, anywhere in its text).

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
from engine.tools.sql_scopes import (
    KEYWORDS,
    from_table_of,
    original_fragment,
    select_list_of,
    split_scopes,
    table_aliases,
)

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
_AGGREGATE_CALL = re.compile(r"\b(count|sum|avg)\s*\(", re.IGNORECASE)
_DISTINCT_PREFIX = re.compile(r"^\s*distinct\b", re.IGNORECASE)
_QUALIFIED_REF = re.compile(r"\b([A-Za-z_]\w*)\s*\.\s*([A-Za-z_]\w*)")
_WORD = re.compile(r"\b([A-Za-z_]\w*)\b")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# Words an aggregate argument may hold that are never a column of the
# scope's tables — keywords and type names. A function name is
# recognised by the parenthesis after it, a string literal is already
# blanked, a hoisted subquery reads as its placeholder.
_ARGUMENT_KEYWORDS = {
    "distinct", "case", "when", "then", "else", "end", "null", "and", "or",
    "not", "in", "is", "like", "between", "as", "true", "false", "interval",
    "exists", "any", "all", "some", "escape", "integer", "bigint", "smallint",
    "double", "float", "real", "decimal", "numeric", "varchar", "text",
    "date", "timestamp", "boolean", "current_date", "current_timestamp",
    "year", "month", "week", "day", "hour", "minute", "second",
}
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


def _aggregate_arguments(select_list: str) -> list[tuple[str, str]]:
    """(function, argument text) for every COUNT/SUM/AVG in a select
    list, the argument read to its balanced closing parenthesis."""
    found: list[tuple[str, str]] = []
    for match in _AGGREGATE_CALL.finditer(select_list):
        depth = 0
        start = match.end() - 1
        for index in range(start, len(select_list)):
            if select_list[index] == "(":
                depth += 1
            elif select_list[index] == ")":
                depth -= 1
                if depth == 0:
                    found.append((match.group(1).lower(), select_list[start + 1 : index]))
                    break
    return found


def _tables_read(
    argument: str,
    aliases: dict[str, str],
    columns_of: dict[str, set[str]],
    in_scope: set[str],
) -> set[str] | None:
    """The tables an aggregate argument reads. None for the row grain —
    COUNT(*), COUNT(1) — which the caller attributes to the FROM table.
    A qualified column resolves through the scope's aliases; a bare one
    through the dictionary when exactly one in-scope table owns it; an
    identifier nothing owns is "?" (the caller reads it conservatively).
    Keywords, function names and hoisted subqueries are not columns."""
    text = _DISTINCT_PREFIX.sub("", argument).strip()
    if text in ("*", "") or _NUMBER.fullmatch(text):
        return None
    tables: set[str] = set()
    for qualifier, _ in _QUALIFIED_REF.findall(text):
        tables.add(aliases.get(qualifier.lower(), qualifier.lower()))
    bare = _QUALIFIED_REF.sub(" ", text)
    called = {name.lower() for name in _FUNC_CALL.findall(bare)}
    for word in _WORD.findall(bare):
        lowered = word.lower()
        if (
            lowered in _ARGUMENT_KEYWORDS
            or lowered in called
            or lowered in aliases
            or lowered.startswith("__")
        ):
            continue
        owners = {t for t in in_scope if lowered in columns_of.get(t, set())}
        tables.add(owners.pop() if len(owners) == 1 else "?")
    return tables


def _join_steps(
    scope: str,
    sql: str,
    aliases: dict[str, str],
    fk_of: dict[tuple[str, str], str],
    one_to_one: set[frozenset[tuple[str, str]]],
) -> list[tuple[str, str | None, str | None, set[str], str]]:
    """Every join step in the scope that is not declared one_to_one:
    (kind, one, many, tables, text). kind is "one_to_many" when exactly
    one side is a foreign key to the other, else "unvouched"; text is
    the condition with its reason in parentheses."""
    steps: list[tuple[str, str | None, str | None, set[str], str]] = []
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
                steps.append((
                    "unvouched", None, None, {ta, tb},
                    f"{left} = {right} (each side is a foreign key to the other)",
                ))
            elif a_fk or b_fk:
                many, one, fk = (ta, tb, c1) if a_fk else (tb, ta, c2)
                steps.append((
                    "one_to_many", one, many, {ta, tb},
                    f"{left} = {right} ({many}.{fk} is a foreign key — "
                    f"several {many} rows can share one {one} row)",
                ))
            else:
                shared = fk_of.get((ta, c1))
                if shared and shared == fk_of.get((tb, c2)):
                    reason = (
                        "both columns are foreign keys with the same target — "
                        f"every {ta} row pairs with every {tb} row that shares it"
                    )
                else:
                    reason = "no foreign key relates these columns"
                steps.append(("unvouched", None, None, {ta, tb}, f"{left} = {right} ({reason})"))
        # A condition with no plain column equality at all that derives
        # its key with an expression (MT2's CONCAT join): no foreign key
        # can vouch for a computed key, and a non-unique derived key
        # fans. Any plain equality above either fired, or vouched the
        # join's grain (AND-ed predicates only filter further) — so this
        # arm is reached only when FK reasoning had nothing to read.
        if not equalities and _derives_a_key(condition):
            snippet = original_fragment(sql, condition)
            tables = {joined} | {
                aliases.get(q.lower(), q.lower())
                for q in re.findall(r"([A-Za-z_]\w*)\s*\.", condition)
            }
            steps.append((
                "unvouched", None, None, tables,
                f"join to {joined} on {snippet} (the join condition derives "
                "its key with an expression — expression joins defeat "
                "foreign-key reasoning, and a derived key that is non-unique "
                "fans out; the Dictionary Map's canonical join paths use real "
                "key columns)",
            ))
    return steps


def _repeated_tables(
    steps: list[tuple[str, str | None, str | None, set[str], str]],
    in_scope: set[str],
) -> dict[str, list[str]]:
    """table -> the reasons this scope's joins repeat its rows. With a
    step nothing vouches for, every table in scope is repeated (by all
    the steps); otherwise the one side of each one-to-many step, and
    the many sides that share a one side."""
    if any(kind == "unvouched" for kind, *_ in steps):
        listed = "; ".join(text for *_, text in steps)
        reason = f"across join condition(s) nothing vouches for: {listed}"
        return {table: [reason] for table in in_scope}
    repeated: dict[str, list[str]] = {}
    siblings: dict[str, list[str]] = {}
    for _, one, many, _, text in steps:
        assert one is not None and many is not None
        condition, _, reason = text.partition(" (")
        repeated.setdefault(one, []).append(
            f"and {condition} repeats each {one} row once per {many} row ({reason}"
        )
        if many not in siblings.setdefault(one, []):
            siblings[one].append(many)
    for one, manys in siblings.items():
        for many in manys:
            others = [m for m in manys if m != many]
            if others:
                repeated.setdefault(many, []).append(
                    f"and this scope joins {one} to both {many} and "
                    f"{', '.join(others)}, so each {many} row repeats once per "
                    f"{', '.join(others)} row of the same {one}"
                )
    return repeated


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
        select_list = select_list_of(scope)
        bandaid = _DISTINCT_AGG_BANDAID.search(select_list) is not None
        plain_agg = _PLAIN_AGGREGATE.search(select_list) is not None
        any_agg = _ANY_AGGREGATE.search(select_list) is not None
        if not (plain_agg or bandaid or any_agg):
            continue
        aliases = table_aliases(scope)

        if plain_agg or bandaid:
            in_scope = set(aliases.values())
            from_table = from_table_of(scope)
            steps = _join_steps(scope, sql, aliases, fk_of, one_to_one)
            repeated = _repeated_tables(steps, in_scope)
            fired_here = False
            for func, argument in _aggregate_arguments(select_list):
                if func == "count" and _DISTINCT_PREFIX.match(argument):
                    continue  # COUNT(DISTINCT x) is the sanctioned repair
                reads = _tables_read(argument, aliases, columns_of, in_scope)
                if reads is None:
                    reads = {from_table} if from_table else set()
                if "?" in reads:
                    reads = (reads - {"?"}) | set(repeated)
                hit = [table for table in sorted(reads) if table in repeated]
                if not hit:
                    continue
                fired_here = True
                shown = original_fragment(sql, f"{func}({argument})")
                for table in hit:
                    for reason in repeated[table]:
                        entry = f"{shown} reads {table} {reason}"
                        if entry not in fan:
                            fan.append(entry)
                    involved.update(
                        t for kind, _, _, tables, _ in steps for t in tables
                        if kind == "unvouched" or table in tables
                    )
            if bandaid and fired_here:
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
                if alias and alias.lower() not in KEYWORDS:
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
            f"Fan-out check: {listed} — the aggregate adds join "
            "combinations, not entities. Aggregate each side in its own "
            "scope, then join the results; count an entity with "
            f"COUNT(DISTINCT <table>.id).{bandaid_note}{hint} If this join "
            "cannot multiply rows, resend the statement unchanged."
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

