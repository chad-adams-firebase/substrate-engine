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
