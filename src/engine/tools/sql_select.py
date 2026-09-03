"""Select-list resolution: which source column a result column was
computed from, read from the statement itself.

One parse, two views (the coverage pass's resolver unification):

- resolve_select_columns — the Verifier's contract, unchanged from the
  module this replaces (verifier/checks/sql_columns.py): the OUTER
  scope only, real tables only, exactly the shapes whose stats bounds
  are sound — SUM(col), AVG(col), SUM(COALESCE(col, <literal>)), and a
  plain col [AS alias]. A CTE or derived-table qualifier surfaces as
  the scope's name, which no stats table matches: the documented
  unchecked case. COALESCE is accepted for SUM only — COALESCE(col, 0)
  does not change a sum (the play pass's W1 wrote exactly this shape)
  — and rejected for AVG, where substituting a literal changes the
  population and the [min, max] bound no longer applies.

- resolve_select_items — the display resolver's view (Play Session #2,
  S-B): every select item as a small expression tree over columns,
  aggregates, literals and arithmetic, FOLLOWING CTEs and derived
  tables so a column read out of a WITH body resolves to the real
  column behind it (the session's `original_cost` was
  `l.extended_price` inside a CTE). Arithmetic over money is money;
  CASE, window functions and unknown functions are Opaque and leave
  the alias to be judged by its spelling. Known numeric-valued
  functions — EPOCH, DATE_DIFF, JULIAN and their spellings — parse as
  Numeric with their arguments visible (the guard pass), so the
  aggregate above the gotcha's own recommended shape,
  AVG(EPOCH(a - b)) / 3600, is read structurally rather than lexically.
  EXTRACT(x FROM y) stays outside the parse: its inner FROM ends the
  select-list scan early (recorded, not built).

Both views come from the same tokenizer and the same tree, so on any
alias both act on they name the same source column. Regex- and
hand-parser-level on purpose (the house precedent, sql_lint.py): no
parser dependency the work machine cannot install. Tools never import
the verifier; the verifier imports this module.

Pure code: no ports, no I/O.
"""

import re
from dataclasses import dataclass, field
from typing import Literal, Union

from engine.tools.sql_lint import (
    select_list_of,
    split_scopes,
    table_aliases,
    unquote_identifiers,
)

# --- The Verifier's view ------------------------------------------------

_AGG_BARE = re.compile(
    r"^\s*(sum|avg)\s*\(\s*(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)\s*\)"
    r"\s+as\s+([A-Za-z_]\w*)\s*$",
    re.IGNORECASE,
)
_SUM_COALESCE = re.compile(
    r"^\s*(sum)\s*\(\s*coalesce\s*\(\s*(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)"
    r"\s*,[^()]*\)\s*\)\s+as\s+([A-Za-z_]\w*)\s*$",
    re.IGNORECASE,
)
_PLAIN = re.compile(
    r"^\s*(?:([A-Za-z_]\w*)\.)?([A-Za-z_]\w*)"
    r"(?:\s+as\s+([A-Za-z_]\w*))?\s*$",
    re.IGNORECASE,
)
_NON_COLUMN = {"distinct", "null", "true", "false"}


@dataclass
class ResolvedColumn:
    """One result column traced to its source column. table is the
    real table name when the reference was qualified, or None when
    the column must be found among the queried tables' stats."""

    alias: str
    table: str | None
    column: str
    aggregate: Literal["sum", "avg"] | None


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


def resolve_select_columns(sql: str) -> dict[str, ResolvedColumn]:
    """Result-column alias -> ResolvedColumn for every select item the
    grammar above can trace. Aliases keep their spelling; table and
    column names are lowercased like the stats lookups downstream."""
    scopes = split_scopes(sql)
    if not scopes:
        return {}
    outer = scopes[-1]
    aliases = table_aliases(outer)
    resolved: dict[str, ResolvedColumn] = {}
    for item in split_items(select_list_of(outer)):
        match = _AGG_BARE.match(item) or _SUM_COALESCE.match(item)
        if match:
            func, qualifier, column, alias = match.groups()
            aggregate: Literal["sum", "avg"] | None = func.lower()  # type: ignore[assignment]
        else:
            plain = _PLAIN.match(item)
            if plain is None:
                continue
            qualifier, column, alias = plain.groups()
            if column.lower() in _NON_COLUMN or (
                alias and alias.lower() in _NON_COLUMN
            ):
                continue
            alias = alias or column
            aggregate = None
        table = aliases.get(qualifier.lower()) if qualifier else None
        if qualifier and table is None:
            continue  # a qualifier the FROM clause never introduced
        resolved.setdefault(
            alias,
            ResolvedColumn(
                alias=alias,
                table=table,
                column=column.lower(),
                aggregate=aggregate,
            ),
        )
    return resolved


# --- The display resolver's view -----------------------------------------


@dataclass(frozen=True)
class Column:
    """A real table's column. table is None when the statement never
    said which of its tables the column belongs to."""

    table: str | None
    column: str


@dataclass(frozen=True)
class Aggregate:
    func: str  # sum | avg | min | max | count
    arg: "Expr | None"  # None for COUNT(*)
    # COUNT(DISTINCT x): the Verifier's count checks compare a distinct
    # count against distinct_count and a plain one against row_count
    # (Polish Pass — the classification used to be a regex).
    distinct: bool = False


@dataclass(frozen=True)
class Number:
    """A numeric literal — dimensionless."""


@dataclass(frozen=True)
class Arith:
    op: str  # + - * /
    left: "Expr"
    right: "Expr"


@dataclass(frozen=True)
class Numeric:
    """A known numeric-valued function over its arguments: EPOCH of an
    interval is seconds, DATE_DIFF('unit', a, b) is units, JULIAN(ts)
    is days. The value is a number — no interval for the interval lint
    to see scaled, no money for the display resolver — and the
    arguments stay visible, so an aggregate above the call (the
    duration ceiling's SUM exemption and AVG refusal) or below it
    (EPOCH(MAX(a) - MIN(b)) is an aggregate's worth of seconds) is read
    from the tree. An argument the grammar cannot read, such as the
    blanked 'hour' literal, is Opaque in place."""

    func: str
    args: tuple["Expr", ...]


@dataclass(frozen=True)
class Opaque:
    """CASE, a window function, a subquery, a string, an unknown
    function: the parse declines to guess. `text` keeps the item's
    source for a consumer that falls back to a lexical read (the
    Verifier's degenerate-duration warn looks for an aggregate name in
    a CASE-wrapped duration, the duration pass); it never takes part
    in equality — an Opaque is an Opaque."""

    text: str = field(default="", compare=False)


Expr = Union[Column, Aggregate, Number, Arith, Numeric, Opaque]

# Functions whose value has the shape of their first argument.
_TRANSPARENT = {"coalesce", "round", "abs", "cast", "ifnull", "nullif"}
_AGGREGATES = {"sum", "avg", "min", "max", "count"}
# Functions whose value is a number whatever their arguments are — the
# unit shapes the time_in_status gotcha recommends, in DuckDB's and
# SQLite's spellings.
_NUMERIC_FUNCTIONS = {
    "epoch", "epoch_ms", "date_diff", "datediff", "date_part", "datepart",
    "julian", "julianday",
}
# AGE(a, b) is a - b: an INTERVAL over two timestamps, so the interval
# lint sees it as the subtraction it is. One-argument AGE (against the
# wall clock) stays Opaque.
_INTERVAL_DIFFERENCE = {"age"}

_TOKEN = re.compile(
    r"\s*(?:"
    r"(?P<string>'(?:[^']|'')*')"
    r"|(?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+)"
    r"|(?P<name>[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)"
    r"|(?P<op>\|\||[-+*/(),])"
    r"|(?P<other>\S)"
    r")"
)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_WITH = re.compile(r"^\s*with\s+(?:recursive\s+)?", re.IGNORECASE)
_CTE_HEAD = re.compile(r"\s*([A-Za-z_]\w*)\s*(?:\([^()]*\)\s*)?as\s*\(", re.IGNORECASE)
_FROM_OR_JOIN_PAREN = re.compile(r"\b(from|join)\s*\(", re.IGNORECASE)
_ALIAS_AFTER = re.compile(r"\s*(?:as\s+)?([A-Za-z_]\w*)", re.IGNORECASE)
_AS_ALIAS = re.compile(r"\s+as\s+([A-Za-z_]\w*)\s*$", re.IGNORECASE)
_SELECT_LIST = re.compile(r"\bselect\b(.*?)\bfrom\b", re.IGNORECASE | re.DOTALL)
_KEYWORDS_NOT_ALIAS = {
    "on", "where", "join", "left", "right", "inner", "outer", "full",
    "cross", "group", "order", "limit", "having", "union", "qualify",
    "using", "natural", "lateral",
}


def _matching_paren(text: str, open_index: int) -> int:
    depth = 0
    for index in range(open_index, len(text)):
        if text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


@dataclass
class _Scope:
    """One parsed SELECT: its items by alias and its sources by alias
    (a real table name, or a nested _Scope for a CTE / derived table)."""

    items: dict[str, Expr]
    sources: dict[str, "str | _Scope"]


def _parse_query(text: str, ctes: dict[str, _Scope]) -> _Scope:
    """Parse one query text (a whole statement, a CTE body, or a derived
    table's body) into a _Scope, recursing into its own WITH clause and
    derived tables. Text arrives with string literals blanked."""
    ctes = dict(ctes)
    match = _WITH.match(text)
    if match:
        cursor = match.end()
        while True:
            head = _CTE_HEAD.match(text, cursor)
            if head is None:
                break
            open_index = head.end() - 1
            close_index = _matching_paren(text, open_index)
            if close_index < 0:
                break
            ctes[head.group(1).lower()] = _parse_query(
                text[open_index + 1 : close_index], ctes
            )
            cursor = close_index + 1
            comma = re.match(r"\s*,", text[cursor:])
            if comma is None:
                break
            cursor += comma.end()
        text = text[cursor:]

    # Derived tables: hoist each (SELECT ...) after FROM/JOIN into a
    # nested scope and replace it with a marker the alias scan can read.
    sources: dict[str, str | _Scope] = {}
    body = text
    while True:
        found = _FROM_OR_JOIN_PAREN.search(body)
        if found is None:
            break
        open_index = found.end() - 1
        close_index = _matching_paren(body, open_index)
        if close_index < 0:
            break
        inner = body[open_index + 1 : close_index]
        alias_match = _ALIAS_AFTER.match(body, close_index + 1)
        alias = (
            alias_match.group(1).lower()
            if alias_match and alias_match.group(1).lower() not in _KEYWORDS_NOT_ALIAS
            else None
        )
        if alias:
            sources[alias] = _parse_query(inner, ctes)
        marker = f" __derived_{len(sources)}__ "
        body = body[: found.start()] + found.group(1) + marker + body[close_index + 1 :]

    for alias, table in table_aliases(_blank_subqueries(body)).items():
        if alias in sources:
            continue
        if table.startswith("__derived_"):
            continue
        sources[alias] = ctes.get(table, table)

    items: dict[str, Expr] = {}
    # Scan the select list with subqueries blanked, so a scalar
    # subquery's own FROM never ends the list early; the placeholder
    # parses as Opaque.
    select_match = _SELECT_LIST.search(_blank_subqueries(body))
    if select_match is None:
        return _Scope(items=items, sources=sources)
    for item in split_items(select_match.group(1)):
        alias, expr_text = _split_alias(item)
        if alias is None:
            continue
        items[alias] = _parse_expr(expr_text, sources)
    return _Scope(items=items, sources=sources)


def _blank_subqueries(text: str) -> str:
    """Replace parenthesised subqueries with a placeholder so the alias
    scan never reads a FROM inside a scalar subquery as a source."""
    out: list[str] = []
    index = 0
    while index < len(text):
        if text[index] == "(":
            close_index = _matching_paren(text, index)
            if close_index < 0:
                out.append(text[index:])
                break
            inner = text[index + 1 : close_index]
            if re.match(r"\s*(select|with)\b", inner, re.IGNORECASE):
                out.append("(__subquery__)")
            else:
                out.append("(" + _blank_subqueries(inner) + ")")
            index = close_index + 1
        else:
            out.append(text[index])
            index += 1
    return "".join(out)


def _split_alias(item: str) -> tuple[str | None, str]:
    """(alias, expression text) for one select item: `expr AS alias`,
    or a bare column whose alias is its own name."""
    match = _AS_ALIAS.search(item)
    if match:
        return match.group(1), item[: match.start()]
    plain = _PLAIN.match(item)
    if plain and plain.group(3) is None:
        column = plain.group(2)
        if column.lower() in _NON_COLUMN:
            return None, item
        return column, item
    return None, item


class _Parser:
    def __init__(self, text: str, sources: dict[str, str | _Scope]) -> None:
        self.tokens = [
            (kind, value)
            for kind, value in _tokens(text)
        ]
        self.pos = 0
        self.sources = sources
        self.text = text.strip()

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.tokens[self.pos]
        self.pos += 1
        return token

    def parse(self) -> Expr:
        try:
            expr = self.expr()
        except _Bail:
            return Opaque(self.text)
        return expr if self.peek() is None else Opaque(self.text)

    def expr(self) -> Expr:
        left = self.term()
        while (token := self.peek()) and token == ("op", "+") or token == ("op", "-"):
            self.take()
            left = Arith(token[1], left, self.term())
        return left

    def term(self) -> Expr:
        left = self.factor()
        while (token := self.peek()) and token[0] == "op" and token[1] in "*/":
            self.take()
            left = Arith(token[1], left, self.factor())
        return left

    def factor(self) -> Expr:
        token = self.peek()
        if token is None:
            raise _Bail
        kind, value = token
        if kind == "op" and value == "-":
            self.take()
            return self.factor()
        if kind == "op" and value == "(":
            self.take()
            inner = self.expr()
            self.expect(")")
            return inner
        if kind == "number":
            self.take()
            return Number()
        if kind == "name":
            self.take()
            lowered = value.lower()
            if self.peek() == ("op", "("):
                return self.call(lowered)
            if lowered in ("null", "true", "false", "case", "distinct", "select"):
                raise _Bail
            if lowered.startswith("__"):
                raise _Bail  # a hoisted subquery: the parse declines to guess
            return self.column(value)
        raise _Bail

    def call(self, name: str) -> Expr:
        self.expect("(")
        if name in _AGGREGATES:
            if self.peek() == ("op", "*"):
                self.take()
                self.expect(")")
                return Aggregate(name, None)
            distinct = False
            if self.peek() == ("name", "DISTINCT") or self.peek() == ("name", "distinct"):
                if name != "count":
                    raise _Bail  # SUM(DISTINCT x) is a challenged band-aid, not a shape
                self.take()
                distinct = True
            arg = self.expr()
            self.expect(")")
            return Aggregate(name, arg, distinct)
        if name in _INTERVAL_DIFFERENCE:
            left = self.expr()
            self.expect(",")
            right = self.expr()
            self.expect(")")
            return Arith("-", left, right)
        if name in _NUMERIC_FUNCTIONS:
            args: list[Expr] = []
            while self.peek() != ("op", ")"):
                args.append(self.argument())
                if self.peek() == ("op", ","):
                    self.take()
                    continue
                break
            self.expect(")")
            return Numeric(name, tuple(args))
        if name in _TRANSPARENT:
            first = self.expr()
            depth = 0
            while (token := self.peek()) is not None and not (
                depth == 0 and token == ("op", ")")
            ):
                if token == ("op", "("):
                    depth += 1
                elif token == ("op", ")"):
                    depth -= 1
                self.take()
            self.expect(")")
            return first
        raise _Bail

    def argument(self) -> Expr:
        """One argument of a numeric function: an expression, or Opaque
        when the grammar cannot read it (a blanked string literal, a
        keyword) — skipped to the next top-level comma or closing paren
        so the call's other arguments still parse."""
        start = self.pos
        try:
            expr = self.expr()
        except _Bail:
            self.pos = start
        else:
            if self.peek() in (("op", ","), ("op", ")")):
                return expr
            self.pos = start
        depth = 0
        while (token := self.peek()) is not None:
            if token == ("op", "("):
                depth += 1
            elif token == ("op", ")"):
                if depth == 0:
                    break
                depth -= 1
            elif token == ("op", ",") and depth == 0:
                break
            self.take()
        return Opaque()

    def column(self, name: str) -> Expr:
        qualifier, _, column = name.rpartition(".")
        column = column.lower()
        if qualifier:
            source = self.sources.get(qualifier.lower())
            if source is None:
                raise _Bail
            if isinstance(source, _Scope):
                inner = source.items.get(column)
                if inner is None:
                    inner = next(
                        (e for a, e in source.items.items() if a.lower() == column),
                        None,
                    )
                return inner if inner is not None else Opaque()
            return Column(source, column)
        # Unqualified: a nested scope that defines the alias wins; else
        # the column is one of the real tables' and the table is unknown.
        nested = [
            s.items[column]
            for s in self.sources.values()
            if isinstance(s, _Scope) and column in s.items
        ]
        if len(nested) == 1:
            return nested[0]
        return Column(None, column)

    def expect(self, op: str) -> None:
        if self.peek() != ("op", op):
            raise _Bail
        self.take()


class _Bail(Exception):
    pass


def _tokens(text: str):
    pos = 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if match is None or match.end() == pos:
            break
        pos = match.end()
        kind = match.lastgroup
        if kind is None:
            continue
        yield kind, match.group(kind)


def _parse_expr(text: str, sources: dict[str, str | _Scope]) -> Expr:
    return _Parser(text, sources).parse()


def resolve_select_items(sql: str) -> dict[str, Expr]:
    """Result-column alias -> expression tree for the statement's outer
    select list, with CTE and derived-table columns resolved through
    their own select items. Aliases keep their spelling."""
    cleaned = _LINE_COMMENT.sub("", unquote_identifiers(sql))
    cleaned = re.sub(r"'(?:[^']|'')*'", "''", cleaned)
    return _parse_query(cleaned, {}).items


def source_column(expr: Expr) -> Column | None:
    """The single real column an expression is a plain reading or a
    plain aggregate of — the display layer's 'inherits the source
    column's format' case. None for arithmetic, literals and Opaque."""
    if isinstance(expr, Column):
        return expr
    if isinstance(expr, Aggregate) and expr.arg is not None:
        return source_column(expr.arg)
    return None
