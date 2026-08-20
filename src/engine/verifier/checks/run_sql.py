"""run_sql check: the richest harvest and the only plausibility suite
in v1 (§9.3) — result values sanity-checked against Univariate Stats.

Known limitations, documented rather than hidden:
- Table extraction is FROM/JOIN tokenization; result columns map to
  stats columns by name equality. Aliases that rename columns escape
  the min/max and date checks. Thresholds are pack config precisely
  because this gets tuned at work against real distributions.
- The aggregate guard is a coarse token scan; an aggregate the scan
  misses could trigger a spurious min/max finding (visible, not
  silent).
- THE VERIFIED-ZERO GAP (Phase 4b eval target): if the generating LLM
  ignores the grounding gotcha and anchors "last week" to the real
  today instead of the data, the query returns 0 rows. "0 invoices"
  is then faithfully drafted, and nothing here can object — an empty
  result carries no date cells, and 0 sits under every bound. Wrong-
  but-verified via wrong window. Grounding (the Dictionary Map's
  data-coverage gotcha) is the mitigation; the eval harness measures
  the residue. When reading a run_sql answer, read the SQL's date
  window, not just the number.
"""

import re
from datetime import date, timedelta

from engine.config.models import ToolName
from engine.substrates.models import StatsRow
from engine.tools.envelope import RunSqlOutput, ToolInvocation
from engine.verifier.checks.base import (
    PlausibilityContext,
    SubstrateCheck,
    identifier_tokens,
)
from engine.verifier.models import (
    CorpusText,
    EvidenceContribution,
    EvidenceValue,
    PlausibilityFinding,
)

_FROM_JOIN = re.compile(r"\b(?:from|join)\s+([A-Za-z_]\w*)", re.IGNORECASE)
_COUNT_ONLY = re.compile(r"select\s+count\s*\(", re.IGNORECASE)
_WHERE = re.compile(r"\bwhere\b", re.IGNORECASE)
_AGGREGATE = re.compile(r"\b(?:sum|avg|count|min|max)\s*\(", re.IGNORECASE)
_ISO_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _fmt(value: float) -> str:
    """Thousands-separated, never scientific: findings are read by
    humans deciding whether to trust an answer."""
    return format(value, ",.10g")


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

        # 1 & 2: COUNT sanity against known table sizes.
        is_single_count = (
            _COUNT_ONLY.search(sql)
            and len(queried) == 1
            and len(table.rows) == 1
            and len(table.columns) == 1
        )
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
                        column, cell, stat, has_aggregate, settings, row_index
                    )
                )
        return findings

    def _cell_findings(
        self,
        column: str,
        cell,
        stat: StatsRow | None,
        has_aggregate: bool,
        settings,
        row_index: int,
    ) -> list[PlausibilityFinding]:
        findings: list[PlausibilityFinding] = []
        where = f"rows[{row_index}].{column}"

        if (
            isinstance(cell, (int, float))
            and not isinstance(cell, bool)
            and any(column.endswith(s) for s in settings.rate_column_suffixes)
        ):
            value = float(cell)
            if not (0.0 <= value <= 1.0 or 0.0 <= value <= 100.0):
                findings.append(
                    PlausibilityFinding(
                        check="run_sql.rate_bounds",
                        severity="fail",
                        detail=(
                            f"{where} = {_fmt(value)} is outside [0,1] and "
                            "[0,100] for a rate-named column"
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
