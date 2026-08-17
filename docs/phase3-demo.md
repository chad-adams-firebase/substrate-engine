# Phase 3 demo — poking every tool by hand

Everything here runs through `engine tool`, which loads the pack,
builds its adapters, constructs the closed tool registry, invokes one
tool, and prints its output. Add `--evidence` to any command to see
the full invocation envelope — arguments, status, output, evidence
bundle, substrates read, manifest ids — exactly what Phase 4's
Verifier will consume.

## Prerequisites

- The invoice-guard clone beside this repo, checked out at the pinned
  commit (`761a18e9`), with a seed-42 world simulated:
  `uv run invoiceguard simulate --seed 42` (from the clone; it refuses
  to overwrite an existing `simout/` — point `--out` fresh and move it
  into place to regenerate).
- The pack's DuckDB built: `uv run engine convert --pack packs/invoiceguard`.
- `uv run engine validate --pack packs/invoiceguard` reports PASS.

All commands below run from the repo root. Everything except the
last section is offline.

## The eight offline tools

```sh
# What is this app? (L0 primer + L1 components, never the CKG)
uv run engine tool --pack packs/invoiceguard app_primer

# Column statistics — the status distribution of the 1,990 invoices
uv run engine tool --pack packs/invoiceguard query_univariate_stats \
  --args '{"table": "invoices", "column": "status"}'

# Dictionary + semantic layer — note the adjustment_totals gotcha riding along
uv run engine tool --pack packs/invoiceguard lookup_data_dictionary \
  --args '{"term": "adjustment"}'

# The ordered-calls question, answered from the real CKG:
# what does run_rules call, in order?
uv run engine tool --pack packs/invoiceguard traverse_code_knowledge_graph \
  --args '{"entry": "invoiceguard.spine.rules_engine.run_rules", "hop": "callees"}'

# The exact lines of a rule function at the pinned commit
uv run engine tool --pack packs/invoiceguard read_source \
  --args '{"node": "invoiceguard.spine.rules_engine.rule_rate_variance"}'

# Why does the 15% threshold exist? (business-context memos)
uv run engine tool --pack packs/invoiceguard search_business_docs \
  --args '{"query": "why fifteen percent rate variance"}'

# Did the stale sweep run on 2026-03-11? (from the real 32k-line log)
uv run engine tool --pack packs/invoiceguard check_execution \
  --args '{"component": "stale_sweep",
           "window_start": "2026-03-11T00:00:00+00:00",
           "window_end": "2026-03-12T00:00:00+00:00"}'

# The planted benchmark outage: 30 WARNING fallbacks on day 10
uv run engine tool --pack packs/invoiceguard check_execution --evidence \
  --args '{"component": "benchmark_scoring", "mode": "recent_errors",
           "window_start": "2026-03-11T00:00:00+00:00",
           "window_end": "2026-03-12T00:00:00+00:00"}'

# The published-analyses library (legitimately empty until Phase 6)
uv run engine tool --pack packs/invoiceguard answer_from_known_items \
  --args '{"query": "flag rate"}'
```

## run_sql — the acceptance moment

This one needs a real LLM (`OPENROUTER_API_KEY` in the environment);
the automated tests exercise the repair loop with the scripted stub,
so **this command is the first and only place real-LLM SQL generation
runs — it is the phase's acceptance gate. Run it, then read the SQL
the model wrote** (in `--evidence` output, alongside every failed
attempt and the grounding prompt it was given):

```sh
uv run engine tool --pack packs/invoiceguard run_sql --evidence \
  --args '{"question": "How many invoices received last week had findings?"}'
```

This is the first time the system answers a question you type,
end to end: grounding assembled from the dictionary, the Dictionary
Map (metrics, join paths, gotchas — including the data-coverage note
that anchors "last week" to the simulated world's end), and the
univariate stats; generation at temperature 0; execution read-only
through DuckDB; on error, the message goes back to the model and it
retries (bounded by `tool_settings.run_sql.max_repair_attempts`).

Ground truth to check the answer against, from the planted world:
**146** invoices received in 2026-05-23..29 had at least one finding
(of 161 received). If the model anchors "last week" to today instead
of the data, the gotcha failed — that is worth knowing, so read the
SQL, not just the number.
