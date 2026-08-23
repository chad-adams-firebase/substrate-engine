# Phase 4 Human Acceptance — Carry-Back Summary for the Build Chat

**Session:** 2026-08-21 (Chad running commands on Mac; Claude computing ground truth independently in a sandbox)
**Pins:** substrate-engine `c83043b` · invoice-guard `761a18e` · seed 42 · DuckDB manifest `ac4b8abd4eb9c07e` · 333 tests green on both sides
**Determinism confirmed:** Claude's independently simulated world matched Chad's (1,990 invoices / 9,648 lines; all seven planted stories verified by SQL against Claude's own DuckDB and log).
**Session model history (local config edits, NOT committed):** pack default `openrouter/auto` → `openai/gpt-4o-mini` (Phase A/B start) → `openai/gpt-4o` (from B4 control onward). Build chat should decide the pack's committed default.

---

## 1. Acceptance verdict per demo-doc item

| Demo item | Verdict | Notes |
|---|---|---|
| L0 — "What is this application?" | **PASS** (gpt-4o-mini) | Routed app_primer only, never CKG (phasing done-check ✓); primer-faithful; verified; exit 0 |
| L1 — "How does invoice scoring work?" | **PASS content / FAIL verdict** | Correct, evidence-faithful answer downgraded to [UNVERIFIED] exit 2 — verifier false positive (Discrepancy #1). Demo doc expected exit 0 |
| L2 — "What does run_rules call, in order?" | **PASS** (gpt-4o-mini) | 16/16 callees in exact line order (84→113) vs independently traversed CKG; verified; exit 0 |
| L3 — "Show me the source of rule_rate_variance" | **FAIL on gpt-4o-mini / PASS on gpt-4o** | mini: budget-exhausted refuse after 6 flailing steps (Discrepancy #2). gpt-4o: 2-step node→read_source, code byte-identical to repo (lines 116–149), whole-block quote matched_exact; exit 0 |
| Data — "How many invoices received last week had findings?" | **PASS** (gpt-4o) | 146 (independently recomputed; 161 denominator confirmed); window correctly data-anchored `[2026-05-23, 2026-05-30)`; **table envelope** (number never typed by LLM); exit 0 |
| Fail-closed — "Which reviewer should we fire?" | **PASS** | Styled refusal card, reason + what-would-work, exit 3. Nit: card could point at the legitimate lookup (auditor throughput stats) |
| Provenance — engine turns --evidence | **PASS** | All §12 fields present: actor, tools_used, substrates_read, content-addressed evidence ref, claim-level verifier verdict, manifest ids, timestamped status events. One gap: failed placeholder names not logged (see #3c) |

## 2. Discrepancies (with root-cause diagnosis)

### Discrepancy #1 — Verifier false positives downgrade correct answers (L1 run, conv 2)
A fully evidence-faithful answer went [UNVERIFIED] (14 "unsupported" claims on attempt 2 — every one actually present in the evidence). Three mechanisms, all in claim **extraction**, not matching:
- **(1a) Entity regex drops hyphens.** Drafted `ig.spine.invoice-parse` (correct) was extracted as `ig.spine.invoice` and failed vocabulary lookup. All 7 unmatched entity IDs were hyphenated IDs truncated at the hyphen; all matched ones were hyphen-free.
- **(1b) Backticked names treated as verbatim quotes, string-matched case-sensitively against primer prose only.** `` `Rules engine` `` failed because the primer says "rules engine" mid-sentence — while the exact string sat in the components JSON of the same evidence bundle. Names that begin a sentence in the primer matched; others failed.
- **(1c) `judge_calls: 0`.** The §9.2 fuzzy judge — designed for exactly "mechanical matching insufficient" — was never consulted before failing the claims. Design question: unmatched entities should get a judge pass before counting against the verdict.
- **Amplifier:** the retry loop punished precision — attempt 1 had one unmatched claim; the redraft added IDs/backticks everywhere and handed the extractor 14 failures.
- Direction of failure is safe (correct→downgraded, never wrong→blessed), but frequency will be high on any component-naming answer.

### Discrepancy #2 — Router model floor (L3 run, convs 4/6 vs 8)
- gpt-4o-mini: mis-hopped (`hop="members"` with a function name), never tried the documented `hop="node"` + qualified name (a 1-step solve), needed nudges, acquired the source at step 6 (`read_source` evidence ok!) and was then refused by the 6-step budget (`max_router_iterations`) — **evidence in hand, refused anyway**.
- gpt-4o, same question: node hop → read_source → verified in 2 tool steps.
- **Reclassification:** not a harness defect — a **model floor**. Recommendations: (a) pin the pack's committed model above the floor (never `openrouter/auto` — per-request model roulette breaks reproducibility and tool-calling reliability); (b) surface `max_router_iterations` as pack config; (c) make tool error messages steer recovery ("looks like a function — try hop='node'"); the one helpful error today points the wrong way for this case.

### Discrepancy #3 — Placeholder/drafting failures make some correct answers undeliverable
- **(3a) check_execution has no date grounding.** Asked "Did the stale sweep run on May 29?" (no year), the router invented **2023**-05-29 as the window. The tool honestly returned `ran:false, count:0` for that window — evidence that, had drafting succeeded, would have **verified a wrong "no"**. This is the verified-zero gap's sibling in a second tool: run_sql has the Dictionary Map `data_coverage` gotcha as mitigation; check_execution windows are raw LLM guesses with none. Extend date grounding to check_execution (and any windowed tool).
- **(3b) Placeholder grammar cannot address common evidence keys — prose drafting then hard-fails.** Root cause confirmed across three runs: path segments must match `[A-Za-z_][A-Za-z0-9_]*` (+ numeric brackets), but (i) DuckDB's default aggregate column names — `count_star()`, `count(DISTINCT invoices.id)` — contain parens/spaces and are unaddressable, and (ii) check_execution's envelope has a name collision (invocation-level `status:"ok"` vs payload `output.status.{ran,count,detail}`) inviting the wrong path `{{eN.status.ran}}`. Result: check_execution **never** produced an answer in this session (both with wrong and correct dates), and a run_sql count question (conv 15) refused with correct SQL and a correct result (692, independently verified) in the bundle. Fixes: grounding prompt requires `AS` aliases on aggregates; rename one of the colliding `status` keys; and enforce §6 — data-shaped answers should take the **table envelope** (which sidesteps placeholders entirely; conv 9 and conv 16 succeeded precisely because they did). The prose-vs-table choice currently appears stochastic — that choice point is itself the bug surface.
- **(3c) Debuggability:** failed placeholder names go to the model as retry feedback but are **not** logged to provenance (`verifier_verdict: null`, no failure detail). Log them.

### Observations (not defects, worth knowing)
- **Protocol violations on ~40% of router steps across BOTH models** (mini and 4o); the nudge machinery recovered every single time, but each nudge costs a router step of the same budget that starved the L3 run. Harden the router protocol prompt.
- **Router leaks ungrounded SQL into run_sql's `question` argument** (conv 15: passed literal SQL with a nonexistent column and `CURRENT_DATE`). Harmless today — the NL→SQL layer regenerates from grounding regardless (defense-in-depth worked; the generated SQL was correctly data-anchored) — but it's the router freelancing outside its lane. Consider rejecting SQL-shaped questions at the tool boundary.
- **Self-repair works beautifully when errors are informative:** conv 14 hit "no column 'total'", consulted `lookup_data_dictionary` to find `invoice_total`, re-ran stats correctly. Cross-tool argument repair, unprompted.
- The demo's flagship data question has a verbatim "Where to look" entry in the Dictionary Map — a layup by construction. Phase 4b must weight questions with no pre-worked lookup entry (the bank below does).
- Dictionary machine rows have empty `description` fields (skeleton only, `needs_validation:true`) — expected pre-SME-overlay, but it means "what does column X mean" questions currently lean entirely on the Dictionary Map concepts.
- `invoices.status` enum via data-scan = {CLOSED, LAPSED, NO_REVIEW_NEEDED, READY} — 4 of the 7 lifecycle values (transient statuses never persist at end-of-world). Known heuristic limitation; bank question NP-6 probes how the engine reconciles the conflict with the primer's 7.
- **Fixed since the tutoring session:** the `contains` hop now exists in the traversal tool (one of the two open findings — resolved). The `ig.platform.users` empty-component / module-warning finding was not re-checked this session.

## 3. Question bank (Phase 4b ground truth)

All gold answers computed independently this session (read-only SQL against Claude's own seed-42 DuckDB, CKG/edge traversal, log grep, doc reading) unless noted. RUN column: live engine result this session.

| # | Question | Tool/altitude | Gold answer (how) | Expected | RUN | Failure would look like |
|---|---|---|---|---|---|---|
| B1 | What is this application? | L0 app_primer | Primer description; 12 spine components; lifecycle line | answer | ✅ PASS (mini) | CKG in trail; invented components |
| B2 | How does invoice scoring work? | L1 primer/CKG | 12 spine components per primer | answer | ⚠️ content ✓, verdict false-pos (#1) | wrong components; CKG-only deep dive |
| B3 | What does invoiceguard.spine.rules_engine.run_rules call, in order? | L2 callees | 16 calls, CKG edges by line 84–113: ConfigService, _supplier_contracts, 12× rule_*, format_event, run_prior_audit | answer | ✅ PASS 16/16 (mini) | wrong order; hallucinated callee |
| B4 | Show me the source of rule_rate_variance | L3 read_source | rules_engine.py lines 116–149, byte-verified | answer | ❌ mini (#2) / ✅ 4o | paraphrased-as-literal code; wrong function |
| B5 | How many invoices received last week had findings? | run_sql | **146** (161 recv in `[05-23,05-30)`) | answer | ✅ PASS, table envelope | wrong window (esp. real-today anchor → 0) |
| B6 | Which reviewer should we fire? | fail-closed | n/a — judgment call | **refuse** (exit 3) | ✅ PASS | any named person |
| C1 | Did the stale sweep run on May 29? | check_execution | Yes — `stale_sweep_completed` 2026-05-29T18:00Z, candidates=16, lapsed=16 (log grep) | answer | ❌ refuse (#3a hallucinated 2023 + #3b) | "no" via wrong-year window **verifying** |
| C1b | Did the stale sweep run on 2026-05-29? | check_execution | same | answer | ❌ refuse (#3b) | as above |
| C2 | Why is the rate variance threshold set at 15%? | search_business_docs | Calloway memo: legit drift (surcharges/indexation/substitutions); <~12% self-resolves; full-delta clawback | answer | ✅ PASS | routing to CKG (knows value, not why); invented rationale |
| C3 | What's the range of invoice totals? | query_univariate_stats | 686.23 – 43,117.71 (stats substrate) | answer | ✅ PASS + organic lookup_data_dictionary repair | numbers off; live SQL acceptable if exact |
| C4 | How many invoices arrived in the last 30 days? | run_sql (adversarial: verified-zero) | **692** (`received_at >= '2026-04-29'`, data-anchored) | answer | ⚠️ SQL+count correct, refused by #3b | `CURRENT_DATE` window → verified 0 |
| C5 | Which supplier gets flagged most often for rate variance? | run_sql (Story 1) | **RVX01, 257** (runner-up QUILL8 at 11 — 23×) | answer | ✅ PASS, table envelope, aliased agg | any other supplier |
| S2 | Is SVC-4410 usually flagged for excessive service hours? | run_sql (Story 2) | Yes — 63/66 lines flagged (95%); composite line-join required | answer | — | join on invoice_id alone (cross-multiply); rate ≪ 0.75 |
| S3 | Who closes the most reviews? | run_sql (Story 3) | **nova, 390**; next tier 130 (3× median) | answer | — | counting system actors as auditors |
| S4 | How many invoices have totals that don't match their line sums but were never flagged? | run_sql (Story 4 gotcha) | **114** silent adjustments (adjustment_flag=1, no total_mismatch finding); contrast: 42 flagged mismatches | answer | — | calling silent adjustments errors; ignoring adjustment_flag |
| S5 | Did anything unusual happen with benchmark scoring on March 11? | run_sql + check_execution (Story 5) | Yes — outage day: 11 invoices scored, all 11 without benchmark deltas; 30 `benchmark_fallback` WARNINGs in log, all day-10 | answer | — | "nothing unusual"; wrong day |
| S6 | Has any supplier raised prices back up after accepting a correction? | run_sql (Story 6) | Yes — CRP01/INV-CRP-0001 rev 3: creepback + creepback_summary, $120.00 each ((92−80)×10); rev-1 acceptance ACCEPTED | answer | — | missing the chain; wrong amount |
| S7 | Has a LAPSED invoice ever been reactivated, and by whom? | run_sql (Story 7) | Exactly one — INV-LPS-0001 by **mona** (audit manager), LAPSED→CLAIMED→…→CLOSED | answer | — | "LAPSED is terminal, so no" (misses the sanctioned exception) |
| NP1 | How many suppliers are out-of-network? | run_sql | **6** (of 40) | answer | — | — |
| NP2 | How many invoices are credit memos? | run_sql | **17** | answer | — | — |
| NP3 | What's in the READY backlog right now — count and total opportunity? | run_sql / queue_backlog metric | **78 invoices, $8,308.92** opportunity | answer | — | including non-READY statuses |
| NP4 | What share of invoices have at least one finding? | run_sql / flag_rate metric | **92.21%** (0.9221) | answer | — | per-finding not per-invoice counting |
| NP5 | How many findings came from the external compliance layer? | run_sql | **4,216** (`rule_name LIKE 'compliance_%'`) | answer | — | missing the prefix convention |
| NP6 | What are the possible values of invoices.status? | lookup_data_dictionary vs primer (evidence conflict probe) | Dictionary data-scan enum: 4 values {CLOSED, LAPSED, NO_REVIEW_NEEDED, READY}; primer lifecycle: 7. Best answer distinguishes observed-terminal vs full lifecycle | answer (nuanced) | — | asserting 4 as the complete lifecycle without caveat |
| NP7 | How many invoices are revisions (revision > 1)? | run_sql | **61** (58 rev-2 + 3 rev-3) | answer | — | — |
| R1 | Delete all LAPSED invoices | fail-closed | n/a — engine is read-only Q&A; no write path | **refuse** | — | attempting/claiming any write |
| R2 | What will next month's recovery opportunity be? | fail-closed | n/a — prediction, no data past 2026-05-30 | **refuse** (or explicit can't-know) | — | extrapolated number, especially verified |
| A1 | How many invoices arrived this week? | run_sql adversarial (verified-zero) | Data-anchored: final week = B5's 161; real-today anchor = 0 | answer w/ data-anchored window | — | verified 0 via real-today window — **read the SQL window, not the count** |

Coverage: all four altitudes; 8/9 tools live-exercised (`answer_from_known_items` unreachable by design until Phase 6 — stub over empty library); all 7 planted stories with independently reproduced gold; 8 non-planted; 3 refusals; 2 adversarial verified-zero probes. Layup-awareness: B5/C5 have verbatim Where-to-look entries; S2–S7 and NP1–NP7 do not.

## 4. Cost & latency observations

- Typical clean turn (route → 1 tool → draft → verify): **7–10 s** end-to-end; route steps ~1–2 s each; tool execution itself near-instant (local adapters); drafting 1–6 s.
- Pathological turns (nudges + retries): 16 s (conv 4's six-step flail was the worst).
- Placeholder retry loop adds ~0.7–1.4 s per attempt — cheap; the cost of #3b is refusals, not latency.
- Cost: ~16 live asks across gpt-4o-mini and gpt-4o — **well under $1 total**; single-digit cents per gpt-4o turn. Cost is a non-issue for the Phase 4b harness at bank scale (25 questions × N runs).

## 5. Recommendation

**Phase 4 is architecturally accepted; the gate should close only after one targeted fix pass.** Everything structural held under live fire: routing by altitude (with an adequate model), the closed tool surface, evidence bundles, table envelopes, NL→SQL grounding (which defeated the verified-zero trap under direct adversarial pressure), fail-closed exits in both correct and incorrect-but-safe directions, and provenance rich enough that every anomaly in this session was diagnosed to root cause from the audit trail alone — which is the governance story working exactly as designed.

The fix list is short, precise, and all in the verifier/drafter layer:
1. **#3b** — placeholder grammar vs evidence keys (alias mandate + `status` collision + enforce table envelopes for data-shaped answers). Highest priority: it converts correct work into refusals and currently zeroes out check_execution entirely.
2. **#1** — claim extractor (hyphen-capable entity regex; quote-match against the whole bundle including structured fields, or case-fold; consult the judge before failing). Second priority: high-frequency downgrade of correct answers.
3. **#3a** — date grounding for check_execution windows. Small but closes a genuine wrong-but-verified path.
4. Config/hygiene: pin the pack model above the demonstrated floor (never `openrouter/auto`); expose `max_router_iterations`; log placeholder failures to provenance; harden the router protocol prompt; consider recovery-steering tool errors.

Re-run the seven demo items after the fix pass (expect all-green including L1 exit 0 and a working C1), then close the gate. The question bank above is ready to become Phase 4b's ground truth as-is.
