# Fix pass 4 — residual triage (S5, S6, NP6; P-N11 post-slice)

The confirmation run (`fp3-confirm.jsonl`) carried three unattributed
failing rows. Attributed here from their inlined evidence so none of
them rides through another pass unexplained. Root causes one line
each; fixes either landed in this pass or queued with mechanism named.

## S5 (0/5) — three stacked causes, two fixed this pass

1. **N12** owned 4/5 reps: every refusal died on an
   `{{e0.output.errors|count}}`-shaped placeholder against the
   scalar-less `errors` list. Fixed — `error_count` landed.
2. Rep 4's numeric `11` from prose "March 11" is **N10**. Fixed —
   yearless date tokens match the `ts` strings recent_errors already
   pools.
3. Rep 4's entity `` `invoiceguard.benchmark_scoring` `` — the
   recent_errors harvest split identifiers at dots, so the dotted
   logger name never entered vocabulary whole. Fixed — folded into
   the N10 commit (`dotted_tokens`).

S5 carries no xfail and stays that way; expected to flip on the
re-run.

## S6 (0/5) — two causes, both queued (bank/eval-design edits)

1. Rep 1 delivered the correct verified answer ("Crestpoint
   Mechanical", table passthrough) and failed only
   `numeric_from_gold(amount=120.0)` — the assertion-shape mismatch
   already flagged in the 4b findings. **Queued:** the bank
   correction (`name_from_gold` on supplier, or split the amount off
   the pass criterion), citing `b3d8375` as its pattern — the same
   collision class: an engine mandate changed correct answers'
   surface, and the gold predates it.
2. Reps 2–5 layered `supplier_acceptance` / `review_reports.disposition`
   / `invoice_history` predicates onto the CREEPBACK category and
   self-annihilated to zero rows; the fix-pass-3 zero/empty-result
   challenge correctly capped them (exit 2/3 — the safety layer
   working on wrong SQL). **Queued:** a creepback /
   `prior_revision_id` grounding gotcha strong enough that the model
   stops adding acceptance predicates.

## P-N11 (0/5 on fp4-slice) — split residuals, neither is N11

The follow-up run's holdout, attributed from `fp4-slice.jsonl`:

1. Reps 1–2 never reached the e0-error/e1-recovery scenario: the
   informal name errored, the error named all seven valid
   components, and the licensed retry didn't fire — an **N5
   early-surrender** instance. Recorded as stochastic N5 residual
   data, **not actioned**; the row's new `setup` block excludes
   unreached reps from the denominator instead.
2. Reps 3–5 reached the scenario and prove the N11 fix working (the
   draft cites e1's `error_count` via placeholder). They fail on two
   other classes: an appended date-disclaimer ("The date 2026-04-15
   is not supported by the tool evidence" — the drafting-attention
   family, disclaiming a value that sits in the evidence arguments)
   and the backticked poolless `error_count` identifier — **N13's
   class** (envelope field names, above). Recorded; queued
   mechanisms unchanged.

## NP6 (1/5) — N13, newly assigned

**N13: structured-identifier claims live in no pool.** The drafter
names identifiers that are real and evidence-derived but that no
harvest yields whole and no matcher decomposes:

- dotted `table.column` composites (`` `invoices.status` `` —
  unmatchable though both halves match vocabulary individually; the
  sole cause of reps 4–5, which gave the correct 4-value gold
  answer);
- envelope/substrate field names (`` `enum_values` ``,
  `` `data_scan` ``, `` `top_values` ``);
- dictionary concept *names* (`` `invoice lifecycle` `` — only
  definitions reach `quote_corpus`).

The one passing rep is O1 text-block injection accidentally rescuing
the row. C1 rep 3's `` `run_status` `` miss is the same class.

**Queued mechanisms**, deliberately not folded in (each changes
matcher semantics and deserves its own regression surface):
(a) dotted `table.column` entity fallback — match the parts, label
`matched_derived`; (b) harvest concept names into `quote_corpus`
(`checks/dictionary.py`); (c) decide whether envelope field names
are matchable vocabulary or non-claims. N13 joins `XfailRef` when a
row first needs the annotation.

---

*Un-triaged and deliberately parked: NP3 (0/5, `currency_format`
float tails) — known since session 3, unchanged this pass.*
