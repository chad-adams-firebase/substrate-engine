# Phasing Appendix — Claude Code Delivery Plan

Feed phases to Claude Code one at a time, each with the Brief + CLAUDE.md + the phase's "done looks like." Do not hand over the whole system at once. Review each phase against the Brief before starting the next; bring the output to the design conversation for review.

Prerequisite (before Phase 1): Python 3.12 available on the Mac; `uv init --app --package --no-pin-python --no-managed-python --build-backend hatchling` succeeds; repo created with `git init -b main`, `.gitattributes` (`* text=auto eol=lf`), CLAUDE.md at root.

---

## Phase 1 — Skeleton: ports, config, pack loader

Build: all port interfaces (§3), pydantic config models, the DI wiring (adapters selected from config at startup), the pack loader (reads a pack directory, validates config.yaml, reports enabled substrates/tools), and local adapters for LLMPort (OpenRouter), SqlPort (SQLite/DuckDB), WorkStore (SQLite), IdentityPort (fake user), SourceCodePort (local directory). Plus the pytest LLM stub.

**Done looks like:** `uv run pytest` green on port + loader unit tests; a stub pack directory loads and the app starts with every adapter resolved from config; swapping an adapter name in config.yaml changes what's instantiated with no code edit; no port imports any adapter.

## Phase 2 — Generators, conformance validator, fixtures

Build: the three generators (dictionary via SqlPort introspection, univariate stats, CKG via stdlib `ast` — nodes, edges incl. reads/writes, conditionals, constants, L1 membership proposals, L0 reference validation); manifests; the conformance validator; generator fixtures with checked-in expected output, built on a vendored snapshot of a small InvoiceGuard subset with its source SHA recorded in the fixture manifest (Brief §15). This phase does NOT build the InvoiceGuard codebase or a seed generator — the application and its simulation driver live in the external invoice-guard repo.

**Done looks like:** running the generators against the pinned invoice-guard clone + its simulated database produces the pack's machine-derived substrates from scratch; regenerating is idempotent (content-addressed IDs stable across runs); fixture tests (vendored snapshot) catch a deliberately introduced extractor bug; the validator passes on InvoiceGuard and produces a legible report; human-overlay rows survive a regeneration untouched.

Note: every manifest records the (source commit SHA, simulation seed) pinning pair — the Phase 4b eval harness depends on that pair to recompute ground truth against exactly the world the substrates were extracted from.

## Phase 3 — Substrates + tools

Build: SubstrateStore local adapter over the generated substrates; the full tool registry (§6): stats, dictionary, CKG traversal, read_source, run_sql with the execute–check–repair loop (§7), primer, business-docs search, check_execution against structured log files from InvoiceGuard simulation runs, answer_from_known_items (stub against empty library).

**Done looks like:** each tool exercised by unit tests against the InvoiceGuard pack; run_sql answers "how many invoices were flagged last week" with correct grounded SQL; CKG traversal answers "what does the scoring entry point call, in order" (the entry-point and rule function names come from InvoiceGuard's functional spec, being authored now in that project; exact names will be substituted when this phase's prompt is written); read_source returns the exact lines for the analogous rule function named in that spec; evidence bundles retained per invocation.

## Phase 4 — Harness + Verifier

Build: the LangGraph graph (router with altitude classification, tool nodes, verifier node, fail-closed exits, checkpointer via WorkStore); status-event emission; the Verifier per §9 — claim extraction, mechanical matching, LLM fuzzy judge, verdict ladder (retry → unverified → refuse), per-substrate checks, plausibility vs stats.

**Done looks like:** end-to-end ask→answer against InvoiceGuard through real OpenRouter; "what is this app" routes to primer, never CKG; verifier tests pass — a deliberately corrupted draft (wrong number) is caught, retried, then labeled unverified; a deliberately wrong SQL result trips the plausibility check; every turn's provenance row is complete.

## Phase 4b — Answer-verification eval harness

Build: a question bank — planted-story questions plus a majority of non-planted questions — with gold answers computed by direct SQL/code inspection and stored outside any engine-readable path; a runner that executes the bank through the real ask→answer path and emits per-question reports (drafted answer, evidence bundle, verifier verdict) from turn provenance; grading happens externally against recomputed ground truth. Refusals are acceptable outcomes; **wrong-but-verified is the critical failure class**.

**Done looks like:** the runner exists; the bank has ≥15 questions; a report generates end-to-end.

## Phase 5 — Chat UI

Build: Flask routes + SSE streaming (status trail + tokens, collapsing chip); three-pane layout; workspaces/conversations CRUD; context management (summary + last-N, turn-reference summaries, nudge banner); response envelope rendering (markdown/table/chart/code); starter prompts; fail-closed cards; evidence inspector panel.

**Done looks like:** a human can hold a real multi-turn conversation against InvoiceGuard in a browser; the progress trail streams live and collapses; clicking a chip shows SQL, rows, and verdict in the inspector; a 30-turn conversation stays coherent and the summary contains turn references, not figures.

## Phase 6 — Crowdsourcing layer

Build: Package flow (proposed turn selection → editable draft → §9.5 publication verification → publish); shared library (browse, read, comment); human gate (canonical toggle); answer_from_known_items over the real library with suggestion-not-redirect; staleness flagging against substrate versions.

**Done looks like:** the full demo arc runs: explore a planted story (the 90%-flagged item code) → package → edit → publish → a second user finds it in the library, comments, and a repeat question surfaces it as a suggestion; a Unit with a corrupted summary is blocked from publishing; regenerating the CKG flags Units grounded on the old manifest.

---

After Phase 6: work-side checklist (Brief §18), target-app pack authoring per the runbook, boss demo on real data.
