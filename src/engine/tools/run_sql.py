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

from engine.config.models import RunSqlSettings, SubstrateName, ToolName
from engine.ports.identity import IdentityPort
from engine.ports.llm import LLMPort
from engine.ports.sql import SqlPort
from engine.ports.substrate_store import SubstrateStoreError, SubstrateStorePort
from engine.ports.types import Message
from engine.tools.base import Tool, manifest_ids_of
from engine.tools.envelope import (
    JsonValue,
    RunSqlEvidence,
    RunSqlOutput,
    SqlAttempt,
    Table,
    ToolInvocation,
)
from engine.tools.grounding import render_grounding

_SQL_FENCE = re.compile(r"```(?:sql)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

_SUBSTRATES = [
    SubstrateName.DATA_DICTIONARY,
    SubstrateName.DATA_DICTIONARY_MAP,
    SubstrateName.UNIVARIATE_STATISTICS,
    SubstrateName.APPLICATION_DATABASE,
]


class RunSqlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str


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
        "the result rows as a table."
    )
    input_model = RunSqlInput

    def __init__(
        self,
        store: SubstrateStorePort,
        sql: SqlPort,
        llm: LLMPort,
        identity: IdentityPort,
        settings: RunSqlSettings,
    ) -> None:
        self._store = store
        self._sql = sql
        self._llm = llm
        self._identity = identity
        self._settings = settings

    def run(self, params: RunSqlInput) -> ToolInvocation:
        try:
            dictionary = self._store.dictionary()
            dictionary_map = self._store.dictionary_map()
            stats = self._store.stats()
        except SubstrateStoreError as exc:
            return self.fail(params, str(exc))

        prompt = render_grounding(
            dictionary, dictionary_map, stats, dialect=self._settings.dialect
        )
        messages = [
            Message(role="system", content=prompt),
            Message(role="user", content=params.question),
        ]
        user = self._identity.current_user()
        manifest_ids = manifest_ids_of(dictionary + stats)
        attempts: list[SqlAttempt] = []

        for _ in range(self._settings.max_repair_attempts + 1):
            response = self._llm.complete(messages, temperature=0.0)
            sql = extract_sql(response.content)
            if sql is None:
                error = (
                    "No SQL statement found in the reply. Reply with exactly "
                    "one SELECT statement in a ```sql fence."
                )
            elif (guard_error := guard_select_only(sql)) is not None:
                error = guard_error
            else:
                try:
                    rows = self._sql.run_sql(sql, user)
                except Exception as exc:
                    error = str(exc)
                else:
                    attempts.append(
                        SqlAttempt(
                            raw_response=response.content,
                            sql=sql,
                            row_count=len(rows),
                        )
                    )
                    return self.ok(
                        params,
                        RunSqlOutput(sql=sql, table=self._to_table(rows)),
                        evidence=RunSqlEvidence(
                            grounding_prompt=prompt, attempts=attempts
                        ),
                        substrates_read=_SUBSTRATES,
                        manifest_ids=manifest_ids,
                    )
            attempts.append(
                SqlAttempt(raw_response=response.content, sql=sql, error=error)
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

    def _to_table(self, rows: list[dict]) -> Table:
        kept = rows[: self._settings.max_result_rows]
        return Table(
            columns=list(rows[0].keys()) if rows else [],
            rows=[
                {key: _to_json_value(value) for key, value in row.items()}
                for row in kept
            ],
            total_row_count=len(rows),
            truncated=len(kept) < len(rows),
        )
