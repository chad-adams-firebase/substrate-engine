# Phase 4 Gate Verdict — Fix-Pass-2 Re-Run (Session 3, Final)

**Session:** 2026-08-23 (Chad on Mac; Claude re-verifying in sandbox). Companion to `docs/phase4-acceptance-carryback.md` and `docs/phase4-gate-closing-addendum.md`.
**Pins:** substrate-engine `ef55477` (11 commits past `3a796a4`) · invoice-guard `761a18e` · seed 42 · DuckDB manifest `ac4b8abd4eb9c07e` — **byte-identical across all three sessions and nineteen engine commits** · 372 tests green.
**Model:** `openai/gpt-4o` (committed). No local config edits; working tree clean throughout.
**Runs:** 16 live turns. Every gold figure in this document traces to an executed query, CKG traversal, or log grep — none transcribed, none remembered (per the session-2 grader's-correction rule).

---

## 1. Per-item verdict across all three sessions (S1 → S2 → S3)

| Item | S1 (c83043b) | S2 (3a796a4) | S3 (ef55477) | S3 notes |
|---|---|---|---|---|
| L0 "What is this application?" | ✅ | ✅ | ✅ | Primer-only route, exit 0, zero nudges |
| L1 "How does invoice scoring work?" | ⚠️ false-pos | ❌ (N1/N2) | ✅ **exit 0 — first ever** | "twelve" survives (N2 ✓); zero placeholder failures in an 8-step 5-tool turn (N1 ✓). New cosmetic observation: text-block injection (§3, O1) |
| L2 ordered callees | ✅ | ❌ regression (N3) | ✅ exit 0 | Textbook verdict: 16 line numbers `matched_injected` with refs `e0.edges[N].line`; 17 entities still fully prosecuted; `judge_calls: 0` (was 10, exhausted) |
| L3 bare name | ❌ | ❌ | ⚠️ exit 2 | **N4 ✓** — bare `rule_rate_variance` resolved in ONE step (was 6-step flail / 1-step surrender). Content byte-faithful incl. correct path + injected line range. Downgraded by NEW N9 (file-path claim) |
| Data flagship (146) | ✅ | ✅ | ✅ | 146, `[05-23, 05-30)`, aliased, table envelope — never wobbled post-model-fix |
| Fail-closed (fire) | ✅ | ✅ | ✅ | Refused at first decision, exit 3 |
| Provenance pull | ✅ | ✅ | ✅ | C1a verdict pulled; `matched_injected` refs and judge reasons all present |
| C1 yearless | ❌ 2023 window | ❌ router surrender | ⚠️ exit 2 | **Correct answer delivered — first in 6 lifetime attempts**: "Yes… 1 event in [2026-05-29…]". Downgraded by NEW N10 (prose "29" as numeric claim). Component resolved first try |
| C1b year supplied | ❌ | ❌ exit 3 | ⚠️ exit 2 | Same: correct "yes", same N10 class. check_execution lifetime: 0-for-7 on exit codes, **2-for-2 on truth today** |
| U5 "most savings" | — | ❌ mis-route | ✅ exit 0 | **N6 ✓ at its only possible live test**: routed `run_sql` first decision; **quantity_spike $610,768.51** — the *effective* figure, gotcha-correct SQL (LEFT JOIN feedback, exceptions zeroed) |
| Who-question (U7 class) | — | ⚠️ id-not-name | ✅ | **nova, 390** — name via `users` join, role-filtered. Grounding mandate confirmed |
| S2 (SVC-4410, first run) | — | — | ✅ | 0.9545 = 63/66 exact; composite join correct; **verified number in prose** |
| NP5 (compliance count, first run) | — | — | ✅ | 4,216 via `LIKE 'compliance_%'` — prefix gotcha learned |
| Multi-turn + N8 | — | ✅ (with warnings) | ✅ | 254 exact; "those" resolved; **zero msgpack warnings** (were nine); SQL-bounce fired live with clean single-retry recovery |

Three-session trajectory in one line: S1 three major discrepancies → S2 eight medium anomalies → S3 four small edges. Each pass closed its list and exposed a strictly smaller stratum beneath.

## 2. The N-ledger (all eight from session 2)

| N | Status | Evidence |
|---|---|---|
| **N1 root-scope** | **CLOSED** | Zero placeholder failures across every prose turn; C1a's count `matched_injected` via `e0.run_status.count`; both `{{e0.output.errors}}` and `{{e0.errors}}` attempted in run 13 (tolerance live; failure there was N12, not roots) |
| **N2 word-numbers** | **CLOSED** | "twelve audit rules" in a verified L1 answer — the exact two-session blocker token |
| **N3 injected double jeopardy** | **CLOSED** | L2 verdict: all 16 lines `matched_injected` with resolution refs; entities still prosecuted; judge spend 10→0. Surgical, not amnesty |
| **N4 bare-name resolution** | **CLOSED** | One-step resolve of `rule_rate_variance`; no error, no surrender |
| **N5 licensed retry** | **CLOSED** | Fired live twice (component-name steering error → exactly one corrected call; SQL-bounce → one corrected call). No over-retry observed anywhere |
| **N6 routing vocabulary** | **CLOSED** | U5 routed `run_sql` first decision — the one fix with no offline assertion, confirmed at its only test site |
| **N7 verified shrug** | **IMPLEMENTED, NOT LIVE-TRIGGERED** | No claim-free insufficiency answer occurred in 16 runs, so the converter never minted; covered by tests (372 green). The feared false-positive edge (honest-no → refusal) DID occur — but via N12, with N7's converter provably uninvolved |
| **N8 msgpack** | **CLOSED (live)** | Checkpoint read-back in run 16: zero deprecation warnings (session 2: nine) |

## 3. New anomalies (fourth stratum — all root-caused)

- **N9 — Delimiter containment + pool reachability for structured strings (L3's exit 2).** The backticked file path failed both ways: model-typed, it's a quote-kind claim shopping in `quote_corpus` (only `output.text` — a file doesn't contain its own path) while the path sits harvested in `vocabulary`; placeholder-injected, the claim span includes the backticks, the injected span doesn't, and `_containing_span` (containment-not-overlap, by design) rejects it — the record's own `injected: true` + `unmatched` combination is the diagnosis. Fixes: trim delimiters before the containment test; let short exact quote claims fall back to vocabulary/strings pools. Fragility signature: S2's qualified-name control passed only because that draft omitted the path.
- **N10 — Date-token numerals (the C1 pair's exit 2s).** Prose "May 29" extracts "29" as a numeric claim; the value pool holds `count=1` and nothing calendar-shaped; the date lives in `run_status.detail` as text, reachable only by quote-matching, which numeric claims never consult. Judge shown values-only honestly says no (2 calls). Fixes: harvest date components from ISO timestamps into the numeric pool, or extract month-name+numeral as a date token matchable against corpus text.
- **N11 — Drafter anchors on errored invocations.** With e0 = component-name error and e1 = clean success, the draft discussed e0 — recited the known-components list, attempted `{{e0.error}}` (unresolvable: errored invocations have `output: None`, and invocation-level fields sit outside the resolvable tree), and shipped "no information" while the answer sat in e1. Exit 2, content-fail — the one non-safe-direction miss of the session (answered-in-evidence, not-in-answer), though honestly labeled UNVERIFIED. Fixes: render errored invocations collapsed in the drafter's view, or instruct drafting from `status: ok` evidence only.
- **N12 — recent_errors envelope can't express a clean day (the honest-negative refusal).** The mode fills only `errors: list`; an empty list is a non-scalar every placeholder legitimately fails to render; no scalar count exists (did_run has `run_status.count`; recent_errors never got its mirror). Drafting exhausts → table fallback correctly finds no table → refuse. Every component per-spec; the composition cannot say "no errors." Fix: one `error_count` scalar field. This — not N7 — is why the watch item's feared outcome occurred.

**Observations (not defects):** (O1) *text-block injection* — N1's fix lets placeholders resolve to whole descriptions/code blocks, which paste inline (L1's lumpy seams, double periods); candidate fix is a length/type guard or a "values, not passages" prompt line; cosmetic until Phase 5 makes prose user-facing. (O2) Router still leaks SQL into run_sql's `question` (3-for-3 lifetime) — but the new bounce converts it from silent defense-in-depth to an explicit contract error with clean licensed-retry recovery; acceptable steady state. (O3) 1×1 count tables remain stilted-but-safe; S2's verified-number-in-prose (run 14) shows the alternative shape now works too.

## 4. Watch items

- **Table-MUST:** no offense in 16 runs; rankings/counts rendered right; run 14 delivered a verified prose answer where prose fit. Keep MUST; softening remains evidence-unsupported.
- **N7 false positives:** the feared outcome (honest-no → refusal) occurred once — mechanism N12, converter uninvolved. Re-test after the N12 fix.
- **N5 over-retry:** none; two live firings, both single, both recovered.

## 5. Violation rate & cost

**7 nudges / 43 router steps ≈ 16%** (S1 ~40%, S2 ~9%). Small samples; treat as a band, not a trend. Cost/latency unchanged: clean turns 6–10 s, ~16 turns, cents total.

## 6. Verdict: CLOSE THE PHASE 4 GATE

The prediction ("all green") formally broke: L3-bare and the C1 pair exited 2. The verdict is close anyway, for reasons the three-session record makes concrete:

1. **Every phasing done-check passes.** End-to-end ask→answer against real OpenRouter across all four altitudes; primer-not-CKG routing; verifier catch/downgrade/refuse behavior proven (and then some); complete per-turn provenance — rich enough that all fifteen anomalies across three sessions were root-caused from the audit trail alone.
2. **All eight session-2 anomalies are closed on live evidence** (N7 implemented, test-covered, unfalsified). The fix passes demonstrably fix what they claim.
3. **The residual class is qualitatively different from what the gate guards.** N9/N10/N12 are verification *recall* gaps — correct answers downgraded — plus one drafting-attention bug (N11). Across 16 runs and three sessions there have been **zero wrong-but-verified outcomes**. The safety invariant the Verifier exists to enforce has never been breached; what remains is its false-downgrade rate.
   *Amended 2026-08-29:* the "zero wrong-but-verified" claim was true of 62 turns and falsified at 225 — nine occurrences across five rows in the 4b baseline (`docs/phase4b-baseline-findings.md`). Faithfulness has never been breached; the breaches were plausibility (wrong-question SQL verifying), and the full invariant awaited fix-pass-3's completion of §9.3.
4. **The right instrument for that rate is Phase 4b, not another hand-run session.** The residuals are stochastic — drafting-habit-dependent (a path stated or omitted, a date phrased in prose or ISO). Regression-protecting them needs N-run repetition with pass-rate thresholds, exactly what 4b builds. A third fix pass graded by hand would spend evenings measuring what the harness should measure.

**Condition of closure:** N9–N12 (plus O1's guard) enter Phase 4b as its opening backlog — first as *expected-fail regression rows with root-cause annotations*, flipped to expected-pass as the small fixes land. The C1 pair, L3-bare, and the run-13 honest-negative probe are the exact row texts.

## 7. Final input for Phase 4b design — the failure modes that most deserve automated rows

Drawn from all fifteen anomalies across three sessions, ranked by regression value:

1. **Verified-wrong sentinels (the invariant).** Rows whose *only* assertion is "never exit 0 with wrong content": the verified-zero window traps (A1, C4), check_execution wrong-date windows, N11-style wrong-evidence anchoring. These guard the moat itself; everything else guards polish.
2. **Drafting-habit stochasticity.** The L2 line numbers, L3 file path, and C1 date tokens all fail *only when the draft happens to state them*. Rows must assert over N repetitions with pass-rate thresholds, and record which optional tokens each draft emitted — otherwise green runs mask coin-flips.
3. **Outcome-shape assertions.** Exit code AND non-empty answer AND (for who-questions) a name, (for money) currency formatting. N7's shrug and the id-not-name answer pass any content-only grader.
4. **Envelope-expressiveness probes per tool mode.** Run 13 proved a tool can work perfectly and be undraftable (N12). One "honest-negative" row per windowed/list-returning tool mode.
5. **Routing-vocabulary synonym pairs** (fires-most / saves-most / most-productive) asserting identical routes — N6's fix is prompt-rendered config and will drift silently as packs change.
6. **Recovery-path rows:** steering error → exactly-one-retry → success (N5's license), and SQL-bounce → English rephrase. Assert the retry count, not just the outcome.
7. **Multi-turn anchor→pronoun rows** (proven live twice) including one crossing a tool switch.
8. **Ambiguity rows expecting exit 4** — clarify has now gone three sessions and 62 live turns without ever firing (U3's dual-reading question is the seed row). Whether it *can* fire is an open empirical question 4b should settle.

The question bank (27 scripted + 10 user-authored rows, all with executed gold) is ready to become 4b ground truth as-is; this session adds runs 11–16 as six more rows including the two N-probe controls.

---

*Housekeeping: no config edits to revert; `work.db` holds 14 conversations of session-3 provenance; the OpenRouter key dies with the terminal window. The world manifest has now survived nineteen engine commits unchanged — determinism is the most-tested law in the codebase, at three-for-three sessions.*
