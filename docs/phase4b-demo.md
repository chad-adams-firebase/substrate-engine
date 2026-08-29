# Phase 4b demo — running and grading the eval bank

Two commands, two machines, mirroring the conformance validator's
travel-back pattern: `engine eval run` executes bank rows through the
real ask path on the machine with `OPENROUTER_API_KEY` (the Mac);
`engine eval grade` replays the resulting report fully offline —
no LLM egress anywhere in it — executing every gold script fresh
against the world. The report JSONL is the artifact that travels;
the rendered grade is what a human reads.

The bank lives at `evals/invoiceguard/` — outside `packs/`, so no
engine tool can ever read its own exam. Expected-fail rows carry the
gate verdict's opening backlog (N9–N12, O1, clarify-open, WBV-*) with
root-cause notes; they flip to expected-pass by deleting their
`xfail` block when a fix lands, a deliberate reviewed bank edit.

## Prerequisites

Same world as the Phase 4 demo: invoice-guard beside this repo at
`761a18e9`, seed-42 simulation output, the pack's DuckDB built
(`engine convert`, manifest `ac4b8abd4eb9c07e`), `engine validate`
PASS. `OPENROUTER_API_KEY` is needed by `eval run` only — never by
`eval grade`.

## First: audit the bank itself

```sh
# Execute every gold script against the world and compare to the
# committed expectations. Detects bank rot before any money is spent.
uv run engine eval grade --bank evals/invoiceguard --check-gold
# expect: every row "ok", RESULT: PASS, exit 0
```

## Run (Mac, live LLM)

```sh
# Smoke slice first: three rows, two reps each — single-digit cents.
uv run engine eval run --bank evals/invoiceguard \
  --out evals/invoiceguard/reports/smoke.jsonl \
  --rows B5,B6,REC-SQL --runs 2
# expect: one "[ROW rep i/N] exit E …" line per rep on stderr, the
# report path on stdout. Ctrl-C freely: each completed rep is already
# fsynced.

# Continue an interrupted sweep — only missing (row, rep) keys run:
uv run engine eval run --bank evals/invoiceguard \
  --out evals/invoiceguard/reports/smoke.jsonl \
  --rows B5,B6,REC-SQL --runs 2 --resume
# expect: refused if the engine SHA, world manifests, model, bank, or
# eval config changed since the report's header — a continuation must
# not measure a different system.

# The full bank at the configured default (5 reps × 45 rows):
uv run engine eval run --bank evals/invoiceguard \
  --out evals/invoiceguard/reports/$(date +%F)-baseline.jsonl
```

## Grade (anywhere, offline)

```sh
uv run engine eval grade --bank evals/invoiceguard \
  --report evals/invoiceguard/reports/smoke.jsonl \
  --out evals/invoiceguard/reports/smoke-grade.txt
echo $?
# Exit codes: 0 pass · 1 error · 2 threshold failures · 3 bank rot ·
# 4 wrong-but-verified INVARIANT BREACH (dominates everything).
```

## Reading the grade

- **The INVARIANT line comes first.** Any rep that exited 0 while a
  content assertion failed (wrong number, wrong name, wall-clock SQL
  window, forbidden content) is a wrong-but-verified occurrence —
  the failure class the whole Phase 4 architecture exists to prevent.
  One occurrence fails the grade at exit 4 regardless of every
  threshold, xfail, and sentinel. Zero such occurrences across three
  human sessions is the record this harness now defends.
- **`[XFAIL]` rows are the opening backlog behaving as documented**
  (C1/C1b → N10, B4 → N9, P-N11 → N11, HN-ERRORS → N12, AMB1/AMB2 →
  clarify-open), plus the 4b baseline's five wrong-but-verified rows
  (S4/S7/C4/MT2/U5 → WBV-*) while fix pass 3 is graded. They do not
  gate — but a breach on any of them still exits 4.
- **`[XPASS]` is good news needing a decision:** the fix appears to
  have landed — delete the row's xfail block in its own commit.
- **Token-stratified notes** name drafting-habit coin-flips: "fails
  exactly when file_paths emitted" is N9 measured instead of masked.
- **Route pairs** must agree on the first-decision tool
  (fires/saves/productive — N6's drift guard); a SPLIT line means
  the routing vocabulary is drifting.
- **`ROT`** means the committed expectation no longer matches the
  executed gold — the bank, not the engine, is on trial; nothing
  else about that row was graded.

## Report-committal policy

Reports are committable travel-back artifacts, but not every scratch
run belongs in history: commit milestone reports deliberately (the
post-4b baseline, pre/post fix-pass pairs), name them meaningfully,
and let ad-hoc runs live uncommitted (see evals/README.md).
