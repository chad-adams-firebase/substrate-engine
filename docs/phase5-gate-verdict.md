# Phase 5 Gate Verdict — Chat UI (Blocks 0–4 and six interlude passes)

**Range:** substrate-engine `e8f10d0` (fix-pass-era close, 2026-08-30) → `15118fc` (Close Pass, 2026-09-04), 74 engine commits plus ten committed report pairs. Companion to `docs/phase4-gate-verdict.md` and `docs/pin-pass-residuals.md`.
**Pins:** invoice-guard `761a18e` · seed 42 · world manifest `ac4b8abd4eb9c07e` — byte-identical across every session, run, and commit since Phase 4 acceptance · stats manifest `13a9fe63…` untouched (the Polish Pass derived precision rather than regenerate) · model `openai/gpt-4o-2024-11-20`, pinned at `98b3232`, unchanged since.
**Suite:** 494 → 919 tests green. **Bank:** 47 → 65 rows, 61 at threshold on the closing run.
**Method:** every figure below traces to something the verification chat executed — a pytest run, a DuckDB query against its own seed-42 world, a hand-probe against the real pack, an exposure replay, or a committed grade — or to the developer's 30-turn browser session, quoted from the account given to the verification chat. Nothing is taken from a relay.

---

## 1. The phasing.md done-checks

| Done-check | Verdict | Evidence |
|---|---|---|
| A human can hold a real multi-turn conversation against the fixture pack in a browser | **PASS** | Live 30-turn conversation (2026-09-04): 24 of 30 turns clean and gold-exact where checked; drill-downs resolved across the whole span, including "the first supplier" at turn 26 (→ Brindle Fabrication Works, correct) and "the backlog total again" at turn 27 (78 / $8,308.92, re-gathered through `run_sql`, not recited from the summary) |
| The progress trail streams live and collapses to a chip | **PASS** | Block 1 acceptance: trail lines appeared one by one; chip reads the status trail — a bounced-and-retried call shows "1 tool · 1 retry"; every past turn keeps its chip |
| Clicking a chip shows SQL, rows, and verdict in the inspector | **PASS** | Block 3 acceptance: SQL attempt ledger with blocked / executed / override marks and lint text by kind, plausibility findings by check and severity, claim-level verdict, evidence tables with column formats, substrates and manifests, progress trail with timings, raw router text on a protocol violation, the refusal's diagnosis on its own line. The turn-4 inspector pane of that session showed the router retyping table cents wrong in a prose violation beside the correct table — transcription hallucination captured live |
| A 30-turn conversation stays coherent and the summary contains turn references, not figures | **PASS** | Fold fired at exactly turn 15 and every 5 after (through 20 at turn 30); the stored summary names entities ("Brindle Fabrication Works") and cites turns ("see turn 4") and carries no figure; the MT4 bank row runs the summarizer live at 5/5 with `summary_excludes_figures` holding |
| Nudge banner past a threshold | **FAIL as shipped** | The mechanism is correct: after turn 30 the conversation endpoint returned `turn_count 30, nudge_after_turns 30` and the page computed the banner due. The presentation is not: `.banner { display: flex }` with no `[hidden]` rule defeats the `hidden` attribute, so the strip is visible — empty — from first paint, and the user dismissed it before it had anything to say. Observed in the 30-turn session; one-line CSS fix |
| Typed envelopes: markdown, table, chart, code | **PASS with a recorded ruling** | markdown and table live; `code` rides as fenced blocks inside markdown (highlight.js); `chart` deferred by the v2.2 ruling |
| Workspaces / conversations CRUD; starter prompts; fail-closed cards; pack-configured branding | **PASS** | Block 3 acceptance and route tests: create, rename, delete → reload → gone; owner scoping 404 never 403; starters return on a new conversation; refusal cards plain-language with the diagnosis inspector-only; branding from the pack `ui:` block |

## 2. The interlude ledger

Six passes ran between the UI blocks, each opened by a finding and closed by a bank run. Every mechanism below has an executed hand-probe proving its guard and a live run showing the flip.

| Pass | Opened by | Mechanism | Fix | Live flip |
|---|---|---|---|---|
| Play (08-31 → 09-02) | Play Session #1: 8 wrong-but-verified in 39 turns; plausibility ran on 2 of 39 | Table answers had no plausibility checks; the fan-out lint was a once-only challenge that left no trace | Sum caps, avg ranges, distinct counts, logged overrides; router vocabulary; creepback metric; interpretations | S6 0/5 → 5/5; C1/HN-DIDRUN 5/5 |
| Pin (09-02) | **Breach 1** — 8 occurrences, MT2/B5/S2, under the new model pin | Expression join invisible to the lint + COUNT-over-join unbounded; dead LEFT JOIN answering the denominator; AVG over a NULL-padded indicator | Join-shape lint; joined-count ceiling; saturated-rate warn; pin-hygiene law | MT2 5/5, B5 5/5, S2 overrides warn-capped |
| Coverage (09-02) | Play Session #2's three WBVs in the review subsystem | Map had no human layer for corrections/acceptance; rates unformatted; enum literals unchallenged | Review-subsystem metrics with executed cardinality; rate as a formatting class; enum-literal lint | W-A/W-B/W-C/R-A/F1 5/5; **Breach 2** surfaced (below) |
| Duration (09-02) | **Breach 2** — W3: an INTERVAL divided by 86400 verified as "0 seconds" | Interval arithmetic unlinted; duration columns unbounded; grader unit-blind; humanizer boundary | Interval lint; duration floor and span ceiling; unit-aware grader; boundary rounding | W3 5/5; REC-SQL 5/5 |
| Guard (09-02) | **Breach 3** — AMB2: the enum lint's own hint steered the model to another table | A guard *causing* the wrong answer | Challenge-principle rule (name what is wrong, never suggest another table); entity-count bound; exposure verb | AMB2 5/5; challenge-principle test |
| Polish (09-03) | Exposure verb: W1's flagship table denied its badge on every run by a direction-blind FK check and a cap $7 short | Correct answers downgraded | Direction-aware fan-out; parse-classified counts; precision-derived cap; text-form control verbs | **W1 5/5 — first badge in its life**; W-D (30.6/day, previously refused) 5/5 |
| Close (09-04) | Polish Pass's own advice taught CTE shapes the lint then punished (AMB1 0/5, W-F 0/5, U-WHO 2/5, S2 1/5) | Fan-out reasoning blind at CTE boundaries | CTE-aware fan-out via projection keys and declared conditional cardinality; native tool messages; reading slot on tables | AMB1 5/5, W-F 5/5, S2 5/5; whole-bank nudges 36 → 15; zero overrides |

Three breaches, one guard-caused. Each was root-caused from the committed report within hours, fixed in a dedicated pass, and confirmed closed on the next full run. None was silent: every one was raised by the harness, never by a user.

## 3. The invariant record

| Run | Rows at threshold | Invariant |
|---|---|---|
| post-Block-1 (08-30) | 41 / 47 | ok |
| post-Play-Pass (08-30) | 43 / 57 | **BREACH** — 8 contradicted (MT2 ×5, B5, S2 ×2), all genuine |
| post-Pin-Pass (09-02) | 50 / 57 | ok |
| post-Block-2 (09-02) | 50 / 57 | ok |
| post-Coverage (09-02) | 55 / 61 | **BREACH** — 2 (W3 ×2): one genuine (interval arithmetic), one grader unit-blindness |
| post-Duration (09-02) | 57 / 61 | **BREACH** — 3 (AMB2 ×3), genuine, guard-caused |
| post-Guard (09-02) | 57 / 61 | ok |
| post-Polish (09-03) | 59 / 64 | ok |
| post-Block-4 (09-04) | 58 / 65 | ok |
| post-Close (09-04) | 61 / 65 | **BREACH** — 5 "unsupported" (U-WHO ×5): the answer is the gold's own second reading to the cent (ava, $564,386.36) for an undeclared ambiguity ("most productive reviewer"); assessed as bank-lag, not a wrong answer — see §5 |

Across ten full runs (roughly 3,000 graded turns) the alarm raised four times; three were genuine and closed within the day; the fourth is a row that must declare its readings. The alarm has never been silent on a wrong verified answer the bank could see.

*Gate rider (2026-09-04, after the verdict).* U-WHO now declares both readings: the closed-opportunity gold (ava, $564,386.36, executed against the pinned world beside the count gold's nova 390) and `name_from_gold` over either top name. The bank hash rotated with the row, so the post-Close report no longer grades against the committed bank; it stays committed as this gate's record, its five "unsupported" read as the row's lag, not the engine's error. Graded offline against the corrected row with only the header's hash patched, the same report reads U-WHO 5/5, 62 / 65 at threshold, `INVARIANT: ok` (B2 3/5 is the one threshold miss; W2 and W4 stay xfail). No re-run was made for the gate; Phase 6's baseline run grades the corrected row.

What the bank cannot see is recorded in §5: the 30-turn browser session produced two wrong-but-verified answers of a class no single-turn row exercises.

## 4. Deferred, and why

- **Asynchronous summary refresh.** The summarizer runs synchronously after finalize (11.5 s at turn 30 in the live session). The `update_state` upgrade path is documented in Brief §10.3; the synchronous form is correct and the cost is latency at fold turns only.
- **ASSOC — association verification.** W2 and W4 carry `keep_until: association verification`. The Verifier checks entity existence, not pairing; W4's twelve rule-name ↔ description pairings can still be zipped wrong. A Phase 6 item.
- **Table-MUST.** Data-shaped answers take the table envelope; a 1×1 count table is stilted but has caused no defect in ten runs. Kept.
- **The parser's stated gaps.** Quoted identifiers and unaliased joins were closed in the Guard Pass; `EPOCH`/`DATE_DIFF`/`JULIAN` classify since the Guard Pass; what remains opaque is recorded per check in `pin-pass-residuals.md`.
- **`chart` envelope** — v2.2 ruling.
- **Never live-witnessed guards** (verified by hand-probe and test only, because grounding steered the model away first): the expression-join lint, the dead-LEFT-JOIN lint, the interval lint, the entity-count bound. Recorded, not a gap.

## 5. Demo-visible state of the flagship questions

From the live sessions, not the bank:

| Question | State |
|---|---|
| "What's in the READY backlog?" | Verified, `78 / $8,308.92` — money formatting (NP3) since Block 1; 5/5 on every run since |
| "Total invoice amount and line-item total per supplier" | Verified, correct, badge earned since the Polish Pass; six `—` cells for suppliers with no lines |
| "How many invoices do we receive per day?" | Verified `30.6/day` since the Polish Pass (a Block-3-era refusal); the per-day table form also verifies |
| "Who closes the most reviews?" | Verified, nova 390, names not ids |
| "How long from RECEIVED to READY?" | Verified, "1 hour" — humanized since Block 2, unit-safe since the Duration Pass |
| "How do I use this chat?" | Answered, not refused, since the Coverage Pass |
| "Who is our most productive reviewer?" | **Ambiguous and undeclared**: verified answers by count (nova) through 09-03, by dollars (ava, labeled with a reading from the wrong metric) on 09-04. The bank row must carry both readings with golds; reading validation must be per-metric. *Gate rider:* the row now carries both golds and accepts either name (§3); per-metric reading validation stays Phase 6 |
| Multi-turn anchors | "The first supplier" and "the backlog total" resolved across the fold at turns 26–27. **But** "that rule" at turn 7 resolved to `new_supplier` when turn 6 had named `line_note` — three verified turns about the wrong rule; and "that invoice's history" at turn 20 ran `WHERE ih.invoice_id = 123 -- Replace 123 with the actual invoice ID` and returned another invoice's history, verified. These are the two wrong-but-verified outcomes of the live session |
| "Summarize what we found today" | Verified shrug, exit 0 — the N7 shape through a phrasing the converter misses; and the summary describes queries asked, not findings, because table turns fold as `[table: SQL]` |
| "Which auditor requested those?" | Verified table labeled `ignored_corrections` that counted distinct invoices (ava 3, sum 17) where the corrections count is ava 4, sum 18 — a unit mislabel |

## 6. Closing pins

substrate-engine `15118fc` · reports through `2026-09-04-post-close` (`cd14180`) · invoice-guard `761a18e` · seed 42 · world `ac4b8abd4eb9c07e` · stats `13a9fe63…` · bank `1bd447e3919905da` (65 rows) · model `openai/gpt-4o-2024-11-20` · 919 tests.

## 7. Recommendation

**Close the Phase 5 gate, with one condition and one carry-forward.**

Every phasing.md done-check but one is met with executed or eyewitness evidence, the invariant has held on every run where the bank could see, and the interlude discipline — breach → root cause from the committed report → dedicated pass → full run — worked six times without a silent failure.

**Condition of closure:** the nudge banner. `.banner[hidden] { display: none; }` (or a class toggle), plus a check that the banner text is set before it is shown; verified by opening the page fresh (no strip) and reaching the threshold (strip with text). This is the only done-check that failed in front of a human. *Met in the gate rider* (`060a0f9`): the rule and the text-before-show guard, pinned by `test_web_static` and executed by a jsdom smoke of the booted page — fresh: no strip (the smoke read `display: flex` before the change); opened at 12 of 10: strip with the turn count; dismissed: gone; below or without a threshold: hidden.

**Carry-forward into Phase 6 as its opening backlog**, in this order, because they are the first wrong-but-verified outcomes found outside the bank since the fix-pass era and every one is conversation-shaped:

1. **A literal key in SQL that appears in no prior evidence** — turn 20's `invoice_id = 123` with a comment admitting the placeholder. A challenge on any SQL comment containing "replace" or on a literal id absent from the conversation's evidence; and grounding that an anchor with a known number joins on the number.
2. **Anchor drift on "that <entity>"** — turn 7 answered about a rule the previous turn never named. The drafter should name the entity it describes; the Verifier should check it against the entity the anchor turn's evidence carried.
3. **The verified shrug through a new phrasing** ("does not contain any data or findings to summarize"), and a summary tool so "summarize what we found" has a source that is not a hallucination.
4. **Column-unit mislabels** (turn 23): an alias naming one unit over a count of another; a same-row consistency check where the map declares the unit.
5. **Readings**: per-metric validation of the reading name; U-WHO declared as a two-reading row.

Multi-turn rows for items 1 and 2 belong in the bank before Phase 6 ships anything on top of them: the session that found them was one human, one afternoon, thirty turns.

---

*A note on the referee.* The verification chat mis-attributed a table in one relay (the MT2 FK, 09-02) and was corrected from the artifacts; it mis-probed the AVG bound once with a string where a float belonged; it built a store without the schema hook and blamed the migration. Each is recorded in its session. The grader's-correction law applies to the grader.
