# Real-adapter notes — debugging at work, by chat

The work machine has no Claude Code and no egress. Debugging there is a
chat assistant reading files and a coding assistant running the
investigation the chat assistant writes. These notes are written for that
pair: each entry names the file and the function, the observable, what it
means, and the question to ask. Diagnosis is the goal; the fix is described
in chat, made on the personal machine, and pulled back.

Both adapters carry the same table in their module docstrings, so the file
itself is the first thing to read: `src/engine/adapters/llm_databricks_fm.py`
and `src/engine/packtools/pull_databricks.py`.

## The Foundation Model adapter

**Where.** `src/engine/adapters/llm_databricks_fm.py` — `DatabricksFmLLM`,
`complete` (the request), `_connect` (credentials, timeouts, retries),
`base_url_for` (the host spelling). A complete sibling of
`llm_openrouter.py`; `tests/test_adapter_llm_databricks_fm.py` proves the
two shape one transcript identically, so a difference between the routes is
the provider's, not the adapter's.

**What it needs.** `DATABRICKS_HOST`, `DATABRICKS_TOKEN` in the shell; the
endpoint name in the pack's `llm.settings.model`. Checked at the first
completion, so `engine info` resolves without them.

**Observable → meaning → ask.**
- `RuntimeError` naming a variable → unset in this shell → "does `set` in
  the same shell show it?"
- HTTP 401 → token expired/revoked/other workspace → "when was the PAT
  created, and does the host match the issuing workspace?"
- HTTP 403 → no CAN QUERY on the endpoint → "who owns the endpoint; can they
  grant CAN QUERY?"
- HTTP 404 → endpoint name, or `base_path` → "what exact name does the
  Serving page show?"
- HTTP 400 mentioning tools → the model has no function calling → "which
  endpoints list function calling as supported?"
- HTTP 429 → rate limit after the SDK's two retries → "what is the
  endpoint's limit; who else is querying it?"
- `LLMTimeoutError` → no answer within the timeouts after the SDK's
  retries. About 16 s of wall with zero completed calls is the connect phase
  (the host is unreachable from this network: the SDK's 5 s connect budget,
  three attempts); a long wait is a slow model → "does
  `scripts/fm_smoke.py` answer right now?"
- a TLS/certificate error → a TLS-intercepting proxy; the HTTP stack
  (`httpx2`) uses the OS certificate store through `truststore` → "is the
  corporate root CA in the Windows certificate store; does `curl` to the
  host succeed?"
- the router loop stops after one tool → the FM API returns one tool call
  per response (no parallel calls); the loop handles it → nothing to
  investigate unless a turn ends without an answer, then read the trail
  with `engine turns`.

**Knobs** (pack config, `llm.settings`): `read_timeout_seconds`,
`connect_timeout_seconds` (unset keeps the SDK's 600 s / 5 s),
`max_retries` (the SDK's own; 2). The eval runner, not the demo, replays a
rep once on `LLMTimeoutError`.

## The pull

**Where.** `src/engine/packtools/pull_databricks.py` — `pull` (the file
and the manifest), `_pull_table` (history, count, pages, the CASTs),
`_Warehouse.execute` (submit, poll, chunks), `_Warehouse._checked` (HTTP
errors), `_hint` (the appended fix), `TYPE_MAP`. `engine pull --dry-run`
prints the statements without network.

**Observable → meaning → ask.** The docstring's table; in short:
- HTTP 401/403/404 → token; CAN USE on the warehouse; `warehouse_id` or the
  API disabled → "does the SQL Warehouses page list this id, and can this
  user open a query editor on it?"
- `statement FAILED` with the warehouse's message → read the message; the
  usual ones are the table name, no SELECT, `DESCRIBE HISTORY` on a view
  (`versioned: false`), the 25 MiB inline limit (lower `page_rows`),
  `near 'ALL'` (set `key`).
- `landed N rows but the count said M` → an unversioned table moved
  mid-pull → re-run, or `versioned: true` if it is Delta.
- `no DuckDB mapping for warehouse type` → a type the map does not know →
  "which column, and what does its `type_text` say?" — then extend
  `TYPE_MAP` on the personal machine.
- currency totals differ from the source in the last cents → `DECIMAL` lands
  as `DOUBLE`, by design → not a bug; say so in the demo if asked.

## The environment

- **`uv sync` through the proxy.** Wheels only: `docs/wheel-audit.md` lists
  every package and the wheel it needs; a failure names a package → "is
  that wheel on the proxy's index page for the package?" A hash mismatch
  means the proxy served a different file than the lock records → "does the
  proxy cache an older upload of this version?"
- **Console encoding.** `UnicodeEncodeError` on any `print` → `PYTHONUTF8=1`
  in the shell.
- **Port 5000.** `engine serve` and the target application's own dev server
  both default to it; `--port 5050` for one of them.
- **An open `app.duckdb`.** Windows will not delete an open file:
  `engine convert`/`engine pull` fail with `[WinError 32]` while `engine
  serve` runs. Stop the server first.
- **`work.db` locked.** Two `engine serve` processes on one pack share one
  SQLite file; run one.
- **`sqlite-vec`.** A dependency of the checkpointer with a Windows wheel;
  it loads as a SQLite extension. If Python reports extension loading is
  disabled, the interpreter is not the official build → "which Python does
  `uv run python -c 'import sys; print(sys.executable)'` name?"
- **Proxies.** `HTTPS_PROXY`/`NO_PROXY` are honoured by the HTTP stack; the
  workspace host may need to be in `NO_PROXY` or reached through the proxy,
  whichever the prior application did → "how did the previous project reach
  the workspace host from this machine?"
