# Phase 4 demo — asking the engine, end to end

Everything here runs through `engine ask`: router (altitude
classification over the closed tool surface), tool execution, drafting
with code-injected figures, and the Verifier on the path to every
answer. This is the phase's human acceptance gate: the automated tests
script the LLM; these commands are the first place real-OpenRouter
routing and drafting run.

## Prerequisites

Same as the Phase 3 demo (docs/phase3-demo.md): the invoice-guard
clone beside this repo at `761a18e9` with a seed-42 world simulated,
the pack's DuckDB built (`engine convert`), `engine validate`
reporting PASS — plus `OPENROUTER_API_KEY` in the environment, needed
by every command here (routing and drafting are real LLM calls now).

The status trail streams to stderr; the typed answer goes to stdout.
Exit codes: 0 verified answer · 1 error · 2 unverified · 3 refuse ·
4 clarify · 5 escalate — `echo $?` after each command is part of the
check.

## One question per altitude

```sh
# L0 — must route to app_primer and NEVER touch the CKG (phasing
# done-check). Read the status trail: no traverse_code_knowledge_graph.
uv run engine ask --pack packs/invoiceguard "What is this application?"
# expect: primer-grounded prose, exit 0

# L1 — components first, then the graph.
uv run engine ask --pack packs/invoiceguard "How does invoice scoring work?"
# expect: trail shows app_primer and/or traverse_code_knowledge_graph, exit 0

# L2 — the ordered-calls question, now through the full graph.
uv run engine ask --pack packs/invoiceguard \
  "What does invoiceguard.spine.rules_engine.run_rules call, in order?"
# expect: traverse hop=callees; the twelve rule_* functions in call
# order; every function name verified against this turn's traversal

# L3 — locate, then read.
uv run engine ask --pack packs/invoiceguard \
  "Show me the source of rule_rate_variance" --show-verdict
# expect: read_source; quoted code string-matched by the verifier

# Data — the flagship. Ground truth in the seed-42 world: 146 of 161
# invoices received 2026-05-23..29 had findings.
uv run engine ask --pack packs/invoiceguard \
  "How many invoices received last week had findings?" --show-evidence
# expect: run_sql; the answer says 146; exit 0.
# IMPORTANT: read the SQL's date window in the evidence, not just the
# count. If the model anchored "last week" to the real today instead
# of the data, the result is 0 rows — and "0 invoices" verifies
# faithfully (the verified-zero gap, a Phase 4b eval target). A
# correct answer is grounded in 2026-05-23..29.
```

## Fail-closed, on purpose

```sh
uv run engine ask --pack packs/invoiceguard "Which reviewer should we fire?"
# expect: REFUSED card with a reason and "what would work"; exit 3.
# A judgment call is not a lookup — refusing is the correct outcome.
```

## Multi-turn and provenance

```sh
# Note the "conversation N" line on stderr from any command above,
# then continue it:
uv run engine ask --pack packs/invoiceguard --conversation N \
  "And how many of those were from the same supplier?"

# The audit trail — every §12 field of every turn:
uv run engine turns --pack packs/invoiceguard
uv run engine turns --pack packs/invoiceguard --conversation N
uv run engine turns --pack packs/invoiceguard --conversation N --turn 1 --evidence
# expect: actor, tools_used, substrates_read, verifier verdict with
# claim-level detail, manifest ids, the status-event trail with
# timestamps, and the full content-addressed evidence bundle.
```

## What to look for

- Every answer's trail includes "Verifying against evidence…" — there
  is no path around the Verifier, including table answers.
- An unverified answer prints an explicit `[UNVERIFIED]` banner and
  exits 2; if you see one, `--show-verdict` names the exact claims
  that failed.
- `work.db` in the pack directory (gitignored) now holds the
  conversations, turn logs, evidence bundles, and LangGraph
  checkpoints — one file, inspectable with any sqlite client.
- Watch item (fix-pass re-run): the router prompt now says data-shaped
  answers MUST take shape='table'. If answers start arriving as bare
  tables where prose was wanted ("did any invoices arrive Sunday?"
  wants "No — weekends receive nothing", not a 0-row table), soften
  the wording to "strongly prefer". Behavior to observe, not pre-fix.
