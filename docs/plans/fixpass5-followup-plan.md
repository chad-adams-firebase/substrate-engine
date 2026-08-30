# N13 — the poolless-identifier class: one fix, two witness rows

## Context

The fp4 follow-up (da26968..c19ac7c) fixed N11 (collapsed failed-call rendering) and N12 (`error_count` on `recent_errors`). The uncommitted holdout re-run `evals/invoiceguard/reports/fp4b-holdouts.jsonl` (engine `c19ac7c` clean, seed 42, rows P-N11 + HN-ERRORS, 5 reps) proves both mechanisms working but neither row flipped. Verified against the report (not the brief):

| row | result | exit-0 drafts | failing drafts | unmatched claims on failures |
|---|---|---|---|---|
| P-N11 | 3/5, reached 5/5 (2 invocations e0-error/e1-ok on every rep) | "…The error count is 0." | reps 2,3: "…The `` `error_count` `` is 0." | entity `` `error_count` `` — "name not present in this turn's evidence" |
| HN-ERRORS | 1/5 (rep 3 exit 0 — that row's first delivered answer) | "No errors occurred on the specified date. The error count is 0." | reps 1,2,4,5: "The evidence does not specify the `` `benchmark_scoring` `` component or the date 2026-04-15. However, … 0 errors." | entity `` `benchmark_scoring` ``; date `2026-04-15` — "date not present in evidence" |

- `backticked` and `iso_dates` on HN-ERRORS **co-occur perfectly** (both on 1,2,4,5; both absent on 3) — the grader will print two identical "fails exactly when … (4 with, 1 without)" notes; neither isolates a cause, but the verdict records show both claim kinds unmatched, so both are real.
- The fp4-slice date-disclaimer ("…is not supported by the tool evidence") is **gone** from all ten drafts; P-N11's sole residual is the backticked field name.
- INVARIANT: ok (grade-derived — the four exit-0 turns satisfy every assertion; neither row has gold-comparing assertions). `judge_calls: 0` everywhere.
- Every exit-0 rep passed on attempt 2 by the redraft loop *deleting* the tokens, not grounding them — the drafts are correct; the verifier's recall is the defect.

**Hypothesis confirmed with receipts.** The clean-day `recent_errors` envelope is `{"error_count":0,"errors":[],"run_status":null}` with `evidence.lines: []`. In `src/engine/verifier/checks/execution.py` every string-ish branch is gated on `run_status`, `errors`, or `evidence.lines` — all empty — so the turn's `vocabulary`, `strings`, and `quote_corpus` are ∅ and only `numbers=[0.0, 0.0]` exists. The three unmatched claims live in three places no harvester reads at all:

1. `arguments.component = "benchmark_scoring"` — `invocation.arguments` is read by **no** check (grepped all of `src/engine/verifier/`).
2. `arguments.window_start/end = "2026-04-15T00:00:00Z"` — same.
3. `error_count` — an **envelope field name**; the drafter sees it verbatim (`render_evidence` is a raw `model_dump(mode="json", exclude_none=True)`, `src/engine/harness/drafter.py:50-52`) but the only key-harvesting sites are error-row keys (`execution.py:58`) and SQL result columns (`run_sql.py:170`).

Same class from the residuals doc: NP6's `` `enum_values` ``/`` `top_values` ``/`` `data_scan` ``, C1 rep 3's `` `run_status` ``, `` `invoice lifecycle` `` (dictionary concept *name* — only definitions reach `quote_corpus`, `checks/dictionary.py:50-57`).

The fix is harvest-side, generic, in the verifier. No drafting-prompt change, no exit-semantics change, no Phase 5.

## Commit 0 — the report pair, before any bank edit

"Block 0" ≠ an in-file record: no report in `evals/invoiceguard/reports/` has ever carried a grade record (line 1 is `kind:"header"`, the rest `kind:"run"`). The precedent (`da26968`, `2619be2`, `8020e37`; `evals/README.md:35-40`) is **`<name>.jsonl` + `<name>-grade.txt` committed together**. The ordering constraint is real for a different reason: the grade records `bank_hash` and warns on drift, so the grade text must be rendered against the bank as it stood during the run (`1cc1cea4312ef91e`), i.e. before the commit-3 bank edits.

```sh
uv run engine eval grade --bank evals/invoiceguard \
  --report evals/invoiceguard/reports/fp4b-holdouts.jsonl \
  --out evals/invoiceguard/reports/fp4b-holdouts-grade.txt
```

Expect: no drift warning, `INVARIANT: ok`, `[XFAIL] HN-ERRORS 1/5 … (N12)` with the two co-occurring token notes, `[XFAIL] P-N11 3/5 … reached 5/5 (N11)` with "fails exactly when backticked emitted (2 with, 3 without)", `RESULT: PASS`, exit 0. Commit both files: "Report: fp4b-holdouts — N11/N12 mechanisms proven, the poolless residual isolated" (body: the table above, the co-occurrence caveat, the missing disclaimer).

## Commit 1 — the class fix: the invocation record joins the pools

### Mechanism 1 + 2: one generic harvester, `src/engine/verifier/checks/invocation.py` (new)

`harvest_invocation(invocation: ToolInvocation, ref: str) -> EvidenceContribution`, called from `Verifier._pools` (`src/engine/verifier/verify.py:105-114`) for **every ok invocation with non-None output, before the per-check lookup and regardless of whether a check is registered** (the gap is structural, not tool-specific). Failed calls still contribute nothing — "failed calls support no claims" holds; P-N11's e0 (`component: "benchmark scoring"`) stays unharvested.

**Arguments → pools** (walk `invocation.arguments` values; recurse into lists; strings only):
- whole value identifier-shaped → `vocabulary` (whole string; also `dotted_tokens(value)` for dotted values, so `invoiceguard.benchmark_scoring` enters whole — mirrors the N10 fold-in). Define "identifier-shaped" by **promoting `claims._IDENTIFIER_SHAPED` to a public `IDENTIFIER_SHAPED`** in `src/engine/verifier/claims.py:31-34` and importing it — harvest and extraction share one definition (same reasoning as the `_DOTTED` mirror comment in `checks/base.py:53-55`).
- whole value ISO-timestamp/date-shaped (`^\d{4}-\d{2}-\d{2}(T…)?$`) → `strings`, whole. The existing date path (`matching.py:98-122`, `candidate[:10] == claim.date`) then grounds `2026-04-15` as `matched_exact/"date"` and a prose "April 15" as `matched_derived/"date-yearless"` — the user-sanctioned reuse of N10's paths.
- free-text values (a docs `query`, a SQL string) are **not tokenized** — whole-value shape only. Blast radius stated: every tool's identifier-shaped arguments become citeable (`read_source` `qualified_name`, ckg `name`, dictionary `term`, `mode` literals like `recent_errors`, stats `table`/`column`). Rationale to record in the docstring: the arguments are part of the invocation record; a draft restating what was asked is grounded in this turn's evidence; a wrong query is a routing error the answer honestly reports, not a faithfulness violation.

**Output field names → vocabulary** (walk the rendered output; collect every dict key, leaf and intermediate, recursing into lists of dicts; no dotted composites):
- Walk **exactly what the drafter saw**: add `ToolInvocation.rendered_output(self) -> dict` to `src/engine/tools/envelope.py` returning `self.output.model_dump(mode="json", exclude_none=True)`, and make `render_evidence` (`drafter.py:50-52`) call it. One function, two consumers — `exclude_none` means `run_status` on a clean-day `recent_errors` stays unharvested, matching the N12 rendering law (a field the drafter never saw cannot ground).
- Keys shorter than 2 chars skipped (matches `identifier_tokens`). `kind` will enter vocabulary; harmless and honest (it is rendered).
- Existing key harvests (`execution.py:58`, `run_sql.py:170`) become redundant but stay — set union, no behaviour change, and they document per-check intent.

Matching side: **no change**. `` `error_count` `` and `` `benchmark_scoring` `` are EntityClaims that hit `_match_entity`'s existing exact membership (`matched_exact/"vocabulary"`); this is harvest recall, not a matcher widening, so the N9 exact-membership line holds unchanged. `folded_vocabulary()` needs no invalidation (pools are built fresh per `verify()`).

### Mechanism 3(b): dictionary names reach the quote corpus — `src/engine/verifier/checks/dictionary.py`

At the three name sites (concept `:51`, metric `:60`, gotcha `:85`) additionally `contribution.quote_corpus.append(CorpusText(text=<name>, ref=f"{ref}.concepts[{i}].name"))`. `identifier_tokens` shatters `invoice lifecycle` at the space; a backticked multi-word name is a QuoteClaim and now matches by exact substring (`matched_exact/"quote"`) — the name *is* the evidence text. No paraphrase widening.

### Mechanism 3(a): dotted `table.column` fallback — **stays queued**

Cannot be pinned tightly: no pool carries table→column structure, so "both parts in vocabulary" would ground `invoices.<column-of-another-table>`. That is precisely the semantic widening the residuals doc warns of. NP6 remains its witness; note this in the residuals doc. NP6 stays deliberately unannotated: its misses included backticked dictionary field names, which the field-name harvest now covers, so it may partially flip from mechanisms 1–2 (with 3(b) helping) and only 3(a)'s composite case left behind — it is in the re-run witness set to measure that decomposition, and an xfail block now would blur it.

## Commit 2 — regression tests, pinned to the witness surfaces

`tests/test_verifier_matching.py` (reuse `_pools`/`_match`, `tests/verifier_support.py`); docstring style "Fix pass 4 follow-up (N13): <mechanism> — <live surface>":
- `test_clean_day_recent_errors_grounds_argument_component_name` — the exact HN-ERRORS envelope + arguments; `` `benchmark_scoring` `` → `matched_exact`, method `vocabulary`.
- `test_window_argument_grounds_prose_and_iso_dates` — "on 2026-04-15" → `matched_exact/date`; "on April 15" → `matched_derived/date-yearless`.
- `test_envelope_field_name_grounds_backticked_error_count` — the P-N11 rep-2 draft; `` `error_count` `` → `matched_exact/vocabulary`.
- `test_none_fields_never_ground` — `run_status: None` on that envelope; `` `run_status` `` stays `unmatched` (harvest mirrors the drafter's view).
- `test_errored_invocation_arguments_never_harvest` — an error-status invocation with identifier args; claim `unmatched`.
- `test_free_text_arguments_are_not_tokenized` — a docs search `query: "why invoices lapse"`; `` `invoices` `` `unmatched` when nothing else provides it.
- `test_dictionary_concept_name_grounds_backticked_quote` — `` `invoice lifecycle` `` → `matched_exact/quote`.
- Every new test uses `make_verifier([])`-style or asserts no judge involvement where the ladder is exercised (`llm.calls == []`) — the quotes-never-reach-the-judge law untouched.

`tests/test_harness_drafter.py`: `test_harvested_field_names_are_exactly_the_rendered_keys` — keys collected by the harvester equal keys in `render_evidence`'s JSON for the same invocation (locks the shared `rendered_output`).

Prior regressions must stay green as-is: `test_backticked_file_path_quote_falls_back_to_vocabulary` (N9 bare-filename refusal, `:370-392`), `test_quotes_never_reach_the_judge` (`:272`), the N10 date tests (`:420-458`), `test_dotted_logger_name_in_error_rows_grounds_entity_claims` (`:477`).

## Commit 3 — bank bookkeeping (after commit 0's grade is in history)

Mirror `39ad640`'s shape:
1. `src/engine/eval/models.py:40-43` — `XfailRef = Literal["N13", "O1", "WBV-S4"]`. **Append** one sentence to the ledger comment (never rewrite prior ones): N11/N12 retire by **re-attribution with mechanisms proven** on fp4b-holdouts (P-N11 reached 5/5 drafting from e1 via placeholder; HN-ERRORS rep 3 exit 0) — distinct from the N9/N10 flip-retirement (XPASS) and from the 6c0e848 luck-flip reversal; their rows' blocks now track N13.
2. `evals/invoiceguard/bank/probes.yaml:11-22` — P-N11 `ref: N13`; note: N11 proven (all five reps reached the two-invocation scenario, e1's `error_count` cited via placeholder, 3/5 exit 0); residual is the backticked poolless `error_count` field name — fails exactly when backticked emitted (2 with, 3 without); the fp4-slice date-disclaimer did not recur. Keep the `setup` block.
3. `evals/invoiceguard/bank/honest_negative.yaml:29-40` — HN-ERRORS `ref: N13`; note: N12 proven (rep 3 verified at exit 0, the row's first delivered answer); residual is the argument-borne `benchmark_scoring` name and the window date `2026-04-15`, neither harvested (backticked and iso_dates co-occur on reps 1,2,4,5).
4. `evals/README.md:16` — ref range `(N13, O1, WBV-*)`.
5. `tests/test_eval_grade.py:282,291,299,466,747` — fabricated rows repointed `N11`/`N12` → `N13`.
6. `docs/fix-pass-4-residuals.md` — append a "fp4b-holdouts" section under P-N11/NP6: hypothesis confirmed with the pool census; mechanisms (b) and (c) resolved (field names are matchable vocabulary, as rendered); (a) still queued with NP6 as witness; the co-occurrence caveat. 7. `docs/phase4b-demo.md:83-86` — the stale N9/N10 mapping updated to the current ledger (N13, O1, WBV-S4).

Commit message body says why: re-attribution, not flip; cites the exit-0 evidence.

## Verification

1. `uv run pytest` — full suite green offline (482 + new).
2. `uv run engine eval grade --bank evals/invoiceguard --check-gold` — every row ok, `RESULT: PASS`, exit 0 (the bank edits changed no gold).
3. Offline replay sanity: build the HN-ERRORS and P-N11 envelopes from the report's `evidence_payload` in a scratch script and run the failing drafts through `Verifier` with `make_verifier([])` — expect `unmatched_count: 0`, `llm.calls == []`.
4. Re-grading the committed report against the edited bank is refused (`bank hash mismatch` is a hard error, not a warning) — which is why commit 0's grade text had to be rendered first: it is the only grade this report can carry.
5. The live flip itself is a work-machine re-run (P-N11 and HN-ERRORS 5/5 together per the N9 acceptance pattern) — out of this session's reach; record as the next-run expectation in the residuals doc.

## Scope fence (unchanged from the brief)

No Phase 5; no exit-semantics changes; S4/NP3/S6 stay queued; no drafting-prompt changes; mechanism 3(a) stays queued; no NP6 xfail block added.
