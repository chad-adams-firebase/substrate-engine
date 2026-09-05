# Technical Build Brief v2.1 — Configurable Crowdsourced Knowledge Engine

Supersedes v1 in full. Consolidates all design decisions through 2026-08-11, including the substrate-acquisition inversion (ship producers, not products), the CKG L0–L3 specification, the Verifier specification, the Unit of Work model, chat infrastructure, and the uv/Python-3.12 toolchain decisions from the pipeline dry run.

**v2.2 changelog (2026-08-30):** Phase 5 rulings, recorded at §10.2/§10.3/§10.5 — (1) no token streaming: an answer cannot reach the screen before its verdict exists (§9.2), so the SSE stream carries status events live and the complete outcome as one terminal frame after verification; (2) the `chart` envelope is deferred to a later phase, and `code` renders via highlight.js on fenced blocks inside `markdown` — no new envelope kind; (3) the frontend libraries are vendored at pinned versions with their licenses, never loaded from a CDN, so the demo runs without egress; (4) the running conversation summary regenerates synchronously inside the turn in v1 — a ruling recorded ahead of its build: context management is Phase 5 Block 4 work, not shipped behavior at this writing (an `update_state`-based asynchronous refresh is the documented upgrade path). Also: tables carry a per-column display hint (`money`) resolved from the Dictionary Map and pack display config, so figures render as currency on every surface. Block 2 (2026-09-02) adds `duration` as the second hint kind (pack alias globs name the unit; `H:MM:SS` strings carry their own), renders NULL cells as an em dash, rounds the way the browser does so every surface prints identical digits, gives fail-closed cards plain-language reason and remedy with the engineer's diagnosis on a separate `detail` field for the inspector, and makes placeholder injection a values-not-passages rule: a passage resolves only inside a fenced code block, otherwise the draft is retried. The Phase 5 interlude coverage pass (2026-09-02, after Play Session #2) adds `rate` as the third hint kind, resolved from a `display.rate` block (scale by alias glob) and read by the Verifier's rate bounds so the two cannot disagree; resolves hints from the statement before the alias through a shared select-list parse that follows CTEs; renders a zero-row table as "No rows matched"; adds an enum-literal SQL lint (a filter on a value the column's dictionary enum never holds draws a repair round naming the observed values); tells the router to hand run_sql the user's own wording; and records a protocol violation's raw router text in the status event, off the live trail line. Phase 5 Block 3 (2026-09-02) lands the three-pane layout of §10.1 and the Inspect mode of §10.4 as described there; the turn log gains the question and the outcome (§12) so a past conversation reopens from it; the chip reads the status trail, so a bounced tool call and its retry count as one tool and one retry; a conversation a failed first turn opened is deleted again, so no failure leaves an orphan. Phase 5 Block 4 (2026-09-03) lands §10.3 as described there: the checkpoint history becomes `HistoryTurn` records and a store written before them upgrades on read (a committed pre-Block-3 fixture proves it); a summarize node after finalize folds the turns past the verbatim window into the running summary every `summary_refresh_after_turns` turns, so the router sees at least the last `last_n_turns` verbatim and never more than N+K−1; the summary cites turns and carries no figure — one regeneration names what was restated, then a scrub replaces any figure from a folded prose answer with "(see turn N)" and blanks a turn the summary could not have seen to "(an earlier turn)"; a failed refresh keeps the previous summary; the page nudges at `nudge_after_turns`; and the bank's MT4 row runs under a one-turn window so the summarizer prompt is measured live. The Phase 5 Close Pass (2026-09-04) makes the router loop's working messages native tool messages — the LLMPort `Message` carries tool calls and a tool result's call id, and the port table below states what an adapter must send — lets a table answer name its reading as a typed field rendered above the SQL on every surface, and lets a join path declare it is one-to-one under a filter (`one_to_one_when`), which the fan-out lint reads through CTEs and derived tables and `--check-gold` executes against the world. The Backlog Pass (2026-09-04) opens Phase 6 on the gate's two conversation-shaped findings: the map declares entity kinds (`entities:`); a generated statement's placeholder admission is a hard challenge and a key literal the conversation never saw draws a repair round, with the grounding stating the keys prior turns established and run_sql receiving what the conversation has shown as a `TurnContext`; a follow-up answer declares what it is about (`about` beside `reading`, rendered on every surface), a table turn's transcript names what its evidence established, and the Verifier checks an answer to 'that <entity>' against the anchor turn; the exposure verb replays per turn. The Fix Pass (2026-09-04) closes the post-backlog run's breach: a turn the anchor check warned establishes nothing and its transcript carries the correction; only an answer establishes an entity; after a warn a kind-less pronoun is read against the surviving anchor until a new entity is established; a declared about may wear its kind noun; and a placeholder admission is a confession word beside a bound literal — bank row MT-ABOUT observes the anchored follow-up's positive path.

**v2.1 changelog (2026-08-15):** amendments recording decisions already made and shipped — the messages-shaped LLMPort, the DuckDB local SqlPort adapter, and InvoiceGuard's move out of the engine repo into its own external repo. The code led; the docs follow. No new design is introduced here. (The file keeps its `technical-build-brief-v2.md` name: CLAUDE.md, phasing.md, docstrings, and prior prompts reference it by that name.)

## 0. Read this first

Build a domain-agnostic engine that answers plain-English questions grounded in an application's data + logic, and lets users crowdsource knowledge back. It is configured per-application by an **instance pack** (config + data), never by code changes.

Ship a working reference instance pack — **InvoiceGuard**, a supplier-invoice audit domain structurally analogous to the real target application — so the whole thing runs, is testable, and exercises end-to-end on a local machine with zero enterprise dependencies. The InvoiceGuard **application** is a standalone, fully functional reference app in its own repo (`chad-adams-firebase/invoice-guard`); the engine consumes it exactly the way it will consume the real target app at work — by running generators against its code and database, pinned to a commit SHA (§14). The InvoiceGuard **pack** is a **private engineering fixture**, not a public demo. The real demo is the target-app pack, authored later on the enterprise side, on real data.

**The engine also ships its own substrate generators.** Substrates are not hand-authored artifacts to be transported; they are **outputs produced in place** by generator tooling that travels with the repo. The personal machine owns schemas, contracts, generator code, and fixtures. The work machine produces instance values (actual columns, actual distributions, actual graph) by running the generators against real code and real tables.

Non-negotiable outcomes:
1. Swapping from InvoiceGuard to a real enterprise app is a configuration + adapter + generator-run task, not a code rewrite.
2. InvoiceGuard's machine-derived substrates are produced **by the generators**, never hand-authored — and the generators run against a real external repo and a real database, the same shape as the work-side task. If the tooling can't regenerate the fixture pack, it can't regenerate the target app's either.

## 1. Guiding principles (apply throughout)

- **Hexagonal / ports & adapters.** Core logic depends only on interfaces. All I/O (data, LLM, SQL, auth, storage, logs, source code) is behind an adapter.
- **Dependency injection.** Adapters are chosen at startup from config; the core never instantiates a concrete adapter.
- **Config over code.** All environment/domain specifics live in config/env, never hardcoded.
- **Engine vs. instance pack.** One engine; each app is a pack. A pack may enable ALL or only SOME substrates.
- **Ship producers, not products.** Substrates are regenerated in place by generators; hand-built substrates are structurally stale from the moment they are built.
- **Regenerable skeleton vs. validated meaning.** Machine-generated content is rebuildable; human-validated content is never overwritten by a rebuild. Everything human-touched carries provenance.
- **Name-based access, always.** Columns are accessed by name, never by position. Join-key normalization (lowercase) is applied once, at the adapter boundary.
- **Minimal canonical schema.** Every speculative field is a field that must be sourced at work. If a field has no consumer, it does not exist.
- **Mock-first.** Every port ships with a working local adapter so the app runs fully offline. "Done + generic" = the InvoiceGuard pack runs through the engine untouched.
- **Fail closed.** When confidence is low or a request is out of scope, refuse/clarify/escalate rather than guess.
- **YAGNI.** No config UI, no autonomous analysis agent, no speculative abstraction beyond what these principles require.

## 2. Tech stack & toolchain

- **Python 3.12, pinned.** `requires-python = ">=3.12,<3.13"` in pyproject.toml. Work machine: uv selects system 3.12 (the enterprise uv.toml sets `python-preference = "only-system"`, `python-downloads = "never"`). Mac: install 3.12 via pyenv/uv — the Mac's system 3.14 is never the project interpreter.
- **uv from the first commit** (per the enterprise Python standard). `pyproject.toml` = intent; `uv.lock` = resolved graph; commit both. No requirements.txt. Workflow: `uv add <dep>`, `uv run <thing>`, `uv sync`. Build backend: hatchling. Init: `uv init --app --package --no-pin-python --no-managed-python --build-backend hatchling`.
- **Flask** (HTTP + app shell, generator-based SSE routes).
- **LangGraph** (agent harness; SqliteSaver checkpointer locally, Postgres-saver pattern documented for the real adapter).
- **pydantic** for config + all substrate/contract models. **pytest** for tests.
- **LLM access:** OpenRouter (personal side) and Databricks Foundation Model serving (work side), both OpenAI-compatible, behind LLMPort. A deterministic stub adapter exists **for pytest only** — it is test plumbing, not a development mode.
- **CKG extraction:** Python stdlib `ast`. No external parsing dependencies for v1.
- **Frontend:** vanilla JS + marked.js, Chart.js, highlight.js. No build pipeline, no framework.

**Repo conventions (from the first commit):**
- `git init -b main`.
- `.gitattributes` containing `* text=auto eol=lf`, committed from the Mac; `git add --renormalize .` after adding.
- Work machine pushes only to dated branches (`report/YYYY-MM-DD`), never to main. Merges happen on the Mac.
- `--force-with-lease` over `--force`, always.

## 3. Ports (the interfaces)

Each port gets (a) a **tested local adapter** now, and (b) a **written-but-untested real adapter** for the enterprise Databricks stack, shipped in the repo, structurally identical to the tested one, debugged at work. "Documented stub" is not sufficient — at work there is no Claude Code; debugging a complete 100-line file with Glean beats authoring a subsystem from a spec.

| Port | Interface (minimum) | Local adapter | Real adapter (written, untested) |
|---|---|---|---|
| **LLMPort** | `complete(messages, tools?, temperature) -> response`; a message is `{role, content, tool_calls?, tool_call_id?}` and a returned tool call carries the provider's `id` — see the contract note below | OpenRouter (sends assistant `tool_calls` and `role="tool"` messages natively) | Databricks FM serving (OpenAI-compatible; near-identical code, different base URL + auth) — must carry the same two message shapes natively |
| **SqlPort** | `run_sql(query, identity) -> rows` (name-keyed) | DuckDB; the target application database is InvoiceGuard's SQLite DB, produced externally by its simulation runs (how DuckDB reads it: open item, §18) | databricks-sql-connector, forwards user token |
| **SubstrateStore** | typed read access to substrate tables | pack files / local tables | Delta tables |
| **WorkStore** | engine-owned persistence (§12) + checkpointer | SQLite | Delta/Postgres-saver pattern |
| **IdentityPort** | `current_user()`, `acls()` | fake user | OBO/forwarded token |
| **ExecutionLogPort** *(pluggable)* | `did_run(component, key, window) -> status`; `recent_errors(component, window) -> rows` | structured log files emitted by real InvoiceGuard simulation runs | Splunk REST (search job API). **Narrow by design** — the port exposes intent-shaped methods; SPL templates live in pack config; the LLM never generates SPL. |
| **SourceCodePort** | `read(file_path, start_line, end_line) -> text` (relative to repo root, at a pinned commit) | local clone of the external invoice-guard repo, pinned to a commit SHA | local git clone of target repo; GitHub Enterprise API adapter documented as the deployment-era option |

**LLMPort signature note:** Phase 1 locked the messages-shaped interface — `complete(messages, tools?, temperature)`, as implemented and documented in `src/engine/ports/llm.py`. This supersedes the v2 minimum single-prompt signature. **Contract extension (Phase 5 Close Pass):** the router loop's working messages are native tool messages. `Message` gained optional `tool_calls` (an assistant message that requested tools) and `tool_call_id` (a `role="tool"` message answering one call), and `ToolCall` gained the provider's `id`. Every provider adapter must send both shapes as the chat-completions API defines them — an assistant message with `tool_calls` (content may be null) and one `tool` message per call carrying its `tool_call_id` — and return each call's `id`; the OpenRouter adapter does, and the not-yet-written production adapter must. The transcript invariant the harness keeps: every assistant tool-call message is immediately followed by exactly one tool message per call, in order; nudges are user messages after them. A provider that rejects the sequence fails on the first multi-step turn, not the first turn — so the work-side smoke test is a two-tool question (one that needs a tool result before it can answer), never "hello".

Also for pytest only: deterministic LLMPort stub (scripted responses).

## 4. Substrates (what the engine reads about the TARGET app)

A pack declares which are enabled. Minimum useful pack: Data Dictionary + Database.

1. **Data Dictionary** — table/column/enum definitions + SME overlay + comments layers.
2. **Data Dictionary Map** — semantic/routing layer: business concepts, canonical metrics (exact table/column/filter/aggregation), canonical join paths, gotchas, question→where-to-look examples. Joins to the dictionary by table/column names.
3. **Code Knowledge Graph (CKG)** — full specification in §5.
4. **CKG Map / Components** — part of the §5 specification (L1).
5. **Primer** — part of the §5 specification (L0).
6. **Source Code Store** — the target app's code (L3), via SourceCodePort, keyed by CKG location refs, snapshotted at the same commit SHA as the CKG extraction.
7. **Application Database** — live data via SqlPort.
8. **Application Logs** *(pluggable)* — via ExecutionLogPort.
9. **Univariate Statistics** — per-column type, null rate, distinct count, min/max/mean, top values.
10. **Business Context Docs** — curated markdown documents (policy memos, process docs — the "why" layer) with front-matter provenance, searchable as a substrate. This is the layer managers and leaders draw on most.

**Named but disabled in v1** (ports may exist on paper only): Data Lineage (partially covered by CKG reads/writes edges; Unity Catalog provides natively later), Change History (git commits API — natural companion of the GitHub API adapter, deployment-era), **Skills/Playbooks** (procedural task documents enabling "do this task for me" — one-paragraph future port; deliberately excluded because it is the autonomous-agent path).

**Schema contract:** every substrate has a stable pydantic model. These models are **generator output contracts** (§13) — designed on the personal side, informed by the shape of the existing enterprise dictionary/stats tables, and produced identically by generators against any codebase/database. All substrate rows carry provenance: `source (machine|human)`, `confidence`, `last_confirmed_by`, `last_confirmed_date`, `needs_validation`. Rebuilds overwrite only `source=machine` rows.

**Dual-role note:** the published Unit-of-Work library functions as reference material (via `answer_from_known_items`) but is engine-owned and lives in WorkStore, not SubstrateStore. This is deliberate; do not "fix" it.

## 5. CKG specification (L0–L3)

Four levels, traversed up and down by question altitude. All IDs referenced across levels.

**L0 — Primer.** Human-authored markdown (~1–2 pages) describing what the app does, referencing component IDs inline. Machine-**checked**, never machine-written: the generator validates that every referenced component ID exists and warns when a regeneration orphans one.

**L1 — Components.** The hybrid layer.
- `component`: `id` (stable, human-meaningful: `ingestion`, `scoring`, `queue`), `name`, `description`, `tier` (1 = subsystem; tier 2 optional mid-grouping).
- `component_membership`: `component_id`, `ckg_node_id`.
- The generator **proposes** memberships (module structure as primary signal + LLM clustering pass for stragglers), landing as `source=machine, needs_validation=true`. Human confirmations become `source=human` overlay that regeneration never overwrites. New nodes in later extractions get proposed memberships; validated ones stay put.

**L2 — CKG proper.** 100% machine-extracted via stdlib `ast`; regenerated freely; zero LLM involvement in structure.
- `ckg_node`: `id` (**content-addressed**: hash of qualified name + kind — never positional or generation-ordered, so incremental refresh is a later feature, not a rewrite), `kind` (`module | class | function | method | constant`), `qualified_name`, `file_path`, `start_line`, `end_line`, `signature`, `docstring`. `constant` nodes capture module-level values (name, literal value, location) — thresholds like `RATE_VARIANCE_PCT = 0.15`. General variable tracking is deliberately excluded.
- `ckg_edge`: `source_id`, `target_id`, `kind` (`calls | imports | contains | reads_table | writes_table`), `line`. The `reads_table`/`writes_table` extraction (SQL strings, ORM calls) is the hardest part of the extractor and the most fixture-tested.
- `ckg_conditional`: `node_id` (owning function), `condition_text`, `line`. This is what makes "do we always flag X over $Y?" answerable — the threshold appears in `condition_text`.
- Optional LLM annotation pass (via LLMPort) writes `summary` fields on nodes as `source=machine` rows.

**L3 — Source.** No schema; the files themselves via SourceCodePort, addressed by L2 location refs, at the extraction commit SHA.

**Manifest linkage:** every L1/L2 row carries the extraction manifest reference (§13). The CKG and the source snapshot share one commit SHA; if they diverge, line references are invalid.

**Traversal:** router picks entry altitude (L0 "what is this app" / L1 "how does scoring work" / L2–L3 "in what order do functions execute"). Tools move between levels via joins: component → members → nodes → edges → line ranges → source. Every hop is a lookup, never an LLM guess — which is what makes CKG answers verifiable (§9.3).

## 6. Tools (the closed tool surface)

Plugin registry: each tool = `name`, `description`, `input_schema`, `run()`. Enabled tools declared per pack. Initial set:

1. `query_univariate_stats`
2. `lookup_data_dictionary`
3. `traverse_code_knowledge_graph`
4. `run_sql` (NL→SQL, §7)
5. `read_source` (retrieve code for a CKG-located node)
6. `answer_from_known_items` (search published Units)
7. `app_primer` (reads L0/L1 only — never the full CKG)
8. `search_business_docs`
9. `check_execution` (only if logs substrate enabled)

Tool outputs are retained per-turn as the **evidence bundle** (§9). Answers that are fundamentally "here is data" (stats rows, result sets) are returned **directly as `table` envelopes** (§10.5) — numbers travel from store to screen without passing through the model.

**In scope, explicitly:** *explain-the-score* — "look at item 123456, why is it in my queue?" answered as: the recorded score, the rule hits behind it, and a CKG/dictionary-grounded explanation of the logic. **Out of scope, explicitly:** *de-novo review* — the system judging an item itself. The first is a lookup + explanation; the second is the autonomous agent.

## 7. NL→SQL (inside run_sql)

- Ground generation with Data Dictionary + Dictionary Map + Univariate Stats — never raw schema alone. The grounding payload IS the Dictionary Map artifact; read it, don't duplicate it.
- Execute–check–repair loop: run generated SQL; on error, bounded retry with the error fed back; on success, hand to the Verifier.
- Generation via LLMPort, execution via SqlPort. Result rows are name-keyed dicts.

## 8. Agent harness (LangGraph)

Single well-routed agent — NOT multi-agent.

- **Router node** — classify intent AND altitude; select tool(s) or a fail-closed exit.
- **Tool node(s)** — invoke registered tools via ports; retain evidence bundles.
- **Verifier node** — §9. Mandatory on the path to every answer.
- **Terminal exits** — answer, or refuse/clarify/escalate as first-class outcomes.
- **Checkpointer** — conversation state per conversation via WorkStore.
- **Status events** — every node emits start/finish events; these stream to the UI (§10.2) and log to turn provenance. One emission, two destinations.

Every turn logs provenance: who asked, tools used, substrates read, evidence bundles, verifier verdict, substrate versions consulted.

## 9. Verifier (the moat — build it well)

The Verifier is a mandatory LangGraph node on the path to every answer — not an optional check bolted on after failures appear. It exists because LLMs do not copy values from evidence into prose; they regenerate them probabilistically, and regenerated numbers, entity names, and quotes are wrong at a low but irreducible rate. The Verifier makes that rate irrelevant by mechanically reconciling every drafted answer against the evidence before it ships.

### 9.1 Two distinct jobs

- **Faithfulness** — does the drafted answer accurately reflect the evidence the tools actually returned this turn? (Catches transcription hallucination: the summary that says 14,000 when the SQL returned 1,440.)
- **Plausibility** — does the evidence itself look sane against what is independently known about the data? (Catches wrong evidence: SQL that ran but answered the wrong question — a proportion contradicting known distributions.)

An answer must pass faithfulness always, and plausibility wherever a check is defined for the substrate involved.

### 9.2 The faithfulness mechanism (substrate-independent)

For every tool invocation, the raw output is retained as the turn's evidence bundle. After drafting:

1. **Extract** verifiable claims from the drafted prose with deterministic code, not an LLM: numeric values (regex-level), named entities (function/table/column names), verbatim quotes.
2. **Match** each claim against the evidence bundle. Exact matches and trivially derivable values (sums, percentages of returned values, standard rounding) resolve in code.
3. **Fuzzy-match with an LLM judge only where mechanical matching is insufficient** ("about 1.4 million" vs 1,442,986; "roughly a third" vs 34.2%). This is a yes/no verification call at temperature 0; the judge never produces the claim it checks.
4. **Verdict.** All claims accounted for → verified. Unmatched → regenerate (bounded retries, mismatch fed back) → downgrade to explicit "unverified" label → refuse. Never return a confident-but-unchecked answer. Verdict + claim-level detail log to turn provenance.

### 9.3 Per-substrate checks (plugin registry, like tools)

- **run_sql** — faithfulness: every numeric claim matches or derives from the result set. Plausibility: cross-check against Univariate Stats (row counts vs known table sizes, proportions vs known distributions, values vs known min/max). Thresholds are pack config, tuned at work against real distributions.
- **traverse_code_knowledge_graph** — faithfulness: every asserted function, call edge, or condition exists in this turn's traversal results. No plausibility check in v1.
- **read_source** — faithfulness: quoted or paraphrased-as-literal code appears in the retrieved file content (string match).
- **app_primer / search_business_docs** — minimal by design (paraphrase of curated human text). Spot-check: named entities appear in retrieved documents.
- **query_univariate_stats / lookup_data_dictionary** — faithfulness: numeric and definitional claims match retrieved rows.

A pack may register domain-specific checks.

### 9.4 Upstream hallucination hygiene (drafting rules)

- Answers containing figures are drafted at temperature 0, from a compact structured result — never a raw dump, never another summary (no summary-of-summary chains).
- Where the answer shape permits, the LLM drafts narrative with value references and **code injects the actual figures** — the LLM never types the number.
- Evidence stays close to generation: the drafting call receives this turn's evidence bundle, not accumulated session history.
- Data-shaped answers return as `table` envelopes directly (§6).

### 9.5 Publication pass

A Unit of Work's draft summary is itself a drafted answer making claims. Before a Unit moves from draft to published, its summary passes the same faithfulness reconciliation against the provenance bundles of its source turns. Hallucinated numbers must not become durable, shared artifacts.

## 10. Chat infrastructure & UX

### 10.1 Information architecture

Three first-class surfaces: private **workspaces**, the shared **library**, **chat**. A workspace is a folder: many conversations plus the Units mined from them. Every user gets a default "scratch" workspace. Layout: three panes — left sidebar (workspaces → conversations, Library link), center transcript + input, right inspector panel. All branding (app name, accent color, starter prompts) comes from pack config; nothing in the UX layer is pack-specific.

### 10.2 Streaming

All agent turns stream via SSE from generator-based Flask routes. Two frame types: **status events** (per LangGraph node start/finish — "Consulting data dictionary…", "Running SQL (attempt 1)…", "Verifying against statistics…") rendered as a live progress trail, and **one terminal outcome frame** carrying the complete, verified `TurnResult` (or an error). There are no token events (v2.2): §9.2 forbids returning a confident-but-unchecked answer, and the verdict exists only after drafting, so nothing of the answer body is shown before it. On completion the trail collapses to a chip ("✓ Verified · 3 tools · 14s"), expandable, persistent on every past turn, doubling as the provenance affordance. One turn runs per process at a time; a concurrent ask is refused (409) rather than interleaved. The frame contract is in the README ("Web layer").

### 10.3 Context management

Per-turn LLM context = system/pack context + running conversation summary + last N turns verbatim (N=10, configurable) + current turn's evidence bundle. The running summary regenerates past a turn-count threshold — synchronously inside the turn in v1 (v2.2; a Phase 5 Block 4 deliverable, ruled ahead of its build), asynchronously as the documented upgrade. **Summaries carry turn references, never restated figures** ("user established flag rates for item 4471 in turn 12") — the agent re-reads a turn's evidence bundle when it needs the actual number. No forced session boundaries: past a threshold, a dismissible banner nudges toward a fresh conversation. **Cross-conversation memory is out of scope for v1**; each conversation stands alone. State persists via the LangGraph checkpointer through WorkStore. Checkpoint shape across the Block 4 change (recorded here so the decision has a home): the turn history becomes a list of `HistoryTurn`s (turn number, question, answer, kind — named apart from the eval's per-turn `TurnRecord`), and legacy checkpoints holding `Message` pairs upgrade through a `mode="before"` validator — turn numbers assigned by pair index, lossy but safe — so an existing `work.db` keeps loading; no migration, no forced reset.

### 10.4 Evidence inspector & the Package flow

Right panel, two modes. **Inspect:** clicking a turn's chip shows its receipts — generated SQL, result rows as a table, CKG paths, source snippets, verifier verdict with claim detail. As built (Phase 5 Block 3), the receipts are everything the turn log and the evidence bundle recorded and the transcript never shows: the SQL attempt ledger with each attempt's lint challenges (fan-out, enum, interval) and whether it was blocked, executed, or overridden; every plausibility finding by check name and severity; the final draft marked claim by claim from the verdict's char offsets; the refusal's engineer diagnosis; the progress trail with per-step durations and the raw router text behind a protocol violation. Recorded at the time, never recomputed — the per-turn slice of what `engine eval exposure` computes over a report. **Package:** the per-conversation "Package this" action switches the panel to the packaging flow — LLM-proposed turn selection with include/exclude checkboxes → editable draft Unit (title, narrative) → §9.5 verification result → Publish. The transcript stays visible alongside; the user curates from it.

### 10.5 Rendering & input

Typed response envelope: `markdown` (marked.js; fenced code blocks inside it are highlighted by highlight.js — this is how source renders, with no separate `code` kind, v2.2) and `table` (HTML table honoring the per-column display hints the engine attached — money cells render as currency and duration cells humanized (`1.1 days`, `1 hour`), NULL cells as an em dash, identically to the CLI's text rendering; rounding follows the browser's half-up on the exact double so the engine and the page never disagree on a digit). `chart` (Chart.js from an LLM-produced JSON spec) is deferred to a later phase (v2.2). No free-form HTML. marked.js and highlight.js are vendored into the engine's static directory at pinned versions with their licenses; nothing loads from a CDN. Input is a single plain-text box — no slash commands, no menus. Empty state shows pack-configured starter prompts. Fail-closed exits render as first-class styled cards stating what can't be answered, why, and what would work — never apologetic prose, and never engineer detail: a step count or a plausibility tolerance is the diagnosis, carried on the outcome's `detail` field for the inspector, not the card. Prose answers inject values, not passages (v2.2, Block 2): a placeholder resolving to a description, a snippet, or source text is accepted only inside a fenced code block; elsewhere the draft is retried with that rule as feedback, and quoted source opens on its `def` line under a language label.

## 11. Crowdsourcing layer

- **Chat is just chat.** Private, messy, topic-jumping by design. No "analysis mode." The turn log silently records full provenance for every turn.
- **Unit of Work — created by the Package action, not by chatting.** A Unit is extracted from a conversation by deliberate user action, after the fact. The LLM proposes which stretch of turns constitutes the coherent analysis; the user includes/excludes turns; the LLM generates a draft summary from the selected turns.
- **The LLM drafts; the human authors.** The draft lands as an editable Unit in the workspace. The user revises; the byline is theirs. (LLM "this looks publishable" nudges: deferred to v2.)
- **Provenance survives mechanically.** The Unit stores source-turn references; each turn carries its logged provenance. Findings link to the turns that produced them. Attribution never depends on the LLM preserving citations through paraphrase.
- **Publish-when-ready, gated by the Verifier** (§9.5). Units failing verification cannot publish until regenerated or corrected.
- **Shared library** — published Units browsable as articles; comments and SME notes.
- **Deflect-repeat-asks** — `answer_from_known_items` searches published Units; matches are **suggested**, never hard-redirected. Low priority; droppable.
- **Human gate** — an admin can mark a Unit canonical (reversible).
- **Staleness flagging** — published Units record the substrate versions (CKG manifest, stats manifest) that grounded them; a regeneration can flag Units grounded in code/data that has since changed.

## 12. Data model owned BY the engine (WorkStore)

- `workspace` (owner, name, created)
- `conversation` (workspace_id, title, created; checkpointer state)
- `unit_of_work` (workspace_id, title, narrative [user-edited], source_turn_refs [ordered], provenance_bundle [derived], substrate_version_refs, state: `draft | published | canonical`, author, timestamps, publication verifier_verdict)
- `comment` (unit_id, author, text, parent_id)
- `turn_log` (conversation_id, turn, actor, action, tools_used, substrates_read, evidence_bundle_ref, verifier_verdict, substrate_versions, status_events [2026-08-20 extension: the per-node trail], question, outcome [Phase 5 Block 3 extension: what reopening a conversation shows — the checkpoint history holds only a transcript placeholder for tables and is the LLM-context record, so the two stay separate; an older store gains the columns in place], timestamps)

## 13. Generators, validator, and pack-authoring tooling (first-class v1 deliverables)

Three generators + one validator, built as product code with fixtures — not throwaway scripts. Each generator emits a **manifest**: source commit SHA (or source table identifiers), extraction timestamp, generator version. Each is tested against InvoiceGuard fixtures with checked-in expected output. Generator correctness is load-bearing: a subtly wrong extractor poisons the substrate silently.

1. **Data Dictionary generator** — schema introspection (via SqlPort) producing the structural skeleton + merge of the SME overlay (human rows preserved per §4 provenance rules).
2. **Univariate Stats generator** — queries over live tables producing the stats substrate.
3. **CKG generator** — repo walk (stdlib `ast`) over the Source Code Store producing L2, proposing L1 memberships, validating L0 references. Optional LLM annotation pass via LLMPort.
4. **Conformance validator** — runs at work; reports whether real tables/code satisfy the contracts. Its small text report is **the only thing that travels back** (by commit, to a `report/` branch). The iterate loop: pull at work → run generators + validator → commit report → fix on the Mac → push again.

**Pack-authoring runbook** — the step-by-step "point this at a new application" document, written for someone who isn't the author, proven by following it verbatim to build the InvoiceGuard pack. At work, this runbook + Glean is the Claude Code substitute.

## 14. Instance pack format + InvoiceGuard

A pack is a directory: `config.yaml` (enabled substrates/tools, adapter selections, table-name mappings, SPL templates, branding, starter prompts, verifier thresholds) + substrate data matching §4 contracts + the L0 primer + business docs.

**InvoiceGuard** (external reference application + engine-side pack; the pack is private, never a public demo):

- **Domain:** suppliers submit invoices with line items; rules score them for audit priority; auditors work a Get Next queue. Structurally analogous to the real target application — same pipeline shape (intake → parse → map → rules → score → queue → review → expiry), different domain.
- **The application is external.** InvoiceGuard is a standalone, fully functional application in its own repo (`chad-adams-firebase/invoice-guard`): a real Flask/SQLAlchemy pipeline with a deterministic simulation driver (seeded RNG + injectable clock) that populates a real SQLite database and emits real structured log files. The engine's pack directory for it contains ONLY: `config.yaml`, generated substrates, the L0 primer, business docs, and pointers (repo path, pinned commit SHA, database path) — never application source.
- **Code access:** SourceCodePort's local adapter points at a local clone of the invoice-guard repo pinned to a commit SHA; the CKG is extracted at that same SHA.
- **Inputs vs. observable state:** seed/simulation tooling lives in the invoice-guard repo and produces INPUTS only. All observable state — scores, rule hits, queue states, logs — comes from actually running its pipeline.
- **Tables:** 14, per invoice-guard's functional spec §3 (`suppliers`, `contracts`, `invoices`, `invoice_lines`, `findings`, `finding_feedback`, `review_reports`, `review_report_lines`, `compliance_reports`, `compliance_rules`, `users`, `config`, `scheduled_tasks`, `invoice_history`).
- **Planted stories** (emerge from simulation runs, not authored data): one supplier with clustering rate variances; one item code flagged ~90% of the time; one reviewer at 3× median throughput; the "invoice total ≠ sum of lines when adjustments exist" gotcha planted in the simulation inputs and documented in the Dictionary Map.
- **Substrates:** dictionary (~60 rows) — skeleton produced by the dictionary generator; Dictionary Map (canonical metrics: flag rate, audit yield; join paths; gotchas); univariate stats — produced by the stats generator; CKG — produced by the CKG generator against the pinned invoice-guard clone; L0 primer referencing the 18 `ig.*` components (12 `ig.spine.*` + 6 `ig.platform.*`, per invoice-guard's functional spec); 2–3 business context docs (audit policy memo explaining why thresholds exist); structured log files from real simulation runs.

## 15. Testing

- Unit tests per port adapter and per tool plugin.
- Generator tests: fixtures with checked-in expected output, run against a **vendored snapshot** of a small InvoiceGuard subset (a few source files + a small extracted DB) checked into this repo's test fixtures, with the source commit SHA recorded in the fixture manifest. Full pack generation uses the live local clone; tests never require network — the suite stays green offline.
- End-to-end tests driven by the InvoiceGuard pack through local adapters: ask → route → tool → verify → answer; package → publish → library → comment.
- Contract tests: any pack conforming to §4 schemas loads and runs.
- Verifier tests with deliberately wrong SQL results and deliberately corrupted summaries, confirming catch + downgrade + refuse behavior.
- Target: full suite green offline via `uv run pytest`; `uv run flask run` demos InvoiceGuard locally.

## 16. Scope

**In:** everything above, including written-but-untested real adapters (Databricks FM, databricks-sql-connector, Delta SubstrateStore/WorkStore, Splunk, git-clone SourceCode) and explain-the-score.

**Out:** autonomous Analysis Agent; de-novo item review; config/admin UI; live enterprise integration & deploy (Databricks Apps — gated on Unity Catalog; separate track); cross-conversation memory; LLM publish-nudges; Skills/Playbooks port; lineage & change-history substrates; deflection beyond suggestion.

## 17. Deliverables

1. Engine: ports, DI/config, pack loader, harness, router, verifier, tool registry, chat layer, crowdsourcing layer, WorkStore.
2. Local adapters (tested) + real adapters (written, untested) for every port + pytest LLM stub.
3. Three generators + conformance validator + manifests (§13).
4. InvoiceGuard instance pack (config, generated substrates, primer, business docs); the application itself and its simulation driver live in the external invoice-guard repo.
5. Test suite (§15).
6. Docs: architecture README, how-to-run-locally, pack-authoring runbook, real-adapter debugging notes (written for Glean-assisted work-side debugging).
7. CLAUDE.md conventions file (repo root).

## 18. Open items & work-side checklist

| Item | Status |
|---|---|
| LLM egress: bare script vs current Databricks FM endpoint + auth | Re-verify at work (~10 min; prior app proved the path, org changes possible) |
| Splunk REST reachability (port 8089) + API token + log answerable-ness | Untested; pluggable substrate, not load-bearing |
| Peer-visibility ACL policy (reviewer stats visible to peers?) | Leadership input; pack-config question, not engineering |
| Personal GitHub on corporate laptop vs policy | Assumed, not confirmed — check |
| Fresh, longer-lived PAT for the real repo; calendar the expiry | Before first work-side clone (current toy PAT expires ~2026-09-10) |
| Verifier plausibility thresholds | Tuned at work against real distributions; pack config |
| Databricks Apps deployment | Gated on Unity Catalog migration; out of scope; demo runs locally at work |
| How the DuckDB SqlPort adapter reads InvoiceGuard's SQLite DB: native attach vs. a conversion step in pack tooling | Decided (Phase 2): conversion step in pack tooling — DuckDB's sqlite_scanner extension requires a runtime network download, which breaks offline-green tests and the corporate proxy; a deterministic stdlib-sqlite3→DuckDB converter with a manifest replaces it |
