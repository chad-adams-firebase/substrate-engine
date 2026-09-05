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

## Duration pass riders (2026-09-02, after the post-coverage run)

The post-coverage run (report `2026-09-02-post-coverage`, committed
`b8ffdaa`) breached twice, both on W3, while everything else was clean
or better than predicted (Play-#2 rows 5/5, W4 reached 5/5, B2 5/5 —
the third clean B2 in nine runs). Both W3 breaches were durations, and
they made the third instance of one pattern: a model SQL habit through
a guard gap in a formatting class. Money got sum caps in the play pass
(W1's fanned totals), rates got bounds and saturation in the pin and
coverage passes (S2's NULL-padded AVG, G-A's scale-suspect), durations
got a humanizer in Block 2 and nothing else. The rule those three
derive is written here as a rule, not a note:

- **Every display-hint kind carries a plausibility bound.** A hint is
  the renderer's knowledge of what a column means; the Verifier reads
  the same hint, so a bound never disagrees with the digits shown.
  Money: SUM ≤ mean × non-null count, AVG within [min, max]. Rates:
  within [0, 1] or [0, 100] at the hint's scale; saturation and
  scale-suspect warn. Durations (this pass): an aggregate below one
  second warns (the floor, suppressed by a same-row count under the
  basis, as with rates), and a cell longer than the queried data's
  timestamp span fails (the ceiling; a SUM is exempt, an item the parse
  cannot classify warns). A new kind lands with its bound in the same
  commit, or it does not land.
- **W3 rep 4 — interval arithmetic, off by 86,400×.** The model paired
  the entering and leaving transitions exactly as the gotcha asked,
  then wrote `AVG(r.at - rr.at) / 86400 AS avg_time_in_days` across a
  CTE. Both are INTERVALs in DuckDB; dividing the average interval by
  86400 yields an interval of 41,667 µs, serialized `0:00:00.041667`,
  which the humanizer read honestly as "0 seconds". Verified: a string
  cell is not 0, so the zero challenge never saw it, and nothing could
  see interval arithmetic. Fixed three ways: the interval-arithmetic
  lint challenges a timestamp difference scaled by a literal before it
  runs (the select-list parse resolves the subtraction through the CTE;
  EPOCH/DATE_DIFF/JULIAN forms are Opaque and silent by construction);
  the degenerate-duration floor takes the badge off an override; the
  `time_in_status` gotcha now names the unit shapes. Expected to flip:
  the rep-4 shape costs one repair round and ships EPOCH-first, or an
  override ships `[UNVERIFIED]` and cannot breach.
- **W3 rep 5 — the humanizer boundary and the unit-blind grader.**
  Correct SQL (`JULIAN(r.at) - JULIAN(e.at)`, averaged) put one hour at
  0.04166666651144624 days = 3599.99998 s, a hair under the boundary:
  the humanizer chose minutes, printed "60 minutes", and the grader
  extracted 60 against gold 1.0. Both humanizers now round to the
  millisecond before choosing a unit (floor of x × 1000 + 0.5 on both
  surfaces, so a tie rounds the same way in Python and JavaScript), and
  `numeric_from_gold` gains `unit`: W3's gold compares in seconds, so
  "60 minutes", "1 hour" and "1:00:00" all match and "0 seconds" and
  "1.0 days" do not — a bare number is not a stated duration under a
  unit, which closes the false pass unit-blindness allowed. Expected to
  flip: 5/5.
- **REC-SQL — the verbatim rule's exception (5/5 → 2/5, attributable).**
  The rule sent the SQL-shaped question through verbatim, the bounce
  fired as designed, and three reps refused instead of rephrasing: the
  rule read as forbidding the one rephrase the bounce asks for. The
  router bullet and the tool description now carry the exception
  beside the rule — after a bounce, the question in plain English is
  the licensed retry, not a paraphrase — pinned together so they cannot
  drift apart. Expected to flip: back to 5/5.
- **S2 — the row grades the number it computes; the indicator is
  EXISTS (0/5, bank lag plus the gotcha's own shape).** All five reps
  computed 0.9545. Three verified as a one-cell rate table rendering
  95.5% and failed `contains \byes\b` — a prose assertion against a
  table answer, the table-MUST watch item's cost, evidence for a later
  revisit rather than the revisit. The numeric is now the check; an
  item-code contains pins the SQL's filter through the caption. Two reps
  were warn-capped by `fan_out_override` on the gotcha's own
  recommended shape, the CASE indicator over the line-grain LEFT JOIN.
  The recommended shape is now `CASE WHEN EXISTS (...)`, the
  `correction_application_rate` template's, which never meets the lint
  (63/66 = 0.9545 in the world). The line-grain join is deliberately
  not declared `one_to_one`: a line may carry more than one finding by
  schema even if none does here — the pin-pass S2 reps were correct by
  luck, and a cardinality claim would make the lint vouch for luck.
  Expected: ≥ 4/5.
- **W4 and W2 keep their ASSOC blocks as deliberate keeps.** W4 XPASS
  4/5 with reached 5/5 (the setup gate did its job; the refusal storm
  did not recur), W2 XFAIL 0/5 as predicted. No association code has
  landed, so the grader's deletion prompt every run was asking for the
  wrong thing. The xfail block gains `keep_until`, naming the milestone
  that retires it; an XPASS under it reads "deliberate keep until
  association verification" and the ledger says so. The block still
  comes off in a reviewed bank edit, never on a pass rate.
- **What this pass leaves.** One-argument `AGE(x)` and `NOW() - col`
  are outside the interval lint (unknown functions bail the parse to
  Opaque; wall-clock SQL is already steered away by the coverage line).
  The humanizer does not promote at the unit ratio: 3598 s still reads
  "60 minutes", honest at one decimal, and the unit-aware grader reads
  it as 3600 s. Byte identity of the two humanizers is pinned by source
  text, not by executing JavaScript — the work machine needs no node. A
  legitimate AVG duration without a same-row count ships `[UNVERIFIED]`
  under the floor, the basis rule's known cost, as with rates; and an
  EPOCH-shaped item past the ceiling warns rather than refuses, since
  the parse cannot rule out a SUM there. Association verification
  itself stays queued.

## Guard pass riders (2026-09-02, after the post-duration run)

The post-duration run (report `2026-09-02-post-duration`, committed
`d42687b`) breached three times on AMB2 ("How many invoices are open?")
while everything the duration pass predicted held (W3 5/5, S2 5/5,
REC-SQL 4/5, keeps reported as keeps). The class is new: **a guard
caused the wrong answer.** Rep 1's attempt 1 — `COUNT(*) AS
invoice_count FROM invoices WHERE status IN ('RECEIVED', 'READY',
'CLAIMED', 'IN_REVIEW')` — was correct (78, in the gold set). The enum
lint challenged the three never-observed members and added "'RECEIVED'
is an observed value of `invoice_history.from_status`,
`invoice_history.to_status`, if that is the column meant"; the model
read that as an instruction and attempt 2 counted `invoice_history`
instead: 6,432 transitions, verified, `plausibility: []`. A filtered
single-table count under invoice_history's own row_count passes every
bound, and nothing tied the alias's noun to a table. Two rules come out
of it, written here as rules:

- **A challenge names what is wrong with the query; it never suggests
  a different subject table.** Audited across all three lints: the
  fan-out check names unqueried tables only inside its parenthesised
  reasons ("(both columns are foreign keys to invoices.id — …)"); the
  join-shape, NULL-semantics and interval checks name no table; the
  enum check's cross-column clause was the one sentence a model could
  read as "query this other thing instead", and it is gone. Mechanised
  in `tests/test_lint_challenge_principle.py`: every dictionary table a
  challenge names outside parentheses is one the statement already
  queries, on each lint's own breach fixture.
- **A new plausibility bound or lint is run read-only against the
  latest committed report before it lands, and the change states its
  hit count and every hit's attribution.** The tool is `engine eval
  exposure --bank … --report … [--check NAME]`: every executed run_sql
  statement in the report faces today's Verifier plausibility suite
  and the three lints under the pack's current substrates, offline,
  every hit attributed to row, rep, turn and statement. The
  entity-count bound below was the first guard measured this way (208
  statements, 53 count aliases resolved, 3 hits, all three AMB2), and
  `tests/test_eval_exposure.py` pins that attribution against the
  committed report so the next guard is measured against the same
  baseline.

What landed, mechanism by mechanism:

- **AMB2 — the enum lint keeps the model on its table, and speaks only
  where the filter is guaranteed empty.** The challenge now reads
  "`invoices.status` never takes 'RECEIVED' in this data — observed
  values: CLOSED, LAPSED, NO_REVIEW_NEEDED, READY. Keep the query on
  `invoices`: choose among its observed values, or ask the user which
  they meant." The parenthetical naming where else the value lives
  does not survive: the grounding prompt already renders every enum
  column with its full value list, so the pointer added nothing a model
  could not see, and in both live cases the pointed-at column was the
  wrong place (AMB2's invoice_history; R-A's supplier_acceptance, when
  the answer was correction_ignored findings). The coverage-pass ruling
  ("say where it lives") is reversed. And the lint's unit of judgement
  is now the column: it fires on a lone `=` against a never-observed
  value (R-A) or an IN list / OR chain with no observed member, and is
  silent on a mixed list — attempt 1 was correct reasoning ("open" is
  the four non-terminal lifecycle states, read from the grounding's own
  `to_status` values; that the resting column shows two of them is
  NP6's 4-vs-7 tension, not a defect), its never-observed members are
  no-ops like the `<>` exemption, and a repair round on a correct query
  is the mechanism that breached, in weaker form. Accepted gap: a typo
  beside an observed value (`IN ('READY', 'CLOSD')`) undercounts
  silently; the grounding's full value list is what prevents it.
- **AMB2 — the entity-count bound.** `run_sql.entity_count_exceeds_table`
  (warn): a COUNT column per the select-list parse whose alias names a
  stats table by a stem rule (strip a count affix — `_count`, `_total`,
  `n_`, `num_`, `number_of_`, `count_of_`, `total_` — then match the
  noun, whole or its last segment, singular or plural, against the
  table names; no affix or no unique table means no claim) must not
  exceed that table's row_count plus the row-count tolerance. Scalar
  shape reads the cell, grouped shape sums an untruncated column. Warn
  because an alias is a convention, not a type. Accepted cost: a
  grouped count whose key does not partition the entity (invoices per
  rule) sums past the table honestly and loses its badge; no live
  statement in 305 turns did. Knob `enforce_entity_count_bound`.
- **AMB2's bank row keeps `numeric_from_gold` with breach semantics as
  its only exit-0 content check.** No caption regex on invoice_history:
  a correct answer may legitimately read it (the latest transition per
  invoice is a sound way to derive current status, and lands at 78);
  the mechanism is diagnosed in the verdict by the entity bound, which
  is a diagnosis, not a regex. Expected: 5/5 across both arms — the
  exit-0 reps execute attempt 1 at 78 with `enum_lint=None`, and a rep
  that still walks to the history table ships `[UNVERIFIED]` rather
  than breaching.
- **REC-SQL accepts 0 or 1 bounce.** The post-duration miss was rep 3
  rephrasing before the bounce ("How many rows are there in the
  invoices table?" on its first call, 1,990): the row measures
  recovery, and pre-emptive recovery is recovery. `retry_count.errors`
  takes a list. Expected: 5/5.
- **The interval lint joins A1/A4 as verified-but-not-live-witnessed.**
  Zero challenges in 305 turns: the `time_in_status` gotcha's unit
  paragraph steered every rep to EPOCH/DATE_DIFF first. Test-verified
  (the W3 rep 4 fixture, every scaled shape, every correct shape) and
  run-tested through `run_sql`; grounding first, guard behind it.
- **Three parser residuals closed.** Quoted identifiers
  (`"invoices"."status"`) bypassed every lint and every parse-based
  bound; one helper, `unquote_identifiers`, now runs at the four
  analysis entry points (the executed statement is never rewritten).
  An unaliased table before JOIN (`FROM findings JOIN
  compliance_reports`) lost the next table to the alias scan — surfaced
  by the principle test, latent under this pin (the model aliases
  everything), fixed with a keyword lookahead. And EPOCH/DATE_DIFF/
  JULIAN parsed as Opaque, so the duration ceiling could only warn on
  the gotcha's own recommended shapes; they are Numeric now with their
  arguments visible — `AVG(EPOCH(a - b)) / 3600` past the span fails,
  `SUM(DATE_DIFF(...))` is exempt, the floor reads the AVG
  structurally, and `EPOCH((a - b) / 3600)` draws the interval lint.
  The recommended shape must never be the shape the guards cannot
  read — the S2 lesson, third application.
- **What this pass leaves.** `EXTRACT(EPOCH FROM …)` stays outside the
  parse — its inner FROM ends the select-list scan, so the item never
  resolves (ceiling warn-only, floor blind); pinned as a known shape,
  not built. SQLite's `strftime('%s', …)` is text until cast and stays
  Opaque. The mixed-list typo gap above. The multi-membership grouped
  count above. Association verification stays queued.

Watch-for on the re-run: a green row whose SQL aliases a count with an
entity noun over a fanning join now loses its badge — correct
direction; note it. And any enum challenge at all — after the
narrowing, one fires only on a filter guaranteed empty.

## Block 3 riders (2026-09-02, after the post-guard-pass run)

The post-guard run (report `2026-09-02-post-guard`, committed
`850f869`, bank `5b2e1e127616bdc0`) read `INVARIANT: ok`. Block 3
(workspaces, conversations, the inspector) landed on it without a bank
re-run: nothing in the block is model-facing — no prompt, lint,
verifier, grading or map text moved — and the committed report grades
identically at the block's HEAD (the only diff is the engine-drift
warning naming the newer sha). What the ledger records from the run:

- **Verified-but-not-live-witnessed, the standing list: the
  expression-join lint (A1), the dead-LEFT-JOIN lint (A4), the
  interval-arithmetic lint, and the entity-count bound.** The run's
  five `fan_out_override` traces are the older guard; `interval_
  arithmetic_override`, `enum_literal_override` and
  `entity_count_exceeds_table` each read zero across the report. All
  four are test-verified on their breach fixtures, run-tested through
  `run_sql` or the Verifier, and replayed by `engine eval exposure`;
  none has fired on a live turn. Grounding first, guard behind it —
  the order the design intends, and the list stays here until a model
  habit walks past the grounding and one of them speaks.
- **The inspector shows the per-turn slice of what exposure computes,
  recorded, never recomputed.** Each attempt's three lint fields and
  the verdict's plausibility records are what the turn wrote; the
  report-level replay stays a CLI artifact.

Deferred to the Polish Pass after this block, not built here: W1's
false-downgrade pair (direction-blind fan-out reasoning; the
`sum_vs_stats` cap under a rounded `null_rate`); a text-form
`give_answer(...)` parsed as the call it is (B2's nine-run root
cause); the fan-out challenge's remaining "aggregate from the table
that carries the filtered column" phrase; placeholder negative indices
and the `output.` prefix (S5).

## Polish Pass riders (2026-09-03, after Block 3)

Nothing here was a breach. Every item was a correct answer denied its
badge, a correct question refused, a right call in the wrong channel,
a placeholder the resolver could not read, or a known interpretation
left unnamed — the failures a demo audience notices without a wrong
number ever appearing. Two were confirmed live from the browser on
2026-09-03 (`packs/invoiceguard/work.db`, conversation 3); the pass
lands under one bank run, and every verifier/lint change was measured
first with `engine eval exposure` against the committed post-guard
report (`850f869`, 206 statements) and against the browser's own
conversation (`--work-store --conversation 3`, 4 statements — the
verb's new source, so a manager's turns are measured like a report).

Exposure, before → after, by commit:

| check | report before | report after | work store before | after |
|---|---|---|---|---|
| `lint.fan_out` | 10 | 3 | 1 | 0 |
| `run_sql.fan_out_override` (recorded) | 10 | 10 | 1 | 1 |
| `run_sql.sum_vs_stats` | 20 | 0 | 0 | 0 |
| `run_sql.count_vs_stats` / `filtered_count_bound` / `distinct_vs_stats` | 0 | 0 | 0 | 0 |
| every other check | unchanged | unchanged | unchanged | unchanged |

The override row reads the challenge recorded on the executed attempt,
so a replay cannot move it; it follows the lint on the re-run. The
three surviving challenges are W1 turn 1 reps 1/2/5 — a join to a
grouped CTE by a key nothing vouches for, deliberately left. No clean
statement was newly flagged; a first draft of the direction rule
flagged W-B's five reps and the `correction_application_rate` template
through a CASE over a correlated subquery, and the grain-attribution
rule below resolved it before anything landed. The prototype replay
also counted count-bounded statements under both gates: 68 → 68.

- **The count gate was a regex (W-D, live).** "How many invoices do we
  receive per day on average?" drafted as `COUNT(*) * 1.0 /
  COUNT(DISTINCT DATE(received_at))`, gold-exact at 30.615, refused:
  `select count(` matched inside a ratio of counts, R4's sibling. The
  lone-cell count checks now read the select-list parse —
  `Aggregate("count")` at the root pins to row_count, `COUNT(DISTINCT
  col)` to distinct_count (the parse records the flag), anything else is
  not a count and gets no row_count bound; the joined-count scalar shape
  reads the same classification, and `_COUNT_ONLY` / `_COUNT_DISTINCT`
  are gone. Accepted cost: `COUNT(*) + 0` is arithmetic, unbounded.
  Expected: W-D 5/5 at exit 0 — the refusal was the finding.
- **Direction-blind fan-out reasoning (W1, the flagship table).** A join
  along a foreign key, `one.id = many.fk`, repeats each one-side row
  once per many-side row and never the many side. The lint read only
  which table stood after JOIN, so `SUM(invoices.invoice_total)` per
  supplier (the many side, grouped by the one side) was challenged on
  every W1 rep of four runs, and the flagship's correct resend — each
  aggregate in its own correlated scope — drew the challenge on the
  lines join inside the scope that aggregates the lines. The rule now:
  in a scope whose steps are all vouched one-to-many joins, the
  repeated tables are each step's one side plus many sides that share a
  one side (siblings repeat each other); a scope with a step nothing
  vouches for repeats every table, as before. An aggregate is
  attributed to the tables its argument reads; one reading no outer
  column (`COUNT(*)`, a CASE over a correlated subquery) counts the row
  grain and is attributed to the FROM table, which keeps lookup chains
  and the `correction_application_rate` template silent. It fires only
  when it reads a repeated table. Attempt 1 (triple LEFT JOIN, two
  SUMs) is challenged naming `SUM(i.invoice_total) reads invoices` and
  the lines step; attempt 2 is silent; W1's first turn is silent. New
  true positive: an aggregate over a lookup's one side
  (`SUM(suppliers.credit_limit)` from invoices), which the from-side
  exemption let through by table position — none in 210 statements.
  Fixtures changed deliberately: `AVG_FANOUT` read the many side and now
  reads the one side (the old shape pinned silent); `W1_OVERRIDE`'s
  reason names the lines step. Expected: W-E 5/5; W1 ≥ 3/5 — see below.
- **The cap under a rounded `null_rate` (W1's second blocker).** Every
  correct SUM summed to the true 16,683,608.50 and drew `sum_vs_stats`
  against a cap $6.90 short (mean × 1,982.99918 for 7 nulls in 1,990
  rows stored as 0.003518). Decided: no `null_count` — it would rotate
  the stats manifest, and both exposure and grade refuse a report whose
  world differs from the pack's, so the committed baseline this pass
  measures against could no longer be measured; it would also leave the
  mean's rounding ($0.0006 short). The cap reads the row at its stored
  precision instead: the non-null count is the largest integer
  consistent with the stored rate (exactly 1,983; unique under ~500k
  rows; never above row_count) and the mean is read at its upper
  half-unit. `STATS_DECIMALS = 6` lives once in the substrate models and
  the generator rounds with it. World manifest `ac4b8abd4eb9c07e` and
  stats manifest `13a9fe6305a057be` untouched. A 5× fan still fails.
- **W1's third blocker, found in planning: the wording.** All five reps
  read "Add invoice-line totals" as a line *count*
  (`total_invoice_line_count`), so the `total_line_amount` numeric would
  keep failing under both guards. Turn 2 now asks for "each supplier's
  total invoice-line amount (the summed line prices)". Expected: W1
  ≥ 3/5 — turn 0 executes attempt 1 unchallenged at exit 0; turn 1 reps
  that write a correlated or derived-table line total pass; a rep
  joining a grouped CTE by an unvouched key still overrides to exit 2
  (3 of 5 under the old wording; the sharpened wording changes the
  shape, so the split is a guess, not a mechanism).
- **The fan-out challenge's last destination phrase.** "aggregate from
  the table that carries the filtered column" is gone. The paragraph
  names the aggregate, the table it reads, and the step that repeats it,
  then "Aggregate each side in its own scope, then join the results;
  count an entity with COUNT(DISTINCT <table>.id)". The shared-target
  reason no longer names the unqueried target table, and the principle
  test reads parentheses too: every table a challenge names, anywhere,
  is one the statement queries — on eleven fan-out fixtures, the enum
  fixture and the interval fixture.
- **B2's chronic 1/5: the right call in the wrong channel.** The router
  writes `give_answer({"shape":"prose","evidence_index":3})` as text —
  14 of 86 recorded raw responses across the four post-* reports, all
  JSON-valid — and was nudged into the eight-step budget four times per
  rep. `parse_route` reads a control verb spelled as the whole response
  (optionally behind the transcript's "Requested: " echo) as that call,
  validated by the same models; a real tool written as text (one
  `traverse_code_knowledge_graph`, B2 rep 4) stays a violation — the
  closed surface is entered by tool calls. The trail says "text-form
  give_answer parsed as the call" with the raw response beside it, and
  the habit has a number: `TurnRecord.lenient_parses` beside `nudges`,
  summed per row and across the run in the grade ("router: 4 nudges, 2
  text-form calls parsed"), so a pin that changes the habit shows in
  the grade. Expected: B2 5/5 — reps 1/2/4/5's third response parses.
- **S5's placeholder.** `{{e1.output.errors[-1].invoice_id}}` — the last
  error, named the way anyone names it; the index grammar accepted only
  non-negative digits. Indices may be negative; the `output.` tolerance
  is a rule of the walk (an `output` segment that is not a key where it
  stands is the wrapper, at any depth; a genuine field wins). Expected:
  S5 5/5.
- **"Savings realized" named no reading (W-F, live).** The answer used
  feedback-authored non-excepted finding amounts (nova $32,584.92) —
  gold-exact for that reading, already the third `recovered_opportunity`
  declares — and the question reached only `rule_savings` through
  "savings". The metric gains whole-phrase synonyms (no bare "saved":
  it would pull the metric into "saved to the queue"; "realized" carries
  the bank question), the drafter's interpretation line shows a term's
  synonyms, and the grounding renders metric synonyms like concept
  synonyms (golden fixtures: one line each). Expected: W-F ≥ 4/5, any
  declared reading's number, the reading named.
- **Web riders.** Legacy turns draw their chip from the trail's finalize
  event and the recorded verdict; `engine store backfill-questions`
  recovered the dev store's 18 questions from the checkpoint threads
  (tied to the pre-Block-4 Message-pair layout, said so in
  `harness.graph.question_of_turn`; Block 4's reconciliation list
  carries the update or retirement); `/api/evidence/<ref>` is visible
  only through the caller's conversations; the text rendering carries
  one charset.
- **The bank.** W-D, W-E, W-F with executed gold; `pattern_count` joins
  the closed assertion union (the flagship's dash cells are twelve — six
  suppliers with no invoices, two columns — a number gold asserts like
  any other); W1's turn 2 reworded. `--check-gold` PASS.
- **What this pass leaves.** Inferring that a grouped CTE or derived
  table joined by its group key is one-per-key (W1's CTE shape stays an
  unvouched join); text-form real tool calls; the per-day refusal's own
  turn, absent from the dev store (likely a deleted conversation — the
  statement is reproduced from the brief); `_COUNT_ALIAS` for the
  grouped joined-count shape, unchanged. Association verification stays
  queued; W2/W4 keep.

Watch-for on the re-run: a statement the direction-aware lint now
leaves alone that fans (the replay shows none of 210); a count answer
previously bounded and now not (68 → 68 in the replay); a green row
whose SUM reads a lookup's one side and is newly challenged (none in
210); any `fan_out_override` at all outside W1's CTE reps. The
verified-but-not-live-witnessed list is unchanged: A1, A4, the interval
lint, the entity bound.

## Block 4 — context management (2026-09-03)

Brief §10.3, the last Phase 5 block. Reconciled against 740ec5d before
it was built; what had moved underneath the plan, and what was decided:

- **History shape.** `TurnState.history` was still Message pairs. It is
  now `HistoryTurn` records (turn, question, answer, kind) — named apart
  from the eval's `TurnRecord`, which already existed; the Brief's
  sentence was amended rather than carrying two classes with one name.
  A `mode="before"` validator upgrades a pre-Block-4 checkpoint's pairs
  on read (LangGraph coerces node input through the schema, so it runs
  at every node entry); the proof is a committed store written by the
  engine at 850f869 — the dev store the Polish Pass backfilled no
  longer exists on disk — continued at turn 3 in
  `tests/test_harness_legacy_store.py`.
- **`engine store backfill-questions`** is kept, not retired: it reads
  through the same upgrade, so a store of either layout recovers.
- **The chip** is keyed by the finalize event's name, not the trail's
  tail, on both surfaces; `summarize` emits its own events and only
  when it runs, so a short conversation's trail is unchanged. On a
  refresh turn the chip's seconds include the summary call — honest.
- **`substrate_versions`** are written as before; the exposure verb's
  work-store arm keeps reading them. **`turn_count`** for the banner is
  every `turn_log` row, legacy rows included.
- **Formatted figures and the scrub.** Answers carry `$8,308.92`,
  `92.2%`, `1 hour`, `1.1 days`, plain numbers, ISO dates, `—`. The
  scrub's grammar mirrors render.py's outputs (its own, small: the
  Verifier's regex is private and has no duration or date, and the eval
  side is not importable from the harness) and is anchored on digits
  with no letter, digit or underscore either side, so `CR147`, `e0`,
  identifiers and the nine reading names (all letters and hyphens)
  never match. A restated figure becomes "(see turn N)" — the first
  turn that stated it; a cited turn outside 1..through becomes "(an
  earlier turn)", never a number the summarizer did not write.
- **Table turns** contribute exactly what they did — `[table: <SQL>]` —
  and no figures by construction (the figure set reads prose records
  only); the SQL is what "the table above" resolves against, and
  changing it would have made this run unattributable.
- **The window.** Verbatim = every turn newer than the summary; the
  fold runs when `(turn − N) − through ≥ K`, so the window is N..N+K−1
  and no turn is ever neither summarized nor shown. Defaults 10/5: a
  30-turn conversation folds at 15, 20, 25, 30.
- **MT4** (user ruling): no bank row reached turn 11, so the summarizer
  prompt — model-facing text — had no regression net. A row may carry a
  `context:` block applied to its own session; MT4 runs 1/1: turn 1
  names the top supplier and its total in a sentence, turns 2–3 are
  MT1's and PLAY-R1's questions, turn 4's "that supplier's total again"
  resolves only through the summary (route must include run_sql, the
  figure re-gathered), and the summary turn 4 ends with must cite turn
  1, name the supplier, and carry no figure from turn 1's answer
  (`summary_contains`, `summary_excludes_figures`: context assertions,
  never a breach). `--check-gold` PASS with MT4.

Predictions for the post-Block-4 run (`2026-09-03-post-block4`):

| row | mechanism | prediction |
|---|---|---|
| MT1, MT2, MT3, W1 | two turns, pack window 10: the router's messages are byte-identical to the post-polish run (records expand to the same pairs; no summary section when the summary is empty) | hold 5/5 each |
| MT4 | new; the summarizer is live for the first time | ≥ 4/5; the risks are turn 1 answered as a table (the name then never enters the history) and the rewrite at turn 4 dropping the turn-1 citation |
| every other row | unchanged messages | unchanged; AMB1 3/5, S2 4/5, B2 2/5, W-F 0/5 move only by model variance |
| INVARIANT | no content path changed | ok |

Watch-for: an MT1–MT3/W1 rep failing to resolve its anchor (that would
be an expansion bug the router test and the checkpoint test should have
caught, not a model shift); MT4 turn 4 routing anywhere but run_sql
(the summary named the supplier but the router answered from it — the
summary must never be quoted as evidence, and the Verifier would refuse
a figure with no tool behind it); a `summarize` failure event in any
MT4 rep (the summary would be empty and the row fails on
`summary_contains`, which is the right outcome).

Deferred to the Close Pass: CTE-aware fan-out; an interpretation slot
for table answers (the caption — W-F's `contains` has nowhere to pass
otherwise, and MT4 would not need a prose-phrased turn 1); the loop
transcript's `Requested:`/`Tool results:` rendering; W4 under ASSOC;
the asynchronous `update_state` summary refresh.

## Close Pass (2026-09-04, after the post-Block-4 run)

The last pass of Phase 5, planned against `15f6424` and the
`2026-09-04-post-block4` report (`INVARIANT: ok`, every multi-turn
row 5/5, MT4 proving the summarizer live). Four rows below threshold
— AMB1 0/5, U-WHO 2/5, W-F 0/5, S2 1/5 — and B2 2/5 on router
nudges. The exposure replay attributed fifteen fan-out challenges
(AMB1 ×5, W-F ×5, U-WHO ×3, S2 ×2) over 237 statements; read against
the lint's code they were three mechanisms, not one, and the pass is
written down by them.

- **The fifteen, by mechanism.** AMB1's five repaired exactly as the
  challenge asked — the many side de-duplicated in a CTE, `SELECT
  DISTINCT invoice_id`, joined on that key — and were challenged again
  at the CTE join: a CTE name has no dictionary row, so the join read
  "no foreign key relates these columns" and every table in the scope
  was repeated. W-F's five and U-WHO's three fired **inside** the CTE
  body on the direction rule itself: `invoices.id =
  invoice_history.invoice_id` repeats invoices once per history row,
  true unfiltered and false under a terminal status — the flat filtered
  statement drew the same challenge, the CTE was incidental. S2's two
  (reps 1/3) were two pass-through CTEs LEFT-joined on the composite
  line key with neither side de-duplicated: the line-grain join the
  Duration pass refused to declare `one_to_one`, in CTE clothing. And
  the mirror image, silent: S2 reps 2/4 hid that composite LEFT JOIN in
  a CTE with no aggregate and counted the CTE outside — 100% for a true
  95.5%, the known gap §2 with its first live witness, caught only by
  the saturated-rate warn.
- **Exposure, per commit, on the post-Block-4 report (237
  statements).** The text layer split: 15, byte-identical. The declared
  filter: 7 (W-F ×5, U-WHO ×3 silent). The projection key: 2 (AMB1 ×5
  silent). Grain propagation: 4 (S2 reps 2/4 added — the wrong
  answers). Every remaining hit is S2; no clean statement was flagged
  at any step; the four are pinned in `tests/test_eval_exposure.py` as
  the baseline the next guard is measured against.
- **The CTE rule, an extension of the direction rule.** The SQL text
  layer — the scope split, table references, the select-list item
  grammar — is `tools/sql_scopes.py`, imported by every lint and parse
  and importing nothing (the resolver used to import the lint, so the
  lint could not read the registry the resolver had). The walk names
  each CTE body and derived table, records which named scopes a scope
  can see, and keeps the literals it blanked in order, so a predicate's
  values are read by index and never by searching the statement. A
  scope has a grain read from its own text: a projection unique on the
  join's columns (`DISTINCT k`, `GROUP BY k`; a primary key beside
  other columns of its table is the key alone) is one row per key and
  a one side, a table is on its primary key, both unique is one-to-one;
  otherwise a plain pass-through column reads the foreign-key knowledge
  of the column behind it — unless the scope's own joins repeat that
  table — so a lookup or a filtered many side written as a CTE behaves
  like its flat twin and a computed column vouches for nothing; and an
  aggregate over a scope that is not deduplicated reads what its rows
  are, the body's repeated tables folded through the scopes it reads in
  turn, while a deduplicated scope propagates nothing. Derived tables
  join the scan under their alias (they were invisible; the dead-LEFT-
  JOIN check still skips them). One fixture moved with it: the
  derived-table S2 repair (COALESCE) draws the fan-out check its flat
  twin always drew, with the NULL-semantics paragraph still gone.
- **Cardinality under a declared filter — config over code.** A join
  path carries `one_to_one_when`, a list of `{column, values}`
  conditions under any of which the path is one row per key, exclusive
  with `cardinality`. The lint treats a step as one-to-one when the
  scope's WHERE or the step's own ON restricts the declared column to
  the declared values — `col = 'v'` or `col IN ('v', …)` on the step's
  own alias (the bare table when referenced once; the bare column when
  exactly one in-scope table owns it and is referenced once), every
  literal within the set, no OR at the top level; `NOT IN`, `<>`, a
  value outside the set, a filter on another alias, and a literal-first
  comparison do not vouch — and a self-join whose two sides are each
  vouched that way is one-to-one (W3's history shape). InvoiceGuard
  declares three on `invoices_to_history`: `to_status ∈ {CLOSED,
  NO_REVIEW_NEEDED}`, `to_status ∈ {RECEIVED}`, `from_status ∈
  {RECEIVED}` — an invoice reaches a terminal status once and is
  received once (a resubmission is a new invoice); the world confirms
  every status occurs at most once per invoice. The line-grain join
  stays undeclared: a line may carry two findings by schema, and a
  declaration would make the lint vouch for luck (the Duration-pass
  ruling, kept by the user's ruling here).
- **The declared facts are executed, not trusted (user addition).**
  `engine eval grade --check-gold` runs every declared condition
  against the world beside the gold scripts — at most one row per key,
  at least one row in all — and a condition the world contradicts, or
  one no row satisfies (how a misspelled value reads), is rot. The
  check reads the map and never a table name of its own, so a
  production pack's lifecycle tables get the same tripwire by
  declaring; the conformance validator refuses a condition column the
  dictionary does not know or the path does not step through. Live:
  1,233 / 1,983 / 1,983 rows matched, max one per invoice, PASS.
- **The lesson, as a corollary of the challenge principle: a
  challenge's recommended shape must be a shape every guard can
  read.** Third instance — S2's EXISTS (the coverage pass), the EPOCH
  unit shapes (the duration pass), and now "aggregate each side in its
  own scope, then join the results", which met a lint that could not
  read a scope's key. The challenge now also names the EXISTS test
  when a LEFT JOIN is among the joins that fired (the shape a match
  indicator should take); MT2's inner joins do not draw it. S2's lever
  is that steer, not a declaration.
- **The loop transcript is native tool messages (Part D, option a).**
  The router pattern-completed the prose rendering — `Requested: …` /
  `Tool results: …` — on B2: it role-played a tool call and fabricated
  the result (edge ids `b8b8b8b8…`, an alphabetic sequence), and wrote
  `give_answer` as text under the same echo; 14 nudges, four failing
  reps at the budget. Prior calls now replay as assistant messages
  carrying `tool_calls` and results as `role="tool"` messages, one per
  call in order, so there is no text format to complete; nudges stay
  user messages; the `Requested:` alternative left the text-form
  parser, since nothing produces it. Option (b) — parse real tools from
  text and drop a fabricated block — would have opened the closed
  surface to text entry and left the pattern in context. **The LLMPort
  contract grew** (user addition, recorded in the Brief's port table):
  `Message` carries `tool_calls` and `tool_call_id`, `ToolCall` the
  provider's `id`, and an adapter must send both shapes natively — the
  OpenRouter adapter does; the production adapter must. The transcript
  invariant: every assistant tool-call message is immediately followed
  by exactly one tool message per call, in order. A provider that
  rejects the sequence fails on the first multi-step turn, so the
  work-side smoke test is a two-tool question, never "hello".
- **The figure grammar (Part B).** A hyphenated token with a letter in
  it (`SVC-4410`, `CR-147`, `INV-CRP-0001`, `INV-2026-0001`) is an
  identifier; the digits inside are never a figure, on either side —
  S2's item code in a folded answer had put 4410 in the figure set and
  the summary naming the same code was scrubbed to `SVC-(see turn 4)`.
  A signed figure (`-$1,234.00`) still harvests whole, a date and a
  numeric range are untouched, and a figure no longer ends in a comma
  (the scrub used to eat it).
- **The interpretation slot (Part C).** The run_sql output carries the
  interpretations of the metrics the question matched, names and
  meanings, so the router sees them in the tool result and can map
  `SUM(i.opportunity)` to a name; `give_answer` takes `reading`, the
  decision carries it, and the table answer keeps it as a **typed
  field** beside the caption — not text injected into the caption, so
  the Verifier's pass-through check is untouched and no caption parser
  exists. Every surface renders one sentence above the SQL, "Reading:
  <name>.", and the eval's caption pool carries it, which is where
  W-F's `contains` now has a home. Validation is the graph's: an
  undeclared name is nudged with the valid set (counted as a nudge); a
  missing name is accepted without a reading (user ruling) — phrase
  matching over-reaches (U-WHO's "most productive" matches
  `rule_savings`, whose readings are money) and a forced reading would
  be a wrong sentence on a right table. The fallback at placeholder
  exhaustion names none. Recorded, not built: the SQL author declaring
  `-- reading: <name>` under the metric template, validated like a
  lint — the follow-up if the live run shows the router omitting it.
- **The grounding line (Part E).** U-WHO failed exactly when a money
  column was added beside the count (3 with, 2 without); the preamble
  says, after the who-rule that adds name columns: beyond those names,
  answer only the columns the question asks for. Golden fixtures
  regenerated deliberately.
- **Fixture note.** A legacy or caption-less table turn reads `[table:
  result set]`; a run_sql table turn reads `[table: <caption>]`, and
  after Part C `[table: Reading: <name>. <caption>]` when the answer
  named one.
- **Recorded, not built.** `USING` joins stay invisible to the step
  scan (0 live uses); a recursive CTE reads its self-reference as an
  unknown plain table; a literal-first predicate (`'CLOSED' =
  ih.to_status`) is not read; `_repeated_tables` dedupes siblings by
  table name, so two aliases of one table on the same one side never
  repeat each other (W3 reps 2/3/5's shape — silent today by that
  hole, silent after by the declared conditions) — an alias-keyed
  sibling rule is a rider for a later pass.

Predictions for the re-run (`2026-09-04-close-pass`):

| row | mechanism | prediction |
|---|---|---|
| AMB1 | the DISTINCT-CTE anti-join and the ON-filtered LEFT JOIN anti-join are both silent; the row's only content check passes trivially | 5/5 |
| W-F | the CTE body's sum is vouched by the terminal condition; the caption names the reading when the router passes it | ≥ 3/5 (threshold 0.6); a rep omitting `reading` fails `contains` only |
| U-WHO | the count shape is silent already; the money shape is vouched but names `ava`, not `nova`; the grounding line steers away from the money column | ≥ 4/5 if the line holds; the residual is the two-reading ambiguity the note records |
| S2 | reps writing EXISTS verify; a CTE⟂CTE composite join without DISTINCT stays warn-capped; a hidden fan is a `fan_out_override` warn instead of `rate_saturated` (same exit) | ≥ 2/5; 3/5 if the EXISTS steer lands in one more rep |
| W3 | the self-join CTEs are vouched by the received-once conditions (replay: silent) | holds 5/5 |
| B2 | no text format to complete: the fabricated transcript shapes cannot occur; the prose-after-primer nudge likely remains, one per rep | nudges 14 → about 5; no rep exhausts the budget; ≥ 4/5 |
| MT1–MT4, W1, W-E | history pairs unchanged; only the loop's working messages change shape | hold 5/5 |
| whole bank | native tool results are a strong "answer in prose now" cue | watch total nudges (36) and any new budget refusal |
| INVARIANT | no content path weakened; the lint sees more, not less | ok |

Watch-for: any statement the CTE rule leaves alone that fans (the
replay shows none beyond S2); any `fan_out_override` outside S2; any
trail detail containing "reading not declared"; W-F reps with no
`Reading:` line; a provider error on the tool-message sequence — a
route failure on the first multi-step turn, so one B2 rep runs before
the full bank; prose-after-results nudges rising on rows that were
clean.

Deferred, with reasons: the asynchronous `update_state` summary
refresh — the synchronous cost is bounded (one LLM call every K turns,
visible in the trail) and the one-turn-in-flight design would need a
second in-flight operation, a design change, post-Phase-5; W4/W2 under
ASSOC — association verification is the design item, the blocks keep;
Table-MUST — S2's yes/no evidence stays here for the revisit.

## Backlog Pass (2026-09-04, after the gate)

Phase 6's opening backlog (gate verdict §7 items 1–2): the two
wrong-but-verified answers of the developer's 30-turn browser session,
both conversation-shaped, both invisible to every single-turn row.
Planned against `c666c05` and the `2026-09-04-post-close` report, built
in nine commits, 920 → 1005 tests, bank 65 → 67 rows (hash
`2bc24a635736663d`). No model pin change; no full run in the pass —
the developer's baseline run grades it, and it is the first run under
the corrected U-WHO row. Read from the dev store (`packs/invoiceguard/
work.db`, conversation 1), not from a relay:

- **Turn 19 → 20, the placeholder key.** Turn 19's table carried
  `invoice_number = INV-00002` and no id. "What was that invoice's
  history?" drafted `ih.invoice_id = :invoice_id` (a parser error), then
  `ih.invoice_id = 123 -- Replace 123 with the actual invoice ID`, which
  executed, returned invoice 123's three transitions, and verified
  (`table_passthrough`, `plausibility: []`). Nothing read comments;
  nothing knew which literals the conversation had shown; run_sql saw
  only the question.
- **Turn 6 → 7 → 9, the anchor drift — and its root cause.** Turn 6's
  table was `rule_name = line_note, fire_count = 505`. The history line
  the router read at turn 7 was `[table: SELECT f.rule_name … LIMIT 1]`:
  a table's cells never entered history, so the router searched the
  docs for "the most frequently fired rule", the drafter described
  `new_supplier`, and the Verifier — which checks that a claimed entity
  exists, never that it is the one under discussion — verified it. Turn
  8 refused (no CKG node `new_supplier`); turn 9 counted `rule_name =
  'new_supplier'`, 197, verified.

Four rulings shaped the build, and one amendment: a table turn's
transcript may carry what its evidence established; MT-ANCHOR is three
turns (turn 6, "Tell me more about that rule.", turn 9); id-like is the
dictionary's primary and foreign keys plus map-declared key columns,
**amended in planning so that rule labels are name-only** — a rule name
is a label the model legitimately spells from the docs on a first turn
(26 distinct values, above the enum scan cap, never rendered into the
grounding), so a key challenge on `rule_name = 'duplicate_line'` would
be a false positive on correct SQL, while a name column still anchors
"that rule"; and a missing `about` is accepted, the check reading the
answer instead. Two more from the plan's approval: planning figures are
adopted only as the gold scripts reproduce them (they did:
`--check-gold` PASS on INV-00426 / $43,117.71 / 5 / 2026-03-20 / ava
and line_note / 505 / new_supplier / 197), and a row's note states its
breach semantics in words.

What landed, mechanism by mechanism:

- **Entity kinds in the map** (`entities:` — invoice, supplier, auditor,
  rule, item; synonyms, key columns, name columns; the validator refuses
  an unknown column). The engine never learns "invoice" in code: the
  catalog (`tools/entities.py`) resolves the declarations against the
  dictionary, a foreign key to a declared key being that key's kind and
  canonical column, so `invoice_history.invoice_id = 123` and
  `invoices.id` compare like with like.
- **The placeholder lint, hard.** An admission token in a `--` or
  `/* */` comment, or a bind shape where a value belongs (`:name`, `?`,
  `$1`, `{{…}}`, `<id>`), blocks on every attempt it fires with no
  license to resend, until the repair budget exhausts and the tool
  fails — the comment is the model's own confession. Comments are read
  from the fenced block, since `extract_sql` pops leading comment lines.
  `<>`, `a < b`, `x <= y` are operators, never placeholders (pinned
  negatives, by amendment); `::` is a cast; a `--` inside a literal is
  never a comment — the comment walk steps over literals. The
  challenge names no table at all.
- **The ungrounded-key lint.** An equality or IN predicate binding an
  id-like column to a literal absent from every user question, every
  key a result or filter carried, and the grounding text draws one
  repair round; the licensed resend executes and
  `run_sql.ungrounded_key_override` takes the badge. What the
  conversation has shown reaches run_sql as a `TurnContext` through
  `run_in_context` (the ABC's default is `run()`; only run_sql
  overrides), never the tool's own question argument — at turn 9 the
  router's paraphrase already carried the invented name. With no
  context (direct tool use) the lint is silent.
- **The grounding states the keys.** When a prior turn established an
  entity, one section — `invoices.invoice_number = 'INV-00426' (invoice,
  turn 1)` and "a follow-up about that entity filters on this key,
  verbatim" — and none otherwise: the two golden prompts are
  byte-identical, a third pins the anchored form. A rule's name is its
  filter key and is listed too.
- **`about` beside `reading`, and anchors on the history.** give_answer
  declares the entity a follow-up is about; both answer shapes carry it;
  every surface renders `About: <x>.` where the reading renders, the
  eval's caption pool included (on prose too). At finalize the turn's
  evidence is harvested — a kind is determinate when its entity is
  single, every column of that kind single-valued and every filter
  agreeing — and kept on the HistoryTurn with every key seen; the
  transcript names it: `[table: About: rule line_note. Reading: … SQL]`.
  Strings only, so the summary scrub has nothing to read; a legacy
  checkpoint loads with none.
- **The anchor check** (`anchor.entity_mismatch`, warn). When the
  question names a kind with a singular demonstrative or a
  back-reference and a prior turn established one entity of that kind,
  the answer is read three ways in order — the declared about, a filter
  literal on one of the kind's columns in this turn's SQL, and for
  prose the anchor's name in the text — and the first that decides,
  decides. Silent where it must be: no kind noun, no prior entity, an
  ambiguous one, a table with no filter on the kind, a key column the
  anchor never carried. Kind-less pronouns ("it", "its") are None on
  purpose: "the rule that flags it" (MT3 turn 2) refers to a rule while
  turn 1 established a supplier, and a comparison there would have
  flagged a ten-run-green row; drift through "it" is caught at the
  kind-bearing turn that begins it. The record carries no tool.
- **Exposure replays per turn.** One accumulator per conversation, in
  order; a turn with no statement (turn 7 was a docs search) still faces
  the anchor check; the work-store arm hands the replay whole turns.

Exposure, measured before landing (`engine eval exposure`, the three
new checks, then every check):

| source | statements | lint.placeholder | lint.ungrounded_key | anchor.entity_mismatch |
|---|---|---|---|---|
| `2026-09-04-post-close.jsonl` | 235 | 0 | 0 | 0 |
| dev store, conversations 1 and 2 | 44 | 1 — conv 1 turn 20, the comment | 1 — conv 1 turn 20, `invoice_history.invoice_id = 123` | 1 — conv 1 turn 7, `line_note` established, never named |

Every hit is one of the two findings that opened the pass; no clean
turn is flagged, on either source; conversation 2 is clean. Turn 9 adds
no anchor hit — "How many findings has it produced?" is kind-less — and
is caught in the bank by MT-ANCHOR turn 3's gold instead. The
unfiltered replay of the post-close report reads `(no hits)` under
every guard. The post-close baseline is pinned in
`tests/test_eval_exposure.py` beside the two earlier ones.

The bank: **MT-KEY** (anchor by invoice number, as at turn 19; the
wrong-invoice sentinel is a date regex — every transition of the
anchored invoice falls on one day, and a history query carries no date
literal, so the SQL caption cannot trip it; the count and the auditor
are breach: false) and **MT-ANCHOR** (turn 6, then "Tell me more about
that rule." with exits [0, 2, 3] — an honest refusal is allowed, another
rule is not, and the About line must name line_note — then turn 9 with
executed gold, where 197 is a contradicted breach). Both user-sourced,
threshold 0.8, notes carrying the breach semantics.

Recorded, not built:

- Kind-less pronouns are unchecked (above).
- A CTE alias before a key column (`FROM cte c WHERE c.invoice_id = 123`)
  resolves to no real table and the key lint does not read it; a
  literal-first predicate (`123 = ih.invoice_id`) is unread, as before.
- The `<name with spaces>` bind shape is not a placeholder to the lint —
  admitting spaces would read `a <b AND c> d`; `<invoice_id>` is.
- Block comments stay invisible to the fan-out, enum and interval lints;
  the two new lints strip them first.
- An auditor established as `invoice_history.actor` and a follow-up
  filtering `users.short_name` are different columns and never compared.
- A grounding number (a row count, a stats range) grounds an id literal
  that happens to equal it.
- The work-store replay has no summary to ground on (the turn log does
  not carry it); a report's replay does.
- `USING` joins, recursive CTEs: opaque to the column resolver, as before.

Predictions for the re-run (the first under bank `2bc24a635736663d`):

| row | mechanism | prediction |
|---|---|---|
| MT-KEY | turn 1 surfaces `INV-00426` (a name, never the id 440); turn 2's grounding names that key, so the model filters on it; a rep that recalls a bare number is challenged and repairs, or overrides to [UNVERIFIED] and cannot breach | ≥ 4/5 |
| MT-ANCHOR | turn 1's transcript carries `About: rule line_note.`; turn 2 declares or names it, or honestly refuses; turn 3 counts `rule_name = 'line_note'` | ≥ 4/5 |
| U-WHO | the first run under the two-reading row | 5/5 |
| MT1–MT4, W1 | messages unchanged except the optional `about` in the give_answer schema and MT3's turn-1 transcript line, which only helps | hold 5/5 |
| every other row | one optional field in the tool schema; no grounding change for a first turn; the transcript changes only on turns whose table established one entity | unchanged; watch nudges (15) |
| INVARIANT | no content path weakened; the lints see more, the Verifier sees more | ok |

Watch-for: any `run_sql.ungrounded_key_override` outside MT-KEY; any
`Placeholder check` in any trail; any `anchor.entity_mismatch` at all
outside MT-ANCHOR; an `About:` line on a non-anaphoric turn; a
`give_answer` nudge mentioning `about`; a first-turn row whose grounding
carries a "Keys this conversation established" section (it cannot — a
first turn has no prior turn); and the router declaring `about` on MT3
turn 2 with the supplier rather than the rule, which the check does not
read (kind-less) but the transcript would show.

## Fix Pass (2026-09-04, after the post-backlog run)

The first breach since the fix-pass era closed. The post-backlog run
(`2026-09-04-post-backlog`, engine `1ccceaa`, bank `2bc24a635736663d`,
the report committed at `e60ba4f` beside its FAIL grade) breached the
wrong-but-verified invariant at **MT-ANCHOR rep 4, turn 3: `197`
verified at exit 0** — the shape the row pre-declared. Planned against
`e60ba4f`, built in six commits, 1005 → 1022 tests, bank 67 → 68 rows
(hash `0c699d410c4024bb`). No model pin change; no full run in the
pass — the developer's run grades it. Read from the report and the dev
store, not from a relay:

- **Rep 4, turn by turn.** Turn 1 anchored `rule line_note` (505).
  Turn 2, "Tell me more about that rule.": the router searched the
  dictionary and the docs for `line_note` — correctly — and the docs
  returned the supplier-onboarding note, whose text names
  `new_supplier`; the drafter described that rule; the anchor check
  fired (prose never names `line_note`; warn, [UNVERIFIED], exit 2).
  Then `finalize` harvested the turn unconditionally, the drift
  overwrote the anchor on the history, and turn 3's router paraphrase
  already read "the rule 'new_supplier'" before any tool ran: 197,
  `table_passthrough`, verified. Per-turn detection, cross-turn
  propagation. The session's original shape was one turn wider —
  warn at 7, honest refusal at 8, breach at 9: the drift survives a
  refusal.
- **MT-KEY 0/5 with the engine correct.** All five reps grounded
  `ih.invoice_id = 440`, the right history, zero challenges — Finding
  1's machinery worked five times out of five. The router declared
  `about: "invoice 440"`; the check compared it by set equality
  against `{440, INV-00426}`; the kind noun failed it; warn, exit 2,
  and the row's `exit: [0]` failed. A check false positive at 100% on
  the shape.
- **F1 from the static relay.** The placeholder lint's admission
  vocabulary fired on `specific`, `actual`, `assum*`, `sample`,
  `example` in any comment, hard: `-- the specific invoice the user
  asked about` (literal fully grounded) and `-- actual receipt time,
  not scored_at` (no literal at all) both blocked, the second with a
  reason about a value that does not exist. Safe direction; repair
  rounds wasted, budget-refusal risk for a comment-happy drafter.

Four rulings, binding, and four more from the plan's questions:

- **R1 — (a) + (b′), not a redraft ladder.** (a) A turn whose verdict
  carries `anchor.entity_mismatch` writes no entity anchors: the prior
  anchor survives on the history and in the next turn's grounding,
  and the transcript entry carries the contradiction the Verifier
  printed in the About sentence's slot — `[table: Unverified: the
  question refers to that rule; turn 1's evidence established
  `line_note`, and this answer never names it. <SQL>]`, or
  `[unverified: …] <prose>` — never the declared About, which is the
  drift itself. (b′) After a warn, kind-less pronouns ("it", "its")
  are read against the surviving anchor of the warned kind, by the
  same three readings, until an unwarned answer establishes a new
  entity; a refusal establishes nothing and keeps the window open.
  Not a fixed turn count: the bank's breach was warn → breach, the
  session's warn → refusal → breach, and a one-turn window closes one
  record and not the other. (c) — feeding the warn back as a redraft
  challenge — declined: it changes the documented warn semantics, and
  the modal honest outcome of an evidence-poor follow-up is a refusal;
  a redraft demand would pressure toward hallucination. Worst case
  inside the window is [UNVERIFIED], never a verified wrong count.
- **R2 — strip-and-match.** One leading article and one leading
  synonym of the question's kind — the pack's declared synonyms, never
  a hardcoded noun — come off the declared about, then equality with
  any single anchor name under the existing casefold. Not containment:
  `invoice 440`, `the invoice INV-00426`, `440` are silent; `invoice
  441` warns; `440 and 441` warns (no partial credit for a list);
  `supplier 440` warns (another kind's noun is not stripped). The
  declared anchor is harvested without its noun too, so a later
  declared-only fallback compares like with like.
- **R3 — narrow AND gate.** The vocabulary is the confessions —
  replace, placeholder, substitute, fill in, todo, dummy,
  hypothetical — and a comment admission fires only when the
  statement compares something to a literal: every comparison
  operator, IN, LIKE, BETWEEN, typed dates included, since a confessed
  threshold is still a confession. Bind shapes stay unconditional.
  Turn 20's recorded attempts still fire; both F1 probes are silent;
  `-- todo: use the real id` fires beside `id = 7` and is silent on a
  literal-free statement.
- **R4 — MT-ABOUT.** The positive path, on a rule that has evidence
  to describe (rate_variance: the threshold memo and the CKG rule
  function): its finding count → "Tell me more about that rule." →
  "Which supplier does it flag most often?", threshold 0.8. Turn 2 at
  exit [0, 2]: a refusal is a miss, an About naming another rule
  breaches at exit 0 and fails at exit 2. Turn 3 breaches on another
  supplier and misses on an omitted count.
- **From the plan's questions.** The window closes on any kind's
  determinate anchor — the newest entity is the pronoun's likeliest
  referent (conv 1's turn 10 closes the window turn 9 left open, so
  turn 19's "Show me an example invoice for it" faces no check); the
  literal gate reads every comparison operator; MT-ABOUT's turn 3 is
  the supplier, not the summed amount (the excepted-amount gotcha
  makes that figure reading-dependent); and only an answer
  establishes an entity — a refusal, clarify or escalate after a
  one-row query keeps its keys (the model saw them) and no entity (the
  user saw nothing), which makes "refusals harvest nothing" literally
  true rather than usually true.

What landed, mechanism by mechanism:

- **The lint** (`tools/key_lint.py`): `_ADMISSION` narrowed;
  `_COMPARES_LITERAL`, read on the comment-stripped, literal-blanked
  statement, gates it.
- **The about** (`tools/entities.py::strip_kind_noun`; the check's
  declared arm; the harvest's declared anchor).
- **The record.** `TurnAnchors.contradicted_kind` and `.contradiction`
  (defaults, so a legacy checkpoint loads with neither);
  `harvest_turn_anchors(answered=, contradiction=)`; `finalize` reads
  the verdict's plausibility for the check and hands both in;
  `transcript_text` renders the contradiction; the replay does the
  same bookkeeping, so exposure's prior anchors are the live
  conversation's.
- **The window** (`verifier/anchor.py`: `open_window`,
  `referent_kind`, `is_kindless_pronoun`), shared by the check,
  `finalize` (the declared about's kind) and the replay. The finding
  names the warning the pronoun follows: "the question's pronoun
  follows turn 2's anchor warning; turn 1's evidence established
  `line_note`, and this answer says it is about `new_supplier`".
- **Why the green rows never open a window** (tested, row by row):
  MT1/MT2's "those" name no kind and no pronoun; MT3's turn 1
  establishes a supplier and the rule together, and its turn 2
  carries "it" but no warn precedes it; MT4's turn 4 is the
  kind-bearing arm, unchanged; W1's turn 1 is multi-row and
  establishes nothing; MT-KEY is silent under R2; every single-turn
  row has no prior. With two kinds' anchors coexisting and one warned,
  only the warned kind is read. Pinned end to end with the real
  Verifier: warn → "it" counting the drift ships [UNVERIFIED]; "it"
  counting the anchor verifies.

Exposure, replayed read-only after the code commits and before the
bank row (`engine eval exposure`, the three checks, then every check):

| source | statements | before → after | attribution |
|---|---|---|---|
| `2026-09-04-post-backlog.jsonl` | 262 | anchor 6 → **2**; placeholder 0 → 0; key 0 → 0 | MT-KEY ×5 turn 2 gone (R2); MT-ANCHOR rep 4 turn 2 as recorded, and its turn 3 — the breach — now flagged through the window |
| `2026-09-04-post-close.jsonl` | 235 | 0 → 0, all three; unfiltered `(no hits)` | — |
| dev store, conversations 1 and 2 | 44 | anchor 1 → **2**; placeholder 1; key 1 | conv 1 turns 7 and 9 (the session's original breach turn, after turn 8's refusal); turn 20's comment (contains `Replace`, binds 123) unchanged; conv 2 clean |

A correction to the pass's brief: it stated the post-backlog baseline
as five anchor hits; the executed replay measured six (MT-KEY ×5 plus
rep 4's turn 2), and the executed count won — the prediction table was
restated against it before any code landed, and the after-count is
two, not one, because rep 4's turn-2 warn is a finding the report
carries and the replay reproduces. The unfiltered replays add only
what the report or the store already carried under checks this pass
never touched (three `run_sql.empty_result` warns on MT-ANCHOR's
refusal reps; conv 1 turn 12's fan-out). Both baselines are pinned in
`tests/test_eval_exposure.py`.

The dry-run (one rep of MT-ABOUT alone, into the scratchpad, not
committed): three turns at exit 0, `INVARIANT: ok`, one router nudge
at turn 2. Turn 2 declared `about: "rule rate_variance"` — the
kind-noun spelling R2 legalized, on the feature's first live
observation of its positive path; under the pre-pass check it would
have warned. Its content came from the CKG function and its docstring
("contracted rate"); turn 3 declared no about and counted Ravenswood
Extrusion / 257.

Recorded, not built:

- The false-downgrade residue of b′: an in-window kind-less turn
  whose prose legitimately omits the anchor's name warns — safe
  direction, [UNVERIFIED]; the window is narrow and closes on the
  next established entity — and the next play session should try to
  produce it. Likewise an "it" that means another kind's entity while
  the window is open: an about naming that entity warns, a filter on
  its columns is silent.
- Pronouns beyond "it"/"its" — "they", "that" alone, "those" — are
  not read; an about wearing two nouns is not stripped.
- A literal-first predicate, the `<name with spaces>` bind shape,
  `USING` joins, the old records' missing about: as before.

Two process notes from the Test Chat: the report pair was committed
correctly despite its FAIL grade — a breach report is the evidence,
and the pass reads from it; and the claim that the Backlog Pass was
pushed was false for some hours — pre-flight now runs `git fetch` and
compares `HEAD` to `origin/main` before anything else (this pass: both
at `e60ba4f`).

Predictions for the re-run (the first under bank `0c699d410c4024bb`):

| row | mechanism | prediction |
|---|---|---|
| MT-KEY | `about: "invoice 440"` is silent under R2; the machinery was already 5/5 | 5/5 |
| MT-ANCHOR | a turn-2 warn leaves turn 3 the correction in its transcript; a drifting turn 3 is read through the window and ships [UNVERIFIED] | ≥ 4/5; any miss an honest refusal or an [UNVERIFIED] — never a wrong About or a wrong count at exit 0 |
| MT-ABOUT | the dry-run's shape | ≥ 4/5 |
| U-WHO | unchanged | 5/5 |
| MT1–MT4, W1 | no warn ever, so no window; messages unchanged | hold 5/5 |
| every other row | the lint narrows, the check reads more, nothing else changes | unchanged; nudges ≈ 15 |
| INVARIANT | no content path weakened | ok |

Watch-for: any `anchor.entity_mismatch` outside MT-KEY, MT-ANCHOR and
MT-ABOUT; any `Placeholder check` in any trail; any `[unverified:` or
`Unverified:` transcript lead outside MT-ANCHOR; an About wearing a
kind noun (legal now — note where); an in-window kind-less turn
downgraded on prose that omitted the name (the residue above); nudges
rising above the run's ~15.

## Rider Pass (2026-09-04, after the post-fix-pass run)

The post-fix-pass run (`2026-09-04-post-fixpass`, engine `96c26d2`,
bank `0c699d410c4024bb`, committed `2645205` beside its FAIL grade)
closed the breach class. The recorded breach shape was reproduced live
and **caught**: MT-ANCHOR rep 5 warned at turn 2 and turn 3's `197`
shipped [UNVERIFIED] with the window warn ("the question's pronoun
follows turn 2's anchor warning"). The transcript correction produced
a **recovery**: rep 4 warned at turn 2 and turn 3 counted `line_note`,
505, at exit 0. True wrong-but-verified across the run's 340 reps:
**zero**. The residue was three small items and one diagnosis, planned
against `2645205`, built in six commits (`433848d` `9673f05` `edbfa85`
`07ad889` `c624256` and this one), 1022 → 1034 tests, bank 68 rows,
hash `0c699d410c4024bb` → `51135d86460f665b`, `--check-gold` PASS. No
model pin change; no full run in the pass — the developer's run grades
it. Read from the report and the live code, not from a relay:

- **MT-KEY rep 1 — the alarm's third false cry.** The INVARIANT line
  read `MT-KEY rep 1 turn 0 [unsupported]: gold name 'INV-00426'
  absent`. The answer was `{invoice_id: 440, invoice_total: 43117.71}`
  — this rep's SQL selected `id AS invoice_id, invoice_total`, an id is
  a legal way to identify an invoice, and turn 2 pulled the correct
  history off that id (`ih.invoice_id = 440`, five transitions on
  2026-03-20, verified, about `invoice 440`). The row's
  `name_from_gold: invoice_number` was narrower than the space of
  correct answers — a bank defect. The alarm has still never missed a
  wolf. **Ruling, recorded: the general grading gate — "name absent
  but gold numeric matched → non-breach" — is declined.** A
  numeric-match gate would someday mask a wrong entity wearing a
  coincidentally right number; the escalation rule stays paranoid and
  per-instance fixes stay in the bank. Fix: the gold script returns
  the id as a string beside the number (executed: `440`), the
  assertion reads either — `field: [invoice_number, invoice_id]`,
  U-WHO's multi-field form — with `forbid_bare_ids: false`, since the
  bare-id refusal exists for U-WHO's person names and an id is the
  point here. Graded offline against the recorded run: all five reps'
  turn 1 pass the new assertion.
- **MT-ANCHOR 3/5 — both misses the fix working.** Rep 4: exit 2 at
  turn 2 with the anchor warn, then the recovery. Rep 5: exit 2 at
  turn 2 with the warn, then `197` at exit 2 with the window warn — the
  caught drift. The assertions predated the machinery: turn 2's About
  and drift assertions had no way to admit a warned wrong answer, and
  turn 3's `exit: [0]` could not admit the caught shape. No assertion
  could read the verdict's findings, though every report keeps them
  under `turns[i].verdict.plausibility`. One grader knob,
  `unless_finding: <check>` on the assertion base (`eval/models.py`,
  `grade.py`), skips an assertion on a turn at exit ≠ 0 whose verdict
  carries that check — at exit 0 every assertion applies and the
  breach detector runs whatever the record carries, by construction
  and not by the Verifier's ladder. New shape: turn 2's About regex and
  `not_contains: new_supplier` stand down only where
  `anchor.entity_mismatch` spoke (a wrong About at exit 2 *without*
  the warn is the check going silent and fails the rep; at exit 0 it
  breaches, as before); turn 3 is `exit: [0, 2]` with the same waiver
  on `fire_count` — `197` at exit 0 stays the contradicted breach
  sentinel verbatim, `197` at exit 2 with the warn passes, `197` at
  exit 2 without it fails, and a correct 505 at exit 2 passes (ruled:
  the same exit-2 semantics as turn 2, one knob rather than a second
  positive `finding_present` kind). The row's note carries the
  encoding in one line: *wrong-count-verified breaches;
  wrong-count-caught passes.* A defaulted schema field, so the knob
  alone moves no hash; the row edits do.
- **MT-ABOUT 3/5 — content 15/15, the About stochastic.** Reps 2, 3
  and 5 declared `about: "rule rate_variance"` (the kind-noun spelling
  R2 legalized); reps 1 and 4 declared nothing, and the row's About
  regex failed them. The plan's brief proposed defaulting the About
  from the turn's harvested evidence anchors. **Finding, from the
  replayed trails: the harvest yields nothing there.**
  `harvest_turn_anchors` reads `run_sql` evidence only, and MT-ABOUT's
  turn 2 gathers `app_primer`, `search_business_docs` or the
  dictionary, two or three CKG hops and `read_source` — no SQL — in
  5/5 reps; the recorded evidence replayed through the live harvest
  and catalog gives zero entity anchors every time. What confirmed
  those turns was the anchor check's prose arm, reading
  `rate_variance` against turn 1's filter anchor
  (`findings.rule_name = 'rate_variance'`). The harvest design would
  have fired on MT-KEY t2, MT-ANCHOR t3 and MT4 t4 — all of which the
  router already declares — and never on the row that motivated it.
  **Ruling: the default is anchor-confirmed.** The check's reading is
  now returned whole (`verifier/anchor.py::read_anchor`, with
  `check_anchor` the finding-only wrapper the ladder and the replay
  read): the finding if an arm contradicted, else which arm confirmed
  — the router's declaration, a filter on the anchor's key, its name in
  the prose — and for the filter and prose arms the one anchor value
  it matched. `VerifierResult.about_default` carries that value when
  the router declared none and nothing contradicted; the harness
  verify node stamps it on the answer body, exactly where a
  declaration sits, and emits `about defaulted to \`x\` — the anchor
  check confirmed it` so a report can tell the two apart. Sequence:
  draft stamps the router's about → verify runs the check with
  `about=None` → on a warn, no default; the outcome ships [UNVERIFIED]
  with no About, finalize harvests nothing and the transcript carries
  the contradiction — every line of the Fix Pass path unchanged → on a
  confirmation, the body is stamped before the outcome exists →
  finalize reads `outcome.body.about` as it reads a declaration,
  stores it stripped as a `declared` anchor, and the transcript
  renders it as one. Only on the answer branch: a Verifier refusal and
  the N7 content-free refusal wear no About (an insufficiency draft
  that names the anchor passes the prose arm, so that gate is
  load-bearing and test-pinned), and a redraft is verified again
  first, so the About is the final attempt's. **Why nothing gets
  quieter:** the injected About exists only on turns the same check
  passed silently before, and states what that check read; a table
  that filtered on nothing is silent *and unconfirmed*, so it gets no
  About; an [UNVERIFIED] for other reasons carries it as a declared
  about would.
- **The seam review's four tightenings, credited.** A read-only design
  review of the seam before any code changed four things in the plan,
  each tightening and none loosening: (1) the injected value is the
  *confirming* value — the anchor's spelling of the column the filter
  matched, or the anchor value the prose named — never the check's
  `" / "` join of two values, which `_norm` matches to no name and
  would replay as a warn under exposure; (2) bare, not kind-prefixed:
  the bare value is exactly what the anchor carries and compares
  like-with-like by construction, with no round-trip through R2's
  stripping (the bank patterns make the noun optional, so nothing
  downstream cares; in the window case the kind is the warned kind and
  a synonym choice would have been arbitrary); (3) the prose arm's
  *confirmation* needs a whole-word match — a substring hit ("ava"
  inside "available", "440" inside "1440") kept the check silent
  before and still does, but writes no About: an injected false claim
  is exactly what the non-quieter argument promises cannot happen; (4)
  injection only in the final answer branch, so N7's content-free
  refusal cannot wear an About. Pinned: the injected About always
  equals a value the anchor actually carries — never a paraphrase,
  never a join — and re-checks silent as a declared about
  (`tests/test_verifier_anchor.py`).
- **The step-3 protocol violation, diagnosed.** Every rep of MT-ABOUT
  carried one nudge at turn 2: rep 1 at step 3, after
  `lookup_data_dictionary`; reps 2–5 at step 6, after `read_source` —
  so "step 3" was rep 1's position, not a constant. The shape is the
  router writing the answer as text ("The `rate_variance` rule
  identifies invoice lines where the billed unit rate exceeds the
  contracted rate…") instead of calling `give_answer`; `parse_route`
  raises the "Respond by calling one of the provided tools" nudge, the
  step is lost, and the next step answers correctly. It is the whole
  run's nudge count: all 20 nudges have this shape — MT-ABOUT 5, B2 5
  (after `read_source`, 5/5 reps), PLAY-R3 3 (after the dictionary),
  PLAY-R6 3 (after `app_capabilities`), R1 2 (a refusal written as
  prose at step 1), MT3 1, B1 1 (after the primer). Cause: the loop
  contract's "when the evidence answers the question, call
  give_answer" plus a whole primer, definition, document or source in
  the tool message reads to the pinned model as "now explain it", and
  the "Protocol, strictly" paragraph does not hold at that step. The
  contained fix is prompt-local — one sentence in the loop contract:
  *an explanation is still an answer: when the primer, a definition, a
  document or the source you have read explains what was asked, call
  give_answer with shape='prose' — never write the explanation here;
  the drafter writes it from the evidence you gathered* — **ruled in
  by the developer and landed as `c624256`**, pinned in the router
  prompt test. Model-facing for every row; the full bank run is its
  instrument.
- **Two process notes.** Plan mode exited before the plan was
  submitted; approval was taken by question, with the go/no-go on the
  sentence as its own ruling, and no code landed before it. And the
  harvest finding was made by replaying the recorded evidence
  payloads through the live harvest and `referent_kind` (a scratchpad
  probe, not committed) before the design was chosen — the brief's
  premise was checked against the instrument rather than assumed.

Regression argument for the About default, row by row, from the
replayed recorded trails and the code paths: single-turn rows have no
prior anchor and no reading (byte-identical); MT1/MT2's "those" names
no kind and no pronoun; MT3's turn 2 ("the rule that flags it") is
kind-less with the window closed; MT4's turn 4 declared its About in
the recorded run (undeclared, the filter arm on `suppliers.name` or
`suppliers.code` would confirm, and the table transcript's evidence
anchors already win); MT-KEY's turn 2 declared `invoice 440` in 5/5
reps (undeclared, `ih.invoice_id = 440` confirms only when turn 1
projected the id, giving `About: 440.`, silent under R2, and the
day-regex sentinel never meets a date in an About); MT-ANCHOR's warned
turn 2 injects nothing and its refusal reps have no answer body; its
turn 3 declared in every rep; MT-ABOUT's turn 2 reps 1 and 4 now wear
`About: rate_variance.` from the prose arm and pass the row's regex
and drift `not_contains`, reps 2/3/5 untouched; its turn 3 ("it"
outside a window — turn 2's record carries no column-bearing anchor
and no `contradicted_kind`, so the window closed at turn 1) has no
reading; W1's multi-row turns establish nothing and "the table above"
names no kind.

Recorded, not built:

- A declared about on a non-anaphoric turn is stored with kind `""`
  and its noun unstripped (MT-ABOUT turn 3, reps 2/4/5:
  `('', '', 'rule rate_variance', 'declared')`) — inert, since
  `_anchor_of` never matches kind `""`.
- The table-shape nudge ("selected evidence is not table-shaped")
  costs a step but is not counted by the runner's `"protocol
  violation"` substring — five uncounted in this run beside the twenty.
- The anchor check's filter arm reads every invocation, not the
  selected `evidence_index`, so an About can name a filter from an
  invocation other than the one shown — pre-existing for declarations.
- A prose that names both the anchor and another entity was silent
  and is now silent with an About naming the anchor — the About is
  exactly as strong as the prose arm's reading, no stronger.
- A later `_anchor_of` resolves to the injected declared anchor's turn
  when that turn's evidence carried no column of the kind, so a later
  warn may say "turn 2's evidence established" for the same value — as
  with a router declaration today.
- The exposure replay cannot model injection: it reads recorded
  abouts. That is the honest limit of the instrument, and why no
  exposure replay was run for this pass — rule 4 covers new
  plausibility bounds and lints, this rider adds neither, and the
  About default's effect exists only in generation. `check_anchor` is
  behavior-identical, so the pinned exposure baselines stand
  (`tests/test_eval_exposure.py`, green).

Predictions for the re-run (the first under bank `51135d86460f665b`):

| row | mechanism | prediction |
|---|---|---|
| MT-KEY | either identifier satisfies turn 1; turn 2 unchanged | 5/5 |
| MT-ANCHOR | the assertions admit the designed outcomes: recovery at exit 0, caught drift at exit 2 with the warn, refusal at exit 3 | 5/5 |
| MT-ABOUT | content was 15/15; the About is declared or defaulted on every confirmed turn 2 | ≥ 4/5, predicted 5/5 |
| U-WHO | unchanged | 5/5 |
| MT1–MT4, W1, every single-turn row | no reading, or a declared about on every recorded turn | hold |
| INVARIANT | no content path weakened; an About is only ever the check's own confirmation | ok, zero occurrences |
| nudges | 20/20 of the run's nudges were the prose-reply class the sentence names | well below 15 |

Watch-for: any About line whose entity disagrees with its turn's SQL
filter or prose — the relay should **count the `about defaulted`
events** in the run and check each against its turn; any `about
defaulted` on a turn that is neither kind-bearing nor in a window; any
`anchor.entity_mismatch` outside MT-KEY, MT-ANCHOR and MT-ABOUT;
`197@0` anywhere; and, as a live check on the sentence, an earlier
`give_answer(shape='prose')` — before the source or the document was
read — surfacing as a content miss on B2, MT-ABOUT's turn 2 or MT3's
turn 2.

## Rider 2 (2026-09-05, after the post-rider run)

The post-rider run (`2026-09-04-post-rider`, engine `8739d42`, bank
`51135d86460f665b`, committed `f37c201` beside its FAIL grade) landed
the Rider Pass's predictions but one. `INVARIANT: ok`, zero
occurrences; MT-ANCHOR 5/5 — the caught shape in every rep: turn 2
warned and shipped [UNVERIFIED], turn 3 counted `line_note`, 505, at
exit 0, five recoveries; MT-ABOUT 5/5 with the About defaulted in 5/5
— the run's five `about defaulted` events, all MT-ABOUT turn 2, all
`rate_variance`, each on a kind-bearing ("that rule"), undeclared turn
the prose arm confirmed against turn 1's filter anchor — the watch-for
satisfied and three-condition-clean; U-WHO 5/5; nudges 20 → 3 (MT4 1,
B2 2), the prose-reply class at zero; no `197@0`; no anchor warn
outside the MT rows. The one miss: **MT-KEY 0/5, deterministic,
safe-direction** — exit 2 at turn 2 in every rep, on a correct
history. Two commits (`3cce723` and this one), 1034 → 1036 tests, bank
untouched — the hash stays `51135d86460f665b` and no report becomes
ungradeable. No model pin change; no full run in the pass — the
developer's run grades it. The push is the developer's step.

- **MT-KEY 0/5 — the join-echo.** Turn 1's table transcript renders
  the harvested anchors joined, one kind per entry and every column's
  value: `About: invoice 440 / INV-00426.` The router — effectively
  deterministic under the loop-contract sentence — echoed that
  spelling verbatim as its declared About in 5/5 reps, where the
  post-fix-pass run had declared `invoice 440` in 5/5: the one-sentence
  prompt change re-rolled the About spelling wholesale. R2's declared
  arm strips one kind noun and compares one name; `440 / INV-00426` is
  in no name set; warn ×5 (*the question refers to that invoice; turn
  1's evidence established `440 / INV-00426`, and this answer says it
  is about `invoice 440 / INV-00426`*). Reproduced offline, against the
  anchor `{440, INV-00426}`, before any code changed:

  | declared about | before | after |
  |---|---|---|
  | `invoice 440 / INV-00426` | warn | silent |
  | `440 / INV-00426` | warn | silent |
  | `invoice 440`, `INV-00426`, `440` | silent | silent |
  | `440 / INV-00427`, `invoice 441 / INV-00426`, `440 / RVX01` | warn | warn |
  | `440 / ` (dangling), `440 /  / INV-00426` (doubled) | warn | warn |
  | `440 / 440` | warn | silent |

  The engine teaches a spelling its checker rejects — the same
  meta-pattern as the kind-noun gap, one layer up.
- **The fix: read the join, never emit it.**
  `verifier/anchor.py::_declared_matches`, used by the declared arm
  alone: the kind noun comes off (R2), the whole remainder is compared
  first — byte-identical for everything that matched before, including
  a value that itself contains ` / ` — then, when the remainder splits
  on `" / "`, every component, stripped and normalized on its own, must
  be one of the anchor's names. A stranger, another kind's value, a
  list (`440 and INV-00426`), a dangling separator (`440 /` after the
  strip's trailing trim: one component, no name) or a doubled one (an
  empty component, and no anchor carries an empty name) warn exactly
  as before; a repeated component is silent. A confirming echo is
  `confirmed_by="declared"`, so no default is stamped and nothing
  reaches the harness's injection path. The seam review's ruling
  stands — the injected About is one bare value the anchor carries,
  never the join — now for the reason that the join is the
  transcript's rendering of several columns, not a value any column
  carries; the rendering itself (`anchors_text`) is untouched, since
  it legitimately emits the join on every multi-column anchor. Probes
  pinned in `tests/test_verifier_anchor.py` (the table above, plus the
  pack's three-column kind — a supplier's id, code and name — read the
  same way); the seam review's own pin that the joined form warns is
  retired with its reason.
- **The meta-pattern, counted.** This is the second instance of *the
  engine teaches a spelling its checker rejects*: the kind noun (Fix
  Pass, R2), then the join (Rider 2). **A third instance triggers a
  design pass on the render/match split — one rendering read by one
  matcher, or a matcher that reads whatever the renderer writes by
  construction — instead of another patch.**
- **The join's shadow — recorded, not built; the candidate third
  instance.** Once a join-echoed About confirms, finalize stores it as
  it stores any declaration (`strip_kind_noun`, then one declared
  anchor): one anchor whose value is the join itself, `440 /
  INV-00426`, kind invoice, no column. On a turn whose evidence carried
  no column of the kind — a SQL-less prose follow-up — that anchor
  shadows turn 1 for `_anchor_of`, and a third anaphoric turn's
  declared `invoice 440`, or a prose naming only `440`, warns again.
  Trigger shape, verbatim, for a play session to produce deliberately:
  **join-echo → SQL-less prose follow-up → third anaphoric turn.**
  Ruled out as a patch: a both-sides read (every anchor value split
  into components) would let that stored join become the confirming
  anchor for a later undeclared prose turn, and `default_about` would
  stamp the join as a bare About — the seam ruling broken through the
  back door; making it safe touches the default-emission path too,
  which is the design pass arriving disguised as three lines. If it
  bites live, the design pass starts from a written position:
  symmetric join reading on both sides, plus a guard that a default is
  never a stored join.
- **Exposure (rule 4), predicted before and replayed after.** Checks
  `anchor.entity_mismatch`, `lint.placeholder`, `lint.ungrounded_key`:

  | source | anchor.entity_mismatch | note |
  |---|---|---|
  | post-rider report (`f37c201`) | **10 → 5** | removed: MT-KEY turn 2, reps 1–5, the join-echo; surviving: MT-ANCHOR turn 2, reps 1–5, "never names it", byte-identical |
  | post-backlog report | **2 → 2** | rep 4's turn 2 and turn 3 untouched |
  | post-close report | **0 → 0** | |
  | placeholder / ungrounded_key, all three | 0 → 0 | this rider touches neither |

  Actuals matched the prediction line for line. The post-rider report
  is now pinned in `tests/test_eval_exposure.py` beside post-backlog
  and post-close (265 statements, five hits, all MT-ANCHOR turn 2).
  Dev-store replay optional and developer-attested: the recorded
  abouts there carry no join, so expect unchanged.
- **Two knob behaviours, from the static relay.** `unless_finding`
  (`eval/grade.py`, `_has_finding`) matches the check's name at any
  severity and at any exit ≠ 0. So (1) a fail-severity record of the
  named check would stand the assertion down as a warn does — within
  spec, and the anchor check emits only warn, so no live witness; and
  (2) the waiver applies at exit 3 too, but MT-ANCHOR's turn-2
  assertions carry `at_exit: [0, 2]` and turn 3 admits `[0, 2]`, so no
  exit-3 turn ever reaches it — within spec, no live witness.
- **MT-ANCHOR turn 2, the experience note.** The modal outcome moved
  from honest refusal (post-fix-pass: refuse ×3 at exit 3, drift ×2 at
  exit 2) to labeled-unverified drift (post-rider: drift ×5 at exit 2
  with the warn, every rep recovering at turn 3). Accepted as
  documented cost, the sentence untouched: prompt surgery re-rolls
  phrasing wholesale — MT-KEY's About went from `invoice 440` to
  `invoice 440 / INV-00426` in 5/5 on one added sentence — and the
  reader sees [UNVERIFIED] with the anchor named either way. True
  cause substrate-side: `line_note` is undescribed in the pack — no
  rules-engine function, no business doc — so "tell me more about that
  rule" has nothing to be about; that is the gap a published Unit of
  Work would fill, and a possible Phase 6 demo beat.
- **Process line, the push convention.** Rider Pass: Claude pushed
  `8739d42` after pre-flight and the developer verified origin-first.
  From Rider 2 the push is the developer's step: Claude lands the
  commits, states `HEAD` against `origin/main` from a fresh fetch, and
  hands off; the developer pre-flights, pushes, and runs the bank.

Predictions for the re-run (bank `51135d86460f665b`, unchanged):

| row | mechanism | prediction |
|---|---|---|
| MT-KEY | the echoed join reads as the anchor; turn 2 confirms as a declaration at exit 0 | 5/5 |
| MT-ANCHOR, MT-ABOUT, U-WHO | untouched | 5/5 each |
| all 65 pre-existing rows | one matcher arm reads more; nothing else changes | hold |
| INVARIANT | no content path weakened; a join confirms only when every component names the anchor | ok, zero occurrences |
| nudges | the sentence stands | ≈ 3 |
| `about defaulted` events | the default's conditions unchanged | all three-condition-clean |

Watch-for: any `anchor.entity_mismatch` on MT-KEY; any About carrying
` / ` on a row other than MT-KEY (note where — the join spreads
wherever a multi-column anchor renders); the shadow's trigger shape in
any trail. If it lands, the fix-pass arc closes and the line un-stops.
