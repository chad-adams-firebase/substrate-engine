"""run_sql check: the richest harvest and the plausibility suite
(§9.3) — result values sanity-checked against Univariate Stats, plus
the zero challenge.

Known limitations, documented rather than hidden:
- Table extraction is FROM/JOIN tokenization; result columns map to
  stats columns by name equality, plus whatever the select-list parse
  (tools/sql_select.py) can trace — SUM(col), SUM(COALESCE(col, _)),
  AVG(col), and plain col AS alias. Anything more complex (CASE,
  arithmetic, CTE references) escapes the bounds checks. Thresholds
  are pack config precisely because this gets tuned at work against
  real distributions.
- The aggregate guard is a coarse token scan; an aggregate the scan
  misses could trigger a spurious min/max finding (visible, not
  silent).
- THE PLAY PASS (aggregate-vs-stats, §9.3 for tables): the first
  free-play session shipped 8 wrong-but-verified answers, none of
  which any check here inspected — every registered check was shaped
  for scalar/count answers, so 0 of the 20 table answers faced
  plausibility. Now: a SUM column resolvable to stats must not exceed
  mean × non-null count (cells and the column total — the total is
  what catches a fanned join whose per-group sums individually sit
  under the cap, W1's shape); an AVG column's cells must lie in
  [min, max]; both band into warn (within tolerance) / fail (beyond).
  A COUNT(DISTINCT col) compares against the column's distinct_count,
  never row_count (R4's false refusal). And a fan-out challenge the
  model overrode — the executed statement still trips the lint — is
  read from the evidence attempts and caps the answer at unverified.
- THE ZERO CHALLENGE (fix pass 3, from the 4b baseline): a query that
  answers the wrong question most often returns nothing — S4 counted
  0 where the truth was 114 (two inverted predicates), S7 returned an
  empty table where the truth was one reactivation (a filter on the
  current status excluded exactly the population asked about). Both
  verified: faithfulness held, the evidence was wrong, and nothing
  here could object — an empty result carries no cells, and 0 sits
  under every bound. So an empty result, or a single scalar cell of
  0/NULL/false, now draws a plausibility WARN: the answer ships
  capped at [UNVERIFIED], never verified, for prose and table
  pass-through alike. Accepted cost: a legitimately-zero answer also
  arrives unverified — a true zero deserves the second look — and one
  zero-result invocation among several caps the whole turn. Honest
  negatives with count receipts live in check_execution, which this
  does not touch. What remains uncaught is a wrong-but-nonzero
  result; the fan-out lint and canonical-metric grounding in run_sql
  address the shapes the baseline found (MT2, U5, C4).
- THE DURATION PASS (every display-hint kind carries a plausibility
  bound): money had sum caps, rates had bounds and saturation,
  durations had a humanizer and nothing else — and the post-coverage
  W3 rep 4 shipped a verified "0 seconds" for a one-hour gap
  (AVG(interval) / 86400 is an interval of 0.041667 seconds). Now a
  duration-hinted aggregate below one second warns (a floor; a
  same-row count under the basis suppresses it, as with rates), and a
  duration longer than the queried tables' timestamp span fails (a
  ceiling; a SUM is exempt, an item the parse cannot classify warns).
  The interval-arithmetic lint's overridden challenge is read from the
  evidence attempts like the other two and caps the answer at
  unverified.
- THE GUARD PASS (the entity-count bound): the post-duration AMB2
  shipped `COUNT(*) AS invoice_count FROM invoice_history WHERE
  to_status IN (...)` = 6,432, verified, against 1,990 invoices in
  existence — a filtered single-table count under invoice_history's
  own row_count passes every check above, and nothing tied the alias's
  noun to a table. Now a COUNT column whose alias names an entity the
  stats substrate knows as a table (a stem rule over the alias: strip
  a count affix, match singular/plural against the table names) warns
  when it exceeds that table's row_count. Warn, not fail: an alias is a
  naming convention, not a type. Silent when the alias makes no entity
  claim or the noun matches no table.
"""

import re
from collections.abc import Iterable
from datetime import date, datetime, timedelta

from engine.config.models import ToolName
from engine.substrates.models import StatsRow
from engine.tools.durations import duration_seconds, is_timestamp_type
from engine.tools.envelope import (
    ColumnFormat,
    RunSqlEvidence,
    RunSqlOutput,
    ToolInvocation,
)
from engine.tools.interval_lint import AGGREGATE_WORD
from engine.tools.sql_lint import unquote_identifiers
from engine.verifier.checks.base import (
    PlausibilityContext,
    SubstrateCheck,
    identifier_tokens,
)
from engine.tools.sql_select import (
    Aggregate,
    Arith,
    Expr,
    Numeric,
    Opaque,
    ResolvedColumn,
    resolve_select_columns,
    resolve_select_items,
)
from engine.verifier.models import (
    CorpusText,
    EvidenceContribution,
    EvidenceValue,
    PlausibilityFinding,
)

_FROM_JOIN = re.compile(r"\b(?:from|join)\s+([A-Za-z_]\w*)", re.IGNORECASE)
_COUNT_ONLY = re.compile(r"select\s+count\s*\(\s*(?!distinct\b)", re.IGNORECASE)
_COUNT_DISTINCT = re.compile(
    r"select\s+count\s*\(\s*distinct\s+(?:[A-Za-z_]\w*\.)?([A-Za-z_]\w*)\s*\)",
    re.IGNORECASE,
)
_WHERE = re.compile(r"\bwhere\b", re.IGNORECASE)
# A non-DISTINCT count aliased in the select list — the grouped shape
# of the joined-count bound (COUNT(DISTINCT ...) stays with the
# distinct_vs_stats guard: a distinct count cannot fan).
_COUNT_ALIAS = re.compile(
    r"\bcount\s*\(\s*(?!distinct\b)[^()]*\)\s+as\s+([A-Za-z_]\w*)",
    re.IGNORECASE,
)
_AGGREGATE = re.compile(r"\b(?:sum|avg|count|min|max)\s*\(", re.IGNORECASE)
_ISO_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")
# The count affixes an alias may carry around its entity noun
# (invoice_count, total_invoices, n_suppliers). No affix, no claim.
_COUNT_SUFFIXES = ("_count", "_total")
_COUNT_PREFIXES = ("number_of_", "count_of_", "num_", "n_", "total_")


def _noun_forms(noun: str) -> set[str]:
    """The spellings a table name may take for one noun: as written,
    pluralised, singularised. Enough for the English plurals a schema
    uses (invoices, findings, suppliers, categories); an irregular
    noun simply fails to resolve and stays silent."""
    forms = {noun, noun + "s", noun + "es"}
    if noun.endswith("s"):
        forms.add(noun[:-1])
    if noun.endswith("y"):
        forms.add(noun[:-1] + "ies")
    if noun.endswith("ies"):
        forms.add(noun[:-3] + "y")
    return forms


def entity_table_for_alias(alias: str, tables: Iterable[str]) -> str | None:
    """The stats table a count alias names, or None. `invoice_count`,
    `total_invoices`, `n_suppliers` name invoices/invoices/suppliers;
    `critical_finding_count` falls back to its last segment and names
    findings; `ready_backlog_count` (no such table), `rules_seen` (no
    count affix) and a noun two tables could spell name nothing. The
    rule reads the stats substrate's table names at run time — no
    table name lives in engine code."""
    known = {table.lower() for table in tables}
    name = alias.lower()
    noun = name
    for suffix in _COUNT_SUFFIXES:
        if noun.endswith(suffix) and len(noun) > len(suffix):
            noun = noun[: -len(suffix)]
            break
    for prefix in _COUNT_PREFIXES:
        if noun.startswith(prefix) and len(noun) > len(prefix):
            noun = noun[len(prefix):]
            break
    if noun == name:
        return None
    for candidate in dict.fromkeys((noun, noun.rsplit("_", 1)[-1])):
        hits = {form for form in _noun_forms(candidate) if form in known}
        if len(hits) == 1:
            return hits.pop()
        if hits:
            return None  # two tables could be meant: no claim to check
    return None


def _fmt(value: float) -> str:
    """Thousands-separated, never scientific: findings are read by
    humans deciding whether to trust an answer."""
    return format(value, ",.10g")


def _as_float(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _as_date(text: str | None) -> date | None:
    if not text:
        return None
    match = _ISO_PREFIX.match(text.strip())
    if match is None:
        return None
    try:
        return date.fromisoformat(match.group(0))
    except ValueError:
        return None


def _as_datetime(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.strip())
    except ValueError:
        return None


def _timestamp_span_seconds(
    queried: list[str], stats_by_table: dict[str, list[StatsRow]]
) -> float | None:
    """Seconds between the earliest and latest timestamp the stats
    substrate records across the queried tables' timestamp columns —
    the longest elapsed time the data can contain. None when no
    queried table has a timestamp column with both bounds, or when
    the span is not positive (a single-moment table bounds nothing)."""
    lows: list[datetime] = []
    highs: list[datetime] = []
    for table_name in queried:
        for row in stats_by_table.get(table_name, []):
            if not is_timestamp_type(row.data_type):
                continue
            low = _as_datetime(row.min_value)
            high = _as_datetime(row.max_value)
            if low is not None:
                lows.append(low)
            if high is not None:
                highs.append(high)
    if not lows or not highs:
        return None
    span = (max(highs) - min(lows)).total_seconds()
    return span if span > 0 else None


def _aggregate_names(expr: Expr | None) -> set[str]:
    """The aggregate functions an item's tree applies. A Numeric call
    (EPOCH, DATE_DIFF, JULIAN — the guard pass) is read through to its
    arguments. An Opaque item (a form the parse declines, such as a
    CASE wrapper) is read lexically from its source text — for the
    degenerate-duration warn only, where a false positive costs a badge
    and a false negative is a verified zero."""
    if expr is None:
        return set()
    if isinstance(expr, Opaque):
        return {name.lower() for name in AGGREGATE_WORD.findall(expr.text)}
    if isinstance(expr, Aggregate):
        names = {expr.func}
        if expr.arg is not None:
            names |= _aggregate_names(expr.arg)
        return names
    if isinstance(expr, Arith):
        return _aggregate_names(expr.left) | _aggregate_names(expr.right)
    if isinstance(expr, Numeric):
        names: set[str] = set()
        for arg in expr.args:
            names |= _aggregate_names(arg)
        return names
    return set()


def _zero_findings(table) -> list[PlausibilityFinding]:
    """An empty result set, or a lone scalar of 0/NULL/false, is the
    shape a wrong-question query most often takes. Warn: the ladder
    caps a warn at unverified (never refuses — a true zero is a
    legitimate answer that merely needs a second look)."""
    if table.total_row_count == 0 or not table.rows:
        return [
            PlausibilityFinding(
                check="run_sql.empty_result",
                severity="warn",
                detail=(
                    "the query returned no rows — a wrong-question query "
                    "most often answers 'nothing'; treat as unverified "
                    "until the predicates are checked"
                ),
            )
        ]
    if len(table.rows) == 1 and len(table.columns) == 1:
        cell = table.rows[0].get(table.columns[0])
        zero_like = cell is None or cell is False or (
            isinstance(cell, (int, float))
            and not isinstance(cell, bool)
            and float(cell) == 0.0
        )
        if zero_like:
            return [
                PlausibilityFinding(
                    check="run_sql.zero_scalar",
                    severity="warn",
                    detail=(
                        f"the single result value is {cell!r} — a zero "
                        "answer is unverified until the predicates are "
                        "checked"
                    ),
                )
            ]
    return []


class RunSqlCheck(SubstrateCheck):
    tool = ToolName.RUN_SQL

    def harvest(
        self, invocation: ToolInvocation, ref: str
    ) -> EvidenceContribution:
        output = invocation.output
        assert isinstance(output, RunSqlOutput)
        table = output.table
        contribution = EvidenceContribution()

        sums: dict[str, float] = {}
        for row_index, row in enumerate(table.rows):
            for column, cell in row.items():
                if isinstance(cell, bool):
                    continue
                if isinstance(cell, (int, float)):
                    contribution.numbers.append(
                        EvidenceValue(
                            value=float(cell),
                            ref=f"{ref}.table.rows[{row_index}].{column}",
                            salience="cell",
                            group=f"{ref}.rows[{row_index}]",
                        )
                    )
                    sums[column] = sums.get(column, 0.0) + float(cell)
                elif isinstance(cell, str):
                    contribution.strings.add(cell)

        if len(table.rows) > 1:
            for column, total in sums.items():
                contribution.numbers.append(
                    EvidenceValue(
                        value=total,
                        ref=f"{ref}.sum({column})",
                        salience="cell",
                    )
                )

        contribution.numbers.append(
            EvidenceValue(
                value=float(table.total_row_count),
                ref=f"{ref}.table.total_row_count",
                salience="count",
            )
        )
        shown_ref = f"{ref}.len(table.rows)"
        if table.truncated:
            shown_ref += " (truncated view)"
        contribution.numbers.append(
            EvidenceValue(
                value=float(len(table.rows)), ref=shown_ref, salience="count"
            )
        )

        contribution.vocabulary |= identifier_tokens(output.sql)
        contribution.vocabulary |= set(table.columns)
        contribution.quote_corpus.append(
            CorpusText(text=output.sql, ref=f"{ref}.sql")
        )
        return contribution

    def plausibility(
        self, invocation: ToolInvocation, ctx: PlausibilityContext
    ) -> list[PlausibilityFinding]:
        output = invocation.output
        assert isinstance(output, RunSqlOutput)
        settings = ctx.settings
        table = output.table
        # Read with identifier quotes stripped (guard pass): every bound
        # below is a bare-name regex or parse, and a quoted statement
        # used to pass all of them. The executed SQL itself is untouched.
        sql = unquote_identifiers(output.sql)
        queried = list(dict.fromkeys(t.lower() for t in _FROM_JOIN.findall(sql)))
        stats_by_table: dict[str, list[StatsRow]] = {}
        for row in ctx.stats:
            stats_by_table.setdefault(row.table_name.lower(), []).append(row)

        findings: list[PlausibilityFinding] = []
        tolerance = settings.row_count_tolerance_pct / 100.0

        # 0: the zero challenge — see the module docstring.
        if settings.challenge_zero_results:
            findings.extend(_zero_findings(table))

        # 0b: a fan-out challenge the model overrode — the executed
        # statement still trips the lint (play pass, W1/W7). Warn:
        # the ladder caps a warn at unverified, so the override costs
        # the badge and leaves a trace, but a join the lint merely
        # cannot vouch for does not refuse a correct answer.
        evidence = invocation.evidence
        if isinstance(evidence, RunSqlEvidence) and evidence.attempts:
            final = evidence.attempts[-1]
            if final.lint is not None and final.error is None:
                findings.append(
                    PlausibilityFinding(
                        check="run_sql.fan_out_override",
                        severity="warn",
                        detail=(
                            "the executed statement still trips the "
                            f"fan-out check it was challenged with: {final.lint}"
                        ),
                    )
                )
            # 0c: an enum-literal challenge the model overrode (coverage
            # pass, R-A): the statement filters on a value its column
            # never holds, so an empty result here is the expected
            # wrong answer — it ships [UNVERIFIED] for this stated
            # reason, not merely because it is empty.
            if final.enum_lint is not None and final.error is None:
                findings.append(
                    PlausibilityFinding(
                        check="run_sql.enum_literal_override",
                        severity="warn",
                        detail=(
                            "the executed statement still filters on a value "
                            "its column never holds: "
                            f"{final.enum_lint}"
                        ),
                    )
                )
            # 0d: an interval-arithmetic challenge the model overrode
            # (duration pass, W3 rep 4): the executed statement still
            # scales a timestamp difference by a literal, so its
            # duration cells are off by a unit factor — [UNVERIFIED]
            # for this stated reason.
            if final.interval_lint is not None and final.error is None:
                findings.append(
                    PlausibilityFinding(
                        check="run_sql.interval_arithmetic_override",
                        severity="warn",
                        detail=(
                            "the executed statement still scales an interval "
                            "it was challenged on: "
                            f"{final.interval_lint}"
                        ),
                    )
                )

        is_single_cell = (
            len(queried) == 1
            and len(table.rows) == 1
            and len(table.columns) == 1
        )

        # 1 & 2: COUNT sanity against known table sizes.
        is_single_count = _COUNT_ONLY.search(sql) and is_single_cell
        if is_single_count and queried[0] in stats_by_table:
            cell = table.rows[0][table.columns[0]]
            known = float(stats_by_table[queried[0]][0].row_count)
            if isinstance(cell, (int, float)) and not isinstance(cell, bool):
                counted = float(cell)
                bound = known * (1 + tolerance)
                if not _WHERE.search(sql):
                    if abs(counted - known) > known * tolerance:
                        findings.append(
                            PlausibilityFinding(
                                check="run_sql.count_vs_stats",
                                severity="fail",
                                detail=(
                                    f"COUNT over {queried[0]} returned "
                                    f"{_fmt(counted)}; stats row_count is "
                                    f"{_fmt(known)} (tolerance "
                                    f"{settings.row_count_tolerance_pct}%)"
                                ),
                            )
                        )
                elif settings.enforce_filtered_count_bound and counted > bound:
                    findings.append(
                        PlausibilityFinding(
                            check="run_sql.filtered_count_bound",
                            severity="fail",
                            detail=(
                                f"filtered COUNT over {queried[0]} returned "
                                f"{_fmt(counted)}, exceeding the table's known "
                                f"size {_fmt(known)}"
                            ),
                        )
                    )

        # 2b: COUNT(DISTINCT col) compares against the column's
        # distinct_count — never against row_count, which refused the
        # play pass's correct cardinality answer (R4). A column with no
        # stats row gets no check at all.
        distinct_match = _COUNT_DISTINCT.search(sql)
        if distinct_match and is_single_cell and queried[0] in stats_by_table:
            column = distinct_match.group(1).lower()
            stat = next(
                (
                    row
                    for row in stats_by_table[queried[0]]
                    if row.column_name.lower() == column
                ),
                None,
            )
            cell = table.rows[0][table.columns[0]]
            if (
                stat is not None
                and isinstance(cell, (int, float))
                and not isinstance(cell, bool)
            ):
                counted = float(cell)
                known = float(stat.distinct_count)
                if not _WHERE.search(sql):
                    if abs(counted - known) > known * tolerance:
                        findings.append(
                            PlausibilityFinding(
                                check="run_sql.distinct_vs_stats",
                                severity="fail",
                                detail=(
                                    f"COUNT(DISTINCT {column}) over "
                                    f"{queried[0]} returned {_fmt(counted)}; "
                                    f"stats distinct_count is {_fmt(known)} "
                                    f"(tolerance "
                                    f"{settings.row_count_tolerance_pct}%)"
                                ),
                            )
                        )
                elif (
                    settings.enforce_filtered_count_bound
                    and counted > known * (1 + tolerance)
                ):
                    findings.append(
                        PlausibilityFinding(
                            check="run_sql.distinct_vs_stats",
                            severity="fail",
                            detail=(
                                f"filtered COUNT(DISTINCT {column}) over "
                                f"{queried[0]} returned {_fmt(counted)}, "
                                f"exceeding the column's known "
                                f"distinct_count {_fmt(known)}"
                            ),
                        )
                    )

        # 2b2: the joined-count bound (pin pass, MT2). The single-table
        # count checks above skip any multi-table query by design; this
        # is their backstop: a count-shaped result cannot honestly
        # exceed the largest queried table, because a filter only
        # lowers a count and only a fanning join raises it. CTE and
        # subquery names never resolve in stats and drop out of the
        # bound — a fan-out hidden inside one is a known-open gap
        # (docs/pin-pass-residuals.md).
        if settings.enforce_joined_count_bound and len(queried) >= 2:
            findings.extend(
                self._joined_count_findings(
                    sql, table, queried, stats_by_table, settings
                )
            )

        # 2b2b: the entity-count bound (guard pass, AMB2). A COUNT
        # column whose alias names a stats table cannot exceed that
        # table, whatever the statement reads — the single-table checks
        # above compare against the queried table, and AMB2's queried
        # table was the wrong one. Warn: the alias is a convention.
        if settings.enforce_entity_count_bound and table.rows:
            findings.extend(
                self._entity_count_findings(
                    sql, table, queried, stats_by_table, settings
                )
            )

        # 2b3: saturated rates (pin pass, S2). Exactly 0.0 or 1.0 (100.0
        # on a percent-scale column) on a rate-hinted column is a legal
        # value no bound can reject, but it is also exactly what AVG
        # over a NULL-padded indicator produces: warn, so the answer
        # ships [UNVERIFIED]. Its sibling, the scale-suspect warn
        # (coverage pass): a percent-scale column whose values all sit
        # at or below 1.0 is a fraction written into a percent alias —
        # rendered 1.0% for a true 100%, and inside the 0–100 bound.
        if settings.challenge_saturated_rates:
            findings.extend(self._saturated_rate_findings(table, settings))
            findings.extend(self._rate_scale_findings(table))

        # 2b4: the duration class's floor and ceiling (duration pass,
        # W3). A duration-hinted aggregate below one second warns; a
        # duration longer than the queried data's timestamp span fails
        # (a SUM is exempt, an unclassifiable item warns).
        if (
            settings.challenge_degenerate_durations
            or settings.enforce_duration_span_bound
        ) and table.rows:
            findings.extend(
                self._duration_findings(
                    sql, table, queried, stats_by_table, settings
                )
            )

        # 2c: aggregate-vs-stats bounds for result columns the
        # select-list parse resolves (play pass — §9.3 finally
        # implemented for tables).
        if settings.enforce_aggregate_bounds and table.rows:
            findings.extend(
                self._aggregate_findings(
                    sql, table, queried, stats_by_table, settings
                )
            )

        # 3-5: per-cell checks for columns that map to stats by name.
        named_stats: dict[str, StatsRow] = {}
        for table_name in queried:
            for row in stats_by_table.get(table_name, []):
                named_stats.setdefault(row.column_name, row)

        has_aggregate = _AGGREGATE.search(sql) is not None
        for row_index, row in enumerate(table.rows):
            for column, cell in row.items():
                stat = named_stats.get(column)
                findings.extend(
                    self._cell_findings(
                        column,
                        cell,
                        stat,
                        has_aggregate,
                        settings,
                        row_index,
                        hint=table.column_formats.get(column),
                    )
                )
        return findings

    @staticmethod
    def _joined_count_findings(
        sql: str,
        table,
        queried: list[str],
        stats_by_table: dict[str, list[StatsRow]],
        settings,
    ) -> list[PlausibilityFinding]:
        """A count over joined tables compared against the largest
        queried table's row_count. Scalar shape: a lone COUNT cell.
        Grouped shape: an aliased count column, summed — only when the
        result is untruncated (a truncated sum understates)."""
        sized = [
            (name, float(stats_by_table[name][0].row_count))
            for name in queried
            if name in stats_by_table
        ]
        if not sized:
            return []  # CTE/subquery names only: nothing to bound with
        largest_name, largest = max(sized, key=lambda pair: pair[1])
        if largest <= 0:
            return []
        value: float | None = None
        described = ""
        if (
            len(table.rows) == 1
            and len(table.columns) == 1
            and _COUNT_ONLY.search(sql)
        ):
            cell = table.rows[0][table.columns[0]]
            if isinstance(cell, (int, float)) and not isinstance(cell, bool):
                value = float(cell)
                described = f"COUNT over {', '.join(queried)} returned"
        elif not table.truncated:
            alias_match = _COUNT_ALIAS.search(sql)
            if alias_match and alias_match.group(1) in table.columns:
                alias = alias_match.group(1)
                cells = [
                    float(cell)
                    for row in table.rows
                    if isinstance(cell := row.get(alias), (int, float))
                    and not isinstance(cell, bool)
                ]
                if cells:
                    value = sum(cells)
                    described = f"the {alias} count column sums to"
        if value is None or value <= largest * settings.joined_count_warn_factor:
            return []
        severity = (
            "fail"
            if value > largest * settings.joined_count_fail_factor
            else "warn"
        )
        return [
            PlausibilityFinding(
                check="run_sql.joined_count_vs_stats",
                severity=severity,
                detail=(
                    f"{described} {_fmt(value)}, {value / largest:.1f}× the "
                    f"largest queried table ({largest_name}: "
                    f"{_fmt(largest)} rows) — a filter only lowers a "
                    "count; only a fanning join raises it"
                ),
            )
        ]

    @staticmethod
    def _entity_count_findings(
        sql: str,
        table,
        queried: list[str],
        stats_by_table: dict[str, list[StatsRow]],
        settings,
    ) -> list[PlausibilityFinding]:
        """A COUNT column (per the select-list parse, DISTINCT or not,
        resolved through CTEs) whose alias names a stats table,
        compared against that table's row_count. Scalar shape: the lone
        cell. Grouped shape: the column's sum, untruncated results only
        (the joined-count bound's precedent). One finding per column."""
        items = resolve_select_items(sql)
        tolerance = settings.row_count_tolerance_pct / 100.0
        findings: list[PlausibilityFinding] = []
        for column in table.columns:
            tree = items.get(column)
            if not (isinstance(tree, Aggregate) and tree.func == "count"):
                continue
            entity = entity_table_for_alias(column, stats_by_table)
            if entity is None:
                continue
            known = float(stats_by_table[entity][0].row_count)
            if known <= 0:
                continue
            cells = [
                float(cell)
                for row in table.rows
                if isinstance(cell := row.get(column), (int, float))
                and not isinstance(cell, bool)
            ]
            if not cells:
                continue
            if len(table.rows) == 1:
                value = cells[0]
                described = f"{column} = {_fmt(value)}"
            elif table.truncated:
                continue
            else:
                value = sum(cells)
                described = f"the {column} column sums to {_fmt(value)}"
            if value <= known * (1 + tolerance):
                continue
            findings.append(
                PlausibilityFinding(
                    check="run_sql.entity_count_exceeds_table",
                    severity="warn",
                    detail=(
                        f"{described}, but the alias counts {entity} and "
                        f"stats know {_fmt(known)} {entity} rows — a count "
                        f"of {entity} cannot exceed the {entity} table; "
                        f"the statement reads {', '.join(queried)}"
                    ),
                )
            )
        return findings

    @staticmethod
    def _saturated_rate_findings(
        table, settings
    ) -> list[PlausibilityFinding]:
        """Exactly 0.0 or 1.0 (100.0 at percent scale) on a rate-hinted
        column: warn, one finding per column. A count-like cell in the
        same row below the minimum basis suppresses it — tiny
        populations saturate honestly; an absent basis warns, since the
        warn only removes the badge."""
        findings: list[PlausibilityFinding] = []
        for column in table.columns:
            hint = table.column_formats.get(column)
            if hint is None or hint.kind != "rate":
                continue
            top = 100.0 if hint.scale == "percent" else 1.0
            for row_index, row in enumerate(table.rows):
                cell = row.get(column)
                if not isinstance(cell, (int, float)) or isinstance(cell, bool):
                    continue
                value = float(cell)
                if value not in (0.0, top):
                    continue
                bases = [
                    float(other)
                    for name, other in row.items()
                    if name != column
                    and isinstance(other, (int, float))
                    and not isinstance(other, bool)
                    and float(other) >= 0
                    and float(other) == int(other)
                ]
                if bases and max(bases) < settings.saturated_rate_min_basis:
                    continue  # a small population saturates honestly
                findings.append(
                    PlausibilityFinding(
                        check="run_sql.rate_saturated",
                        severity="warn",
                        detail=(
                            f"rows[{row_index}].{column} = {_fmt(value)} is "
                            "a saturated rate — every row on one side. "
                            "Legitimate at the extremes, but also exactly "
                            "what AVG over a NULL-padded indicator "
                            "produces (unmatched rows vanish from the "
                            "denominator)"
                        ),
                    )
                )
                break  # one finding per column says it all
        return findings

    @staticmethod
    def _rate_scale_findings(table) -> list[PlausibilityFinding]:
        """A percent-scale rate column whose numeric cells all sit at or
        below 1.0 across more than one row — or a lone cell at or below
        1.0 that is not exactly 0 or 1 — is a fraction written into a
        percent alias (ROUND(x, 2) AS flag_pct over a 0–1 x): it renders
        1.0% for a true 100% and passes the 0–100 bound. Warn: the
        alias's scale is the SQL author's word, and the word may be
        wrong, but only the badge comes off."""
        findings: list[PlausibilityFinding] = []
        for column in table.columns:
            hint = table.column_formats.get(column)
            if hint is None or hint.kind != "rate" or hint.scale != "percent":
                continue
            cells = [
                float(row[column])
                for row in table.rows
                if isinstance(row.get(column), (int, float))
                and not isinstance(row.get(column), bool)
            ]
            if not cells or max(cells) > 1.0:
                continue
            if len(cells) == 1 and cells[0] in (0.0, 1.0):
                continue  # a lone 0 or 1 is the saturation check's case
            findings.append(
                PlausibilityFinding(
                    check="run_sql.rate_scale_suspect",
                    severity="warn",
                    detail=(
                        f"{column} is a percent-scale rate column (its alias "
                        "says so) but every value sits at or below 1.0 — a "
                        "fraction written into a percent alias renders as "
                        f"{_fmt(max(cells))}% where the truth may be "
                        f"{_fmt(max(cells) * 100)}%"
                    ),
                )
            )
        return findings

    @staticmethod
    def _duration_findings(
        sql: str,
        table,
        queried: list[str],
        stats_by_table: dict[str, list[StatsRow]],
        settings,
    ) -> list[PlausibilityFinding]:
        """The duration class's bounds, read through the same hint the
        renderer uses. Floor: an aggregate cell below one second warns
        (an instant transition is legal, but so was W3's scaled
        interval), suppressed by a same-row count under the basis.
        Ceiling: a cell longer than the queried data's timestamp span
        fails when the parse can see the item is not a SUM, warns when
        it cannot classify the item, and is silent for a SUM. One
        finding per column per bound."""
        hinted = {
            column: hint
            for column in table.columns
            if (hint := table.column_formats.get(column)) is not None
            and hint.kind == "duration"
        }
        if not hinted:
            return []
        items = resolve_select_items(sql)
        span = (
            _timestamp_span_seconds(queried, stats_by_table)
            if settings.enforce_duration_span_bound
            else None
        )
        findings: list[PlausibilityFinding] = []
        for column, hint in hinted.items():
            tree = items.get(column)
            aggregated = bool(_aggregate_names(tree) & {"avg", "sum", "min", "max"})
            summed = "sum" in _aggregate_names(tree) and not isinstance(tree, Opaque)
            unclassified = tree is None or isinstance(tree, Opaque)
            floor_done = False
            ceiling_done = False
            for row_index, row in enumerate(table.rows):
                cell = row.get(column)
                seconds = duration_seconds(cell, hint.unit)
                if seconds is None:
                    continue
                where = f"rows[{row_index}].{column}"
                if (
                    settings.challenge_degenerate_durations
                    and not floor_done
                    and aggregated
                    and abs(seconds) < 1.0
                ):
                    bases = [
                        float(other)
                        for name, other in row.items()
                        if name != column
                        and isinstance(other, (int, float))
                        and not isinstance(other, bool)
                        and float(other) >= 0
                        and float(other) == int(other)
                    ]
                    if not (
                        bases
                        and max(bases) < settings.degenerate_duration_min_basis
                    ):
                        floor_done = True
                        findings.append(
                            PlausibilityFinding(
                                check="run_sql.duration_degenerate",
                                severity="warn",
                                detail=(
                                    f"{where} = {cell!r} is a degenerate "
                                    "duration — an aggregate below one "
                                    "second. Legitimate for an instant "
                                    "transition, but also exactly what "
                                    "interval arithmetic scaled by a unit "
                                    "produces (AVG(a - b) / 86400 is "
                                    "0.041667 seconds, not days)"
                                ),
                            )
                        )
                if (
                    span is not None
                    and not ceiling_done
                    and not summed
                    and seconds > span
                ):
                    ceiling_done = True
                    detail = (
                        f"{where} = {cell!r} ({_fmt(seconds / 86400)} days) "
                        "exceeds the span of the queried data's timestamps "
                        f"({_fmt(span / 86400)} days)"
                    )
                    if unclassified:
                        detail += (
                            "; the select-list parse cannot classify the "
                            "column, so this warns rather than refuses"
                        )
                    findings.append(
                        PlausibilityFinding(
                            check="run_sql.duration_span_bound",
                            severity="warn" if unclassified else "fail",
                            detail=detail,
                        )
                    )
                if floor_done and (ceiling_done or span is None):
                    break
        return findings

    def _aggregate_findings(
        self,
        sql: str,
        table,
        queried: list[str],
        stats_by_table: dict[str, list[StatsRow]],
        settings,
    ) -> list[PlausibilityFinding]:
        """SUM caps, AVG ranges, and alias-resolved cell bounds for the
        result columns resolve_select_columns can trace. The bounds are
        impossible-if-clean (a subset's sum cannot exceed the whole for
        a non-negative column; a subset's average cannot leave
        [min, max]), so tolerance covers only float slop and stats
        staleness: within it warns, beyond it fails."""
        findings: list[PlausibilityFinding] = []
        tol = settings.aggregate_bound_tolerance_pct / 100.0
        for alias, resolved in resolve_select_columns(sql).items():
            if alias not in table.columns:
                continue
            stat = self._stat_for(resolved, queried, stats_by_table)
            if stat is None:
                continue
            if resolved.aggregate == "sum":
                findings.extend(
                    self._sum_findings(alias, stat, table, tol, settings)
                )
            elif resolved.aggregate == "avg":
                findings.extend(self._avg_findings(alias, stat, table, tol))
            elif alias != stat.column_name:
                # A renamed passthrough column: the name-equality loop
                # below never sees it, so run the same per-cell logic
                # here, sample-capped. Name-equal columns stay with the
                # existing loop (no double findings).
                sample = table.rows[: settings.aggregate_cell_sample_rows]
                for row_index, row in enumerate(sample):
                    findings.extend(
                        self._cell_findings(
                            alias,
                            row.get(alias),
                            stat,
                            # A traced passthrough is a raw column value
                            # (a group key, a projected cell) even when
                            # the query aggregates elsewhere: the
                            # min/max bound applies.
                            False,
                            settings,
                            row_index,
                            hint=table.column_formats.get(alias),
                        )
                    )
        return findings

    @staticmethod
    def _stat_for(
        resolved: ResolvedColumn,
        queried: list[str],
        stats_by_table: dict[str, list[StatsRow]],
    ) -> StatsRow | None:
        tables = [resolved.table] if resolved.table else queried
        for table_name in tables:
            for row in stats_by_table.get(table_name, []):
                if row.column_name.lower() == resolved.column:
                    return row
        return None

    def _sum_findings(
        self, alias: str, stat: StatsRow, table, tol: float, settings
    ) -> list[PlausibilityFinding]:
        low = _as_float(stat.min_value)
        if stat.mean is None or low is None or low < 0:
            return []  # unknown sign, or signed: no valid cap
        cap = stat.mean * stat.row_count * (1.0 - stat.null_rate)
        findings: list[PlausibilityFinding] = []
        cells = [
            float(cell)
            for row in table.rows
            if isinstance(cell := row.get(alias), (int, float))
            and not isinstance(cell, bool)
        ]
        source = f"{stat.table_name}.{stat.column_name}"
        for value in cells:
            if value > cap:
                severity = "fail" if value > cap * (1 + tol) else "warn"
                findings.append(
                    PlausibilityFinding(
                        check="run_sql.sum_vs_stats",
                        severity=severity,
                        detail=(
                            f"{alias} = {_fmt(value)} exceeds the maximum "
                            f"possible SUM over {source}: mean × non-null "
                            f"count = {_fmt(cap)} — a join fan-out "
                            "multiplies exactly this way"
                        ),
                    )
                )
                break  # one cell finding per column says it all
        total = sum(cells)
        if len(cells) > 1 and total > cap:
            severity = "fail" if total > cap * (1 + tol) else "warn"
            findings.append(
                PlausibilityFinding(
                    check="run_sql.sum_vs_stats",
                    severity=severity,
                    detail=(
                        f"the {alias} column sums to {_fmt(total)}, "
                        f"exceeding the maximum possible SUM over "
                        f"{source}: mean × non-null count = {_fmt(cap)} — "
                        "a join fan-out multiplies exactly this way"
                    ),
                )
            )
        return findings

    def _avg_findings(
        self, alias: str, stat: StatsRow, table, tol: float
    ) -> list[PlausibilityFinding]:
        low = _as_float(stat.min_value)
        high = _as_float(stat.max_value)
        if low is None or high is None:
            return []
        span = high - low
        findings: list[PlausibilityFinding] = []
        source = f"{stat.table_name}.{stat.column_name}"
        for row in table.rows:
            cell = row.get(alias)
            if not isinstance(cell, (int, float)) or isinstance(cell, bool):
                continue
            value = float(cell)
            if low <= value <= high:
                continue
            outside = max(low - value, value - high)
            severity = "fail" if outside > span * tol else "warn"
            findings.append(
                PlausibilityFinding(
                    check="run_sql.avg_vs_stats",
                    severity=severity,
                    detail=(
                        f"{alias} = {_fmt(value)} is an AVG over {source} "
                        f"outside its known range [{_fmt(low)}, "
                        f"{_fmt(high)}] — no subset's average can leave "
                        "the column's own bounds"
                    ),
                )
            )
            break  # one finding per column says it all
        return findings

    def _cell_findings(
        self,
        column: str,
        cell,
        stat: StatsRow | None,
        has_aggregate: bool,
        settings,
        row_index: int,
        hint: ColumnFormat | None = None,
    ) -> list[PlausibilityFinding]:
        findings: list[PlausibilityFinding] = []
        where = f"rows[{row_index}].{column}"

        # Rate bounds read the table's own hint — the scale the renderer
        # shows is the scale the bound holds, so a correctly
        # pre-multiplied percent is never refused by a fraction bound
        # and a fanned 1.0476 on a fraction column no longer passes as
        # "under 100".
        if (
            hint is not None
            and hint.kind == "rate"
            and isinstance(cell, (int, float))
            and not isinstance(cell, bool)
        ):
            value = float(cell)
            top = 100.0 if hint.scale == "percent" else 1.0
            if not (0.0 <= value <= top + 1e-9):
                findings.append(
                    PlausibilityFinding(
                        check="run_sql.rate_bounds",
                        severity="fail",
                        detail=(
                            f"{where} = {_fmt(value)} is outside [0,{_fmt(top)}] "
                            f"for a {hint.scale or 'fraction'}-scale rate column"
                        ),
                    )
                )

        if stat is None:
            return findings

        if (
            settings.enforce_min_max_bounds
            and not has_aggregate
            and isinstance(cell, (int, float))
            and not isinstance(cell, bool)
        ):
            try:
                low = float(stat.min_value) if stat.min_value else None
                high = float(stat.max_value) if stat.max_value else None
            except ValueError:
                low = high = None
            value = float(cell)
            if low is not None and high is not None and not (
                low <= value <= high
            ):
                findings.append(
                    PlausibilityFinding(
                        check="run_sql.min_max_bounds",
                        severity="fail",
                        detail=(
                            f"{where} = {_fmt(value)} is outside the known "
                            f"range [{_fmt(low)}, {_fmt(high)}] of "
                            f"{stat.table_name}.{stat.column_name}"
                        ),
                    )
                )

        if settings.enforce_date_bounds and isinstance(cell, str):
            cell_date = _as_date(cell)
            low_date = _as_date(stat.min_value)
            high_date = _as_date(stat.max_value)
            if cell_date and low_date and high_date:
                grace = timedelta(days=settings.date_bound_grace_days)
                if cell_date < low_date - grace or cell_date > high_date + grace:
                    findings.append(
                        PlausibilityFinding(
                            check="run_sql.date_bounds",
                            severity="fail",
                            detail=(
                                f"{where} = {cell_date} is outside the "
                                f"known range [{low_date}, {high_date}] of "
                                f"{stat.table_name}.{stat.column_name} "
                                f"(+{settings.date_bound_grace_days}d grace)"
                            ),
                        )
                    )
                elif cell_date < low_date or cell_date > high_date:
                    findings.append(
                        PlausibilityFinding(
                            check="run_sql.date_bounds",
                            severity="warn",
                            detail=(
                                f"{where} = {cell_date} is outside the "
                                f"stats snapshot range [{low_date}, "
                                f"{high_date}] but within grace"
                            ),
                        )
                    )
        return findings
