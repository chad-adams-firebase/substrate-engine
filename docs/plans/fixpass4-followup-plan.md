# Fix-pass-4 follow-up: proven flips out, N12 drafting-attention fix, setup assertions

## Context

Fix pass 4 (d51caad..56a98bc) landed N9–N12. The work-machine N-slice re-run (`evals/invoiceguard/reports/fp4-slice.jsonl`, untracked, verified: header engine_sha=56a98bc clean, seed 42, 8 rows × 5 reps, bank_hash e6b72355576bab68) proves six flips: B4, MT3, P-L3Q (N9), C1, C1b (N10), S5 (unannotated) all 5/5 exit 0 verified. Two holdouts, and the report **corrects the brief** on one:

- **HN-ERRORS (N12)**: 5/5 exit 2, single clean `check_execution recent_errors` call each rep. The rendered output `{"error_count":0,"errors":[],"kind":"check_execution","run_status":null}` — reps 1/4 deny error_count outright; reps 2/3/5 use it but type the `0` and disclaim component+date. The denial half is the drafting-attention defect Fix 1 targets.
- **P-N11 (N11)**: reps 1–2 exit 3 — single errored invocation, no licensed retry (N5 early-surrender; the error listed all seven valid components). **Reps 3–5 exit 2 DID reach the two-invocation error→ok scenario** — the drafter correctly cited e1's error_count via placeholder (N11's mechanism works), then failed verification on the appended date-disclaimer ("The date 2026-04-15 is not supported…") and the backticked poolless `error_count` identifier (N13's class). So the row is part-unreached (reps 1–2), part-failed-on-a-different-residual (reps 3–5).

User decisions (confirmed): skip the P-N11 question-phrasing edit (scenario already the modal path, 3/5); update both stale xfail notes to the observed mechanisms; an xfail row grading INCONCLUSIVE stays non-gating (exit 0 contribution) — INCONCLUSIVE gates exit 2 only on non-xfail rows.

Scope fence: no Phase 5, no other exit-semantics changes, S4/NP3/S6/N13 stay queued.

---

## Block 0 — bookkeeping (three commits)

**Ordering constraint:** grade fp4-slice BEFORE any bank edit — `_bank_hash` is over raw bytes and grading refuses on mismatch (`src/engine/eval/grade.py:139-144`).

### Commit 1 — the milestone report
- Run the grader on `evals/invoiceguard/reports/fp4-slice.jsonl` (CLI: `uv run engine eval grade --bank evals/invoiceguard ...`; check `--help` for the report flag), capture to `evals/invoiceguard/reports/fp4-slice-grade.txt` per the committed-pair precedent (baseline-4b `8020e37`, fp3-confirm `2619be2`). Confirm it reads INVARIANT ok / PASS (six XPASS lines, two xfail lines expected — XPASS does not change exit code).
- Commit both files. `.work.db` sidecar stays gitignored.

### Commit 2 — bank flips (precedent: d51caad)
Delete exactly five xfail blocks:
- B4 — `evals/invoiceguard/bank/scripted.yaml:65-72`
- C1 — `evals/invoiceguard/bank/scripted.yaml:119-125`
- C1b — `evals/invoiceguard/bank/scripted.yaml:140-142`
- MT3 — `evals/invoiceguard/bank/multiturn.yaml:61-70`
- P-L3Q — `evals/invoiceguard/bank/probes.yaml:41-50`

Retire `"N9", "N10"` from the `XfailRef` literal (`src/engine/eval/models.py:36-39`) and record why in the policy comment above it (lines 27-35), exactly as d51caad did for the WBV refs. N11/N12 stay (P-N11, HN-ERRORS blocks remain). Update `evals/README.md:14-18` ("N9–N12" → "N11–N12").

Commit message: notes the grade read PASS with two rows still broken — the xfail mechanism working as designed.

### Commit 3 — delete `docs/plans/fixpass4-plan.md` (review scaffolding, missed rider from last pass).

---

## Fix 1 — N12's drafting-attention defect (commit 4)

### Code
1. **`render_evidence`** — `src/engine/harness/drafter.py:47`: `invocation.output.model_dump(mode="json")` → add `exclude_none=True`. Extend the docstring (lines 31-36) with one line on why None fields are suppressed. Safe because:
   - Placeholders resolve from the live invocation output (`src/engine/harness/placeholders.py:110-111`), never the rendered string.
   - The verifier harvests independently (`src/engine/verifier/verify.py:105-114`; the clean-day 0 is grounded twice in `src/engine/verifier/checks/execution.py:75-93`).
   - The router's rendering is a separate function (`summarize_invocation`, `src/engine/harness/router.py:72-94`) — untouched, already pinned by `tests/test_harness_router.py:79-107`.
2. **Drafting-prompt rule** — `render_drafter_prompt`, `src/engine/harness/prompts.py:105-126`: add one bullet (after the failed-call rule at 120-123, the N11 precedent for this kind of rule): a scalar count of 0 in the evidence is an answer ("zero errors occurred"), not an absence of information; fields present with value 0 must be used, never disclaimed.
3. **Goldens**: none exist for the drafter prompt (only the `run_sql` grounding prompt has goldens, `tests/golden_grounding.py`) — nothing to regenerate. State this in the commit message rather than silently skipping.
4. **HN-ERRORS xfail note** — `evals/invoiceguard/bank/honest_negative.yaml:29-36`: rewrite the note (keep ref N12). Current note says it flips when error_count lands — it landed; the observed residual is the drafter denying/disclaiming present fields (and typing the 0 instead of the placeholder). Bank edit — fine, grading already done in commit 1.

### Tests (offline)
- **Unit** (`tests/test_harness_drafter.py`, beside the collapsed-render test at :60-88): render a recent_errors `CheckExecutionOutput(error_count=0, errors=[], run_status=None)` invocation → rendered line lacks `"run_status"`, keeps `"error_count":0` and `"errors":[]`. The existing :87 assertion (`'"run_status"' in second`) uses a did_run output with non-None run_status — stays green.
- **Scripted-drafter graph test** (`tests/test_harness_graph.py`) pinning the HN-ERRORS shape, modeled on `test_draft_cites_the_clean_invocation_not_the_errored_one` (:44-69) + `test_real_verifier_swaps_in_and_verifies_a_clean_turn` (:305-338, `real_verifier=True`): router scripted via `tool_call("check_execution", {...mode: "recent_errors", component: "benchmark_scoring", window over 2026-03-13...})` — the fixture's clean day (0 errors, `tests/test_tool_check_execution.py:73-85`) — then a draft citing `{{e0.error_count}}` saying zero. Assert: outcome verified, answer text says zero; the drafter's rendered evidence message (via `stub.calls`, pattern at :63-69) lacks `"run_status"` and contains `"error_count":0`. Builders: `tests/harness_support.py` (`build_ask_session`, `tool_call`).
- Trap to design around: the insufficiency guard (`src/engine/harness/graph.py:82-93, 371-390`) refuses claim-free hedging drafts — the scripted draft must carry the placeholder claim.

---

## Fix 2 — setup assertions / INCONCLUSIVE (commits 5–6)

### Schema (`src/engine/eval/models.py`)
- New `SetupSpec` model (extra=forbid): `min_invocations: int | None`, `min_errored: int | None`, `min_ok: int | None`, `tool: str | None` (scopes the counts to one tool; per spec "optionally per-tool"). All predicates over the turn's invocations from provenance.
- `Expectation.setup: SetupSpec | None = None` (`models.py:260-266`) — per-turn; a rep reaches the scenario iff every turn with a setup block satisfies it.
- `BankRow.reached_floor: int = 2` (ge=1) — row-configurable INCONCLUSIVE floor.
- Bank-hash safety: hashing is over raw bytes, so a defaulted schema field does not orphan historical reports (pinned by `tests/test_eval_bank.py:145`).

### Grader (`src/engine/eval/grade.py`)
- `_grade_rep` (:522-578) returns a tri-state (`passed | failed | not-reached`, failures). Setup predicates evaluate first from `_TurnView.invocations` (parsed from `evidence_payload`; `ToolInvocation.status` is `"ok" | "error"`, `envelope.py:252-266`). A missing turn on a setup-bearing turn → not-reached. **Breach detection still runs on not-reached reps** — the invariant outranks every annotation (matches the existing docstring's stance at :528-530).
- Row aggregation (:699-713): for a row with any setup, `reached` = reps not classified not-reached; if `reached < reached_floor` → status `"inconclusive"` (never xpass, even with an xfail block); else `met = passes / reached`. Rows without setup: unchanged denominator (`len(graded)`).
- `RowGrade`: add `"inconclusive"` to the status Literal (:86) and a `reached: int | None = None` field (None ⇒ no setup; renderer shows `reached n/N`).
- `exit_code()` (:119-128): an inconclusive row contributes exit 2 **only when the row has no xfail block** (user decision: xfail keeps it non-gating). Ordering stays breach(4) > rot(3) > 2 > 0.

### Report (`src/engine/eval/report.py`)
- `_MARKS` (:9-15): add `"inconclusive"` with a ≤5-char mark (column width at :64 assumes 5), e.g. `INCON`.
- Per-row line (:60-78): append `reached n/N` when `reached is not None`.
- Verdict map (:106-111) is a total lookup by exit code — exit 2 already has an entry, no new key needed; verify wording still reads correctly.

### Bank application (commit 6, can fold into 5)
- P-N11 (`evals/invoiceguard/bank/probes.yaml:5-29`): add `setup: {min_invocations: 2, min_errored: 1, min_ok: 1}` to its expect; default floor 2 stands. Rewrite the xfail note (keep ref N11): the drafter-anchoring mechanism is fixed (reps 3–5 cite e1); observed residuals are the date-disclaimer and poolless backticked `error_count`; reps 1–2 were N5 early-surrenders the setup block now excludes.
- HN-ERRORS: **no setup block** (single clean call, trivially reached).
- No question-text change for P-N11 (decided: the error→recovery path is already modal at 3/5; no artificial forcing).
- `evals/README.md`: document the setup family + INCONCLUSIVE semantics (one or two lines in the bank-schema section).

### Tests
Patterns from `tests/test_eval_grade.py` (`make_env` :43-76, `retry_payload` :408-432 — the existing errored-invocation payload builder to extend, `make_turn`/`make_record` :97-152, row-YAML string constants):
- Fabricated report where **no rep reaches** the scenario → row status `inconclusive`, exit 2, `render()` shows the mark and `reached 0/5`.
- Same row **with an xfail block** → status `inconclusive`, exit 0, and never `xpass` (the can-never-XPASS pin).
- **3/5 reach** (2 not-reached, 3 reached-and-passing) → graded on those 3: status ok, `reached 3/5` rendered.
- Predicate coverage: min_errored/min_ok/tool-scoped counting; a not-reached rep with a wrong-but-verified turn still records a breach.
- `tests/test_eval_bank.py`: setup block parses; invalid `reached_floor` (0) rejected.
- `tests/test_cli_eval.py`: exit-code propagation for an inconclusive (non-xfail) row → code 2, if cheap to add alongside :79-96.

---

## Residuals doc (rides commit 6)

`docs/fix-pass-4-residuals.md`: new `## P-N11` section in the existing per-cause numbered form (sections are S5 :8-22, S6 :24-40, NP6 :42-66):
- Reps 1–2: N5 early-surrender residual — the error named all seven valid components, the licensed retry didn't fire. Stochastic residual data, **not actioned**. (Rep 2 is the same shape as rep 1; record both.)
- Reps 3–5: scenario reached, N11 mechanism confirmed working; failures are the date-disclaimer (drafting-attention family) and the backticked poolless `error_count` (N13's class). Recorded; queued mechanisms unchanged.

---

## Verification

1. Full suite: `uv run pytest` — green, offline.
2. `uv run engine eval grade --bank evals/invoiceguard --check-gold` — green (0).
3. fp4-slice grade run (commit 1) happened **before** any bank edit and read INVARIANT ok / PASS.
4. Goldens: no drafter-prompt goldens exist; grounding-prompt goldens and generator fixtures untouched — confirm `git status` shows no fixture drift.
5. Prior regressions untouched: no changes to router rendering (pinned tests), placeholder resolution, or verifier harvest.

## After landing

Update auto-memory `substrate-engine-phase-status.md` (per the fixpass4-plan precedent): flips committed, N9/N10 retired, Fix 1/Fix 2 landed awaiting the next work-machine re-run for HN-ERRORS/P-N11.
