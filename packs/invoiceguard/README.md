# InvoiceGuard instance pack

The engine-side pack for the InvoiceGuard reference application — a
private engineering fixture, not a demo (Brief §14). The application
itself lives in its own repo (`chad-adams-firebase/invoice-guard`);
this directory holds only configuration, generated substrates, and
pack-authored artifacts. Never application source.

Pinning: everything here is extracted from or authored against the
clone at commit `761a18e9`, simulation seed `42`. The clone is
expected as a sibling checkout (`../../../invoice-guard` from this
directory, per `config.yaml`), simulated via
`uv run invoiceguard simulate --seed 42` (which refuses to overwrite
an existing `simout/` — point `--out` at a fresh directory to
regenerate, then move it into place).

## What lives here

- `config.yaml` — enabled substrates/tools, adapter selections,
  execution-log templates, tool settings.
- `substrates/` — machine-generated (dictionary, stats, CKG,
  memberships + manifests). Always produced by `engine generate`;
  never hand-edited. If output looks wrong, fix the generator.
- `overlays/` — human SME rows, merged in by generators; regeneration
  never touches them.
- `components.yaml`, `primer.md` — pack-authored L1/L0.
- `dictionary_map.yaml` — pack-authored semantic layer; run_sql's
  grounding payload.
- `business_docs/` — snapshots of the app repo's business-context
  memos (see below).
- `app.duckdb` — converted application database (`engine convert`);
  gitignored, rebuilt locally.

## Business docs are snapshots, refreshed deliberately

The three memos under `business_docs/` are copies of
`docs/business-context/*.md` from the invoice-guard repo, taken at the
pinned commit recorded in each file's front matter (`source_repo`,
`source_path`, `source_commit_sha`, `copied_date`). They are not
synced automatically: when the source memos change, re-copy them at a
new pinned SHA and update the front matter — a deliberate act, like a
recarve, so the pack never silently drifts from what its provenance
claims.
