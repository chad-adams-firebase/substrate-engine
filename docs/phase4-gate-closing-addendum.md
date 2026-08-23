# Phase 4 Gate-Closing Addendum — Fix-Pass Re-Run Verdict

**Session:** 2026-08-23 (Chad on Mac; Claude re-verifying in sandbox). Companion to `docs/phase4-acceptance-carryback.md`.
**Pins:** substrate-engine `3a796a4` (8 fix commits past `c83043b`) · invoice-guard `761a18e` · seed 42 · DuckDB manifest `ac4b8abd4eb9c07e` — **byte-identical to the acceptance session across the entire fix pass** · 353 tests green both sides.
**Model:** `openai/gpt-4o`, committed pack default (no local config edits this session).
**Runs:** 23 live turns — 13 scripted re-runs/controls + 10 user-authored free-play probes (marked U#), including the engine's first-ever multi-turn exchange.

---

## 1. Per-item verdict table (before → after)

| Item | Before (c83043b) | After (3a796a4) | Notes |
|---|---|---|---|
| L0 "What is this application?" | ✅ | ✅ | No regression; zero nudges (was 1) |
| L1 "How does invoice scoring work?" | ⚠️ content ✓ / verdict false-pos | ❌ exit 2 | **Original #1 mechanisms all fixed and verified working**; two NEW bugs (N1 root-scope, N2 word-number) now cause the failure — and N1 degraded the prose itself (verbatim evidence dump) |
| L2 ordered callees | ✅ | ❌ exit 2 — **regression** | Content perfect 16/16 vs gold; draft now cites line numbers; injected line values face double jeopardy against a corpus that never harvests `edge.line` (N3) |
| L3 source of rule_rate_variance | ❌ mini / ✅ 4o | **SPLIT** | Qualified name: ✅ 2-step, byte-faithful, exit 0. Bare name: ❌ exit 3 — exact-match `resolve_node`, no suffix steering, router surrendered after 1 recoverable error with 7 budget steps left (N4, N5) |
| Data flagship (146) | ✅ | ✅ | 146, window `[05-23, 05-30)`, **alias mandate live** (`invoice_count`), table envelope |
| Fail-closed (fire) | ✅ (2 nudges) | ✅ (0 nudges, refused step 1) | Hardened prompt's good face |
| Provenance / #3c | ✅ (gap: no placeholder names) | ✅ | Failure names now printed in every affected trail — and that logging is what exposed N1 |
| C1 "stale sweep May 29?" (yearless) | ❌ (2023 hallucination) | ❌ exit 2 | **Window now anchors 2026 (#3a fix confirmed at its original trigger)** — but router surrendered after the component-name stumble without retrying (N5) |
| C1b (year supplied) | ❌ | ❌ exit 3 | Router recovered; window correct; evidence contains the gold log line, `ran:true` — killed solely by N1 (`{{e1.output.run_status.count}}`: rename adopted ✓, prefix wrong ✗). Table fallback correctly declined (no table-shaped evidence) |
| C4 "last 30 days" (692) | ❌ refused w/ correct evidence | ✅ exit 0 | Cleanest flip of the day: 692, data-anchored, aliased, table envelope chosen up front |

Discretionary (all first-time runs): S3 "who closes most" ✅ **nova 390**, role-filter trap avoided · S4 gotcha "silent mismatches" ✅ **114**, self-named alias `count_of_silent_adjustments` · NP3 READY backlog ✅ **78 / $8,308.92**, multi-aggregate clean. **run_sql finished 5-for-5 scripted + 5-for-6 user-authored.**

## 2. The three carryback discrepancies

| Discrepancy | Status | Evidence |
|---|---|---|
| **#1 Verifier false positives** | **CLOSED** | Every original mechanism verified working in the L1 verdict: hyphenated IDs extract whole and match (`ig.spine.invoice-parse` et al., all matched_exact); backticked names match via quote corpus with structured refs (`` `Rules engine` `` → `e0.components[3].name`); judge consulted (`judge_calls: 10`, was 0). Residual L1 failures are N1/N2, not #1. 1c (entities to judge) remains deliberately unimplemented and the evidence supports that call |
| **#2 Model floor** | **CLOSED, with successor concern** | gpt-4o committed; L3-with-qualified-name 2-steps clean; routing quality visibly higher throughout. Successor: N5 early-surrender (below) |
| **#3a check_execution date grounding** | **CLOSED** | Yearless "May 29" → window `2026-05-29T00:00:00Z..23:59:59Z` (was 2023); confirmed on both phrasings from tool-input arguments |
| **#3b placeholder/envelope** | **PARTIAL** | Shipped and working: `run_status` rename (adopted by the model unprompted), alias mandate (grounding prompt carries it; every aggregate today aliased), deterministic table fallback (fired correctly in C1b, correctly declined non-table evidence), table envelopes for data answers (C4 flip). Still broken: N1 root-scope prefix — the previously-invisible bug that #3c's logging unmasked. check_execution remains **0-for-5 lifetime** but every remaining symptom reduces to N1 alone |
| **#3c placeholder logging** | **CLOSED** | Names in every trail; directly enabled N1's diagnosis |

## 3. New anomalies (all root-caused from provenance + code)

- **N1 — Placeholder root-scope mismatch (highest leverage).** `render_evidence` shows the drafter `{"index":0,...,"output":{...}}`; the resolver navigates from *inside* `output`. The model, believing the JSON over the prompt's (correct) examples, writes `{{e0.output.path}}`; the extra segment fails every resolution. Proof in isolation: C1b's `{{e1.output.run_status.count}}` — right index, right renamed field, right leaf, one spurious prefix. Consequences: L1's three failed drafts and verbatim-dump degradation (model pasted evidence inline after giving up on placeholders, including primer YAML front-matter — which then generated 3 of L1's 6 claim failures); C1/C1b death; check_execution's lifetime zero. **Fix: accept/strip a leading `output.` in `_navigate`, or render evidence flattened to match the examples. One line either way; predicted to flip L1, C1b, and check_execution green.**
- **N2 — Word-number corpus gap.** "twelve" (in the primer verbatim; 12 rules exist) extracts as a numeric claim but the value corpus holds only digit-parsed values (`len(edges)` counts exist, but the traversal in conv 2's route didn't provide the 12-edge set; the judge, shown values only, honestly said no). Blocks L1 even on a clean draft. Fix: parse number-words into claims' comparable values, or treat prose-sourced number-words as quote-matchable against text.
- **N3 — Injected-value double jeopardy + harvest gap (the L2 regression).** All 16 line numbers were `injected: true` — copied from evidence *by code*, faithfulness guaranteed by construction — then prosecuted against a corpus that never harvests `edge.line` (CkgCheck harvests node vocab, docstring numerics, conditionals, counts; edges contribute only `target_table`). Judge budget (10) exhausted after 5 honest NOs. **Design fix (preferred): spans marked injected are pre-verified with their resolution ref; the verifier audits only model-typed spans. Mechanical fix: harvest `edge.line` + node line ranges.** Either flips L2.
- **N4 — Bare-name resolution gap.** `resolve_node` is exact-match (id | qualified name); a bare `rule_rate_variance` — the most human phrasing — dead-ends with no near-miss hint. Internal inconsistency: the verifier's CKG check accepts `dotted_suffixes()` as valid vocabulary, so the engine's verifier recognizes names its tool can't resolve. Fix: unambiguous-suffix resolution (this name matches exactly one node) or an error naming near-misses.
- **N5 — Stochastic early surrender (successor to #2).** The hardened protocol prompt bought the violation collapse (§4) at the cost of retry-after-recoverable-error: conv 8 answered empty-handed after one bad component name; conv 9, same stumble, retried and recovered; conv 4 refused with 7/8 budget unspent. Same model, same prompt, opposite choices. Fix direction: protocol prompt licenses exactly one retry after a tool error that names valid alternatives; steering errors (which the fix pass began) help.
- **N6 — Routing vocabulary gap (user-discovered, U5/U6).** "Which rule produces the most savings / is most productive at finding opportunity" → routed to `answer_from_known_items` + `search_business_docs`, never `run_sql` — twice. The question is a `SUM(amount) GROUP BY rule_name` (gold: **quantity_spike, $730,751 raw / $610,769 effective after excepted findings**). Business-flavored synonyms ("savings", "productive") miss the SQL route while "fires the most" hits it. The Dictionary Map's concept aliases ("opportunity aka recovery opportunity, dollars on the table") don't inform routing. Fix: render metric/concept aliases into the router prompt. Silver lining: first live `answer_from_known_items` calls — the stub behaves correctly (9/9 tools now exercised).
- **N7 — Verified-shrug outcome shape (U6, conv 8).** "The evidence does not provide…" passed verification (no claims) and exited **0**; its twin refused with exit 3. A content-free answer reporting success is the worst shape in the taxonomy and outcome shape is currently stochastic. Fix: an answer asserting evidence-insufficiency should route to refuse/clarify, never exit 0.
- **N8 — LangGraph msgpack deprecation.** Checkpoint deserialization warns on unregistered `engine.*` types; harmless now, **breaking in a future LangGraph version**. Register `allowed_msgpack_modules` before it becomes a mystery outage.

**Persisting observations:** router still leaks hallucinated SQL into run_sql's `question` (2-for-2 reproduced; defense-in-depth regenerated correctly both times — contract tightening still recommended). "Who" questions can return raw ids (U7: correct **nova/390** substance delivered as `reviewer_id 7`) — join to names for person-shaped answers. Clarify (exit 4) has never fired in either session, including U3 where "go unreviewed per day" was genuinely ambiguous (engine chose current-backlog 78; never-reviewed LAPSED reading = 679 — verified-correct SQL for a silently-chosen interpretation). Float formatting in money cells (`8308.92139244107`).

## 4. Protocol-violation rate

**5 nudges in 54 router steps ≈ 9%** (baseline ~40%). Every violation recovered. The hardened prompt works; N5 is its over-correction tax.

## 5. Table-MUST watch item

**No offense observed in 23 turns.** Rankings/breakdowns rendered exactly right as tables; the marginal case is 1×1 count tables (C4's lone `692`) — stilted but safe. Verdict: keep MUST for the fix-pass re-run; revisit only if Phase 5's chat surface makes 1×1 tables feel wrong in situ. Softening to "strongly prefer" is not yet evidence-supported.

## 6. User free-play findings (first genuine user contact)

Ten unscripted probes produced five findings scripted testing missed (N6, N7, id-vs-name, meta-question gap, ambiguity/clarify gap) and one major de-risk: **multi-turn context works** — conv 22 turn 2 resolved "how many of those" against turn 1 via the checkpointer, correct per-month breakdown (**674/682/634**), verified. §10.3's machinery is live in Phase 4; Phase 5 only needs to wrap UI around it. Chad's design notes, endorsed: (a) capability discovery ("what can I ask you?") needs a conversational path — §10.5 starter prompts cover the UI empty state but not the asked-in-chat case; (b) drill-down context management — confirmed working, surface it.

**Grader's correction:** one gold figure this session (monthly breakdown) was initially asserted by the grader without executing a query, and was wrong; the engine was right (674/682/634 vs invented 675/660/655). Caught and corrected by running the SQL. All other gold figures in this addendum and the carryback were re-audited: each traces to an executed query, traversal, or grep. Recorded deliberately — it is the project's thesis demonstrated on its own referee, and 4b's design answer to it is below.

## 7. Cost & latency

Comparable to acceptance: clean turns 6–10 s; conv 2 (three drafts + two verifies) worst at ~18 s. ~23 live turns, single-digit cents each, well under $1 total.

## 8. Recommendation

**The gate stays open for one more targeted pass — a short one.** The three carryback discrepancies are substantially closed (#1, #2, #3a, #3c fully; #3b partially), and every fix that shipped demonstrably works. The new list exists largely because the fix pass's own strictness and logging unmasked the stratum beneath: N1 was invisible until #3c printed placeholder names; N3 exists because injection now works well enough to out-run the harvest.

Priority order for the pass:
1. **N1** (one line; predicted to flip L1 prose, C1b, and check_execution's lifetime zero simultaneously)
2. **N3** (pre-verify injected spans — architecturally the right reading of §9.4: code-injected values are faithful by construction; flips L2)
3. **N2** (word-number comparability; L1's independent blocker)
4. **N7** (evidence-insufficiency answers must not exit 0)
5. **N4 + N5** (suffix resolution; one-licensed-retry protocol language)
6. Hygiene: N8 msgpack registration; N6 metric aliases into router grounding; name-joins for who-questions; SQL-shaped-question rejection at run_sql's boundary.

Re-run after: the seven demo items, the C1 pair, and U5 ("most savings"). Predicted all-green including L1 and L2 at exit 0 and check_execution's first-ever delivered answer.

## 9. Fold into Phase 4b eval design

- **Assert outcome shape, not just content:** expected exit code AND answer non-emptiness (N7's verified shrug passes any content-only check).
- **Add the ten user-authored rows to the bank** (marked user-sourced) — they found five things 40 scripted runs missed. Reserve a free-play segment in every future human session.
- **Ambiguity rows expecting exit 4** (U3's "go unreviewed": backlog-78 vs lapsed-679) — clarify has never fired; test whether it can.
- **Routing-vocabulary synonym pairs** (fires-most vs saves-most) asserting identical routes.
- **Answer-shape checks:** person-questions return names; money renders as currency.
- **Multi-turn rows** (anchor → pronoun follow-up) now that continuation is proven.
- **Stochasticity accounting:** N5 and prose-vs-table selection vary run to run — 4b needs N-run repetition per question with pass-rate thresholds, not single-shot grading.
- **Harness rule from the grader's own failure:** every gold answer in 4b must be produced by executed code committed beside the expectation — never transcribed, never remembered. The referee is subject to §9.4 too.
