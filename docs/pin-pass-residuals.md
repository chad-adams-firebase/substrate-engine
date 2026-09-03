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
