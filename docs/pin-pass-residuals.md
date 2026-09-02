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
