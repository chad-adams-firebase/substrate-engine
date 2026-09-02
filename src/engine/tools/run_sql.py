"""run_sql — NL→SQL with the execute–check–repair loop (Brief §7).

Generation is grounded on Dictionary + Dictionary Map + Stats (never
raw schema alone), via LLMPort at temperature 0. Execution goes
through SqlPort with the caller's identity. On failure the error text
goes back to the LLM verbatim and the loop retries, bounded by pack
config. Every attempt — including the losers — is retained in
evidence.

Result rows return as data (a Table), not prose about them (§9.4):
numbers travel from store to screen without passing through a model.

The SELECT-only guard lives here, in the tool, deliberately: DuckDB's
read_only flag does not apply to :memory: databases, and the real
warehouse adapter forwards a user token that may hold write grants —
a guard in one adapter protects one adapter; a guard here protects
every path SQL can take.
"""

import re

from pydantic import BaseModel, ConfigDict

from engine.config.models import (
    DisplaySettings,
    RunSqlSettings,
    SubstrateName,
    ToolName,
)
from engine.ports.identity import IdentityPort
from engine.ports.llm import LLMPort
from engine.ports.sql import SqlPort
from engine.ports.substrate_store import SubstrateStoreError, SubstrateStorePort
from engine.ports.types import Message
from engine.substrates.models import DictionaryMap
from engine.tools.base import Tool, manifest_ids_of
from engine.tools.column_formats import money_column_names, resolve_column_formats
from engine.tools.envelope import (
    JsonValue,
    RunSqlEvidence,
    RunSqlOutput,
    SqlAttempt,
    Table,
    ToolInvocation,
)
from engine.tools.enum_lint import lint_enum_literals
from engine.tools.grounding import render_grounding
from engine.tools.sql_lint import lint_fan_out

_SQL_FENCE = re.compile(r"```(?:sql)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

# Must stay identical to the name group of the placeholder grammar's
# _SEGMENT (engine.harness.placeholders): a result column the drafter
# cannot address as {{eN.table.rows[i].<name>}} is a value the answer
# cannot cite. Tools never import harness, so the pattern is
# duplicated here and pinned equal by a test.
_ADDRESSABLE_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_SUBSTRATES = [
    SubstrateName.DATA_DICTIONARY,
    SubstrateName.DATA_DICTIONARY_MAP,
    SubstrateName.UNIVARIATE_STATISTICS,
    SubstrateName.APPLICATION_DATABASE,
]


class RunSqlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str


# A question argument that is itself SQL — the observed router leak.
# Deliberately case-sensitive: leaked SQL comes uppercase-keyworded,
# while English like "Select the invoices from last week" must pass.
_SQL_SHAPED = re.compile(
    r"^\s*(?:SELECT|WITH)\b.*\bFROM\b"
    r"|\bGROUP BY\b|\bORDER BY\b|\bCOUNT\(\*\)"
    r"|\bJOIN\b.*\bON\b",
    re.DOTALL,
)


def extract_sql(response_text: str) -> str | None:
    """The statement out of an LLM response, deterministically: prefer
    a fenced block, else accept bare text that starts with SELECT/WITH
    (after leading comments). None means no statement found."""
    match = _SQL_FENCE.search(response_text)
    candidate = match.group(1) if match else response_text
    lines = candidate.strip().splitlines()
    while lines and (
        not lines[0].strip() or lines[0].lstrip().startswith("--")
    ):
        lines.pop(0)
    candidate = "\n".join(lines).strip().rstrip(";").strip()
    if not candidate:
        return None
    first_word = candidate.split(None, 1)[0].upper()
    if match is None and first_word not in ("SELECT", "WITH"):
        return None
    return candidate


def guard_select_only(sql: str) -> str | None:
    """The reason a statement is not allowed, or None if it is."""
    first_word = sql.split(None, 1)[0].upper()
    if first_word not in ("SELECT", "WITH"):
        return (
            f"Only a single read-only SELECT (or WITH) statement is "
            f"allowed; got a statement starting with {first_word}."
        )
    if ";" in sql:
        return "Multiple SQL statements are not allowed; send exactly one."
    return None


def _to_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)  # datetimes, decimals, and friends travel as text


class RunSql(Tool):
    name = ToolName.RUN_SQL
    description = (
        "Answer a data question by generating and executing a read-only "
        "SQL query against the application database, grounded in the "
        "data dictionary, canonical metrics, and known gotchas. Returns "
        "the result rows as a table. question: the user's question in "
        "the user's own words (only references to earlier turns "
        "resolved) — never a paraphrase: the SQL author is grounded in "
        "the domain's vocabulary and must see the original phrasing."
    )
    input_model = RunSqlInput

    def __init__(
        self,
        store: SubstrateStorePort,
        sql: SqlPort,
        llm: LLMPort,
        identity: IdentityPort,
        settings: RunSqlSettings,
        display: DisplaySettings | None = None,
    ) -> None:
        self._store = store
        self._sql = sql
        self._llm = llm
        self._identity = identity
        self._settings = settings
        self._display = display or DisplaySettings()

    def run(self, params: RunSqlInput) -> ToolInvocation:
        if _SQL_SHAPED.search(params.question):
            # Steering error, recoverable: the router re-asks with the
            # user's question instead of its own SQL.
            return self.fail(
                params,
                "run_sql writes its own SQL — send the English "
                "question, not a SQL statement.",
            )
        try:
            dictionary = self._store.dictionary()
            dictionary_map = self._store.dictionary_map()
            stats = self._store.stats()
        except SubstrateStoreError as exc:
            return self.fail(params, str(exc))

        prompt = render_grounding(
            dictionary,
            dictionary_map,
            stats,
            dialect=self._settings.dialect,
            question=params.question,
        )
        messages = [
            Message(role="system", content=prompt),
            Message(role="user", content=params.question),
        ]
        user = self._identity.current_user()
        manifest_ids = manifest_ids_of(dictionary + stats)
        attempts: list[SqlAttempt] = []
        # Each lint BLOCKS at most once per call: its word is a repair
        # round with an explicit license to resend unchanged, so the
        # second submission is the model's considered answer. Reasons
        # not yet challenged block together in one round; after a
        # kind's challenge, later attempts are re-linted in detection-
        # only mode — they execute regardless, but a still-tripping
        # reason is recorded on the attempt so overriding leaves a
        # trace (and, via the Verifier, costs the verified badge).
        fan_out_challenged = False
        enum_challenged = False

        for _ in range(self._settings.max_repair_attempts + 1):
            response = self._llm.complete(messages, temperature=0.0)
            sql = extract_sql(response.content)
            row_count: int | None = None
            lint_reason: str | None = None
            enum_reason: str | None = None
            blocking: list[str] = []
            if sql is None:
                error = (
                    "No SQL statement found in the reply. Reply with exactly "
                    "one SELECT statement in a ```sql fence."
                )
            elif (guard_error := guard_select_only(sql)) is not None:
                error = guard_error
            else:
                if self._settings.fan_out_lint:
                    lint_reason = lint_fan_out(sql, dictionary, dictionary_map)
                if self._settings.enum_literal_lint:
                    enum_reason = lint_enum_literals(sql, dictionary)
                if lint_reason is not None and not fan_out_challenged:
                    fan_out_challenged = True
                    blocking.append(lint_reason)
                if enum_reason is not None and not enum_challenged:
                    enum_challenged = True
                    blocking.append(enum_reason)
            if sql is not None and guard_select_only(sql) is None and blocking:
                error = " ".join(blocking)
            elif sql is not None and guard_select_only(sql) is None:
                try:
                    rows = self._sql.run_sql(sql, user)
                except Exception as exc:
                    error = str(exc)
                else:
                    # Zero rows carry no keys and nothing citable, so
                    # only non-empty results face the alias check.
                    bad = (
                        [
                            column
                            for column in rows[0].keys()
                            if not _ADDRESSABLE_COLUMN.match(column)
                        ]
                        if rows
                        else []
                    )
                    if not bad:
                        attempts.append(
                            SqlAttempt(
                                raw_response=response.content,
                                sql=sql,
                                row_count=len(rows),
                                lint=lint_reason,
                                enum_lint=enum_reason,
                            )
                        )
                        return self.ok(
                            params,
                            RunSqlOutput(
                                sql=sql,
                                table=self._to_table(rows, dictionary_map, sql),
                            ),
                            evidence=RunSqlEvidence(
                                grounding_prompt=prompt, attempts=attempts
                            ),
                            substrates_read=_SUBSTRATES,
                            manifest_ids=manifest_ids,
                        )
                    row_count = len(rows)
                    named = ", ".join(repr(column) for column in bad)
                    error = (
                        f"Result column name(s) {named} are not plain "
                        "identifiers. Add an AS alias (letters, digits, "
                        "underscores) to every aggregate or expression, "
                        "e.g. COUNT(*) AS invoice_count, and resend the "
                        "full statement."
                    )
            attempts.append(
                SqlAttempt(
                    raw_response=response.content,
                    sql=sql,
                    error=error,
                    row_count=row_count,
                    lint=lint_reason,
                    enum_lint=enum_reason,
                )
            )
            messages.append(Message(role="assistant", content=response.content))
            messages.append(
                Message(
                    role="user",
                    content=(
                        f"That attempt failed: {error}\n"
                        f"Reply with the corrected SQL only."
                    ),
                )
            )

        return self.fail(
            params,
            f"SQL generation failed after {len(attempts)} attempt(s); "
            f"last error: {attempts[-1].error}",
            evidence=RunSqlEvidence(grounding_prompt=prompt, attempts=attempts),
            substrates_read=_SUBSTRATES,
        )

    def _to_table(
        self, rows: list[dict], dictionary_map: DictionaryMap, sql: str
    ) -> Table:
        kept = rows[: self._settings.max_result_rows]
        columns = list(rows[0].keys()) if rows else []
        return Table(
            columns=columns,
            rows=[
                {key: _to_json_value(value) for key, value in row.items()}
                for row in kept
            ],
            total_row_count=len(rows),
            truncated=len(kept) < len(rows),
            # Parse first (the statement says what each column was
            # computed from), alias spelling second — see column_formats.
            column_formats=resolve_column_formats(
                columns,
                money_column_names(dictionary_map),
                self._display.money,
                self._display.duration,
                self._display.rate,
                sql=sql,
            ),
        )
