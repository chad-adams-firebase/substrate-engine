# N13 closeout — regex family fix, breach-by-kind, P-N11 onto N5, stats enum harvest

## Context

The N13 fix (`acfb274`, bank bookkeeping `17429b4`) was re-run on the work
machine as `evals/invoiceguard/reports/n13-witnesses.jsonl` (engine `17429b4`
clean, seed 42, gpt-4o, rows P-N11 + HN-ERRORS + NP6, 5 reps, bank
`f57627c9255c7f64` = the bank as it stands now). Graded to stdout this
session; **every claim below is verified against the report, not the brief**:

| row | grade | what the reps did |
|---|---|---|
| HN-ERRORS | `[XFAIL] 2/5 (N13) failures: contains` | **5/5 verified at exit 0.** Reps 1–2: "No errors occurred in the `benchmark_scoring` component on 2026-04-15. The error count is 0." Reps 3–5: "The `benchmark_scoring` component had 0 errors on 2026-04-15." All three claims (`benchmark_scoring` → `matched_exact/vocabulary`, `2026-04-15` → `matched_exact/date`, `0` → injected from `e0.error_count`) ground on attempt 1. Reps 3–5 fail only `contains '\b(no\|none\|zero\|clean)\b'` — the digit/word gap. |
| P-N11 | `[INCON] 1/1 (N13) reached 1/5` | Rep 1 reached (e0 `benchmark scoring` errored naming the seven valid components → e1 retry ok), verified exit 0: "No errors occurred in benchmark scoring on 2026-04-15. The `error_count` is 0." — backticked `error_count` `matched_exact/vocabulary` (N13 proven). Reps 2–5 never retried: one errored call, then refuse (exit 3 ×3) / unverified shrug (exit 2 ×1). |
| NP6 | `[ ok] 4/5 threshold 0.60 failures: contains` | Reps 1,2,4,5 give the seven-value lifecycle list verified at exit 0 (their `` `invoices.status` `` composite is unmatched on attempt 1 and redrafted to "`status` in the `invoices` table" — 3(a) still live, absorbed by the redraft). Rep 3: see below. |

Grader output: `INVARIANT BREACH — 5 occurrence(s): 0 contradicted, 5 unsupported`
(HN-ERRORS reps 3,4,5 on the contains pattern; NP6 rep 3 on `NO_REVIEW_NEEDED`
and `READY`); `RESULT: FAIL (INVARIANT BREACH)`, exit 4. No "fails exactly
when backticked emitted" note anywhere in the run — no failure is a
backticked-identifier failure. **N13 is closed in substance**; the exit 4 is a
bank-assertion defect on five verified-correct bodies.

**NP6 rep 3 — corrected classification (differs from the brief).** The
attempt-1 draft enumerated all four statuses (`CLOSED`, `LAPSED`,
`NO_REVIEW_NEEDED`, `READY` — the gold exactly). The Verifier marked backticked
`` `NO_REVIEW_NEEDED` `` and `` `READY` `` `unmatched — "name not present in
this turn's evidence"` and the redraft loop deleted them; the delivered body
"`CLOSED`, `LAPSED`" is what survived. Cause: `StatsCheck.harvest` puts
`top_values` into `strings` (`src/engine/verifier/checks/stats.py:71`) while a
backticked identifier-shaped token extracts as an EntityClaim that shops only
`vocabulary` (`src/engine/verifier/matching.py:273`). `CLOSED`/`LAPSED` matched
only because the `status_is_current` gotcha detail text happens to tokenize
them. The un-backticked `NO_REVIEW_NEEDED` later in the same draft was
`matched_injected` via `e2.rows[0].top_values[2].value` — the drafter saw the
values; the entity path could not. Not drafting completeness, not 3(a): a
harvest pool-shape gap, fixed below (user's decision: record it and land the
one-line fix).

Decided by the user this session: breach-by-kind semantics; HN-ERRORS **and**
P-N11 both gain the digit; NP6 rep 3 recorded accurately with the stats fix
landed.

## Commit sequence

### Commit 0 — the report pair, before any bank edit (hash constraint)

`preflight` refuses on `bank_hash` mismatch (`src/engine/eval/grade.py:144-175`,
hash = raw bytes of `eval.yaml` + `bank/*.yaml` + `gold/*.py`,
`src/engine/eval/bank.py:181-191`), so this grade is the only one the report
can ever carry. Render it against the **current grader** (the breaches are the
evidence for commit 1):

```sh
uv run engine eval grade --bank evals/invoiceguard \
  --report evals/invoiceguard/reports/n13-witnesses.jsonl \
  --out evals/invoiceguard/reports/n13-witnesses-grade.txt   # exits 4 — expected
```

Expect no drift warning (HEAD == 17429b4), the five breach lines, the three
row lines above, ledger `N13: HN-ERRORS, P-N11`. Commit `n13-witnesses.jsonl`
+ `n13-witnesses-grade.txt` + `docs/plans/n13-closeout-plan.md` (a copy of
this plan — the `83a12d6` precedent; deleted in the cleanup commit).
Message: "Report: n13-witnesses grade — N13 closed in substance, exit 4 is a
bank-assertion defect". Body: the table; **quote rep 3's body verbatim**
("The `benchmark_scoring` component had 0 errors on 2026-04-15.") beside the
pattern it fails; P-N11 reached 1/5; NP6 rep 3's real mechanism; zero
backticked failures run-wide.

### Commit 1 — Grade: contains failures are threshold failures, never breaches

`src/engine/eval/grade.py` — `_alarm_worthy` (`:626-637`) is the single gate
(`_grade_rep` `:601-617` is the only BreachRecord site); add the kind check
there rather than a new ClassVar:

```python
if assertion.kind == "contains":
    return False   # pattern absence is phrasing, not wrong content
```

Docstring rewrite states the rationale for the commit: both historical
false-alarm families — A1's window literals and HN-ERRORS' digit/word gap —
were pattern-kind; both catastrophic shapes (S4, S7) were `numeric_from_gold`,
untouched. `not_contains` still breaches (forbidden content present IS wrong
content); `name_from_gold`/`numeric_from_gold`/`window_data_anchored` and the
per-assertion `breach: false` override unchanged. `_severity` unchanged.
Extend the `breach` field comment in `src/engine/eval/models.py:74-80` with
one line: `contains` never breaches regardless of this flag.

Tests, `tests/test_eval_grade.py`, copying `test_omission_tolerant_assertion_gates_without_the_alarm` (`:311-334`) and the `ROW_DATA.replace(...)` string-surgery pattern:
- `test_contains_failure_gates_without_the_alarm` — exit-0 `make_turn` whose body
  misses a `contains` pattern → `exit_code() == 2`, `breaches == []`, row
  `fail`, `"contains" in failure_classes`, render has `INVARIANT: ok` and
  `RESULT: FAIL (thresholds)`.
- `test_not_contains_still_trips_the_alarm` — same shape with a forbidden
  pattern present → exit 4, `breach.assertion == "not_contains"`, severity
  `contradicted`.
- `test_wrong_but_verified_trips_the_alarm`, `test_breach_severity_labels_…`,
  `test_breach_outranks_xfail_annotation`, `test_not_reached_rep_still_records_a_breach`,
  window/sentinel tests: **untouched**.

Docs: `docs/phase4b-demo.md:78-81` parenthetical listing what breaches — add
"pattern absence gates only" so the demo stays truthful.

### Commit 2 — Bank: HN-ERRORS closes; P-N11 onto N5; N13 retires

`evals/invoiceguard/bank/honest_negative.yaml:23-51` (HN-ERRORS):
- pattern → `'\b(no|none|zero|clean|0)\b'`; **delete the xfail block**.
- Rationale in the message: proven in substance — code-backed N13 fix plus
  five verified-correct bodies in one run; the assertion was the defect.
  **Quote rep 3's body** as the evidence.

`evals/invoiceguard/bank/probes.yaml:5-33` (P-N11):
- pattern → same digit fix (user decision; identical defect).
- `xfail.ref: N13` → `N5`; note rewritten: N11 and N13 both proven for this
  row (rep 1 reached, verified exit 0, backticked `error_count` matched);
  what the row measures now is the licensed retry's firing rate — reached
  1/5 on n13-witnesses (reps 2–5 surrendered after one error naming the
  valid components: N5's shape from the addendum,
  `docs/phase4-gate-closing-addendum.md:43`). `setup` block, `reached_floor`
  default and `not_contains` unchanged.

`src/engine/eval/models.py:26-50`:
- `XfailRef = Literal["N5", "O1", "WBV-S4"]`.
- **Append** to the ledger comment (never rewrite prior entries): N13 retired
  on n13-witnesses with no remaining rows — HN-ERRORS 5/5 verified exit 0,
  NP6 4/5 threshold-clearing, zero backticked failures run-wide; P-N11's
  block re-attributed to N5 (the retry rate), the re-attribution shape again.

Sweep the ref: `evals/README.md:16` `(N13, O1, WBV-*)` → `(N5, O1, WBV-*)`;
`docs/phase4b-demo.md:84-86` mapping → "P-N11 → N5 (HN-ERRORS flipped), …
N9–N13 retired"; `tests/test_eval_grade.py:282,291,466,747` fabricated rows
`ref: N13` → `ref: N5`.

### Commit 3 — Verifier: stats top_values reach vocabulary (NP6 rep 3)

`src/engine/verifier/checks/stats.py:70-71`: beside `strings.add(top.value)`,
add `if IDENTIFIER_SHAPED.match(top.value): contribution.vocabulary.add(top.value)`
(import `IDENTIFIER_SHAPED` from `engine.verifier.claims` — the same shared
shape `checks/invocation.py` uses, so harvest and extraction stay one
definition). Free-text top values (names with spaces) stay strings-only; no
matcher change; exact membership, so the N9 line holds.

Test, `tests/test_verifier_matching.py` (reuse the `query_univariate_stats`
invocation builder at `:52` and `_pools`/`_match`): `test_stats_top_values_ground_backticked_enum_claims`
— the NP6 rep-3 stats row (`top_values` CLOSED/LAPSED/NO_REVIEW_NEEDED/READY,
no dictionary evidence) and the attempt-1 draft; `` `NO_REVIEW_NEEDED` `` and
`` `READY` `` → `matched_exact/vocabulary`; a spaced top value stays out of
vocabulary; zero judge calls.

### Commit 4 — Engine: the licensed retry becomes an expectation

`src/engine/harness/prompts.py:98-101` (`render_router_prompt`, the only
occurrence repo-wide). Replace

> When a tool returns an error that names valid alternatives or tells you what to send instead, spend a step retrying once with a corrected tool call before you consider refusing.

with

> When a tool returns an error that names valid alternatives or tells you what to send instead, your next step MUST be one retry with a corrected tool call — refusing without that retry is an early surrender. Exactly one retry: if the corrected call also errors, refuse.

Still bounded to one; the violation classes (prose replies, empty replies)
are not reopened. Commit body cites P-N11's reached rate (5/5 on
fp4b-holdouts → 1/5 on n13-witnesses under the permissive wording) as the
motivation.

`tests/test_harness_router.py:172-174` — the prompt-truth assertion becomes
`assert "your next step MUST be one retry with a corrected tool call" in prompt`
and `assert "Exactly one retry" in prompt`; comment updated (N5, expectation
not permission). No golden embeds the router prompt (`tests/golden_grounding.py`
pins the run_sql grounding prompt only) — nothing to regenerate; state that in
the message.

### Commit 5 — Docs + cleanup

`docs/fix-pass-4-residuals.md` — new section "n13-witnesses — N13 closed":
HN-ERRORS closed (5/5 verified-correct bodies; the pattern was the defect;
breach-by-kind is the grade-side consequence). P-N11 migration note (N11 and
N13 proven; the row now measures the retry rate under N5; prompt strengthened
in commit 4; next-run expectation: reached ≥ floor). NP6: 4/5, deliberately
unannotated still; rep 3 recorded as the stats pool-shape gap, fixed in
commit 3; reps 1,2,4,5 show 3(a) still live and redraft-absorbed — 3(a) stays
queued. Update the "Next-run expectation" sentence in the fp4b-holdouts
section (P-N11 did not flip; explain why in one line).

Delete `docs/plans/fixpass5-followup-plan.md` and
`docs/plans/n13-closeout-plan.md` (committed in commit 0). Nothing references
either (grepped).

After the last commit: update the memory file
`substrate-engine-phase-status.md` (N13 closed 2026-08-30; XfailRef = N5/O1/WBV-S4;
P-N11 measures retry rate; S4/NP3/S6/3(a) queued).

## Verification

1. Commit 0 first, then confirm the constraint bites: re-grading
   `n13-witnesses.jsonl` after commit 2 must raise `bank hash mismatch`.
2. Offline replay of the bank fix (scratch script, not committed): run
   `grade._contains` with the new pattern over all five HN-ERRORS bodies and
   P-N11 rep 1 → all True; HN-ERRORS would grade 5/5 `[ ok]`.
3. Offline replay of commit 3: the NP6 rep-3 `evidence_payload` from the
   report through `Verifier` with `make_verifier([])` and the attempt-1 draft
   → `unmatched_count: 0`, `llm.calls == []`.
4. `uv run pytest` — full suite green offline after every commit (491 + new).
5. `uv run engine eval grade --bank evals/invoiceguard --check-gold` → exit 0
   (no gold changed).
6. `grep -rn N13 src tests evals docs` → only ledger/history prose, no live
   ref. `grep -rn "spend a step retrying"` → nothing.
7. Prior regressions untouched: the alarm tests, N9/N10 matching tests,
   `test_quotes_never_reach_the_judge`.

## Scope fence

No Phase 5. S4, NP3, S6, mechanism 3(a) stay queued as-is. No NP6 xfail
block. No other prompt edits. The stats fix is the one addition the user
authorised beyond the brief.
