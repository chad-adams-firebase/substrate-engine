# Pin-pass residuals — the first invariant breach, mechanism by mechanism

The 2026-08-30 post-play-pass run (report `2026-08-30-post-playpass`,
committed d0bf7f3) produced the project's first invariant breach: 8
wrong-but-verified occurrences across three rows. Root cause settled
by close-run SQL comparison against `2026-08-30-post-block1` (old
model `openai/gpt-4o`): the pin to `openai/gpt-4o-2024-11-20`
(98b3232) changed SQL-writing habits, and three habits fell through
guard gaps the old model never exercised. The pin stays — it exists
to prevent exactly the silent drift that would otherwise make every
re-run noise. The lesson that cost an evening of confound-untangling
is now law in CLAUDE.md: model pin changes are isolated commits and
trigger a full bank re-run before any other change lands.

## MT2 (0/5) — the expression join

Every rep joined `findings` to `compliance_rules` via
`rule_name = CONCAT('compliance_', rule_code)` — a derived key with
10 codes across 4,216 rows, fanning to 107,509 vs gold 254 (17.8× the
largest queried table). `lint=None`: the join-condition parser read
only plain column equalities. `plausibility: []`: the count checks
skip multi-table queries by design. Shipped verified as
`table_passthrough`. The close-run model never joined at all — all
five reps ran `SELECT COUNT(*) FROM compliance_rules WHERE severity =
'CRITICAL'`, which the `invoices_to_compliance` note already
prescribes.

Fixed twice over: the lint challenges any ON condition that derives
its key with an expression and carries no plain FK-vouched equality
(sql_lint), and the joined-count bound refuses a count-shaped result
past 3.0× the largest queried table — 17.8× cannot ship at any exit
but refuse. The Dictionary Map's `rule_name_prefixes` gotcha now says
"never a join key" in as many words. Expected to flip 5/5; the
mechanism is deterministic and double-guarded.

## B5 (4/5) — the dead LEFT JOIN

"How many invoices received last week had findings?" drafted as
`LEFT JOIN findings` + `COUNT(DISTINCT invoices.id)`: the join
filters nothing, so the count is the window's *received* 161, not the
flagged 146 — B5's own gold predicts the wrong answer as its
`received` field. Wrong-question SQL violating no bound, and
COUNT(DISTINCT) slips the fan-out gate. The rep demonstrably read the
where-to-look guidance (exact prescribed window, exact count
expression) and still drafted LEFT JOIN — which is why the fix is a
guard, not more prose: a LEFT JOIN referenced only inside its own ON
condition, in any aggregate scope, draws the join-shape challenge.
The where-to-look entry also now names the join type. Expected to
flip; an overriding rep ships exit 2 via the recorded lint and cannot
breach.

## S2 (0/5) — the NULL-skipped AVG

`AVG(is_flagged)` over a LEFT-JOIN-built indicator: unmatched lines
are NULL, AVG skips NULLs, the rate saturates to exactly 1.0 vs gold
63/66 = 0.9545. The missing `COALESCE(..., 0)` is the whole defect,
and 1.0 is a legal rate no range check can reject. Fixed on both
sides: the lint challenges AVG over the nullable side of a LEFT JOIN
(subquery joins visible to this check), and the saturated-rate warn
takes the verified badge off any exactly-0.0/1.0 rate-named result —
warn only, so a legitimate 100% ships [UNVERIFIED] rather than
refused. The `rate_needs_unflagged_side` gotcha grounds the repair
shape. Expected to flip to exit 0 via the lint's COALESCE challenge;
an unrepaired rep lands exit 2, warn-capped, no breach.

## Bank-lag corrections (not breaches)

- **REC-CKG (1/5)**: the pinned model clarifies on the genuinely
  ambiguous 'health' (4/5 reps) instead of picking a candidate and
  retrying — the gold itself says `candidate_count: 2`. The row
  gained the AMB2-shaped clarify arm; the retry arm stays,
  at_exit-scoped.
- **PLAY-R1 (0/5)**: all five reps answered the two-status difference
  from the dictionary at exit 0 — right outcome, other mechanism.
  Route assertion widened to `must_include_any_of`; PLAY-R3 keeps the
  pure `app_primer` probe.
- **PLAY-R3 (1/5, genuine residue)**: "define each one" ground six
  dictionary calls into a budget refusal. The definitional bullet now
  routes plural/enumeration definitional questions to one primer
  call. Wording, not machinery.

## Known-open gaps (deliberate, unbuilt)

Recorded here because the next model-habit shift could walk straight
into either, and this doc is where the next diagnosis starts:

1. **Expression joins to subqueries are invisible to the lint.**
   `_JOIN_ON` requires a bare table name after JOIN, deliberately — a
   subquery alias carries no FK knowledge, and feeding it to the
   equality loop would challenge masses of legitimate SQL. An
   MT2-shaped derived-key join written against a subquery would pass
   the lint (the joined-count bound may still catch its fan).
2. **A fan-out inside a CTE or subquery evades the joined-count
   bound.** The bound applies at `len(queried) >= 2` with names
   resolvable in stats; `COUNT(*) FROM cte` presents as a
   single-table count over an unresolvable name and skips every
   bound — the count checks and the joined bound both stand down.

Neither is this breach's mechanism; neither is built. Watch for them.

## Watch-for on the re-run

Previously-green rows the new guards could push to exit 2: a rep that
drafts (a) a function-only ON condition without an FK-vouched
equality, (b) a LEFT JOIN plus bare AVG over the joined side — now
visible even through subqueries, (c) a grouped joined count summing
past the largest queried table, or (d) an exactly-0.0/1.0
rate-suffixed cell. No committed canonical template trips any of
these (`rule_savings` is SUM/CASE over a declared one_to_one join;
`creepback_suppliers` is plain FK joins). Honest line-grain counts
land at exactly 1.0× and pass — the bound is strictly-greater.

## Block 2 riders (2026-09-02, after the post-pin-pass re-run)

The re-run (`2026-09-02-post-pinpass`, committed 5b6b2e0) restored the
invariant: MT2 5/5, B5 5/5, S2 3/5 with both misses warn-capped. What
the report shows about the guards, mechanism by mechanism:

- **Expression-join lint (A1) and dead-LEFT-JOIN lint (A4): not
  live-witnessed.** Every MT2 and B5 rep executed on its first
  attempt with `lint=None` and `plausibility: []` — the Dictionary
  Map's `rule_name_prefixes` "never a join key" wording and the
  where-to-look entry's "an inner join" clause steered the model
  before either lint had anything to read. Both lints are verified by
  hand probes and by `test_tool_sql_lint.py` (MT2's CONCAT shape, the
  `||` spelling, B5's shape, the enrichment/semi-join/one_to_one
  negatives) and by the `run_sql` flow test; they have not fired on a
  live turn. Grounding first, guard behind it — the intended order,
  and the guard stays unexercised until a model habit walks past the
  grounding.
- **AVG-over-LEFT-JOIN lint (A3) and the saturated-rate warn: not
  live-witnessed either.** S2's first attempt in every rep wrote
  `AVG(CASE WHEN f.id IS NOT NULL THEN 1.0 ELSE 0.0 END)` over the
  LEFT JOIN — a NULL-safe indicator the A3 regex (bare
  `AVG(alias.col)`) does not read. What fired five of five was the
  fix-pass-3 fan-out check on the both-FK join `invoice_lines
  .invoice_id = findings.invoice_id`. Three reps repaired with the
  CASE indicator inside a subquery and verified; two rewrote the
  indicator as a per-row `COUNT(f.id)` over the same join, re-tripped
  the fan-out check on the resend, and shipped `[UNVERIFIED]` via the
  play pass's `fan_out_override` trace — the warn cap doing its job,
  on the older guard. No rate saturated, so the warn never ran. Net:
  of the pin pass's three new guards, none has fired on a live turn.
  The `rate_needs_unflagged_side` gotcha now states the indicator
  shape outright (CASE, never COUNT), which is the grounding the two
  override reps lacked.
- **P-N11 retired.** Reached 0/5, [INCON]: the play pass's
  definitional vocabulary puts every component name in the router
  prompt, so the errored-call-then-licensed-retry scenario the row
  existed to count stopped occurring. The retry is unit-tested; the
  row measured a firing rate that has no live path left. Deleted, not
  xfailed — a scenario starved by an unrelated fix is a fourth
  retirement shape, recorded in the eval models ledger.
- **W4 keeps its ASSOC xfail.** XPASS 5/5 twice (2026-08-30,
  2026-09-02). No association code has landed; the correct pairing is
  the pinned model's habit, not a checked property, so the next habit
  shift could flip it back and a deleted block would read that as a
  regression instead of the known gap. The note records the two runs.
- **S4 keeps its WBV-S4 xfail.** XPASS 5/5 twice. Checked for
  attribution: all ten reps wrote the gotcha's exact query, but the
  `adjustment_flag` gotcha is unchanged since the pack's first commit
  (da53ee0) and no engine or pack change since the play pass touches
  the mechanism; the only difference from the 4/5 post-Block-1 run —
  where the old model's rep 1 wrote `adjustment_flag = 0` and `NOT
  EXISTS` any finding — is the model pin (98b3232). A habit shift is
  not a fix. The note records the finding; the block stays until a
  landed change explains the flip.
- **O1's guard landed (2ccc5eb); B2's `xfail_ref: O1` stays.** A
  passage-valued placeholder now resolves only inside a fenced code
  block and retries otherwise. Test-verified, not live-witnessed; the
  per-assertion xfail comes off on the first live run that passes it.

## Coverage pass riders (2026-09-02, after Play Session #2)

Play Session #2 (browser at `864114d`, 11 free-play turns) produced
three wrong-but-verified answers in the one schema region the map had
never covered — reviews, corrections, supplier acceptance — while the
bank's same-day run read `INVARIANT: ok`. The coverage pass covered the
region (four executed metric templates, two concepts, two gotchas, five
join paths, five where-to-look entries), locked it into the bank (W-A,
W-B, W-C, R-A, F1), and landed the guards. What it deliberately left:

- **S4's WBV-S4 block retired; the standard is revised.** XPASS 5/5 on
  three consecutive runs (2026-08-30, post-pin-pass, post-Block-2)
  under one model pin (`98b3232`), every rep the gotcha's exact query.
  A pinned, reproducible model is not luck: three stable runs on one
  pin attribute to the pin, and an xfail that predicts nothing masks
  the regression it would have caught. The ref leaves the `XfailRef`
  literal; the row's note carries the sentence. ASSOC is unaffected —
  W4's pairing is a checked-nowhere property, not a stable habit.
- **W4 gates on reaching a drafted answer.** Post-Block-2 0/5 was five
  refusals (four early surrenders after the primer and the documents,
  one `{{e3.text.CONSTANT}}` path into source text) graded as expected
  failures under a block that names the association gap. `setup:
  {exit: [0, 2]}` makes a refusal scenario-not-reached; the ASSOC
  block stays on the pairing assertion until association verification
  lands (W2/W4, still queued).
- **The fixture is not extended to negotiation.** Partial compliance,
  justification text, negotiated middles: the production pack's question
  class, not InvoiceGuard's. Accepted and recorded, not built.
- **G-A's false downgrade on genuinely saturated rates stays.** A true
  100% on a large basis ships [UNVERIFIED] by design (the saturation
  warn cannot tell it from a NULL-padded AVG); the trade is accepted,
  not tuned.
- **A fraction written into a percent alias is warn-capped.** The
  display scale is the alias author's word; `ROUND(x, 2) AS flag_pct`
  over a 0–1 x renders 1.0% and sits inside the 0–100 bound. The
  `rate_scale_suspect` warn (a percent column whose values all sit at
  or below 1.0) takes the badge off; the digits stay the author's.
- **`_ratio` aliases carry no rate hint.** A ratio may exceed 1 and is
  not a percent; such a column renders as a plain number with no rate
  bound — the pack's `display.rate` lists say so.
- **The verbatim rule reaches run_sql only.** The router still
  paraphrases for other tools; the work store showed the loss only on
  run_sql's grounding, where the map's vocabulary lives.
