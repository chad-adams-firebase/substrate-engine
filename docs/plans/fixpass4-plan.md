# Fix-pass-3 closeout + fix pass 4 — faithfulness-recall residuals (N9–N12)

## Context

Fix-pass-3 and its closeout are confirmed accepted: the confirmation run (`evals/invoiceguard/reports/fp3-confirm.jsonl`, 236 records, engine `9ddc7fb`) graded `INVARIANT: ok — no wrong-but-verified occurrence`, `RESULT: FAIL (thresholds)`, exit 2. The wrong-but-verified class is closed; what remains are the four faithfulness-recall anomalies from `docs/phase4-gate-verdict.md` §3 (lines 46–49) — N9–N12, all cases where a **correct** answer is downgraded to exit 2/3, never wrong content. The confirmation run's `emitted_tokens` stratification pre-localized every target; each mechanism has now been verified in the code. This pass: close out the bank bookkeeping (block 0), land the four fixes, and diagnose (not fix) the three unattributed residuals S5/S6/NP6.

**Discrepancy found during exploration (resolved by Chad):** the brief says "commit the confirmation report from the working tree", but `fp3-confirm.jsonl` + `fp3-confirm-grade.txt` are **already tracked** (commit `2619be2`) — Chad confirmed that satisfies the milestone item. The only untracked file, `evals/invoiceguard/reports/fp3-rerun-grade.txt` (the *interim* re-run's grade, engine `bf7a3e1`, exit 4 on A1's false-alarm breach, companion jsonl absent), is to be **deleted**, not committed.

## Verified mechanisms (one line each; full detail in the fix sections)

- **N9** — `src/invoiceguard/spine/rules_engine.py` is a QuoteClaim (slash fails `_IDENTIFIER_SHAPED`, `claims.py:31-34`); `_match_quote` shops only `pools.quote_corpus` (`matching.py:298`) while the path sits in `vocabulary` (`checks/read_source.py:32`); and the backticked surface fails `_containing_span`'s containment by one char each side (`verify.py:55-64` vs `placeholders.py:126-134`). B4's records show both halves: attempt 1 `injected=False` (pool half), attempt 2 `injected=True` (containment half) — fixing one leaves B4 at 0/5.
- **N10** — "May 29" is unmasked (`_MONTH_DATE` requires a comma+year, `claims.py:47-50`) so `_NUMBER` emits a bare `29`; the date branch of `_match_numeric` consults only `pools.strings` (`matching.py:96`), which did_run mode never populates — the ISO stamps live inside `run_status.detail` text. C1b shows the ISO-claim path already exists ("date not present in evidence", mechanical) but has no pool.
- **N11** — `render_evidence` (`drafter.py:30-43`) renders errored invocations with their full content-rich error strings; the verifier harvests nothing from non-ok invocations (`verify.py:95`), so anything cited from them is guaranteed unmatched. Nothing in `render_drafter_prompt` (`prompts.py:105-122`) says to ignore them.
- **N12** — `CheckExecutionOutput.errors` is a bare list with no scalar sibling (`envelope.py:150-161`); `_render` returns None for lists (`placeholders.py:79-88`) → drafting exhausts → table fallback finds no table → refuse. The verifier already harvests `len(errors)` (`checks/execution.py:62-69`) — the gap is purely that no walkable field exists for the drafter. `error_count` exists nowhere in src/ or tests/.

## Block 0 — closeout (bank edits, deliberate and reviewed)

All in one or two commits before any code changes.

1. **Confirmation report** — already committed at `2619be2` as `fp3-confirm.jsonl` / `fp3-confirm-grade.txt` (naming per `evals/README.md:27-32` policy); Chad confirmed this satisfies the milestone item. **Delete the untracked `evals/invoiceguard/reports/fp3-rerun-grade.txt`** (its jsonl is gone; its breach line was the A1 false alarm; deliberate-milestones policy). Fix the stale "after" line: `docs/phase4b-baseline-findings.md:105` still cites `fp3-rerun.jsonl` "(uncommitted pending the confirmation run)" — update it to name `fp3-confirm.jsonl` (engine `9ddc7fb`) as the committed "after" to `baseline-4b.jsonl`'s "before".
2. **Delete exactly four xfail blocks** (all XPASS in fp3-confirm, two clean runs each):
   - WBV-C4 → `evals/invoiceguard/bank/scripted.yaml:191-198`
   - WBV-MT2 → `evals/invoiceguard/bank/multiturn.yaml:36-44`
   - WBV-S7 → `evals/invoiceguard/bank/scripted.yaml:340-347`
   - WBV-U5 → `evals/invoiceguard/bank/user.yaml:17-24`
   Also retire the four refs `WBV-C4, WBV-MT2, WBV-S7, WBV-U5` from the closed `XfailRef` literal (`src/engine/eval/models.py:35-38`), following the `clarify-open` retirement precedent. `WBV-S4` stays (S4 keeps its block).
   **Do not delete** MT3's N9 block (`multiturn.yaml:70-79`) or C1b's N10 block (`scripted.yaml:140-142`): both XPASS on drafting/retry luck — MT3 has attempt-1 N9 misses in 4/5 reps, C1b attempt-1 N10 misses in 5/5 reps; no code changed for either. The commit message says so.
3. **Update S4's xfail note** (`scripted.yaml:273-279`, ref `WBV-S4`): residual is stochastic wrong-question SQL capped by the zero-challenge — 3/5 pass, misses at exit 2 — the designed trade, kept annotated.
4. **Add P-L3Q's N9 xfail block** (`probes.yaml:31-46`, currently no block — third N9 site): fp3-confirm rep 4 emitted the backticked file path and failed by exactly N9's mechanism (`injected=True` + unmatched quote, `judge_calls: 0`); note cites the emitted-token evidence (`file_paths`/`backticked` stratification — rep 4 is the only rep where the path was backticked).

## The four fixes

One commit per fix, N-order, full suite green per commit. All new tests carry the fix-pass docstring convention: `"""Fix pass 4 (gate verdict Nxx): …"""`.

### Commit 1 — N9: delimiter-trimmed containment + quote pool fallback

Two independent halves, one per B4 failure mode; both required (B4's records prove fixing one leaves it 0/5). Acceptance: B4, MT3, P-L3Q flip together on the re-run.

- **Containment half** — `src/engine/verifier/verify.py`: new module-level helper `_delimiter_trimmed(claim) -> tuple[int, int]` below `_overlaps` — strips leading/trailing backticks from `claim.surface` (`lstrip`/`rstrip` lengths) and returns the narrowed `(start, end)`. `_containing_span` (verify.py:55-64) tests the trimmed bounds. Only backtick delimiters trim — model-typed non-delimiter characters still defeat containment, preserving the containment-not-overlap rule. `_overlaps` (the honest `injected` annotation at verify.py:205) stays untouched. No pydantic model changes; `ClaimRecord.start/end` remain full-surface offsets.
- **Pool-fallback half** — `src/engine/verifier/matching.py` `_match_quote` (282-313): after the `quote_corpus` loop, before the unmatched return, for **inline** claims only (`not claim.fenced`): exact membership of `claim.text` in `pools.vocabulary` → `MatchOutcome(status="matched_derived", method="quote-vocabulary")`; else in `pools.strings` → `matched_derived`, method `quote-string`. Exact, case-sensitive, no normalization — the pool membership itself is the "short" cap; `matched_exact` is never widened (and the table-passthrough probe in `verify.py:127-141` requires `matched_exact`, so it cannot be loosened by this). Docstring updated to state the policy.
- **New tests**:
  - `tests/test_verifier_matching.py::test_backticked_file_path_quote_falls_back_to_vocabulary` — B4 attempt-1/MT3 surface: `` `src/invoiceguard/spine/rules_engine.py` `` matched `matched_derived`/`quote-vocabulary` via the read_source path harvest; plus a near-miss body (path minus `.py`) staying `unmatched`.
  - `tests/test_verifier_matching.py::test_exact_evidence_string_quote_falls_back_to_strings` — `matched_derived`/`quote-string`.
  - `tests/test_verifier_ladder.py::test_backticked_injected_path_is_contained_after_delimiter_trim` — B4 attempt-2 / P-L3Q rep-4 surface: `InjectedSpan` covering only the path, backticks outside → `matched_injected`, verified.
- **Guards that must stay green** (re-run first): `test_a_claim_extending_past_an_injected_span_verifies_normally` (ladder:175 — no backticks, unchanged), ladder:118/:144, `test_quote_matching_stays_case_sensitive` (matching:261 — `RULES ENGINE` is in no pool), `test_quotes_never_reach_the_judge`.

### Commit 2 — N10: date-token numerals (+ the S5 dotted-logger fold-in)

Constraint stated per the brief: **date claims match date-tagged/date-shaped candidates only** — a bare "29" never enters the date path (no `claim.date`), and date claims never consult `pools.numbers`, so "29" can never match an unrelated 29 in either direction. `NumericClaim` already supports this shape (`value: float | None` — "None for date-form claims", `date: str | None`, `models.py:49-50`); no field changes, only a comment update ("ISO YYYY-MM-DD, or MM-DD for yearless").

- **Extraction** — `src/engine/verifier/claims.py`: new `_MONTH_DAY` regex beside `_MONTH_DATE` (same month alternation, no comma/year); in step 4 (213-237), a loop **after** the `_MONTH_DATE` loop (full dates already masked, so no lookahead needed): validate `1 <= day <= 31` (else leave the numeral to `_NUMBER` as today), emit `NumericClaim(date=f"{month:02d}-{day:02d}", value=None)`, mask the span. "May 29" stops extracting a bare 29.
- **Matching** — `matching.py` `_match_numeric` date branch (95-105): keep the full-ISO loop; add a yearless branch (`len(claim.date) == 5`): candidate `head = candidate[:10]` must be ISO-shaped (`\d{4}-\d{2}-\d{2}`) and `head[5:] == claim.date` → `matched_derived`, method `date-yearless` (derived, not exact — the year is context the claim didn't state, same honesty policy as `vocabulary-casefold`).
- **Harvest** — `src/engine/verifier/checks/execution.py`: module-level ISO-date regex; in the did_run branch, `contribution.strings |= set(findall(run_status.detail))` (the adapter embeds the window's isoformat stamps in `detail` — the *actual* evidence-derived answer, unlike the requested window in invocation args); same per raw evidence line in the existing lines loop. recent_errors mode already puts full `ts` timestamps into `strings` (execution.py:59-61), which `candidate[:10]` handles — S5's "March 11" needs no further harvest change.
- **Fold-in (same commit, same file/run)** — S5 rep 4's entity `` `invoiceguard.benchmark_scoring` ``: `checks/base.py` gains `dotted_tokens(text)` (harvest-side mirror of `claims._DOTTED`); `checks/execution.py:61` adds `contribution.vocabulary |= dotted_tokens(value)` beside the existing `identifier_tokens` call in the errors branch. Raw dotted string suffices for `_match_entity`; `dotted_suffixes` expansion deliberately not taken.
- **New tests**: `test_yearless_month_day_is_a_date_token_not_a_numeral` (claims; "March 45" still a plain numeral), `test_prose_date_matches_harvested_iso_window` (C1 surface → `date-yearless`), `test_iso_date_claim_matches_harvested_window` (C1b surface → `matched_exact`/`date`), `test_date_claims_never_match_bare_numerals_and_vice_versa` (the guard both directions), `test_dotted_logger_name_in_error_rows_grounds_entity_claims` (S5 rep 4 surface). No existing test extracts a yearless month-day (searched) — nothing to update.
- **Known safe-direction exposure** (noted, accepted): "May" as modal verb before a small number could false-extract a date claim; a false date claim can only *unmatch* (downgrade, never wrong-content), same case-sensitive-month exposure `_MONTH_DATE` already carries.

### Commit 3 — N11: errored invocations collapsed for the drafter

Both remedies from the verdict, and the error text is **dropped** from the drafter's view, not truncated: the verifier harvests nothing from non-ok invocations (`verify.py:95`), so any echoed error content is guaranteed-unmatched prose; the router (the agent that can act on error text) already saw it in its own loop — `render_evidence` is drafter-only (confirmed).

- `src/engine/harness/drafter.py` `render_evidence` (30-45): when `status != "ok"`, emit only `{"index", "tool", "status", "note": "call failed; supports no citations or placeholders"}` — no error string, no dead `output` slot. Ok entries unchanged.
- `src/engine/harness/prompts.py` `render_drafter_prompt` (105-122): new rule bullet — draft only from `status: ok` entries; errored entries contain no usable evidence (never cite, quote, or place placeholders into them); if no ok entry covers part of the question, say so plainly.
- **New tests**: `tests/test_harness_drafter.py::test_errored_invocations_render_collapsed_without_error_text` (unit — rendered block lacks the error content and any `output` key, carries the note; ok entry renders in full); `tests/test_harness_graph.py::test_draft_cites_the_clean_invocation_not_the_errored_one` (harness-level P-N11 shape: e0 errored check_execution + e1 clean did_run; scripted draft cites `{{e1.run_status.count}}`; assert the drafter's message contains e0's stub but not its error text, no resolution failures, answer carries e1's value). Regression target: P-N11 (0/5).
- If every invocation errored, the drafter sees only stubs → the existing fallback/refusal path produces an honest insufficiency — intended.
- No existing test pins the `error` key in `render_evidence` output (searched); router-side error rendering untouched.

### Commit 4 — N12: `error_count` scalar on CheckExecutionOutput

The scalar sibling recent_errors never got, mirroring did_run's `run_status.count`. Regression target: HN-ERRORS (0/5); also fixes 4/5 of S5.

- `src/engine/tools/envelope.py` `CheckExecutionOutput` (150-161): add `error_count: int | None = None`; docstring notes it as recent_errors' mirror of `run_status.count`, **pre-truncation total**.
- `src/engine/tools/check_execution.py` (118-126): recent_errors return fills `error_count=len(events)` — the pre-cap total, deliberately (matches `RunStatus.count`'s "true total" semantics and `Table.total_row_count`; a capped day reporting 30-shown when 42 occurred would be a new dishonesty). A clean day yields `errors=[] , error_count=0`.
- `src/engine/verifier/checks/execution.py`: harvest `EvidenceValue(value=float(error_count), ref=f"{ref}.error_count", salience="count")` — exact shape of the `run_status.count` harvest at 28-35. The existing `len(errors)` harvest (62-69) is **kept**: it truthfully grounds "the N errors shown" when truncated; the values coincide otherwise.
- No placeholder changes needed: `error_count` is a walkable scalar — `{{e0.error_count}}` / `{{e0.output.error_count}}` resolve through existing `_candidates`/`_navigate`; `0` renders as `"0"`.
- **Commit message** cites the `2ffde27` run_status-rename precedent: pre-Phase-5 internal envelope field change, frozen guarantees (codec round-trip, kind discriminators, outer ToolInvocation fields) untouched; stale local `work.db` bundle caveat noted per the brief (an optional-with-None field still deserializes *old* bundles; bundles written by new code won't load in an older checkout).
- **New tests**: envelope round-trip with `error_count` (`tests/test_envelope.py`); `tests/test_tool_check_execution.py` — extend the recent_errors parsed-rows test with `error_count == 30`, extend the cap-visibility test with `error_count == 30` while `len(errors) == 10` (pins pre-cap semantics), new `test_recent_errors_clean_day_reports_zero` (`errors == []` and `error_count == 0`); `tests/test_harness_placeholders.py::test_error_count_paths_resolve` (mirror of the run_status precedent tests at :92-158 — both placeholder spellings render `"0"`, the "clean day is now sayable" pin); `tests/test_verifier_matching.py::test_error_count_grounds_a_no_errors_claim` ("There were 0 errors that day." → `matched_exact`; assert the match, not the winning ref — both `error_count` and `len(errors)` truthfully carry 0).
- No serialized check_execution fixture exists on disk (searched); the round-trip test builds both sides fresh — no byte-pinned artifact breaks.

## Residual triage (diagnosis; fold only trivially in-scope fixes)

- **S5 0/5** — three stacked causes, two of which this pass fixes: (1) **N12** owns 4/5 reps (all refused on `{{e0.output.errors|count}}`-shaped placeholder failures — the missing scalar); (2) rep 4's `numeric "11"` from prose "March 11" is **N10** (recent_errors mode already has ISO `ts` strings in `pools.strings`, so the N10 matcher will reach them); (3) rep 4's entity `` `invoiceguard.benchmark_scoring` `` misses because the recent_errors branch harvests event string fields into `strings` but never `vocabulary` — **fold in**: one additive harvest line in `checks/execution.py`'s errors branch (already touched by N12). S5 has no xfail block and stays that way; expected to flip on Chad's re-run.
- **S6 0/5** — two distinct causes, both **queued, not fixed** (bank/eval-design edits, out of this pass's scope fence): (1) rep 1 delivered the correct verified answer ("Crestpoint Mechanical", table passthrough) and fails **only** `numeric_from_gold(amount=120.0)` — the assertion-shape mismatch `docs/phase4b-baseline-findings.md:41` already flagged; the queued fix is the bank correction (name_from_gold on supplier, or split the amount off the pass criterion). (2) Reps 2–5 layer `supplier_acceptance`/`review_reports.disposition`/`invoice_history` predicates onto the CREEPBACK category, self-annihilate to zero rows, and the fix-pass-3 zero/empty-result challenge correctly caps them (exit 2/3 — the safety layer working as designed on wrong SQL). Queued fix: a creepback/`prior_revision_id` grounding gotcha strong enough that the model stops adding acceptance predicates.
- **NP6 1/5** — a recurring un-ledgered class: the drafter names **structured/envelope identifiers that live in no pool** — dotted `` `invoices.status` `` (unmatchable composite though both halves match vocabulary individually; sole cause of reps 4–5, which gave the correct 4-value gold answer), envelope field names `` `enum_values` ``/`` `data_scan` ``/`` `top_values` ``, and dictionary concept *names* (rep 1's `` `invoice lifecycle` `` — only definitions reach `quote_corpus`, `checks/dictionary.py:66,98`). The one pass (rep 3) is O1 text-block injection accidentally rescuing the row. **Queued** with mechanisms named: (a) dotted `table.column` entity fallback (match parts, label `matched_derived`) — deliberately not folded in: it changes entity-matching semantics and deserves its own regression surface; (b) harvest concept names into `quote_corpus`; (c) decide whether envelope field names are matchable vocabulary or non-claims. This class is a candidate for its own N-number in the next gate-verdict update.

## Scope fence

No Phase 5. No exit-semantics changes (the unsupported open item stays open). No bank deletions beyond block 0's four. NP3/currency stays parked (0/5, sole failure `currency_format`, float-tail `8308.92139244107` — known, untouched). Xfail blocks for N9 (B4, MT3, P-L3Q), N10 (C1, C1b), N11 (P-N11), N12 (HN-ERRORS) all **stay in place** this pass — they come out in a follow-up edit only after Chad's re-run proves the flips (the XPASS banner will call for it).

## Verification

- `uv run pytest` — full suite green offline (461 + new regression tests; no network).
- `uv run engine eval grade --bank evals/invoiceguard --check-gold` — green (exit 0; gold scripts vs committed `expected_gold`).
- New regression tests per fix (named in the fix sections), each docstring-anchored "Fix pass 4 (gate verdict Nxx): …" per the fix-pass-3 convention.
- The acceptance pair untouched and green: `test_acceptance_a_corrupted_draft_caught_retried_then_unverified` (`tests/test_verifier_ladder.py:33`), `test_acceptance_b_wrong_sql_result_is_refused_despite_faithful_prose` (`tests/test_verifier_plausibility.py:30`). All prior fix-pass-3 regressions untouched.
- Grounding/rendering goldens: not expected to change (no grounding-prompt text in scope); if any drafter-prompt golden exists and is touched by N11's prompt line, regenerate deliberately via the documented mechanism and say so in the commit.
- The eval-level flips (B4/MT3/P-L3Q, C1/C1b, P-N11, HN-ERRORS, S5) are proven only by Chad's re-run on the work machine — out of scope here; xfails stay until then.

## After implementation

Update the auto-memory phase-status note (`substrate-engine-phase-status.md`): the confirmation re-run is no longer pending — fp3 accepted at `fp3-confirm` (INVARIANT ok), fix pass 4 (N9–N12) landed, xfail flips awaiting Chad's work-machine re-run.
