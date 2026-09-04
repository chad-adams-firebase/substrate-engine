# substrate-engine

A domain-agnostic engine that answers plain-English questions grounded
in an application's data and logic, and lets users crowdsource
knowledge back. Configured per application by an **instance pack**
(config + data), never by code changes. The spec is
`docs/technical-build-brief-v2.md`; the delivery plan is
`docs/phasing.md`; working conventions are `CLAUDE.md`.

## Running the tests

```
uv run pytest
```

Green offline: no network, no external clone required. Generator tests
run against the vendored snapshot in
`tests/fixtures/invoiceguard_snapshot/` (pinned by commit SHA and
simulation seed in its `fixture_manifest.json`).

## Building the InvoiceGuard pack from scratch

The reference target application lives in its own repo. From this
repo's parent directory:

```
git clone https://github.com/chad-adams-firebase/invoice-guard.git
cd invoice-guard
git checkout 761a18e9b9253870d930f1b13b3a852ce516d603   # the pack's pinned SHA
uv sync
uv run invoiceguard simulate --seed 42                   # writes simout/
cd ../substrate-engine
```

Then the three pack-build steps (each emits or refreshes a manifest
carrying the (commit SHA, simulation seed) pinning pair):

```
uv run engine convert  --pack packs/invoiceguard   # SQLite -> app.duckdb
uv run engine generate --pack packs/invoiceguard   # all machine substrates
uv run engine validate --pack packs/invoiceguard   # conformance report
```

`engine generate --check` regenerates to a scratch directory and
byte-compares against the pack's committed substrates — the
idempotency probe. `engine info --pack <dir>` shows what a pack
enables and which adapter each port resolved to.

The port table is in the Brief (§3). One row carries a contract a new
adapter is most likely to get wrong: **LLMPort** sends the router loop's
working messages as native tool messages — an assistant message with
`tool_calls`, then exactly one `role="tool"` message per call, in
order, each carrying the provider's call `id` — and a provider that
rejects that sequence fails on the first multi-step turn, not the
first. The smoke test for a new adapter is a two-tool question, never
"hello".

Machine-generated substrates under `packs/*/substrates/` are committed
but never hand-edited; if output looks wrong, fix the generator
(CLAUDE.md). Human knowledge goes in `packs/*/overlays/` and is never
touched by regeneration.

## Web layer

```
uv run engine serve --pack packs/invoiceguard      # http://127.0.0.1:5000
```

`engine serve` composes the same session `engine ask` uses (load
pack → build ports → build tools → build harness) and hands the
resolved objects to a thin Flask shell (`src/engine/web/`). Routes
contain no engine logic and never import adapters; every answer path
is `AskSession.ask()`, i.e. the Verifier's path. Branding, accent
color, and starter prompts come from the pack's `ui:` block — the
page never knows which pack it serves.

**SSE contract.** `POST /api/ask` with `{"question": str,
"conversation_id": int | null, "workspace_id": int | null}` returns
`text/event-stream`. `workspace_id` places a new conversation (the
sidebar's current workspace; omitted, the owner's scratch workspace);
with a `conversation_id` it is ignored. Before the stream starts, a
bad body is `400`, an unknown conversation or workspace `404`, and a
turn already running `409` (one turn per process; the session refuses
to interleave). No row is created before the turn runs, and a turn
that raises deletes the conversation it opened, so a failed first
turn leaves no orphan. Frames are `event:` + one JSON `data:` line:

| event | data | when |
|---|---|---|
| `status` | `StatusEvent` — `{node, phase, detail, at}` | every graph node start/finish, live |
| `result` | `{exit_code, result: TurnResult}` — the same JSON `engine ask --json` prints | terminal, once, after verification |
| `error` | `{message}` | terminal, once, if the turn raised |
| `: keepalive` | (comment) | after 15 s of silence |

No answer text is ever streamed before its verdict exists (Brief
§9.2, §10.2 as amended in v2.2). The browser reads the stream with
`fetch` (POST) and a small frame parser in `static/app.js`.

**Workspaces, conversations, receipts** (`src/engine/web/routes_work.py`,
Phase 5 Block 3). Every workspace a route touches belongs to the
current user; another user's is `404`, never `403`. The scratch
workspace is created on the first listing.

| route | does |
|---|---|
| `GET /api/workspaces` · `POST {name}` | list (creating scratch on first use) · create |
| `DELETE /api/workspaces/<id>` | `204`; `409` while conversations remain |
| `GET /api/workspaces/<id>/conversations` · `POST {title}` | list · create |
| `PATCH /api/conversations/<id> {title}` · `DELETE` | rename · delete the turns, the bundles only it cites, its checkpoint thread (`409` under a running turn) |
| `GET /api/conversations/<id>` | the conversation with its turn count (every logged row), the running summary and the turn it reaches, and the pack's nudge threshold — one fetch for the banner (Block 4) |
| `GET /api/conversations/<id>/turns` | every logged turn: question, outcome, verdict, status events, `evidence_bundle_ref` — the shapes the terminal frame carries; `?format=text` renders the transcript through `web/render.py` |
| `GET /api/evidence/<ref>` | the bundle JSON as stored, fetched by the inspector on demand |

The turn log carries the question and the outcome since Block 3
(`turn_log.question`, `turn_log.outcome`); an older `work.db` gains
the two columns in place on the next `ensure_schema`, and its rows
read back with an empty question and no outcome.

**The chip** reads the status trail, not `tools_used`: a bounced
`run_sql` and its English retry are `1 tool · 1 retry`, an errored
call nothing followed is `1 failed`; `web/render.py:chip_label` and
`app.js` apply the one rule.

**Cell rendering.** `Table.column_formats` carries a per-column hint
that run_sql resolves from the pack, and the CLI, the eval grader's
answer text, prose placeholder injection, and the browser all render
it by one rule (`src/engine/harness/render.py`):

- `{"kind": "money", "symbol": "$"}` — from the Dictionary Map's
  `column_formats` list of money columns plus `display.money` in
  `config.yaml` (symbol, alias glob patterns, the marker tokens that
  veto an alias such as `opportunity_pct`). Renders `$8,308.92`,
  never a float tail.
- `{"kind": "duration", "unit": "days"}` — from `display.duration`
  alone: alias globs per unit (`days`, `hours`, `minutes`, `seconds`)
  for numeric cells, plus `clock` for aliases whose cells are
  `H:MM:SS` strings, which carry their own unit. Renders in the
  largest unit filled, one decimal: `1.1 days`, `1 hour`, `30
  minutes`.
- `{"kind": "rate", "scale": "fraction"}` — from `display.rate` alone:
  alias globs per scale (`fraction` cells are 0–1 and show ×100,
  `percent` cells were already multiplied by 100). Renders one
  decimal: `92.2%`. The Verifier's rate bounds and saturation checks
  read the same hint, so a percent column is bounded on 0–100 and a
  percent column whose values all sit at or below 1.0 loses its badge
  (a fraction written into a `_pct` alias).
- NULL cells render as an em dash, never blank; a zero-row table
  renders "No rows matched" instead of an empty box.

Hints resolve from the statement before the alias
(`src/engine/tools/sql_select.py`): `AVG(invoices.opportunity)`
inherits its source column's format, `extended_price - amount` is
money, a CTE column resolves through the CTE's own select item, and
the alias's spelling decides only what the parse cannot classify.

Rounding is the browser's: the exact double, half-up, so the engine
and `app.js` print identical digits on every value.

**Values, not passages.** A placeholder that resolves to a whole
description, a document snippet, or a block of source is a passage.
Under the pack's `harness.inline_value_max_chars` (anything longer on
one line, or anything multi-line), a passage resolves only inside a
fenced code block; anywhere else the draft is retried with feedback
naming the rule, and when the retries run out the passage ships as
written rather than costing the answer. The drafter prompt states the
rule and the fenced-code convention (a language label, a quoted
function starting at its `def` line).

**Fail-closed cards.** A refusal's `reason` and `what_would_work` are
plain language for the person who asked; the engineer's diagnosis
(which bound tripped, by how much) is `RefuseOutcome.detail`, which
the CLI prints and no card renders — the inspector shows it, labelled
as the diagnosis. `src/engine/web/render.py` renders every outcome
kind as text exactly as the page shows it, for tests and for the text
form of the turns endpoint.

**Inspector** (Brief §10.4; the right pane). Clicking a turn's chip
shows its receipts, none of which the transcript shows: the SQL
attempt ledger — each attempt's fan-out, enum and interval challenges
and whether it was blocked, executed, or executed as an override (the
licensed resend the Verifier warns on); the result table under the
same `column_formats`; CKG nodes, edges and conditionals; source under
a language label; the verdict claim by claim, the final draft marked
by each claim's status from its char offsets, and every plausibility
finding by check name and severity; the refusal's diagnosis; the
progress trail with per-step durations and, on a protocol violation,
the raw router text. The bundle is fetched when the chip is clicked.
This is the per-turn slice of what `engine eval exposure` computes
over a whole report — recorded at the time, never recomputed. Package
mode is a disabled tab until Phase 6.

**Frontend.** Vanilla JS + vendored marked.js and highlight.js (the
common-languages build), pinned in
`src/engine/web/static/vendor/VERSIONS.md` with their licenses beside
them. No build pipeline, no framework, no CDN, no browser storage: a
reload opens the first workspace's empty state with the starter
prompts. The JS is pinned by source text in `tests/test_web_static.py`
and `tests/test_web_render.py`; nothing in the suite executes it.

**Past conversations written before the turn log kept questions and
outcomes** (rows from before Phase 5 Block 3) still carry their
trail, verdict and evidence ref, so the transcript draws their chip
from the trail's finalize event and the verdict's disposition, and
the inspector opens on them; only the outcome card reads "(outcome
not recorded)". Their questions are recovered once from the
conversation's checkpoint history:

```
uv run engine store backfill-questions --pack packs/invoiceguard [--dry-run]
```

The verb reads the checkpoint history in either layout — today's
`HistoryTurn` records, or the user/assistant message pairs a store
written before Phase 5 Block 4 holds, which upgrade on read (the
committed fixture under `tests/fixtures/legacy_work_db/` is such a
store, and `tests/test_harness_legacy_store.py` continues a
conversation from it).

**Context management** (Brief §10.3; `harness.context` in pack
config: `last_n_turns`, `summary_refresh_after_turns`,
`nudge_after_turns`). The router's system message carries the running
summary of the turns it no longer sees, and every turn newer than the
summary follows verbatim as user/assistant pairs — never fewer than
`last_n_turns`, never more than N+K−1: a `summarize` node after
`finalize` folds the turns past the window into the summary once
`summary_refresh_after_turns` of them have accumulated (one LLM call
at temperature 0, synchronous inside the turn, evented as "Updating
conversation summary…"). The summary cites turns by number and never
restates a figure: one regeneration names what the reply restated,
then `engine.harness.summary.scrub_figures` replaces any figure from a
folded prose answer with "(see turn N)" and blanks a cited turn the
summary could not have seen to "(an earlier turn)" — a table turn
contributes its caption line and no figures, and numbers the user
typed are allowed. A failed refresh keeps the previous summary and
says so on the trail. `TurnResult.summary` carries the summary the
turn ended with; `GET /api/conversations/<id>` answers with the turn
count (every logged row), the summary, and the nudge threshold, and
the page draws a dismissible banner past it (in-page state only — it
returns on reopen) and shows the summary in the inspector. A bank row
may name its own `context:` block, applied to its session for its
turns only; MT4 runs under a one-turn window so the summarizer prompt
is measured live, with `summary_contains` and
`summary_excludes_figures` assertions reading the summary a turn ends
with.

**Evidence bundles are owner-scoped.** `GET /api/evidence/<ref>`
answers only when a conversation in the caller's workspaces logged a
turn referencing the ref; otherwise 404, like every other route.
