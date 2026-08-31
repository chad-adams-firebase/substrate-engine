"""The run_sql grounding prompt — a pure, deterministic rendering of
Dictionary + Dictionary Map + Univariate Stats (Brief §7: never raw
schema alone; the map IS the grounding payload, rendered, not
duplicated).

Pinned by a golden fixture: changing this rendering changes generated
SQL everywhere, so it changes fixtures deliberately or not at all.
"""

import re

from engine.substrates.models import CanonicalMetric, DictionaryMap, DictionaryRow, StatsRow

# Top values are shown only for genuinely enum-ish columns.
_TOP_VALUES_MAX_DISTINCT = 12


def match_metrics(
    question: str | None, dictionary_map: DictionaryMap
) -> list[CanonicalMetric]:
    """The canonical metrics the question names — by metric name (with
    underscores read as spaces) or any synonym, whole-phrase and
    case-folded. Deterministic: no model decides what a question is
    about."""
    if not question:
        return []
    text = question.casefold()
    matched = []
    for metric in dictionary_map.metrics:
        phrases = [metric.name.replace("_", " "), *metric.synonyms]
        if any(
            re.search(rf"\b{re.escape(phrase.casefold())}\b", text)
            for phrase in phrases
            if phrase
        ):
            matched.append(metric)
    return matched


def _interpretation_lines(interpretations) -> list[str]:
    """A declaring entry's readings, rendered under it — the SQL
    author sees which attribution it is choosing (play pass W6/W8),
    and the drafter rule makes the answer say which one it chose."""
    if not interpretations:
        return []
    lines = ["  interpretations (the answer must name the one used):"]
    for interpretation in interpretations:
        lines.append(f"    - {interpretation.name}: {interpretation.meaning}")
    return lines


def render_grounding(
    dictionary: list[DictionaryRow],
    dictionary_map: DictionaryMap,
    stats: list[StatsRow],
    *,
    dialect: str,
    question: str | None = None,
) -> str:
    # Renders the full dictionary and map verbatim — right at this
    # reference pack's size; an enterprise-scale pack may need
    # selection here instead of full inclusion.
    lines: list[str] = [
        f"You write SQL for the application database ({dialect} dialect).",
        "Use only the tables, columns, joins, and definitions below.",
        "Reply with exactly one read-only SELECT (or WITH) statement in",
        "a ```sql fence and nothing else.",
        "Give every aggregate or computed select-list expression an AS",
        "alias made of letters, digits, and underscores",
        "(COUNT(*) AS invoice_count), never a default name like",
        "count_star().",
        "When the question asks who, join id columns to their",
        "human-readable name columns via the join paths below, so the",
        "result names people and suppliers rather than bare ids.",
        "Relative windows (\"last N days\", \"last week\", \"this week\")",
        "end on the data's final day, inclusive, as a half-open range:",
        "column >= last_day - N + 1 days AND column < last_day + 1 day,",
        "with last_day taken from the data (see data_coverage below),",
        "never from CURRENT_DATE. A week is always that trailing window,",
        "never a calendar week (the data's last day is not a week end and",
        "week-start conventions differ); \"this month\" is the calendar",
        "month containing last_day, the reporting unit.",
    ]

    # A question that names a canonical metric gets that metric's
    # definition as the template, first — the 4b baseline showed the
    # right prose warning further down the prompt being ignored
    # (retrieval beats exhortation).
    for metric in match_metrics(question, dictionary_map):
        lines.append("")
        lines.append(
            f"## Canonical template for this question — metric {metric.name}"
        )
        lines.append(metric.description)
        lines.append(
            "Use this definition as written: adapt only the WHERE filters, "
            "ordering, and limit. Never re-derive the aggregation or the "
            "join shape."
        )
        if metric.template_sql:
            lines.append("```sql")
            lines.append(metric.template_sql.strip())
            lines.append("```")
        else:
            lines.append(f"tables: {', '.join(metric.tables)}")
            if metric.filter_sql:
                lines.append(f"filter: {metric.filter_sql}")
            lines.append(f"aggregation: {metric.aggregation_sql}")
        if metric.notes:
            lines.append(f"notes: {metric.notes}")

    lines.append("")
    lines.append("## Tables and columns")

    stats_by_column = {(row.table_name, row.column_name): row for row in stats}
    row_counts = {row.table_name: row.row_count for row in stats}
    by_table: dict[str, list[DictionaryRow]] = {}
    for row in dictionary:
        by_table.setdefault(row.table_name, []).append(row)

    for table in sorted(by_table):
        rows = by_table[table]
        table_row = next((r for r in rows if r.column_name == ""), None)
        count = row_counts.get(table)
        suffix = f" — {count} rows" if count is not None else ""
        lines.append(f"\n### {table}{suffix}")
        if table_row is not None and table_row.description:
            lines.append(table_row.description)
        for row in rows:
            if row.column_name == "":
                continue
            parts = [f"- {row.column_name} {row.data_type}"]
            if row.is_primary_key:
                parts.append("PK")
            if row.fk_target:
                parts.append(f"-> {row.fk_target}")
            if row.nullable is False:
                parts.append("NOT NULL")
            if row.enum_values:
                parts.append("values: " + ", ".join(row.enum_values))
            if row.description:
                parts.append(f"— {row.description}")
            column_stats = stats_by_column.get((table, row.column_name))
            if column_stats is not None:
                facts = []
                if column_stats.null_rate > 0:
                    facts.append(f"null_rate={column_stats.null_rate}")
                if (
                    column_stats.min_value is not None
                    and column_stats.max_value is not None
                ):
                    facts.append(
                        f"range [{column_stats.min_value} .. "
                        f"{column_stats.max_value}]"
                    )
                if (
                    column_stats.top_values
                    and column_stats.distinct_count <= _TOP_VALUES_MAX_DISTINCT
                ):
                    facts.append(
                        "top: "
                        + ", ".join(
                            f"{value.value}({value.count})"
                            for value in column_stats.top_values
                        )
                    )
                if facts:
                    parts.append("[" + "; ".join(facts) + "]")
            lines.append(" ".join(parts))

    if dictionary_map.concepts:
        lines.append("\n## Business concepts")
        for concept in dictionary_map.concepts:
            names = concept.name
            if concept.synonyms:
                names += f" (aka {', '.join(concept.synonyms)})"
            lines.append(f"- {names}: {concept.definition}")
            lines.extend(_interpretation_lines(concept.interpretations))

    if dictionary_map.metrics:
        lines.append("\n## Canonical metrics (use these definitions)")
        for metric in dictionary_map.metrics:
            lines.append(f"- {metric.name}: {metric.description}")
            lines.append(f"  tables: {', '.join(metric.tables)}")
            if metric.filter_sql:
                lines.append(f"  filter: {metric.filter_sql}")
            lines.append(f"  aggregation: {metric.aggregation_sql}")
            if metric.notes:
                lines.append(f"  notes: {metric.notes}")
            lines.extend(_interpretation_lines(metric.interpretations))

    if dictionary_map.join_paths:
        lines.append("\n## Canonical join paths")
        for join_path in dictionary_map.join_paths:
            steps = "; ".join(
                f"{s.from_table}.{s.from_column} = {s.to_table}.{s.to_column}"
                for s in join_path.steps
            )
            note = f" ({join_path.notes})" if join_path.notes else ""
            lines.append(f"- {join_path.name}: {steps}{note}")

    if dictionary_map.gotchas:
        lines.append("\n## Gotchas (read before writing SQL)")
        for gotcha in dictionary_map.gotchas:
            lines.append(f"- {gotcha.name}: {gotcha.summary}")
            lines.append(f"  {gotcha.detail}")

    if dictionary_map.examples:
        lines.append("\n## Where to look")
        for example in dictionary_map.examples:
            lines.append(f"- Q: {example.question}")
            lines.append(f"  {example.guidance}")

    return "\n".join(lines) + "\n"
