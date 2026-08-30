# evals/ — Phase 4b answer-verification banks

This directory holds question banks, gold scripts, and run reports
for `engine eval run` / `engine eval grade`. It sits at the repo top
level **outside every engine-readable path** on purpose: substrate
stores and doc search serve `packs/…` only, so nothing the LLM ever
sees can include the bank or its gold answers.

Layout per bank (`evals/<name>/`):

- `eval.yaml` — runner/grader defaults (reps, thresholds, pack path).
  Deliberately not pack config: the pack must not know it is being
  examined, so nothing eval-shaped may live in `packs/`.
- `bank/*.yaml` — machine-readable rows (schema:
  `engine.eval.models.BankRow`). Expected-fail rows carry an `xfail`
  block naming the anomaly (N5, O1, WBV-*) and its root
  cause; flipping a row to expected-pass = deleting that block, a
  reviewed bank edit. Probe rows may carry a `setup` block on an
  expect (scenario preconditions over the rep's recorded
  invocations: `min_invocations`, `min_errored`, `min_ok`,
  optionally per-`tool`); a rep failing setup is
  scenario-not-reached and leaves the pass-rate denominator, and a
  row with fewer reached reps than its `reached_floor` (default 2)
  grades INCONCLUSIVE — neither pass nor fail, never XPASS, gating
  like a threshold failure unless the row's xfail predicted failure
  anyway.
- `gold/*.py` — one executable gold script per gold-bearing row.
  Every gold answer is **produced by executed code committed beside
  the expectation** — never transcribed, never remembered (the
  grader's-correction law). `engine eval grade --check-gold` executes
  all of them against the world and reports rot.
- `reports/*.jsonl` — run reports. The `<report>.work.db` sidecars
  are debugging scratch and stay gitignored (`*.db`).

Report-committal policy: reports are committable travel-back
artifacts, but not every scratch run belongs in history — commit
milestone reports deliberately (the post-4b baseline, pre/post
fix-pass pairs), name them meaningfully, and let ad-hoc runs live
uncommitted; multi-MB JSONL with inlined evidence is fine
occasionally, noisy as a habit.
