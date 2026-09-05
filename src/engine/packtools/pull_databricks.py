"""Warehouse -> DuckDB pull: the pack-build step that brings the target
application's tables down once (`engine pull`, work-side).

Why this shape. The demo runs against local data — no per-turn queries
against enterprise tables — so the tables are copied once into the
pack's DuckDB file, and everything downstream (generators, the DuckDB
SqlPort, the verifier's world) reads that file exactly as it reads
InvoiceGuard's. The copy goes through the SQL Statement Execution REST
API over httpx2 (the openai SDK's HTTP stack, already in the tree),
not a connector: zero new dependencies to clear the cp312-win_amd64
wheel bar, and every failure is an HTTP status plus a JSON message a
chat assistant can read beside this file. If the API is disabled in
the workspace, the runbook names the connector as break-glass.

Adapter-category code, deliberately, like convert_sqlite.py: it
imports httpx2 and duckdb because it manufactures the artifact the
DuckDB SqlPort adapter serves. It never imports engine.adapters.

Per table (_pull_table):
  1. DESCRIBE HISTORY <table> LIMIT 1 -> the current Delta version
     (skipped when `versioned: false`); every later statement reads
     VERSION AS OF that number, so all pages see one snapshot.
  2. SELECT COUNT(*) -> the row count the pull must land.
  3. Pages of `page_rows`: by key when the table names one
     (WHERE key > :last_key ORDER BY key — the intended path for any
     large table), else by offset over ORDER BY ALL — correct on a
     versioned snapshot, slower.
  4. Column types come from the first page's schema; rows arrive as
     strings (JSON_ARRAY) and land through explicit CASTs so DuckDB
     does the parsing. Columns are read by NAME, never position.
The manifest records the schema and each table's version as
source_snapshot; a re-pull at the same versions reproduces the id.

Types. DECIMAL lands as DOUBLE, as the converter maps SQLite NUMERIC:
the stats generator recognizes DOUBLE and not DECIMAL(p,s), and every
world so far has been DOUBLE-only. Currency sums in the local world
can therefore differ from the source system in the last cents — that
is this mapping, not an engine bug (the runbook says the same where a
person compares numbers). BINARY and complex types (ARRAY, MAP,
STRUCT) land as their text rendering.

Failure modes (file: this one; function: _Warehouse.execute unless
named) — the observable, what it means, the question to ask:

  PullError naming DATABRICKS_HOST or DATABRICKS_TOKEN (cli._pull)
      unset in the shell that ran `engine` — "does `set` in that shell
      show both?"
  HTTP 401
      the token is expired, revoked, or for another workspace — "when
      was the PAT created; does the host match the issuing workspace?"
  HTTP 403
      no CAN USE on the SQL warehouse — "who owns warehouse_id, and
      can they grant CAN USE?"
  HTTP 404
      warehouse_id unknown to this workspace, or the Statement
      Execution API is disabled — "does the SQL Warehouses page list
      this id?"
  statement FAILED: TABLE_OR_VIEW_NOT_FOUND / PERMISSION_DENIED
      the catalog.schema.table name, or no SELECT on it — "what does
      SHOW TABLES IN catalog.schema return for this user?"
  statement FAILED on DESCRIBE HISTORY
      not a Delta table (a view, an external non-Delta source) — set
      `versioned: false` on that table.
  statement FAILED mentioning the inline limit / 25 MiB
      one page is too wide — lower pull.page_rows.
  statement FAILED at or near 'ALL'
      the warehouse runtime predates ORDER BY ALL — set `key` on the
      table; keyset paging never uses it.
  PullError: landed N rows but the count said M
      an unversioned table moved during the pull — re-run, or set
      `versioned: true` if the table is Delta.
  PullError: no DuckDB mapping for type
      extend TYPE_MAP deliberately, as convert_sqlite says.
"""

import time
from pathlib import Path
from typing import Any, NamedTuple

import duckdb
import httpx2
from pydantic import BaseModel, ConfigDict

from engine.config.models import PullConfig, PullTable
from engine.substrates.manifest import build_manifest
from engine.substrates.models import Manifest

GENERATOR_VERSION = "1.0.0"
HOST_ENV_VAR = "DATABRICKS_HOST"
TOKEN_ENV_VAR = "DATABRICKS_TOKEN"
STATEMENTS_PATH = "/api/2.0/sql/statements"
# Seconds between polls of a statement still PENDING/RUNNING after
# the API's own 50 s wait. Tests set 0.
POLL_SECONDS = 1.0
WAIT_TIMEOUT = "50s"

# Statement Execution API type_name -> DuckDB type. Anything unlisted
# raises: silently guessing a type would poison every downstream
# substrate (the converter's rule).
TYPE_MAP = {
    "STRING": "VARCHAR",
    "CHAR": "VARCHAR",
    "VARCHAR": "VARCHAR",
    "BYTE": "TINYINT",
    "SHORT": "SMALLINT",
    "INT": "INTEGER",
    "LONG": "BIGINT",
    "FLOAT": "FLOAT",
    "DOUBLE": "DOUBLE",
    "DECIMAL": "DOUBLE",  # see the module docstring: last-cents drift
    "BOOLEAN": "BOOLEAN",
    "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMP",
    "TIMESTAMP_NTZ": "TIMESTAMP",
    "BINARY": "VARCHAR",
    "ARRAY": "VARCHAR",
    "MAP": "VARCHAR",
    "STRUCT": "VARCHAR",
    "NULL": "VARCHAR",
}


class PullError(Exception):
    """The pull cannot proceed faithfully. The message names the table,
    the statement or the HTTP status, and the fix."""


class Column(NamedTuple):
    name: str
    type_name: str
    type_text: str


class PulledTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    rows: int
    version: int | None


class PullOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: Manifest
    tables: list[PulledTable]


def base_url_for(host: str) -> str:
    """`https://<host>` from a host given with or without a scheme or
    trailing slash."""
    if "://" not in host:
        host = "https://" + host
    return host.rstrip("/")


def _client_factory(host: str, token: str) -> httpx2.Client:
    """Seam: tests replace this with a client over a MockTransport."""
    return httpx2.Client(
        base_url=base_url_for(host),
        headers={"Authorization": f"Bearer {token}"},
        timeout=httpx2.Timeout(120.0, connect=10.0),
    )


def _bq(identifier: str) -> str:
    """Databricks identifier quoting (backticks)."""
    return "`" + identifier.replace("`", "``") + "`"


def _dq(identifier: str) -> str:
    """DuckDB identifier quoting (double quotes), as the converter does."""
    return '"' + identifier.replace('"', '""') + '"'


class _Warehouse:
    """One SQL warehouse reached through the Statement Execution API."""

    def __init__(self, client: httpx2.Client, config: PullConfig) -> None:
        self._client = client
        self._config = config

    def execute(
        self, statement: str, parameters: list[dict[str, Any]] | None = None
    ) -> tuple[list[Column], list[list[Any]]]:
        body: dict[str, Any] = {
            "warehouse_id": self._config.warehouse_id,
            "statement": statement,
            "catalog": self._config.catalog,
            "schema": self._config.schema_name,
            "format": "JSON_ARRAY",
            "disposition": "INLINE",
            "wait_timeout": WAIT_TIMEOUT,
            "on_wait_timeout": "CONTINUE",
        }
        if parameters:
            body["parameters"] = parameters
        payload = self._checked(
            self._client.post(STATEMENTS_PATH, json=body), "submitting a statement"
        )
        statement_id = payload["statement_id"]
        while payload["status"]["state"] in ("PENDING", "RUNNING"):
            time.sleep(POLL_SECONDS)
            payload = self._checked(
                self._client.get(f"{STATEMENTS_PATH}/{statement_id}"),
                "polling a statement",
            )
        state = payload["status"]["state"]
        if state != "SUCCEEDED":
            error = payload["status"].get("error") or {}
            raise PullError(
                f"statement {state}: {error.get('error_code', '?')}: "
                f"{error.get('message', '(no message)')}{_hint(error)}\n"
                f"  statement: {statement}"
            )
        columns = [
            Column(c["name"], c["type_name"], c.get("type_text") or c["type_name"])
            for c in payload["manifest"]["schema"]["columns"]
        ]
        result = payload.get("result") or {}
        rows = list(result.get("data_array") or [])
        next_index = result.get("next_chunk_index")
        while next_index is not None:
            chunk = self._checked(
                self._client.get(
                    f"{STATEMENTS_PATH}/{statement_id}/result/chunks/{next_index}"
                ),
                "fetching a result chunk",
            )
            rows.extend(chunk.get("data_array") or [])
            next_index = chunk.get("next_chunk_index")
        return columns, rows

    @staticmethod
    def _checked(response: httpx2.Response, doing: str) -> dict[str, Any]:
        hints = {
            401: f"the token is expired, revoked, or for another workspace "
            f"({TOKEN_ENV_VAR} / {HOST_ENV_VAR})",
            403: "no CAN USE on this SQL warehouse for the token's user",
            404: "warehouse_id unknown to this workspace, or the Statement "
            "Execution API is disabled",
        }
        if response.status_code >= 400:
            hint = hints.get(response.status_code)
            raise PullError(
                f"HTTP {response.status_code} while {doing}"
                + (f" — {hint}" if hint else "")
                + f": {response.text[:500]}"
            )
        return response.json()


def _hint(error: dict[str, Any]) -> str:
    message = str(error.get("message", "")).lower()
    if "inline" in message and ("limit" in message or "mib" in message):
        return " — one page is too wide: lower pull.page_rows"
    if "'all'" in message or " all'" in message:
        return (
            " — this runtime has no ORDER BY ALL: set `key` on the table "
            "so the pull pages by key"
        )
    if "describe history" in message or "delta" in message:
        return " — not a Delta table? set `versioned: false` on it"
    return ""


def _duckdb_type(column: Column, table: str) -> str:
    base = column.type_name.split("(")[0].strip().upper()
    if base not in TYPE_MAP:
        raise PullError(
            f"{table}.{column.name}: no DuckDB mapping for warehouse type "
            f"{column.type_name!r} ({column.type_text}) — extend TYPE_MAP "
            f"deliberately."
        )
    return TYPE_MAP[base]


def _source(config: PullConfig, table: PullTable, version: int | None) -> str:
    fq = f"{_bq(config.catalog)}.{_bq(config.schema_name)}.{_bq(table.name)}"
    return fq + (f" VERSION AS OF {version}" if version is not None else "")


def _where(table: PullTable) -> str:
    return f" WHERE ({table.where})" if table.where else ""


def _history_statement(config: PullConfig, table: PullTable) -> str:
    return f"DESCRIBE HISTORY {_source(config, table, None)} LIMIT 1"


def _count_statement(config: PullConfig, table: PullTable, version: int | None) -> str:
    return f"SELECT COUNT(*) AS n FROM {_source(config, table, version)}{_where(table)}"


def _page_statement(
    config: PullConfig,
    table: PullTable,
    version: int | None,
    *,
    after_key: bool,
    offset: int,
) -> str:
    source = _source(config, table, version) + _where(table)
    if table.key:
        key_clause = ""
        if after_key:
            key_clause = (" AND " if table.where else " WHERE ") + (
                f"{_bq(table.key)} > :last_key"
            )
        return (
            f"SELECT * FROM {source}{key_clause} ORDER BY {_bq(table.key)} "
            f"LIMIT {config.page_rows}"
        )
    return (
        f"SELECT * FROM {source} ORDER BY ALL LIMIT {config.page_rows} "
        f"OFFSET {offset}"
    )


def plan_statements(config: PullConfig) -> list[str]:
    """What `engine pull --dry-run` prints: the statements the pull
    would issue per table, with placeholders where a value comes from
    the warehouse. No network, no credentials."""
    lines = []
    for table in config.tables:
        lines.append(f"-- {table.name}")
        version = "<version>" if table.versioned else None
        if table.versioned:
            lines.append(_history_statement(config, table))
        placeholder = PullTable(
            name=table.name, key=table.key, versioned=table.versioned, where=table.where
        )
        source_version: Any = version
        lines.append(_count_statement(config, placeholder, source_version))
        lines.append(
            _page_statement(config, placeholder, source_version, after_key=False, offset=0)
        )
        if table.key:
            lines.append(
                _page_statement(config, placeholder, source_version, after_key=True, offset=0)
                + "   -- :last_key = the previous page's last key"
            )
        else:
            lines.append(
                _page_statement(
                    config, placeholder, source_version, after_key=False,
                    offset=config.page_rows,
                )
                + "   -- and so on"
            )
    return lines


def _column_index(columns: list[Column], name: str, table: str) -> int:
    for index, column in enumerate(columns):
        if column.name == name:
            return index
    raise PullError(
        f"{table}: the warehouse returned no column named {name!r} "
        f"(columns: {[c.name for c in columns]})"
    )


def _pull_table(
    warehouse: _Warehouse,
    config: PullConfig,
    table: PullTable,
    target: duckdb.DuckDBPyConnection,
    status,
) -> PulledTable:
    version: int | None = None
    if table.versioned:
        columns, rows = warehouse.execute(_history_statement(config, table))
        if not rows:
            raise PullError(f"{table.name}: DESCRIBE HISTORY returned no rows.")
        version = int(rows[0][_column_index(columns, "version", table.name)])

    columns, rows = warehouse.execute(_count_statement(config, table, version))
    expected = int(rows[0][_column_index(columns, "n", table.name)])

    landed = 0
    created = False
    last_key: Any = None
    key_type_text = "STRING"
    offset = 0
    insert = ""
    while True:
        statement = _page_statement(
            config, table, version, after_key=last_key is not None, offset=offset
        )
        parameters = None
        if last_key is not None:
            parameters = [
                {"name": "last_key", "value": str(last_key), "type": key_type_text}
            ]
        columns, rows = warehouse.execute(statement, parameters)
        if not created:
            types = [_duckdb_type(column, table.name) for column in columns]
            target.execute(
                f"CREATE TABLE {_dq(table.name)} (\n  "
                + ",\n  ".join(
                    f"{_dq(column.name)} {duck}" for column, duck in zip(columns, types)
                )
                + "\n)"
            )
            insert = (
                f"INSERT INTO {_dq(table.name)} VALUES ("
                + ", ".join(f"CAST(? AS {duck})" for duck in types)
                + ")"
            )
            created = True
        if rows:
            target.executemany(insert, rows)
        landed += len(rows)
        status(f"  {table.name}: {landed:,} rows so far")
        if len(rows) < config.page_rows:
            break
        if table.key:
            key_index = _column_index(columns, table.key, table.name)
            last_key = rows[-1][key_index]
            key_type_text = columns[key_index].type_text
        else:
            offset += config.page_rows

    if landed != expected:
        raise PullError(
            f"{table.name}: landed {landed:,} rows but the count said "
            f"{expected:,} — the table moved during the pull (unversioned?) "
            f"or a page was cut short; re-run, or set `versioned: true`."
        )
    return PulledTable(name=table.name, rows=landed, version=version)


def pull(
    config: PullConfig,
    duckdb_path: Path,
    *,
    host: str,
    token: str,
    status=lambda line: None,
) -> PullOutcome:
    """Copy every configured table into a fresh DuckDB file (the pack's
    database is a regenerated artifact, never hand-tended) and return
    the manifest and per-table counts. The caller stores the manifest."""
    if duckdb_path.exists():
        duckdb_path.unlink()
    duckdb_path.parent.mkdir(parents=True, exist_ok=True)

    pulled: list[PulledTable] = []
    with _client_factory(host, token) as client:
        warehouse = _Warehouse(client, config)
        target = duckdb.connect(str(duckdb_path))
        try:
            for table in config.tables:
                status(f"{table.name}: pulling")
                pulled.append(_pull_table(warehouse, config, table, target, status))
        finally:
            target.close()

    snapshot = f"{config.catalog}.{config.schema_name}|" + ",".join(
        f"{t.name}@{t.version if t.version is not None else 'unversioned'}"
        for t in sorted(pulled, key=lambda t: t.name)
    )
    manifest = build_manifest(
        "databricks_pull",
        GENERATOR_VERSION,
        source_tables=[t.name for t in pulled],
        source_snapshot=snapshot,
    )
    return PullOutcome(manifest=manifest, tables=pulled)
