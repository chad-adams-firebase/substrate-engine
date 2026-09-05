"""A fake SQL Statement Execution API for tests — no network. It
answers the statement shapes `engine pull` issues from in-memory
tables, in the API's own JSON (states, polling, result chunks,
JSON_ARRAY strings), so the pull is exercised end to end against the
wire format it meets at work. Knobs reproduce the documented failure
modes. Test plumbing, like tests/stubs/llm_stub.py: never engine code."""

import json
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2

STATEMENTS = "/api/2.0/sql/statements"

_FQ = r"`(?P<cat>[^`]+)`\.`(?P<sch>[^`]+)`\.`(?P<tbl>[^`]+)`"
_HISTORY = re.compile(rf"DESCRIBE HISTORY {_FQ} LIMIT 1")
_COUNT = re.compile(
    rf"SELECT COUNT\(\*\) AS n FROM {_FQ}"
    r"(?: VERSION AS OF (?P<ver>\d+))?(?: WHERE \((?P<where>.*)\))?"
)
_PAGE = re.compile(
    rf"SELECT \* FROM {_FQ}"
    r"(?: VERSION AS OF (?P<ver>\d+))?"
    r"(?: WHERE \((?P<where>.*?)\))?"
    r"(?: (?:WHERE|AND) `(?P<key>[^`]+)` > :last_key)?"
    r" ORDER BY (?:`(?P<okey>[^`]+)`|(?P<all>ALL))"
    r" LIMIT (?P<limit>\d+)(?: OFFSET (?P<offset>\d+))?"
)


@dataclass
class FakeTable:
    columns: list[tuple[str, str, str]]  # (name, type_name, type_text)
    rows: list[list[Any]]  # JSON_ARRAY values: str | None
    version: int | None = 7  # None: DESCRIBE HISTORY fails, as for a view


def _typed(value: Any, type_name: str) -> Any:
    if value is None:
        return None
    if type_name in ("LONG", "INT", "SHORT", "BYTE"):
        return int(value)
    if type_name in ("DOUBLE", "FLOAT", "DECIMAL"):
        return float(value)
    return str(value)


@dataclass
class FakeWarehouse:
    tables: dict[str, FakeTable]
    warehouse_id: str = "wh-1"
    catalog: str = "cat"
    schema: str = "sch"
    token: str = "dapi-test"
    # Rows per result chunk: a page larger than this arrives in several.
    chunk_rows: int = 1000
    # GET polls that answer RUNNING before SUCCEEDED (the API's own
    # wait timed out).
    pending_polls: int = 0
    # A page with more rows than this fails as the 25 MiB inline cap.
    inline_limit_rows: int | None = None
    order_by_all_supported: bool = True
    # WHERE text -> predicate over a name-keyed row (the fake does not
    # parse SQL; a where the test did not register is a syntax error).
    where_filters: dict[str, Callable[[dict[str, Any]], bool]] = field(
        default_factory=dict
    )
    # Added to every COUNT(*): reproduces a table moving mid-pull.
    count_bias: int = 0
    statements: list[str] = field(default_factory=list)
    _results: dict[str, dict[str, Any]] = field(default_factory=dict)
    _polls: dict[str, int] = field(default_factory=dict)

    # -- wiring ---------------------------------------------------------

    def client_factory(self, host: str, token: str) -> httpx2.Client:
        """Drop-in for pull_databricks._client_factory: the real token
        flows and is checked, the transport is this fake."""
        base = host if "://" in host else "https://" + host
        return httpx2.Client(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            transport=httpx2.MockTransport(self.handler),
        )

    @classmethod
    def from_sqlite(cls, path: Path, **kwargs) -> "FakeWarehouse":
        """Serve a SQLite database's tables as a warehouse would: typed
        columns, every value a string, booleans as true/false,
        timestamps in the API's ISO form. `keys` names each table's
        single-column primary key, for the pull config."""
        mapping = {
            "INTEGER": ("LONG", "BIGINT"),
            "BIGINT": ("LONG", "BIGINT"),
            "VARCHAR": ("STRING", "STRING"),
            "TEXT": ("STRING", "STRING"),
            "FLOAT": ("DOUBLE", "DOUBLE"),
            "REAL": ("DOUBLE", "DOUBLE"),
            "DOUBLE": ("DOUBLE", "DOUBLE"),
            "NUMERIC": ("DOUBLE", "DOUBLE"),
            "BOOLEAN": ("BOOLEAN", "BOOLEAN"),
            "DATETIME": ("TIMESTAMP", "TIMESTAMP"),
            "DATE": ("DATE", "DATE"),
            "JSON": ("STRING", "STRING"),
            "BLOB": ("BINARY", "BINARY"),
        }
        connection = sqlite3.connect(str(path))
        connection.row_factory = sqlite3.Row
        tables: dict[str, FakeTable] = {}
        keys: dict[str, str | None] = {}
        try:
            names = [
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            for name in names:
                info = connection.execute(f"PRAGMA table_info({name})").fetchall()
                columns = []
                for column in info:
                    declared = column["type"].split("(")[0].strip().upper()
                    type_name, type_text = mapping[declared]
                    columns.append((column["name"], type_name, type_text))
                pk = [column["name"] for column in info if column["pk"] > 0]
                keys[name] = pk[0] if len(pk) == 1 else None
                rows = []
                for record in connection.execute(f"SELECT * FROM {name}"):
                    values = []
                    for column_name, type_name, _ in columns:
                        value = record[column_name]
                        if value is None:
                            values.append(None)
                        elif type_name == "BOOLEAN":
                            values.append("true" if value else "false")
                        elif type_name == "TIMESTAMP":
                            text = str(value).replace(" ", "T")
                            values.append(text if text.endswith("Z") else text + "Z")
                        else:
                            values.append(str(value))
                    rows.append(values)
                tables[name] = FakeTable(columns=columns, rows=rows)
        finally:
            connection.close()
        warehouse = cls(tables, **kwargs)
        warehouse.keys = keys  # type: ignore[attr-defined]
        return warehouse

    # -- the API --------------------------------------------------------

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx2.Response(
                401, json={"error_code": "PERMISSION_DENIED", "message": "Invalid access token."}
            )
        path = request.url.path
        if request.method == "POST" and path == STATEMENTS:
            body = json.loads(request.content)
            if body.get("warehouse_id") != self.warehouse_id:
                return httpx2.Response(
                    404,
                    json={
                        "error_code": "RESOURCE_DOES_NOT_EXIST",
                        "message": f"SQL warehouse {body.get('warehouse_id')} does not exist.",
                    },
                )
            statement = body["statement"]
            self.statements.append(statement)
            statement_id = f"stmt-{len(self.statements)}"
            self._results[statement_id] = self._run(statement, body.get("parameters") or [])
            if self.pending_polls:
                self._polls[statement_id] = self.pending_polls
                return httpx2.Response(
                    200, json={"statement_id": statement_id, "status": {"state": "PENDING"}}
                )
            return httpx2.Response(200, json=self._payload(statement_id))
        match = re.fullmatch(STATEMENTS + r"/(stmt-\d+)", path)
        if request.method == "GET" and match:
            statement_id = match.group(1)
            if self._polls.get(statement_id, 0) > 0:
                self._polls[statement_id] -= 1
                return httpx2.Response(
                    200, json={"statement_id": statement_id, "status": {"state": "RUNNING"}}
                )
            return httpx2.Response(200, json=self._payload(statement_id))
        match = re.fullmatch(STATEMENTS + r"/(stmt-\d+)/result/chunks/(\d+)", path)
        if request.method == "GET" and match:
            return httpx2.Response(
                200, json=self._chunk(match.group(1), int(match.group(2)))
            )
        return httpx2.Response(404, json={"error_code": "NOT_FOUND", "message": path})

    def _payload(self, statement_id: str) -> dict[str, Any]:
        result = self._results[statement_id]
        if "error" in result:
            code, message = result["error"]
            return {
                "statement_id": statement_id,
                "status": {"state": "FAILED", "error": {"error_code": code, "message": message}},
            }
        rows = result["rows"]
        chunk_count = max(1, -(-len(rows) // self.chunk_rows))
        return {
            "statement_id": statement_id,
            "status": {"state": "SUCCEEDED"},
            "manifest": {
                "format": "JSON_ARRAY",
                "schema": {
                    "column_count": len(result["columns"]),
                    "columns": [
                        {"name": name, "type_name": type_name, "type_text": type_text, "position": i}
                        for i, (name, type_name, type_text) in enumerate(result["columns"])
                    ],
                },
                "total_chunk_count": chunk_count,
                "total_row_count": len(rows),
                "truncated": False,
            },
            "result": self._chunk(statement_id, 0),
        }

    def _chunk(self, statement_id: str, index: int) -> dict[str, Any]:
        rows = self._results[statement_id]["rows"]
        start = index * self.chunk_rows
        piece = rows[start : start + self.chunk_rows]
        chunk: dict[str, Any] = {
            "chunk_index": index,
            "row_offset": start,
            "row_count": len(piece),
            "data_array": piece,
        }
        if start + self.chunk_rows < len(rows):
            chunk["next_chunk_index"] = index + 1
        return chunk

    # -- the statements -------------------------------------------------

    def _resolve(self, match: re.Match) -> FakeTable | tuple[str, str]:
        name = match.group("tbl")
        if (
            match.group("cat") != self.catalog
            or match.group("sch") != self.schema
            or name not in self.tables
        ):
            return (
                "TABLE_OR_VIEW_NOT_FOUND",
                f"[TABLE_OR_VIEW_NOT_FOUND] The table or view "
                f"`{match.group('cat')}`.`{match.group('sch')}`.`{name}` cannot be found.",
            )
        return self.tables[name]

    def _rows(self, table: FakeTable, match: re.Match) -> list[dict[str, Any]] | tuple[str, str]:
        version = match.group("ver")
        if version is not None and table.version != int(version):
            return ("DELTA_VERSION_NOT_FOUND", f"Cannot time travel to version {version}.")
        names = [name for name, _, _ in table.columns]
        rows = [dict(zip(names, row)) for row in table.rows]
        where = match.group("where")
        if where is not None:
            if where not in self.where_filters:
                return ("PARSE_SYNTAX_ERROR", f"[PARSE_SYNTAX_ERROR] Syntax error in WHERE ({where}).")
            rows = [row for row in rows if self.where_filters[where](row)]
        return rows

    def _run(self, statement: str, parameters: list[dict[str, Any]]) -> dict[str, Any]:
        match = _HISTORY.fullmatch(statement)
        if match:
            table = self._resolve(match)
            if isinstance(table, tuple):
                return {"error": table}
            if table.version is None:
                return {
                    "error": (
                        "DELTA_TABLE_NOT_FOUND",
                        "DESCRIBE HISTORY is only supported for Delta tables.",
                    )
                }
            return {
                "columns": [("version", "LONG", "BIGINT"), ("timestamp", "TIMESTAMP", "TIMESTAMP")],
                "rows": [[str(table.version), "2026-09-05T00:00:00.000Z"]],
            }
        match = _COUNT.fullmatch(statement)
        if match:
            table = self._resolve(match)
            if isinstance(table, tuple):
                return {"error": table}
            rows = self._rows(table, match)
            if isinstance(rows, tuple):
                return {"error": rows}
            return {"columns": [("n", "LONG", "BIGINT")], "rows": [[str(len(rows) + self.count_bias)]]}
        match = _PAGE.fullmatch(statement)
        if match:
            table = self._resolve(match)
            if isinstance(table, tuple):
                return {"error": table}
            rows = self._rows(table, match)
            if isinstance(rows, tuple):
                return {"error": rows}
            types = {name: type_name for name, type_name, _ in table.columns}
            key = match.group("okey")
            if key:
                if match.group("key"):
                    (parameter,) = parameters
                    last = _typed(parameter["value"], types[key])
                    rows = [row for row in rows if _typed(row[key], types[key]) > last]
                rows.sort(key=lambda row: _typed(row[key], types[key]))
                page = rows[: int(match.group("limit"))]
            else:
                if not self.order_by_all_supported:
                    return {
                        "error": (
                            "PARSE_SYNTAX_ERROR",
                            "[PARSE_SYNTAX_ERROR] Syntax error at or near 'ALL'.",
                        )
                    }
                rows.sort(key=lambda row: tuple((v is None, str(v)) for v in row.values()))
                offset = int(match.group("offset") or 0)
                page = rows[offset : offset + int(match.group("limit"))]
            if self.inline_limit_rows is not None and len(page) > self.inline_limit_rows:
                return {
                    "error": (
                        "INVALID_STATE",
                        "The statement result exceeds the inline limit of 25 MiB; "
                        "use the EXTERNAL_LINKS disposition.",
                    )
                }
            names = [name for name, _, _ in table.columns]
            return {"columns": table.columns, "rows": [[row[name] for name in names] for row in page]}
        return {"error": ("PARSE_SYNTAX_ERROR", f"[PARSE_SYNTAX_ERROR] Unrecognized statement: {statement}")}
