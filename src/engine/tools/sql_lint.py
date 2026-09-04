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
Map declares one_to_one — always, or under a filter the scope applies
(one_to_one_when, the Close Pass: invoice_history -> invoices fans in
general and is one row per invoice under a terminal status, which the
lint cannot infer from SQL and the pack declares; the scope's WHERE or
the step's own ON must restrict the declared column to the declared
values, with no top-level OR, and a self-join whose two sides are each
vouched that way is one-to-one) — and COUNT(DISTINCT ...).
Comma-separated FROM lists escape the check (the model does not write
them).

A CTE or derived table is read through the scope registry (the Close
Pass — AMB1's five reps were challenged on a join to a DISTINCT CTE by
its key, "no foreign key relates these columns", the shape the
challenge itself recommends): a scope whose projection is unique on
the join's columns (SELECT DISTINCT k, GROUP BY k — a primary key
beside other columns of its table counts as the key alone) is one row
per key and a one side; a table is on its primary key; both unique and
the join is one-to-one. Otherwise a scope's plain pass-through column
reads the foreign-key knowledge of the column behind it (unless the
scope's own joins repeat that table), so a lookup or a filtered many
side written as a CTE behaves exactly like its flat twin, and a
computed column vouches for nothing. And an aggregate over a scope that
is not deduplicated reads what that scope's rows are: COUNT(*) FROM a
CTE whose body LEFT JOINs the line grain reads the lines across that
join (S2 reps 2/4's 100%, silent before — the hidden-fan gap the pin
pass recorded, closed), SUM(x.total) over a CTE that repeats invoices
reads invoices, and a deduplicated scope propagates nothing. When a
LEFT JOIN is among the joins that fired, the challenge also names the
EXISTS test — the shape a match indicator should take.

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
from dataclasses import dataclass

from engine.substrates.models import DictionaryMap, DictionaryRow
from engine.tools.sql_scopes import (
    EQUALS_LITERAL,
    IN_LITERALS,
    KEYWORDS,
    QueryScope,
    from_table_of,
    literals_between,
    original_fragment,
    scope_tree,
    select_list_of,
    split_alias,
    split_items,
    table_aliases,
    table_references,
)

# A join to a table, a CTE (by name) or a derived table (a hoisted
# subquery under its alias — visible since the Close Pass, read through
# the scope registry rather than fed to the foreign-key loop blind).
_JOIN_ON = re.compile(
    r"\bjoin\s*(?:\(__subquery__\)\s+(?:as\s+)?([A-Za-z_]\w*)"
    r"|([A-Za-z_]\w*)(?:\s+(?:as\s+)?([A-Za-z_]\w*))?)\s+on\s+(.*?)"
    r"(?=\b(?:left|right|inner|outer|full|cross|join|where|group|order"
    r"|limit|having|union|qualify)\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_GROUP_BY = re.compile(
    r"\bgroup\s+by\b(.*?)(?=\b(?:having|order|limit|qualify|union|window)\b|$)",
    re.IGNORECASE | re.DOTALL,
)
_TOP_LEVEL_UNION = re.compile(r"\bunion\b", re.IGNORECASE)
_PLAIN_COLUMN = re.compile(r"^\s*(?:([A-Za-z_]\w*)\s*\.\s*)?([A-Za-z_]\w*)\s*$")
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

@dataclass(frozen=True)
class _Condition:
    """A declared one_to_one_when, split for the lint: the table and
    column the filter reads, and the values under which the path is
    one row per key."""

    table: str
    column: str
    values: frozenset[str]


@dataclass
class _Context:
    """What one lint_fan_out call reads from the dictionary and the
    map, built once: foreign keys, primary keys, columns per table, the
    column pairs declared one-to-one, and the ones declared one-to-one
    under a filter."""

    sql: str
    fk_of: dict[tuple[str, str], str]
    pk: set[tuple[str, str]]
    columns_of: dict[str, set[str]]
    one_to_one: set[frozenset[tuple[str, str]]]
    conditional: dict[frozenset[tuple[str, str]], list[_Condition]]


def _context(
    sql: str, dictionary: list[DictionaryRow], dictionary_map: DictionaryMap
) -> _Context:
    fk_of: dict[tuple[str, str], str] = {
        (row.table_name.lower(), row.column_name.lower()): row.fk_target.lower()
        for row in dictionary
        if row.fk_target and row.column_name
    }
    pk = {
        (row.table_name.lower(), row.column_name.lower())
        for row in dictionary
        if row.is_primary_key and row.column_name
    }
    columns_of: dict[str, set[str]] = {}
    for row in dictionary:
        if row.column_name:
            columns_of.setdefault(row.table_name.lower(), set()).add(
                row.column_name.lower()
            )
    one_to_one: set[frozenset[tuple[str, str]]] = set()
    conditional: dict[frozenset[tuple[str, str]], list[_Condition]] = {}
    for path in dictionary_map.join_paths:
        conditions = [
            _Condition(
                table=condition.table.lower(),
                column=condition.column_name.lower(),
                values=frozenset(condition.values),
            )
            for condition in path.one_to_one_when
        ]
        for step in path.steps:
            pair = frozenset(
                {
                    (step.from_table.lower(), step.from_column.lower()),
                    (step.to_table.lower(), step.to_column.lower()),
                }
            )
            if path.cardinality == "one_to_one":
                one_to_one.add(pair)
            for condition in conditions:
                if condition.table in (step.from_table.lower(), step.to_table.lower()):
                    conditional.setdefault(pair, []).append(condition)
    return _Context(
        sql=sql,
        fk_of=fk_of,
        pk=pk,
        columns_of=columns_of,
        one_to_one=one_to_one,
        conditional=conditional,
    )


# The scope's WHERE clause, up to the next clause keyword; subqueries
# are already hoisted, so an inner WHERE never appears here.
_WHERE_REGION = re.compile(
    r"\bwhere\b(.*?)(?=\b(?:group|order|limit|having|qualify|union|window)\b|$)",
    re.IGNORECASE | re.DOTALL,
)
# A column restricted to string literals — the literals are blanked to
# '' in scope text and read back by index from the scope. The word
# before IN must be the column itself, so NOT IN never reads as IN;
# <>, != and a literal-first comparison never match.
_EQUALS_LITERAL = EQUALS_LITERAL  # promoted to sql_scopes (Backlog Pass)
_IN_LITERALS = IN_LITERALS
_PAREN_OR_OR = re.compile(r"[()]|\bor\b", re.IGNORECASE)


def _top_level_or(text: str) -> bool:
    depth = 0
    for match in _PAREN_OR_OR.finditer(text):
        token = match.group()
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
        elif depth == 0:
            return True
    return False


def _filter_regions(scope: QueryScope, on_span: tuple[int, int]) -> list[tuple[int, int]]:
    """Where a step's filter can stand: the step's own ON condition
    and the scope's WHERE clause, as spans of scope.text."""
    regions = [on_span]
    match = _WHERE_REGION.search(scope.text)
    if match is not None:
        regions.append((match.start(1), match.end(1)))
    return regions


def _condition_satisfied(
    scope: QueryScope,
    regions: list[tuple[int, int]],
    alias: str,
    table: str,
    aliases: dict[str, str],
    condition: _Condition,
    ctx: _Context,
) -> bool:
    """The scope restricts this alias's declared column to the declared
    values: an equality or IN over string literals, qualified with the
    step's own alias (or the bare table when the scope references it
    once, or unqualified when exactly one in-scope table owns the
    column and that table is referenced once), in a region with no
    top-level OR, every literal within the declared set."""
    if condition.table != table:
        return False
    once = sum(1 for name, _ in table_references(scope.text) if name == table) == 1
    for start, end in regions:
        region = scope.text[start:end]
        if _top_level_or(region):
            continue
        for pattern in (_EQUALS_LITERAL, _IN_LITERALS):
            for match in pattern.finditer(region):
                qualifier, column = match.group(1), match.group(2).lower()
                if column != condition.column:
                    continue
                if qualifier is not None:
                    qualifier = qualifier.lower()
                    if qualifier != alias and not (once and aliases.get(qualifier) == table):
                        continue
                else:
                    owners = {
                        name
                        for name in set(aliases.values())
                        if column in ctx.columns_of.get(name, set())
                    }
                    if owners != {table} or not once:
                        continue
                literals = literals_between(scope, start + match.start(), start + match.end())
                if literals and all(value in condition.values for value in literals):
                    return True
    return False


def _vouched_conditionally(
    scope: QueryScope,
    regions: list[tuple[int, int]],
    pair: frozenset[tuple[str, str]],
    sides: tuple[tuple[str, str], ...],
    aliases: dict[str, str],
    ctx: _Context,
) -> bool:
    """A one_to_one_when declared on the pair's path holds in this
    scope for one of the sides, given as (alias, table)."""
    return any(
        _condition_satisfied(scope, regions, alias, table, aliases, condition, ctx)
        for condition in ctx.conditional.get(pair, [])
        for alias, table in sides
    )


def _one_to_one_step(
    scope: QueryScope,
    regions: list[tuple[int, int]],
    a: str,
    c1: str,
    b: str,
    c2: str,
    aliases: dict[str, str],
    ctx: _Context,
) -> bool:
    """The map vouches for this equality's shape: declared one_to_one,
    or one_to_one_when under a filter the scope applies."""
    a, b, c1, c2 = a.lower(), b.lower(), c1.lower(), c2.lower()
    ta, tb = aliases.get(a, a), aliases.get(b, b)
    pair = frozenset({(ta, c1), (tb, c2)})
    return pair in ctx.one_to_one or _vouched_conditionally(
        scope, regions, pair, ((a, ta), (b, tb)), aliases, ctx
    )


Step = tuple[str, str | None, str | None, set[str], str]


@dataclass
class ScopeGrain:
    """What a named scope's rows are, read from its own text (Close
    Pass): the table its row grain follows, the output columns it is
    unique on (a DISTINCT projection, a GROUP BY key), what each output
    column reads, which output columns are plain columns of a body
    table, and the tables its own joins repeat."""

    from_table: str | None
    unique_on: frozenset[str] | None
    reads: dict[str, set[str] | None]
    passthrough: dict[str, tuple[str, str]]
    repeated: dict[str, list[str]]
    steps: list[Step]
    has_left_join: bool


def _grain_of(
    scope: QueryScope, name: str, ctx: _Context, memo: dict[int, ScopeGrain]
) -> ScopeGrain | None:
    """The grain of the CTE or derived table this scope references
    under name, or None when name is a real table."""
    named = scope.named.get(name)
    return None if named is None else _scope_grain(named, ctx, memo)


def _scope_grain(
    scope: QueryScope, ctx: _Context, memo: dict[int, ScopeGrain]
) -> ScopeGrain:
    if id(scope) in memo:
        return memo[id(scope)]
    text = scope.text
    aliases = table_aliases(text)
    in_scope = set(aliases.values())
    steps = _join_steps(scope, aliases, ctx, memo)
    repeated = _repeated_tables(steps, in_scope)
    select_list = select_list_of(text)
    distinct = _DISTINCT_PREFIX.match(select_list) is not None
    if distinct:
        select_list = _DISTINCT_PREFIX.sub("", select_list, count=1)
    items: list[tuple[str | None, str]] = []
    for item in split_items(select_list):
        name, expression = split_alias(item)
        items.append((name.lower() if name else None, expression))
    reads: dict[str, set[str] | None] = {}
    passthrough: dict[str, tuple[str, str]] = {}
    for name, expression in items:
        if name is None:
            continue
        reads[name] = _tables_read(expression, aliases, ctx.columns_of, in_scope)
        plain = _PLAIN_COLUMN.match(expression)
        if plain is None:
            continue
        qualifier, column = plain.group(1), plain.group(2).lower()
        if qualifier is not None:
            table = aliases.get(qualifier.lower())
            if table is not None:
                passthrough[name] = (table, column)
        else:
            owners = {t for t in in_scope if column in ctx.columns_of.get(t, set())}
            if len(owners) == 1:
                passthrough[name] = (owners.pop(), column)
    if _TOP_LEVEL_UNION.search(text):
        unique_on = None
        reads = {name: {"?"} for name in reads}
    elif distinct:
        named = [name for name, _ in items]
        unique_on = frozenset(named) if all(named) else None
    else:
        unique_on = _group_key(text, items, passthrough, ctx)
    grain = ScopeGrain(
        from_table=from_table_of(text),
        unique_on=unique_on,
        reads=reads,
        passthrough=passthrough,
        repeated=repeated,
        steps=steps,
        has_left_join=_LEFT_JOIN_ON.search(text) is not None,
    )
    memo[id(scope)] = grain
    return grain


def _group_key(
    text: str,
    items: list[tuple[str | None, str]],
    passthrough: dict[str, tuple[str, str]],
    ctx: _Context,
) -> frozenset[str] | None:
    """The output columns a GROUP BY makes the scope unique on: each
    group expression matched to an item by alias, by expression text,
    by ordinal, or by bare column name — any unmatched expression and
    the scope vouches for no key. A key that carries a table's primary
    key beside other columns of the same table (GROUP BY s.id, s.name)
    is that primary key alone."""
    match = _GROUP_BY.search(text)
    if match is None:
        return None
    by_name = {name: index for index, (name, _) in enumerate(items) if name}
    by_text = {_squeeze(expression): name for name, expression in items if name}
    names: list[str] = []
    for expression in split_items(match.group(1)):
        expression = expression.strip()
        if not expression:
            continue
        if expression.lower() in by_name:
            names.append(expression.lower())
        elif _squeeze(expression) in by_text:
            names.append(by_text[_squeeze(expression)])
        elif expression.isdigit() and 1 <= int(expression) <= len(items) and items[int(expression) - 1][0]:
            names.append(items[int(expression) - 1][0])  # type: ignore[arg-type]
        else:
            return None
    keys = frozenset(names)
    for name in keys:
        behind = passthrough.get(name)
        if behind is not None and behind in ctx.pk and all(
            passthrough.get(other, (None, None))[0] == behind[0]
            for other in keys
            if other != name
        ):
            return frozenset({name})
    return keys


def _squeeze(expression: str) -> str:
    return re.sub(r"\s+", "", expression.lower())


def _unique_side(
    table: str,
    column: str,
    on_columns: set[str],
    grain: ScopeGrain | None,
    ctx: _Context,
) -> bool:
    """One row per value of its join columns: a real table on its
    primary key, or a scope whose projection is unique on a subset of
    the columns this ON reads from it."""
    if grain is None:
        return (table, column) in ctx.pk
    return grain.unique_on is not None and grain.unique_on <= on_columns


def _behind(
    table: str, column: str, grain: ScopeGrain | None
) -> tuple[str, str] | None:
    """The real column a side reads: itself for a table, the plain
    column behind a scope's output column, None when the scope's
    column is computed."""
    if grain is None:
        return (table, column)
    return grain.passthrough.get(column)


def _join_steps(
    scope: QueryScope,
    aliases: dict[str, str],
    ctx: _Context,
    memo: dict[int, ScopeGrain],
) -> list[Step]:
    """Every join step in the scope the map or the scope's own text
    does not vouch for: (kind, one, many, tables, text). kind is
    "one_to_many" when exactly one side is one row per join value,
    else "unvouched"; text is the condition with its reason in
    parentheses."""
    steps: list[Step] = []
    for match in _JOIN_ON.finditer(scope.text):
        joined = (match.group(1) or match.group(2)).lower()
        condition = match.group(4)
        regions = _filter_regions(scope, (match.start(4), match.end(4)))
        equalities = [
            (a.lower(), c1.lower(), b.lower(), c2.lower())
            for a, c1, b, c2 in _EQUALITY.findall(condition)
        ]
        on_columns: dict[str, set[str]] = {}
        for a, c1, b, c2 in equalities:
            on_columns.setdefault(a, set()).add(c1)
            on_columns.setdefault(b, set()).add(c2)
        seen: set[tuple[str, str]] = set()
        for a, c1, b, c2 in equalities:
            ta, tb = aliases.get(a, a), aliases.get(b, b)
            grain_a = _grain_of(scope, ta, ctx, memo)
            grain_b = _grain_of(scope, tb, ctx, memo)
            if grain_a is None and grain_b is None:
                step = _table_step(scope, regions, a, c1, b, c2, aliases, ctx)
            else:
                step = _scope_step(
                    ta, c1, grain_a, on_columns[a],
                    tb, c2, grain_b, on_columns[b],
                    ctx, seen,
                )
            if step is not None:
                steps.append(step)
        # A condition with no plain column equality at all that derives
        # its key with an expression (MT2's CONCAT join): no foreign key
        # can vouch for a computed key, and a non-unique derived key
        # fans. Any plain equality above either fired, or vouched the
        # join's grain (AND-ed predicates only filter further) — so this
        # arm is reached only when FK reasoning had nothing to read.
        if not equalities and _derives_a_key(condition):
            snippet = original_fragment(ctx.sql, condition)
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


def _table_step(
    scope: QueryScope,
    regions: list[tuple[int, int]],
    a: str,
    c1: str,
    b: str,
    c2: str,
    aliases: dict[str, str],
    ctx: _Context,
) -> Step | None:
    """An equality between two real tables' columns, classified by the
    dictionary's foreign keys and the map's declarations; None when
    the map vouches for it."""
    ta, tb = aliases.get(a, a), aliases.get(b, b)
    left, right = f"{ta}.{c1}", f"{tb}.{c2}"
    if _one_to_one_step(scope, regions, a, c1, b, c2, aliases, ctx):
        return None
    a_fk = ctx.fk_of.get((ta, c1)) == right
    b_fk = ctx.fk_of.get((tb, c2)) == left
    if a_fk and b_fk:
        return (
            "unvouched", None, None, {ta, tb},
            f"{left} = {right} (each side is a foreign key to the other)",
        )
    if a_fk or b_fk:
        many, one, fk = (ta, tb, c1) if a_fk else (tb, ta, c2)
        return (
            "one_to_many", one, many, {ta, tb},
            f"{left} = {right} ({many}.{fk} is a foreign key — "
            f"several {many} rows can share one {one} row)",
        )
    shared = ctx.fk_of.get((ta, c1))
    if shared and shared == ctx.fk_of.get((tb, c2)):
        # Two rows of the same target's many side (W3's history
        # self-join): one-to-one when each side is vouched one row per
        # target key by its own filter.
        target_table, _, target_column = shared.partition(".")
        target = (target_table, target_column)
        if _vouched_conditionally(
            scope, regions, frozenset({(ta, c1), target}), ((a, ta),), aliases, ctx
        ) and _vouched_conditionally(
            scope, regions, frozenset({(tb, c2), target}), ((b, tb),), aliases, ctx
        ):
            return None
        reason = (
            "both columns are foreign keys with the same target — "
            f"every {ta} row pairs with every {tb} row that shares it"
        )
    else:
        reason = "no foreign key relates these columns"
    return ("unvouched", None, None, {ta, tb}, f"{left} = {right} ({reason})")


def _scope_step(
    ta: str,
    c1: str,
    grain_a: ScopeGrain | None,
    on_a: set[str],
    tb: str,
    c2: str,
    grain_b: ScopeGrain | None,
    on_b: set[str],
    ctx: _Context,
    seen: set[tuple[str, str]],
) -> Step | None:
    """An equality with a CTE or derived table on at least one side.
    A side unique on its join columns — a scope by its projection, a
    table by its primary key — is a one side; both unique and the join
    is one-to-one. Otherwise a scope's column is read through to the
    plain table column behind it (unless the scope's own joins repeat
    that table) and the foreign keys decide, as for two tables. A
    composite join to a unique scope is one step, not one per column."""
    left, right = f"{ta}.{c1}", f"{tb}.{c2}"
    a_unique = _unique_side(ta, c1, on_a, grain_a, ctx)
    b_unique = _unique_side(tb, c2, on_b, grain_b, ctx)
    if a_unique and b_unique:
        return None
    if a_unique or b_unique:
        one, many, key = (ta, tb, on_a) if a_unique else (tb, ta, on_b)
        if (one, many) in seen:
            return None
        seen.add((one, many))
        # Name the real column a scope's many side carries, so the
        # model sees which table it joined through.
        many_column, many_grain = (c2, grain_b) if a_unique else (c1, grain_a)
        behind = _behind(many, many_column, many_grain)
        note, tables = "", {ta, tb}
        if many_grain is not None and behind is not None:
            note = f"; {many}.{many_column} reads {behind[0]}.{behind[1]}"
            tables.add(behind[0])
        return (
            "one_to_many", one, many, tables,
            f"{left} = {right} ({one} is one row per {', '.join(sorted(key))}{note} — "
            f"several {many} rows can share one {one} row)",
        )
    behind_a, behind_b = _behind(ta, c1, grain_a), _behind(tb, c2, grain_b)
    for name, column, grain, behind in (
        (ta, c1, grain_a, behind_a), (tb, c2, grain_b, behind_b)
    ):
        if grain is None:
            continue
        if behind is None:
            return (
                "unvouched", None, None, {ta, tb},
                f"{left} = {right} (nothing vouches for {name}.{column}: {name} is "
                f"not one row per {column}, and the column is computed, not a "
                "table's own)",
            )
        if behind[0] in grain.repeated:
            return (
                "unvouched", None, None, {ta, tb, behind[0]},
                f"{left} = {right} ({name}.{column} reads {behind[0]}.{behind[1]}, "
                f"but the joins inside {name} repeat {behind[0]})",
            )
    assert behind_a is not None and behind_b is not None
    (rta, rc1), (rtb, rc2) = behind_a, behind_b
    read = " and ".join(
        note
        for note, grain in (
            (f"{ta}.{c1} reads {rta}.{rc1}", grain_a),
            (f"{tb}.{c2} reads {rtb}.{rc2}", grain_b),
        )
        if grain is not None
    )
    tables = {ta, tb, rta, rtb}
    a_fk = ctx.fk_of.get((rta, rc1)) == f"{rtb}.{rc2}"
    b_fk = ctx.fk_of.get((rtb, rc2)) == f"{rta}.{rc1}"
    if a_fk and b_fk:
        return (
            "unvouched", None, None, tables,
            f"{left} = {right} ({read} — each side is a foreign key to the other)",
        )
    if a_fk or b_fk:
        many, one = (ta, tb) if a_fk else (tb, ta)
        fk_column = f"{rta}.{rc1}" if a_fk else f"{rtb}.{rc2}"
        return (
            "one_to_many", one, many, tables,
            f"{left} = {right} ({read}; {fk_column} is a foreign key — "
            f"several {many} rows can share one {one} row)",
        )
    shared = ctx.fk_of.get((rta, rc1))
    if shared and shared == ctx.fk_of.get((rtb, rc2)):
        reason = (
            f"{read} — both foreign keys with the same target — "
            f"every {ta} row pairs with every {tb} row that shares it"
        )
    else:
        reason = f"{read} — no foreign key relates these columns"
    return ("unvouched", None, None, tables, f"{left} = {right} ({reason})")


def _repeated_tables(steps: list[Step], in_scope: set[str]) -> dict[str, list[str]]:
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


@dataclass
class _Through:
    """What an aggregate reads when it reads a scope that is not
    deduplicated: the scope's name, the real tables its rows come from,
    the tables the scope's own joins repeat (folded through the scopes
    it reads in turn), those joins' steps, and whether a LEFT JOIN is
    among them."""

    scope_name: str
    tables: set[str]
    repeated: dict[str, list[str]]
    steps: list[Step]
    has_left_join: bool


def _read_through(
    name: str,
    columns: set[str] | None,
    scope: QueryScope,
    ctx: _Context,
    memo: dict[int, ScopeGrain],
) -> _Through | None:
    """The tables an aggregate reads through the named scope — its row
    grain when columns is None, else what its output columns read —
    and the scope's repeated map. None for a real table, and for a
    deduplicated scope, which propagates nothing: its own aggregates
    were linted in its body and its rows are one per key."""
    named = scope.named.get(name)
    if named is None:
        return None
    grain = _scope_grain(named, ctx, memo)
    if grain.unique_on is not None:
        return None
    row_grain = {grain.from_table} if grain.from_table else set()
    sources: set[str] = set()
    if columns is None:
        sources = set(row_grain)
    for column in columns or ():
        if column not in grain.reads:
            sources.add("?")  # SELECT *, or a column the scope never named
        else:
            read = grain.reads[column]
            sources |= read if read else row_grain
    if "?" in sources:
        sources = (sources - {"?"}) | set(grain.repeated)
    result = _Through(name, set(), dict(grain.repeated), list(grain.steps), grain.has_left_join)
    for source in sources:
        deeper = _read_through(source, None, named, ctx, memo)
        if deeper is None:
            result.tables.add(source)
            continue
        result.tables |= deeper.tables
        for table, reasons in deeper.repeated.items():
            known = result.repeated.setdefault(table, [])
            known.extend(reason for reason in reasons if reason not in known)
        result.steps.extend(deeper.steps)
        result.has_left_join |= deeper.has_left_join
    return result


def _scope_columns_read(
    argument: str,
    aliases: dict[str, str],
    scope: QueryScope,
    in_scope: set[str],
    ctx: _Context,
    memo: dict[int, ScopeGrain],
) -> dict[str, set[str]]:
    """scope name -> the output columns of it an aggregate argument
    reads: qualified through the scope's alias, or bare when no real
    table in scope owns the word and exactly one scope names it (W3's
    AVG(time_in_seconds) over a CTE)."""
    text = _DISTINCT_PREFIX.sub("", argument).strip()
    columns: dict[str, set[str]] = {}
    for qualifier, column in _QUALIFIED_REF.findall(text):
        name = aliases.get(qualifier.lower(), qualifier.lower())
        if name in scope.named:
            columns.setdefault(name, set()).add(column.lower())
    bare = _QUALIFIED_REF.sub(" ", text)
    called = {name.lower() for name in _FUNC_CALL.findall(bare)}
    for word in _WORD.findall(bare):
        lowered = word.lower()
        if (
            lowered in _ARGUMENT_KEYWORDS
            or lowered in called
            or lowered in aliases
            or lowered.startswith("__")
            or any(lowered in ctx.columns_of.get(t, set()) for t in in_scope)
        ):
            continue
        owners = [
            name
            for name in in_scope
            if name in scope.named
            and lowered in _scope_grain(scope.named[name], ctx, memo).reads
        ]
        if len(owners) == 1:
            columns.setdefault(owners[0], set()).add(lowered)
    return columns


def lint_fan_out(
    sql: str, dictionary: list[DictionaryRow], dictionary_map: DictionaryMap
) -> str | None:
    """The reason the statement risks a join fan-out (or a LEFT JOIN
    shape that answers the wrong question), or None."""
    ctx = _context(sql, dictionary, dictionary_map)
    memo: dict[int, ScopeGrain] = {}

    fan: list[str] = []
    dead: list[str] = []
    avg_null: list[str] = []
    involved: set[str] = set()
    bandaid_seen = False
    left_join_seen = False
    for scope in scope_tree(sql):
        text = scope.text
        select_list = select_list_of(text)
        bandaid = _DISTINCT_AGG_BANDAID.search(select_list) is not None
        plain_agg = _PLAIN_AGGREGATE.search(select_list) is not None
        any_agg = _ANY_AGGREGATE.search(select_list) is not None
        if not (plain_agg or bandaid or any_agg):
            continue
        aliases = table_aliases(text)

        if plain_agg or bandaid:
            in_scope = set(aliases.values())
            grain = _scope_grain(scope, ctx, memo)
            from_table, steps, repeated = grain.from_table, grain.steps, grain.repeated
            fired_here = False
            for func, argument in _aggregate_arguments(select_list):
                if func == "count" and _DISTINCT_PREFIX.match(argument):
                    continue  # COUNT(DISTINCT x) is the sanctioned repair
                reads = _tables_read(argument, aliases, ctx.columns_of, in_scope)
                row_grain = reads is None
                if reads is None:
                    reads = {from_table} if from_table else set()
                if "?" in reads:
                    reads = (reads - {"?"}) | set(repeated)
                # An aggregate over a scope that is not deduplicated reads
                # what that scope's rows are — the hidden fan (S2 reps
                # 2/4): a LEFT JOIN inside a CTE, COUNT(*) FROM it outside.
                columns = (
                    {}
                    if row_grain
                    else _scope_columns_read(argument, aliases, scope, in_scope, ctx, memo)
                )
                throughs = [
                    through
                    for name in sorted(reads | set(columns))
                    for through in [
                        _read_through(name, columns.get(name), scope, ctx, memo)
                    ]
                    if through is not None
                ]
                hits: list[tuple[str, str | None, str]] = [
                    (table, None, reason)
                    for table in sorted(reads)
                    for reason in repeated.get(table, [])
                ]
                for through in throughs:
                    hits.extend(
                        (table, through.scope_name, reason)
                        for table in sorted(through.tables)
                        for reason in through.repeated.get(table, [])
                    )
                if not hits:
                    continue
                fired_here = True
                shown = original_fragment(sql, f"{func}({argument})")
                for table, via, reason in hits:
                    entry = (
                        f"{shown} reads {table} {reason}"
                        if via is None
                        else f"{shown} reads {via}, whose rows are {table} {reason}"
                    )
                    if entry not in fan:
                        fan.append(entry)
                hit_tables = {table for table, _, _ in hits}
                touched = list(steps)
                left_join_seen = left_join_seen or grain.has_left_join
                for through in throughs:
                    if through.tables & hit_tables:
                        touched.extend(through.steps)
                        left_join_seen = left_join_seen or through.has_left_join
                involved.update(
                    t for kind, _, _, tables, _ in touched for t in tables
                    if kind == "unvouched" or tables & hit_tables
                )
            if bandaid and fired_here:
                bandaid_seen = True

        for match in _LEFT_JOIN_ON.finditer(text):
            sub_alias, table, alias, condition = match.groups()
            regions = _filter_regions(scope, (match.start(4), match.end(4)))
            if any(
                _one_to_one_step(scope, regions, a, c1, b, c2, aliases, ctx)
                for a, c1, b, c2 in _EQUALITY.findall(condition)
            ):
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
            remainder = text[: match.start()] + text[match.end() :]
            used_qualified = re.search(
                rf"\b(?:{'|'.join(qualifiers)})\s*\.", remainder, re.IGNORECASE
            )
            bare_columns = ctx.columns_of.get(table.lower(), set())
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
        # The recommended shapes must be shapes every guard can read
        # (the challenge principle's corollary): a scope de-duplicated
        # on its key, COUNT(DISTINCT), and — when a LEFT JOIN is in
        # play — an EXISTS test, which hoists to a scope nothing joins.
        exists_note = (
            ", or test whether a row has a match with EXISTS rather than a "
            "LEFT JOIN"
            if left_join_seen
            else ""
        )
        parts.append(
            f"Fan-out check: {listed} — the aggregate adds join "
            "combinations, not entities. Aggregate each side in its own "
            "scope, then join the results; count an entity with "
            f"COUNT(DISTINCT <table>.id){exists_note}.{bandaid_note}{hint} "
            "If this join cannot multiply rows, resend the statement "
            "unchanged."
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

