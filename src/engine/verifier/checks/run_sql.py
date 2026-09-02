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
"""

import re
from datetime import date, timedelta

from engine.config.models import ToolName
from engine.substrates.models import StatsRow
from engine.tools.envelope import (
    ColumnFormat,
    RunSqlEvidence,
    RunSqlOutput,
    ToolInvocation,
)
from engine.verifier.checks.base import (
    PlausibilityContext,
    SubstrateCheck,
    identifier_tokens,
)
from engine.tools.sql_select import ResolvedColumn, resolve_select_columns
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
        sql = output.sql
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
