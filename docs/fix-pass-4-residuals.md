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
   surface, and the gold predates it. **Landed (Phase 5 opening
   rider):** the gold returns the supplier's code and name, the row
   gates on `name_from_gold [supplier, supplier_name]`, and the
   amount assertion is gone.
2. Reps 2–5 layered `supplier_acceptance` / `review_reports.disposition`
   / `invoice_history` predicates onto the CREEPBACK category and
   self-annihilated to zero rows; the fix-pass-3 zero/empty-result
   challenge correctly capped them (exit 2/3 — the safety layer
   working on wrong SQL). **Queued:** a creepback /
   `prior_revision_id` grounding gotcha strong enough that the model
   stops adding acceptance predicates. **Landed (Phase 5 interlude,
   play pass C1):** the `creepback_suppliers` canonical metric —
   manager-phrasing synonyms, a template over `findings.rule_name
   LIKE '%creepback%'`, and notes stating outright that acceptance
   predicates re-test what the finding already proves — plus
   synonyms on the creep-back concept so the phrasings reach both
   the router's vocabulary and the grounding's metric match.

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

## fp4b-holdouts — N13 confirmed, one fix landed

The holdout re-run (`fp4b-holdouts.jsonl`, engine c19ac7c) proved
N11 and N12 working — P-N11 3/5 with reached 5/5, every draft citing
e1's `error_count` via placeholder; HN-ERRORS 1/5 with rep 3
verified at exit 0, that row's first delivered answer — and left
both rows failing on this class. Pool census of the clean-day
`recent_errors` envelope: `errors: []` yields no vocabulary,
`run_status: null` and no log lines yield no strings or corpus;
only `numbers = [0, 0]` exists. The unmatched claims were
`` `error_count` `` (P-N11 reps 2–3; "fails exactly when backticked
emitted, 2 with 3 without" — the fp4-slice date-disclaimer did not
recur) and `` `benchmark_scoring` `` plus `2026-04-15` (HN-ERRORS
reps 1,2,4,5 — the `backticked` and `iso_dates` notes co-occur
perfectly there, so neither isolates a cause; the verdict records
show both unmatched). The three facts live in the invocation's
*arguments* and the envelope's *field names*, which no check read.

**Landed** (`checks/invocation.py`): a tool-agnostic harvest of every
ok invocation's record — identifier-shaped argument values →
vocabulary, whole ISO timestamps → strings, rendered field names →
vocabulary (through the drafter's own None-suppressed view). That
settles (c): envelope field names are matchable vocabulary, exactly
as rendered. Mechanism (b) landed alongside (concept/metric/gotcha
names reach the quote corpus). Mechanism (a) stays queued: no pool
carries table→column structure, so matching the parts would ground
cross-table composites.

Both xfail blocks re-attributed to N13; N11/N12 retired by
re-attribution with mechanisms proven (the third retirement shape).
Next-run expectation at the time: P-N11 and HN-ERRORS flip together
(the N9 acceptance pattern). What happened instead is in the
n13-witnesses section below — HN-ERRORS flipped in substance and
P-N11 did not reach its scenario often enough to say. **NP6 stays deliberately unannotated**: its
misses mixed backticked dictionary field names (`enum_values`,
`top_values`, `data_scan`), which the field-name harvest now covers,
a concept name (b), and the composite (a) — so it may flip partially
from (b)+(c) alone with only the composite case left behind. It sits
in the re-run witness set precisely to measure that decomposition;
an xfail block now would blur what the run is measuring.

## n13-witnesses — N13 closed; HN-ERRORS closed; P-N11 onto N5

The witness re-run (`n13-witnesses.jsonl`, engine 17429b4, seed 42,
rows P-N11 + HN-ERRORS + NP6) graded exit 4 with five breaches, every
one a `contains` miss on a verified-correct body; no failure in the
run was a backticked-identifier failure. N13 is closed in substance
and retires from `XfailRef` with no remaining rows.

**HN-ERRORS — closed.** 5/5 verified at exit 0, all three claims
grounded on attempt 1 (the argument-borne `benchmark_scoring`, the
window date, the injected 0). Reps 3–5 answered "The
`benchmark_scoring` component had 0 errors on 2026-04-15." and failed
only `\b(no|none|zero|clean)\b` — the pattern had no digit. The
assertion was the defect: it gains `|0` (P-N11's identical pattern
too) and the xfail block is deleted. Grade-side consequence: breach
is now by kind — a `contains` miss gates the rep's threshold and
never alarms (both historical false alarms, A1's window literals and
this one, were pattern-kind; both catastrophic shapes, S4/S7, were
`numeric_from_gold`, untouched). `not_contains` still breaches.

**P-N11 — migrated N13 → N5.** Rep 1 reached the e0-error/e1-recovery
scenario and verified at exit 0 with the backticked `error_count`
matched from the envelope's field names — N11 and N13 both proven for
this row. Reps 2–5 took the error naming all seven valid components
and did not retry (three refusals, one unverified shrug): reached
1/5, INCONCLUSIVE. What the row measures now is the licensed retry's
firing rate, the addendum's N5. Landed alongside: the protocol
prompt's retry sentence moves from permission to expectation
(MUST-form, still exactly one retry). Next-run expectation: reached
at or above the floor; the setup block and floor are unchanged.
*Closing the thread (2026-09-02): after N5 retired on the post-N13
bank, the play pass's definitional vocabulary starved the scenario
outright — reached 0/5 on the post-pin-pass run — and the row was
retired in Phase 5 Block 2; see `docs/pin-pass-residuals.md`.*

**NP6 — 4/5, still deliberately unannotated.** Reps 1,2,4,5 gave the
seven-value lifecycle list verified at exit 0; each had the
`` `invoices.status` `` composite unmatched on attempt 1 and redrafted
it to "`status` in the `invoices` table" — mechanism (a) is still
live and redraft-absorbed, and stays queued. Rep 3's delivered body
listed only CLOSED and LAPSED, but its attempt-1 draft enumerated all
four gold statuses; the Verifier marked backticked
`` `NO_REVIEW_NEEDED` `` and `` `READY` `` unmatched and the redraft
deleted them. Cause: `StatsCheck.harvest` put `top_values` into
`strings`, while a backticked identifier extracts as an entity claim
that shops `vocabulary` only; CLOSED/LAPSED survived because the
`status_is_current` gotcha text tokenizes them. Not drafting
completeness — a pool-shape gap. **Landed:** identifier-shaped top
values now enter vocabulary as well (`checks/stats.py`), pinned by a
replay of the rep-3 enumeration. The dictionary field-name misses
from the earlier NP6 census (`enum_values`, `top_values`,
`data_scan`) did not recur.

---

*Un-triaged and deliberately parked: NP3 (0/5, `currency_format`
float tails) — known since session 3, unchanged this pass. (Since
resolved: the Phase 5 Block 0 money display hint flipped NP3 to 5/5
on the 2026-08-30 post-Block-1 run.)*
