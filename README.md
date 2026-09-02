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
"conversation_id": int | null}` returns `text/event-stream`. Before
the stream starts, a bad body is `400`, an unknown conversation
`404`, and a turn already running `409` (one turn per process; the
session refuses to interleave). Frames are `event:` + one JSON
`data:` line:

| event | data | when |
|---|---|---|
| `status` | `StatusEvent` — `{node, phase, detail, at}` | every graph node start/finish, live |
| `result` | `{exit_code, result: TurnResult}` — the same JSON `engine ask --json` prints | terminal, once, after verification |
| `error` | `{message}` | terminal, once, if the turn raised |
| `: keepalive` | (comment) | after 15 s of silence |

No answer text is ever streamed before its verdict exists (Brief
§9.2, §10.2 as amended in v2.2). The browser reads the stream with
`fetch` (POST) and a small frame parser in `static/app.js`.

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
- NULL cells render as an em dash, never blank.

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
the CLI prints and no card renders — the inspector will.
`src/engine/web/render.py` renders every outcome kind as text exactly
as the page shows it, for tests and for text surfaces that show past
turns.

**Frontend.** Vanilla JS + vendored marked.js and highlight.js (the
common-languages build), pinned in
`src/engine/web/static/vendor/VERSIONS.md` with their licenses beside
them. No build pipeline, no framework, no CDN, no browser storage.
