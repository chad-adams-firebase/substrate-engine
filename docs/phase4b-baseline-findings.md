# Phase 4b Baseline — Findings Carry-Back for the Build Chat

**Session:** 2026-08-27 (Chad running the harness on his Mac; Claude verifying independently in a sandbox). Companion to `docs/phase4-acceptance-carryback.md`, `docs/phase4-gate-closing-addendum.md`, `docs/phase4-gate-verdict.md`.

**Pins:** substrate-engine `c8b8ea7` · invoice-guard `761a18e` · seed 42 · DuckDB manifest `ac4b8abd4eb9c07e` — **byte-identical across all four sessions and twenty-plus engine commits** · 435 tests green · model `openai/gpt-4o` (committed pack default).

**Artifacts:** `evals/invoiceguard/reports/baseline-4b.jsonl` (225 records) and `baseline-4b-grade.txt` (rendered grade).

**Provenance note:** the run's working tree was reported dirty (`engine_dirty: true` in the report header). **No engine code was modified.** `_engine_sha()` derives the flag from `bool(git status --porcelain)`, which counts *untracked* files; at launch, `evals/invoiceguard/reports/` held the uncommitted smoke and resume-test reports and their `work.db` sidecars. That is the entire dirt. The header's `world_manifests.sqlite_convert` reads `ac4b8abd4eb9c07e`, identical to sessions 1–3, and `git status` after the run shows no modified tracked files. The measurement is sound.

**This is itself a finding.** Because the runner writes its report into the repo and then asks git whether the repo is clean, `engine_dirty` will be true on essentially every eval run — including every run made *after* committing the previous report, since the new report is untracked until committed. The flag therefore cannot distinguish "uncommitted engine changes invalidate this measurement" (which matters) from "a report file exists" (which does not). Fix options: restrict the check to tracked modifications (`git status --porcelain --untracked-files=no`), exclude the bank's `report_dir` from the check, or write reports outside the repo by default.

**Grader discipline:** every gold figure and every counter-figure in this document was produced by an executed query against the world in this session — none transcribed, none remembered (the grader's-correction law). The engine's own SQL was re-executed verbatim to confirm each wrong answer.

---

## 1. Headline

**The baseline exited 4 — invariant breach, 14 occurrences across 6 rows.** Diagnosis: **nine genuine wrong-but-verified outcomes across five rows**, plus four false alarms on one row (S6, right-but-incomplete) and one detail-reporting artifact.

Two of the nine are the catastrophic shape the Verifier exists to prevent:
- **S4 answered `0`** to a question whose true answer is **114**.
- **S7 answered an empty table** ("never happened") to a question whose true answer is **yes, once, by mona**.

The gate verdict's central claim — *"zero wrong-but-verified outcomes across 62 live turns"* — was true of what had been measured. It does not survive 225 turns. The claim needs restating, not retracting: three human sessions sampled each of these rows once or not at all, and every one of these failures is intermittent.

**This is the harness justifying its existence on its first real outing.** It found, in twenty-five minutes and a few dollars, a defect class that three evenings of careful human testing could not have found by construction.

## 2. The nine breaches, root-caused

Each engine SQL below was re-executed against the world this session and reproduced the reported wrong answer exactly.

| Row | Reps | Engine answered | Truth | Root cause |
|---|---|---|---|---|
| **C4** | 5/5 | 662 | **692** | Window `[2026-04-29, 2026-05-29)` excludes the final data day (30 invoices). B5 gets the same boundary right — the convention is inconsistent across phrasings, not absent |
| **MT2** turn 1 | 1/5 | 1,065 | **254** | **Join fan-out.** `findings JOIN compliance_reports ON invoice_id JOIN compliance_rules` cross-multiplies every finding against every critical rule on the same invoice |
| **S4** | 1/5 | **0** | **114** | Two inversions: `adjustment_flag = 0` (the gotcha specifies `= 1`) and `NOT EXISTS (any finding)` instead of `NOT EXISTS (... rule_name = 'total_mismatch')` |
| **S7** | 1/5 | **empty** | **mona** | Filters `i.status = 'LAPSED'` — but a *reactivated* invoice is no longer LAPSED. The filter excludes exactly the population the question asks about |
| **U5** | 1/5 | duplicate_line, $11,004.73 | **quantity_spike, $610,768.51** | `WHERE ff.valid_exception = 0` silently converts the LEFT JOIN to an INNER JOIN, restricting the sum to findings that happen to carry feedback rows |

**S6 (4 reps) is not a breach.** It answered "Crestpoint Mechanical" — CRP01, the correct supplier, correct substance. The `numeric_from_gold` assertion demanded the $120.00 amount, which a supplier-name answer has no reason to contain. This is an assertion-shape mismatch, not a wrong answer. Recommend splitting S6's assertions or relaxing to `name_from_gold`.

## 3. The architectural finding: plausibility, not faithfulness

Every one of the nine passed the Verifier **legitimately**. In each case the number in the answer matches the number the SQL returned. Faithfulness held perfectly, as it has for three sessions.

What failed is the evidence itself — SQL that executed cleanly and answered the wrong question. That is Brief §9.1's **plausibility** job, and §9.3 currently defines it only as stats cross-checks on `run_sql` (row counts vs known table sizes, proportions vs known distributions, values vs known min/max). None of the nine trips those: 0, 662, an empty table, and $11K are all plausible-looking magnitudes.

**Faithfulness is built and works. Plausibility is a stub with a single check family.** Three sessions of hand-testing exercised the faithfulness side thoroughly and the plausibility side barely at all; the residual ledger (N9–N12) is entirely faithfulness-recall polish. This baseline is the first systematic look at the other half.

**Sharper still:** four of the five root causes are gotchas documented *verbatim in the grounding prompt the model receives*.

- `adjustment_totals` prints the exact correct S4 query, including `adjustment_flag = 1` and `rule_name = 'total_mismatch'`.
- `null_amount_findings` states that dollar aggregations "must LEFT JOIN finding_feedback and zero out excepted rows" — precisely what U5 inverted.
- `lines_to_findings` warns that joining on `invoice_id` alone cross-multiplies — MT2's bug.

The warnings are present, correctly written, and were ignored. Grounding-by-inclusion is not grounding-by-enforcement. This is an argument for mechanical post-checks on generated SQL, not for a longer prompt.

## 4. Why three human sessions missed it

Not luck — sampling. Per-row pass rates: C4 **0/5**, S7 1/5, S4 4/5, U5 4/5, MT2 4/5. Sessions 1–3 graded each of these rows once or not at all, and four of the five pass most of the time.

The gate verdict predicted the residuals were "stochastic, drafting-habit-dependent" and required N-run repetition with pass-rate thresholds. It was **right about the method and wrong about the target**: the damaging stochasticity is in **SQL generation**, not in drafting habits. The `emitted_tokens` machinery built to catch drafting coin-flips worked (see §6) — it just wasn't where the real risk lived.

## 5. What held

Worth keeping in proportion. The bulk of the engine is solid:

- **SEN-MONTH 5/5 and A1 5/5** — both purpose-built verified-zero traps passed every rep. The traps that existed worked; C4 slipped through a boundary nobody had written a trap for.
- **Every refusal row passed:** B6, R1, R2, U-META all 5/5.
- **Routing consistency:** `rule_metric [RT-fires, U5, U6]` → `run_sql` ×15, consistent. N6's fix holds under repetition.
- **B5 5/5, C5 5/5, S2 5/5, S3 5/5, NP1/NP2/NP4/NP5/NP7 5/5** — including the composite-join and compliance-prefix gotchas, which the model *did* apply correctly here.
- **Multi-turn works:** MT1 5/5, MT3 5/5; MT2's failure is a SQL bug in turn 2, not a context-carrying failure — "how many of those" resolved correctly against turn 1.

## 6. Secondary findings

- **XPASS annotation is wrong.** MT3 and P-L3Q XPASSed on N9 with *"the fix appears to have landed."* No fix has landed — the engine is at `c8b8ea7`, the gate-close commit. B4 still XFAILs on N9. The N9 annotation appears mis-assigned to two rows that don't state a bare file path. Recommend re-scoping N9's xfail to B4 only, and rewording the XPASS message so it doesn't assert a code change it cannot observe.
- **Breach detail lines are misleading.** They report "answer numerics `[]`" for table-envelope answers, because the numeric extractor doesn't reach table cells. This actively misled the first pass of this diagnosis toward "the answer stated no number" when the answer was in fact a table containing a *wrong* number. Fix: harvest table cells into the reported numerics, or label the envelope kind in the detail line.
- **Breach detection does not distinguish wrong from absent.** `_grade_rep` flags any content assertion failing at exit 0. That is the right conservative default, but it means an incomplete-but-correct answer (S6) rings the loudest alarm in the system. Consider a severity split: *contradicted* (a competing value present) vs *unsupported* (gold token absent, no competing value).
- **`engine_dirty` is a permanent false positive.** See the provenance note above: the flag counts untracked files, and the runner's own report output is untracked. Every eval run will report dirty. A provenance signal that is always on carries no information and trains readers to ignore it — which is exactly what you do not want from a field whose purpose is to invalidate compromised measurements.
- **NP3 fails 0/5 on `currency_format`** — the float-tail issue observed in session 3 (`8308.92139244107`), now measured rather than noted.
- **AMB1/AMB2 both 0/5, exit 0 not 4.** Clarify has now gone four sessions and 287 live turns without firing once. The open empirical question is settled in the negative: under current routing it does not fire on genuinely dual-reading questions. Worth a design decision rather than another wait.
- **HN-DIDRUN 5/5 at threshold 0.80** — the honest-negative path works for `did_run`. HN-ERRORS remains 0/5 on N12 as predicted (`recent_errors` still cannot express a clean day).

## 7. Recommended priority for fix-pass-3

The scope has changed. N9–N12 are verification *recall* gaps — correct answers downgraded, the safe direction. What this baseline found is the unsafe direction.

1. **Plausibility checks for `run_sql`** (new, highest priority):
   - **Empty-result challenge.** A zero or empty result on an existence question must not exit 0 unchallenged. Re-query without the most restrictive predicate; if that returns rows, refuse or clarify rather than answer.
   - **Fan-out detection.** A COUNT exceeding the row count of its base table is a join defect, mechanically detectable from the dictionary's row counts already in the grounding payload.
   - **Boundary convention.** Fix the trailing-window inclusion rule so "last 30 days" and "last week" resolve identically. C4 is 0/5 — deterministic, and the cheapest of these to fix.
2. **Gotcha enforcement.** Where a gotcha names an exact predicate (`adjustment_flag = 1`, `rule_name = 'total_mismatch'`, the feedback LEFT JOIN pattern), consider a post-generation lint that flags generated SQL contradicting it, rather than relying on the model to honor prose.
3. **N9–N12** as previously planned, demoted below the above.
4. **Bank corrections:** S6 assertion shape; N9 xfail re-scoping; breach detail numerics.

## 8. Cost, latency, protocol

225 turns in roughly twenty-five minutes. Clean turns 2.5–7s; worst was P-L3Q rep 2 at 30.5s and MT3's second turns at 14–24s. Cost consistent with prior sessions — single-digit cents per turn, a few dollars total. Cost is not a constraint on running this bank as often as wanted, which matters: **fix-pass-3 should be graded by re-running this exact command, not by hand.**

---

*This report is the "before" picture. Every row that failed here is a row fix-pass-3 gets graded against, and the nine breaches are the first entries in a class the project had not previously observed in itself.*
