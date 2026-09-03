/* The Block 3 page: three panes — workspaces and conversations on the
   left, the transcript in the middle, the inspector on the right
   (Brief §10.1, §10.4). Vanilla JS; no framework, no build, no
   browser storage (CLAUDE.md): a reload starts in the empty state of
   the first workspace, with the starter prompts.

   Rendering mirrors the engine's rules — cells format exactly as
   harness/render.py does, chips and cards exactly as web/render.py
   does. The transcript speaks to the person who asked; the inspector
   shows the receipts, engineer detail included. */
(function () {
  "use strict";

  // ---- DOM --------------------------------------------------------------
  const transcript = document.getElementById("transcript");
  const transcriptInner = document.getElementById("transcript-inner");
  const emptyState = document.getElementById("empty-state");
  const startersBox = document.getElementById("starters");
  const composer = document.getElementById("composer");
  const input = document.getElementById("question");
  const send = document.getElementById("send");
  const workspaceList = document.getElementById("workspaces");
  const conversationList = document.getElementById("conversations");
  const sideNote = document.getElementById("side-note");
  const inspectorTitle = document.getElementById("inspector-title");
  const inspectorBody = document.getElementById("inspector-body");

  // ---- state (in-page only) ------------------------------------------
  const state = {
    workspaces: [], workspaceId: null,
    conversations: [], conversationId: null,
    turns: [],          // this conversation's turn records, oldest first
    inspecting: null,   // the record whose receipts the inspector shows
  };

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  async function api(method, path, body) {
    const response = await fetch(path, {
      method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (response.status === 204) return null;
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.message || `HTTP ${response.status}`);
    return payload;
  }

  // ---- cell formatting (mirrors harness/render.py) ---------------------
  // Same rule as engine/harness/render.py format_money: sign, symbol,
  // thousands separators, two decimals. toFixed rounds the exact
  // binary value half-up; the engine's _fixed does the same, so the
  // digits agree on every double.
  function formatMoney(value, symbol) {
    const sign = value < 0 ? "-" : "";
    const fixed = Math.abs(value).toFixed(2);
    const [whole, frac] = fixed.split(".");
    return sign + symbol + whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + "." + frac;
  }

  // Same rule as render.py humanize_seconds: the largest unit the
  // duration fills, one decimal, trailing .0 dropped, singular at 1.
  // The magnitude is rounded to the millisecond before the unit is
  // chosen (3599.99998 s is one hour, not "60 minutes"); floor of
  // x * 1000 + 0.5 so a tie rounds the same way as the engine.
  const UNIT_SECONDS = { seconds: 1, minutes: 60, hours: 3600, days: 86400 };
  const HUMAN_UNITS = [["day", 86400], ["hour", 3600], ["minute", 60], ["second", 1]];
  const CLOCK = /^(\d+):([0-5]\d):([0-5]\d)(?:\.(\d+))?$/;
  function humanizeSeconds(seconds) {
    const sign = seconds < 0 ? "-" : "";
    const magnitude = Math.floor(Math.abs(seconds) * 1000 + 0.5) / 1000;
    let name, amount;
    for (const [unit, size] of HUMAN_UNITS) {
      if (magnitude >= size || size === 1) { name = unit; amount = (magnitude / size).toFixed(1); break; }
    }
    const text = amount.endsWith(".0") ? amount.slice(0, -2) : amount;
    return sign + text + " " + (text === "1" ? name : name + "s");
  }
  function formatDuration(value, unit) {
    if (typeof value === "string") {
      const m = CLOCK.exec(value.trim());
      if (!m) return null;
      let total = Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]);
      if (m[4]) total += Number("0." + m[4]);
      return humanizeSeconds(total);
    }
    if (unit && typeof value === "number") return humanizeSeconds(value * UNIT_SECONDS[unit]);
    return null;
  }

  // Same rule as render.py format_rate: one decimal, a fraction shown
  // x100, a percent-scale cell as it stands; past 100% still renders.
  function formatRate(value, scale) {
    const shown = scale === "percent" ? value : value * 100;
    return shown.toFixed(1) + "%";
  }

  // Same rule as render.py format_cell: NULL is an em dash, never blank.
  const NULL_CELL = "\u2014";
  function formatCell(value, hint) {
    if (value === null || value === undefined) return NULL_CELL;
    if (hint && hint.kind === "duration") {
      const rendered = formatDuration(value, hint.unit);
      if (rendered !== null) return rendered;
    }
    if (typeof value === "number" && hint && hint.kind === "money") return formatMoney(value, hint.symbol);
    if (typeof value === "number" && hint && hint.kind === "rate") return formatRate(value, hint.scale);
    if (typeof value === "boolean") return value ? "true" : "false";
    return String(value);
  }

  // ---- transcript renderers (mirror web/render.py) ---------------------
  function renderMarkdown(text) {
    const box = el("div", "markdown");
    box.innerHTML = marked.parse(text);
    box.querySelectorAll("pre code").forEach(block => {
      if (window.hljs) hljs.highlightElement(block);
    });
    return box;
  }

  function codeBlock(text, language) {
    const pre = el("pre");
    const code = el("code", language ? "language-" + language : null, text);
    pre.appendChild(code);
    if (window.hljs) hljs.highlightElement(code);
    return pre;
  }

  // Same sentence as render.py NO_ROWS: a zero-row table says so.
  const NO_ROWS = "No rows matched";
  function renderTable(table, caption) {
    const wrap = el("div", "table");
    if (!table.rows.length) {
      wrap.appendChild(el("p", "empty-rows", NO_ROWS));
      if (caption) wrap.appendChild(el("div", "caption", caption));
      return wrap;
    }
    const t = el("table");
    const head = el("tr");
    table.columns.forEach(c => head.appendChild(el("th", null, c)));
    t.appendChild(head);
    const formats = table.column_formats || {};
    table.rows.forEach(row => {
      const tr = el("tr");
      table.columns.forEach(c => {
        const value = row[c];
        const td = el("td", typeof value === "number" ? "num" : null, formatCell(value, formats[c]));
        tr.appendChild(td);
      });
      t.appendChild(tr);
    });
    wrap.appendChild(t);
    if (table.truncated) wrap.appendChild(el("div", "caption", `(${table.rows.length} of ${table.total_row_count} rows)`));
    if (caption) wrap.appendChild(el("div", "caption", caption));
    return wrap;
  }

  function renderCard(kind, title, fields) {
    const card = el("div", "card " + kind);
    card.appendChild(el("h4", null, title));
    const dl = el("dl");
    fields.forEach(([label, value]) => {
      if (!value) return;
      dl.appendChild(el("dt", null, label));
      dl.appendChild(el("dd", null, value));
    });
    card.appendChild(dl);
    return card;
  }

  function renderOutcome(outcome) {
    if (outcome.kind === "answer") {
      const box = el("div", "answer");
      if (outcome.verification === "unverified") {
        box.appendChild(el("div", "badge", "UNVERIFIED — this answer could not be fully checked against its evidence"));
      }
      if (outcome.body.kind === "table") box.appendChild(renderTable(outcome.body.table, outcome.body.caption));
      else box.appendChild(renderMarkdown(outcome.body.text));
      return box;
    }
    // Cards speak to the person who asked: reason and remedy, in the
    // engine's plain language. The engineer's diagnosis (the outcome's
    // detail field) is never rendered here — the inspector shows it.
    // Titles mirror engine/web/render.py CARD_TITLES verbatim.
    if (outcome.kind === "refuse") {
      return renderCard("refuse", "This can't be answered", [
        ["Why", outcome.reason], ["What would work", outcome.what_would_work]]);
    }
    if (outcome.kind === "clarify") {
      return renderCard("clarify", "One thing to clarify first", [["Question", outcome.question]]);
    }
    return renderCard("escalate", "This needs a person", [["Why", outcome.reason]]);
  }

  // ---- the chip (mirrors web/render.py chip_label) ---------------------
  const CHIP_LABELS = {
    verified: "✓ Verified", unverified: "⚠ Unverified",
    refuse: "⊘ Refused", clarify: "? Clarify", escalate: "↑ Escalated",
  };
  // Read from the trail, not from tools_used: a bounced run_sql and its
  // English retry are two invocations of one tool — "1 tool · 1 retry",
  // not "2 tools". A finish event of a tool node reads "evidence[i] ok"
  // or "error: …"; an unknown-tool skip is neither.
  function toolTally(events) {
    const finishes = events
      .filter(e => e.phase === "finish" && e.node.startsWith("tool:"))
      .map(e => [e.node, e.detail]);
    let ok = 0, retries = 0, failed = 0;
    finishes.forEach(([node, detail], index) => {
      if (detail.startsWith("evidence[")) ok += 1;
      else if (detail.startsWith("error:")) {
        if (finishes.slice(index + 1).some(([later]) => later === node)) retries += 1;
        else failed += 1;
      }
    });
    return { ok, retries, failed };
  }
  function elapsedSeconds(events) {
    if (events.length < 2) return 0;
    const delta = (Date.parse(events[events.length - 1].at) - Date.parse(events[0].at)) / 1000;
    return Math.max(1, Math.floor(delta + 0.5));
  }
  // Without an outcome — a row written before the turn log kept
  // outcomes — the trail's finalize event says what ended the turn and
  // the recorded verdict says how an answer fared (mirrors chip_key).
  function chipKey(outcome, events, verdict) {
    if (outcome) return outcome.kind === "answer" ? outcome.verification : outcome.kind;
    const ended = (events || []).slice().reverse()
      .find(e => e.node === "finalize" && e.phase === "finish");
    if (!ended) return null;
    if (ended.detail === "answer") {
      const disposition = verdict && verdict.disposition;
      return disposition in CHIP_LABELS ? disposition : "unverified";
    }
    return ended.detail in CHIP_LABELS ? ended.detail : null;
  }
  function chipLabel(outcome, events, verdict) {
    const tally = toolTally(events);
    const parts = [CHIP_LABELS[chipKey(outcome, events, verdict)], `${tally.ok} tool${tally.ok === 1 ? "" : "s"}`];
    if (tally.retries) parts.push(`${tally.retries} ${tally.retries === 1 ? "retry" : "retries"}`);
    if (tally.failed) parts.push(`${tally.failed} failed`);
    const seconds = elapsedSeconds(events);
    if (seconds) parts.push(`${seconds}s`);
    return parts.join(" · ");
  }
  function chipFor(record) {
    const key = chipKey(record.outcome, record.events, record.verdict);
    const chip = el("button", "chip " + key, chipLabel(record.outcome, record.events, record.verdict));
    chip.type = "button";
    chip.title = "Show this turn's receipts in the inspector";
    chip.addEventListener("click", () => inspect(record));
    record.chip = chip;
    return chip;
  }

  // ---- SSE over fetch (POST; EventSource is GET-only) -----------------
  async function streamAsk(question, onFrame) {
    const body = { question, conversation_id: state.conversationId };
    if (state.conversationId === null) body.workspace_id = state.workspaceId;
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.message || `HTTP ${response.status}`);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let split;
      while ((split = buffer.indexOf("\n\n")) >= 0) {
        const raw = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        let event = "message", data = "";
        raw.split("\n").forEach(line => {
          if (line.startsWith(":")) return;               // keepalive comment
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trim();
        });
        if (data) await onFrame(event, JSON.parse(data));
      }
    }
  }

  // ---- the transcript -------------------------------------------------
  function scrollToEnd() { transcript.scrollTop = transcript.scrollHeight; }
  function clearTranscript() {
    transcriptInner.querySelectorAll(".turn").forEach(node => node.remove());
  }

  // Same sentences as web/render.py for rows written before the turn
  // log carried the question and the outcome.
  const QUESTION_NOT_RECORDED = "(question not recorded)";
  const OUTCOME_NOT_RECORDED = "(outcome not recorded)";

  function appendPastTurn(turn) {
    const record = {
      turn: turn.turn, question: turn.question, outcome: turn.outcome, verdict: turn.verdict,
      events: turn.status_events || [], evidence_bundle_ref: turn.evidence_bundle_ref, evidence: null,
    };
    const turnEl = el("div", "turn");
    turnEl.appendChild(el("div", "question", record.question || QUESTION_NOT_RECORDED));
    // The chip is drawn whenever the trail can name one: a legacy
    // turn's verdict, events and evidence are all in the row, and the
    // inspector shows them; only the outcome card is missing.
    if (chipKey(record.outcome, record.events, record.verdict)) turnEl.appendChild(chipFor(record));
    if (record.outcome) turnEl.appendChild(renderOutcome(record.outcome));
    else turnEl.appendChild(el("div", "muted", OUTCOME_NOT_RECORDED));
    record.element = turnEl;
    state.turns.push(record);
    transcriptInner.appendChild(turnEl);
  }

  async function ask(question) {
    question = (question || "").trim();
    if (!question || send.disabled) return;
    input.value = "";
    send.disabled = true;
    emptyState.hidden = true;

    const record = { question, events: [], outcome: null, verdict: null, evidence_bundle_ref: null, evidence: null, turn: null };
    const turnEl = el("div", "turn");
    turnEl.appendChild(el("div", "question", question));
    const trail = el("ul", "trail");
    turnEl.appendChild(trail);
    record.element = turnEl;
    transcriptInner.appendChild(turnEl);
    scrollToEnd();

    try {
      await streamAsk(question, async (event, payload) => {
        if (event === "status") {
          record.events.push(payload);
          const li = el("li", payload.phase, payload.detail);
          if (payload.phase === "finish" && /^(error|protocol violation)/.test(payload.detail)) li.className = "error";
          trail.appendChild(li);
        } else if (event === "result") {
          const result = payload.result;
          record.turn = result.turn;
          record.outcome = result.outcome;
          record.verdict = result.verdict;
          record.events = result.events || record.events;
          record.evidence_bundle_ref = result.evidence_bundle_ref;
          const opened = state.conversationId === null;
          state.conversationId = result.conversation_id;
          state.turns.push(record);
          trail.remove();                          // the inspector shows it with durations
          turnEl.appendChild(chipFor(record));
          turnEl.appendChild(renderOutcome(result.outcome));
          if (opened) await loadConversations();   // the new conversation joins the sidebar
        } else if (event === "error") {
          throw new Error(payload.message);
        }
        scrollToEnd();
      });
    } catch (err) {
      const chip = el("span", "chip error", "✗ Error");
      turnEl.insertBefore(chip, trail.parentNode === turnEl ? trail : null);
      turnEl.appendChild(renderCard("error", "Something went wrong", [
        ["What happened", "The engine stopped before it could answer; nothing was verified or saved for this turn."],
        ["What would work", "Asking again in a moment. If it keeps happening, the server log has the cause."],
        ["Detail", String(err.message || err)]]));
      if (state.conversationId === null) emptyState.hidden = state.turns.length > 0;
    } finally {
      send.disabled = false;
      input.focus();
    }
  }

  // ---- sidebar: workspaces --------------------------------------------
  function note(text) { sideNote.textContent = text || ""; sideNote.hidden = !text; }

  async function loadWorkspaces() {
    state.workspaces = await api("GET", "/api/workspaces");
    renderWorkspaces();
  }
  function renderWorkspaces() {
    workspaceList.replaceChildren(...state.workspaces.map(workspace => {
      const li = el("li", "side-item" + (workspace.id === state.workspaceId ? " active" : ""));
      const name = el("button", "side-name", workspace.name);
      name.type = "button";
      name.addEventListener("click", () => selectWorkspace(workspace.id));
      const remove = el("button", "side-action", "×");
      remove.type = "button";
      remove.title = "Delete this workspace (only when it holds no conversations)";
      remove.addEventListener("click", () => deleteWorkspace(workspace));
      li.append(name, remove);
      return li;
    }));
  }
  async function selectWorkspace(id) {
    state.workspaceId = id;
    renderWorkspaces();
    await loadConversations();
    newConversation();
  }
  async function createWorkspace() {
    const name = window.prompt("Workspace name");
    if (!name || !name.trim()) return;
    try {
      const workspace = await api("POST", "/api/workspaces", { name: name.trim() });
      await loadWorkspaces();
      await selectWorkspace(workspace.id);
    } catch (err) { note(err.message); }
  }
  async function deleteWorkspace(workspace) {
    if (!window.confirm(`Delete workspace "${workspace.name}"? Only an empty workspace can be deleted.`)) return;
    try { await api("DELETE", `/api/workspaces/${workspace.id}`); note(""); }
    catch (err) { note(err.message); return; }
    await loadWorkspaces();
    if (state.workspaceId === workspace.id) await selectWorkspace(state.workspaces[0].id);
  }

  // ---- sidebar: conversations ------------------------------------------
  async function loadConversations() {
    state.conversations = state.workspaceId === null
      ? [] : await api("GET", `/api/workspaces/${state.workspaceId}/conversations`);
    renderConversations();
  }
  function renderConversations() {
    conversationList.replaceChildren(...state.conversations.map(conversation => {
      const li = el("li", "side-item" + (conversation.id === state.conversationId ? " active" : ""));
      const title = el("button", "side-name", conversation.title);
      title.type = "button";
      title.title = conversation.title;
      title.addEventListener("click", () => openConversation(conversation.id));
      const rename = el("button", "side-action", "✎");
      rename.type = "button";
      rename.title = "Rename";
      rename.addEventListener("click", () => renameConversation(conversation));
      const remove = el("button", "side-action", "×");
      remove.type = "button";
      remove.title = "Delete this conversation and its turns";
      remove.addEventListener("click", () => deleteConversation(conversation));
      li.append(title, rename, remove);
      return li;
    }));
  }
  async function renameConversation(conversation) {
    const title = window.prompt("Conversation title", conversation.title);
    if (!title || !title.trim() || title.trim() === conversation.title) return;
    try { await api("PATCH", `/api/conversations/${conversation.id}`, { title: title.trim() }); }
    catch (err) { note(err.message); return; }
    await loadConversations();
  }
  async function deleteConversation(conversation) {
    if (!window.confirm(`Delete "${conversation.title}" and every turn in it?`)) return;
    try { await api("DELETE", `/api/conversations/${conversation.id}`); note(""); }
    catch (err) { note(err.message); return; }
    if (state.conversationId === conversation.id) newConversation();
    await loadConversations();
  }

  // "New conversation": back to the empty state with the starters; the
  // row is created by the first turn, never by the click, so an
  // abandoned start leaves nothing behind.
  function newConversation() {
    state.conversationId = null;
    state.turns = [];
    clearTranscript();
    emptyState.hidden = false;
    clearInspector();
    renderConversations();
    input.focus();
  }
  async function openConversation(id) {
    let payload;
    try { payload = await api("GET", `/api/conversations/${id}/turns`); }
    catch (err) { note(err.message); return; }
    state.conversationId = id;
    state.turns = [];
    clearTranscript();
    emptyState.hidden = true;
    clearInspector();
    renderConversations();
    payload.turns.forEach(appendPastTurn);
    scrollToEnd();
    input.focus();
  }

  // ---- inspector: the receipts (Brief §10.4) ---------------------------
  // Everything below reads what five interludes recorded: the SQL
  // attempt ledger with its lint challenges and overrides, the verdict
  // claim by claim with every plausibility finding by check name, the
  // outcome's engineer diagnosis, and the raw router text behind a
  // protocol violation. The transcript never shows these.
  function clearInspector() {
    state.inspecting = null;
    document.querySelectorAll(".chip.inspecting").forEach(c => c.classList.remove("inspecting"));
    inspectorTitle.textContent = "Receipts";
    inspectorBody.replaceChildren(el("p", "hint",
      "Click a turn's chip to see its receipts: the SQL attempts and their challenges, the evidence, the verdict claim by claim, the progress trail."));
  }
  function section(title, ...children) {
    const s = el("section", "insp");
    s.appendChild(el("h4", null, title));
    children.forEach(child => { if (child) s.appendChild(child); });
    return s;
  }
  function field(label, value, cls) {
    const row = el("div", "field" + (cls ? " " + cls : ""));
    row.appendChild(el("span", "label", label));
    row.appendChild(el("span", "value", value));
    return row;
  }
  function plural(n, word) { return `${n} ${word}${n === 1 ? "" : "s"}`; }

  async function inspect(record) {
    state.inspecting = record;
    document.querySelectorAll(".chip.inspecting").forEach(c => c.classList.remove("inspecting"));
    if (record.chip) record.chip.classList.add("inspecting");
    inspectorTitle.textContent = record.turn ? `Turn ${record.turn}` : "Turn";
    const evidenceSection = section("Evidence", el("p", "hint",
      record.evidence_bundle_ref ? "Loading…" : "No tool was called on this turn."));
    inspectorBody.replaceChildren(
      section("Question", el("p", "question-text", record.question || QUESTION_NOT_RECORDED)),
      section("Outcome", renderOutcomeReceipt(record.outcome)),
      section("Verifier verdict", renderVerdict(record.verdict, record.outcome)),
      evidenceSection,
      section("Progress trail", renderTrail(record.events)),
    );
    if (!record.evidence_bundle_ref) return;
    try {
      if (!record.evidence) record.evidence = await api("GET", `/api/evidence/${record.evidence_bundle_ref}`);
      if (state.inspecting !== record) return;
      evidenceSection.replaceChildren(el("h4", null, `Evidence · ${record.evidence_bundle_ref}`), renderEvidence(record.evidence));
    } catch (err) {
      evidenceSection.appendChild(el("p", "error", err.message));
    }
  }

  function renderOutcomeReceipt(outcome) {
    const box = el("div");
    if (!outcome) { box.appendChild(el("p", "hint", OUTCOME_NOT_RECORDED)); return box; }
    if (outcome.kind === "answer") {
      box.appendChild(field("Kind", `answer · ${outcome.body.kind} · ${outcome.verification}`));
      if (outcome.body.kind === "table") {
        box.appendChild(field("Rows", `${outcome.body.table.rows.length} of ${outcome.body.table.total_row_count}`));
      }
      return box;
    }
    box.appendChild(field("Kind", outcome.kind));
    if (outcome.kind === "refuse") {
      box.appendChild(field("Reason", outcome.reason));
      if (outcome.what_would_work) box.appendChild(field("What would work", outcome.what_would_work));
      // The engineer's diagnosis — which bound tripped, by how much.
      // Inspector-only: the card in the transcript never shows it.
      if (outcome.detail) box.appendChild(field("Diagnosis", outcome.detail, "diagnosis"));
    } else if (outcome.kind === "clarify") {
      box.appendChild(field("Question", outcome.question));
    } else {
      box.appendChild(field("Reason", outcome.reason));
    }
    return box;
  }

  function renderVerdict(verdict, outcome) {
    if (!verdict) {
      return el("p", "hint", "No verdict: the turn ended before the Verifier (the router refused, asked to clarify, or escalated).");
    }
    const box = el("div");
    box.appendChild(field("Disposition", `${verdict.disposition} · ${verdict.mode} · ${plural(verdict.judge_calls, "judge call")}`));
    if (verdict.reason) box.appendChild(field("Reason", verdict.reason));
    const findings = verdict.plausibility || [];
    box.appendChild(el("h5", null, `Plausibility findings (${findings.length})`));
    if (findings.length) {
      const list = el("ul", "findings");
      findings.forEach(finding => {
        const li = el("li", "finding " + finding.severity);
        li.appendChild(el("span", "severity", finding.severity));
        li.appendChild(el("code", null, finding.check));
        li.appendChild(el("span", "detail", finding.detail));
        list.appendChild(li);
      });
      box.appendChild(list);
    } else {
      box.appendChild(el("p", "hint", "None — every evidence-side check was silent."));
    }
    const attempts = verdict.attempts || [];
    attempts.forEach((attempt, index) => {
      box.appendChild(el("h5", null,
        `Attempt ${attempt.attempt} — ${plural(attempt.claims.length, "claim")}, ${attempt.unmatched_count} unsupported`));
      const last = index === attempts.length - 1;
      if (last && verdict.mode === "prose" && outcome && outcome.kind === "answer" && outcome.body.kind === "markdown") {
        box.appendChild(renderClaimedText(outcome.body.text, attempt.claims));
      }
      if (attempt.claims.length) box.appendChild(renderClaims(attempt.claims));
    });
    return box;
  }

  // The final draft with each claim marked by how it fared, from the
  // claim records' char offsets (ClaimRecord.start/end).
  function renderClaimedText(text, claims) {
    const box = el("div", "claimed-text");
    let cursor = 0;
    claims.slice().sort((a, b) => a.start - b.start).forEach(claim => {
      if (claim.start < cursor || claim.end > text.length) return;   // overlap, or offsets from another draft
      if (claim.start > cursor) box.appendChild(document.createTextNode(text.slice(cursor, claim.start)));
      const mark = el("mark", "claim " + claim.status, text.slice(claim.start, claim.end));
      mark.title = claim.status
        + (claim.matched_value ? ` → ${claim.matched_value}` : "")
        + (claim.method ? ` (${claim.method})` : "")
        + (claim.evidence_ref ? ` ${claim.evidence_ref}` : "");
      box.appendChild(mark);
      cursor = claim.end;
    });
    if (cursor < text.length) box.appendChild(document.createTextNode(text.slice(cursor)));
    return box;
  }

  function renderClaims(claims) {
    const wrap = el("div", "table");
    const t = el("table");
    const head = el("tr");
    ["status", "kind", "claim", "matched", "how", "evidence", "note"].forEach(h => head.appendChild(el("th", null, h)));
    t.appendChild(head);
    claims.forEach(claim => {
      const tr = el("tr");
      tr.appendChild(el("td", "status-" + claim.status, claim.status));
      tr.appendChild(el("td", null, claim.kind));
      tr.appendChild(el("td", null, claim.surface));
      tr.appendChild(el("td", null, claim.matched_value === null || claim.matched_value === undefined ? NULL_CELL : String(claim.matched_value)));
      tr.appendChild(el("td", null, claim.method || (claim.injected ? "injected" : NULL_CELL)));
      tr.appendChild(el("td", null, claim.evidence_ref || NULL_CELL));
      tr.appendChild(el("td", null, claim.reason || ""));
      t.appendChild(tr);
    });
    wrap.appendChild(t);
    return wrap;
  }

  // Any list of row-shaped records (stats rows, dictionary rows, log
  // errors) as a table: provenance dropped, nested values as JSON —
  // the same projection harness/tables.py applies.
  function rowsToTable(rows) {
    if (!rows || !rows.length) return el("p", "hint", "No rows.");
    const columns = Object.keys(rows[0]).filter(key => key !== "provenance");
    const flat = rows.map(row => {
      const cells = {};
      columns.forEach(column => {
        const value = row[column];
        cells[column] = value !== null && typeof value === "object" ? JSON.stringify(value) : value;
      });
      return cells;
    });
    return renderTable({ columns, rows: flat, total_row_count: rows.length, truncated: false, column_formats: {} }, "");
  }

  function renderEvidence(invocations) {
    const box = el("div");
    invocations.forEach((invocation, index) => {
      const item = el("details", "evidence-item");
      item.open = true;
      item.appendChild(el("summary", null, `e${index} · ${invocation.tool} · ${invocation.status}`));
      if (invocation.error) item.appendChild(field("Error", invocation.error, "error"));
      item.appendChild(field("Arguments", JSON.stringify(invocation.arguments)));
      const evidence = invocation.evidence;
      if (evidence && evidence.kind === "run_sql") item.appendChild(renderSqlLedger(evidence.attempts));
      if (invocation.output) item.appendChild(renderOutput(invocation.output));
      if (evidence && evidence.kind === "search_business_docs") item.appendChild(renderDocSections(evidence.sections));
      if (evidence && evidence.kind === "check_execution") {
        item.appendChild(el("h5", null, `Matched log lines (${evidence.lines.length}${evidence.truncated ? ", truncated" : ""})`));
        item.appendChild(codeBlock(evidence.lines.join("\n"), null));
      }
      const read = (invocation.substrates_read || []).join(", ");
      if (read) item.appendChild(field("Substrates read", read));
      const manifests = (invocation.manifest_ids || []).join(", ");
      if (manifests) item.appendChild(field("Manifests", manifests));
      box.appendChild(item);
    });
    return box;
  }

  // The SQL attempt ledger: every round of the execute–check–repair
  // loop, with the lint challenges it drew. An attempt is blocked when
  // a challenge stopped it before execution, executed when it ran, and
  // an executed attempt that still carries a challenge is an override
  // — the licensed resend the Verifier warns on.
  const LINT_KINDS = [["lint", "Fan-out check"], ["enum_lint", "Enum check"], ["interval_lint", "Interval check"]];
  function attemptOutcome(attempt) {
    const challenged = LINT_KINDS.some(([key]) => attempt[key]);
    const executed = attempt.row_count !== null && attempt.row_count !== undefined;
    if (executed) return challenged ? "executed · override" : "executed";
    if (challenged && attempt.error) return "blocked by lint";
    return attempt.sql ? "failed" : "no SQL";
  }
  function renderSqlLedger(attempts) {
    const box = el("div", "ledger");
    box.appendChild(el("h5", null, `SQL attempts (${attempts.length})`));
    attempts.forEach((attempt, index) => {
      const outcome = attemptOutcome(attempt);
      const row = el("div", "attempt " + outcome.split(" ")[0]);
      let head = `#${index + 1} · ${outcome}`;
      if (attempt.row_count !== null && attempt.row_count !== undefined) head += ` · ${plural(attempt.row_count, "row")}`;
      row.appendChild(el("div", "attempt-head", head));
      if (attempt.sql) row.appendChild(codeBlock(attempt.sql, "sql"));
      else {
        const raw = el("details");
        raw.appendChild(el("summary", null, "raw reply (no SQL statement found)"));
        raw.appendChild(codeBlock(attempt.raw_response, null));
        row.appendChild(raw);
      }
      LINT_KINDS.forEach(([key, name]) => {
        if (!attempt[key]) return;
        const challenge = el("div", "challenge");
        challenge.appendChild(el("b", null, name + (outcome.startsWith("executed") ? " — overridden" : "")));
        challenge.appendChild(el("span", null, attempt[key]));
        row.appendChild(challenge);
      });
      // A blocking round's error is the challenge text itself; show
      // an error only when it says something the challenges do not.
      if (attempt.error && !LINT_KINDS.some(([key]) => attempt[key] && attempt.error.includes(attempt[key]))) {
        row.appendChild(el("div", "error", attempt.error));
      }
      box.appendChild(row);
    });
    return box;
  }

  const LANGUAGES = { py: "python", sql: "sql", js: "javascript", json: "json", yaml: "yaml", yml: "yaml" };
  function languageOf(path) {
    const extension = (path || "").split(".").pop().toLowerCase();
    return LANGUAGES[extension] || null;
  }

  function renderOutput(output) {
    const box = el("div", "output");
    switch (output.kind) {
      case "run_sql":
        box.appendChild(el("h5", null, "Result"));
        box.appendChild(renderTable(output.table, ""));
        break;
      case "query_univariate_stats":
        box.appendChild(rowsToTable(output.rows));
        break;
      case "lookup_data_dictionary":
        box.appendChild(rowsToTable(output.rows));
        [["Concepts", output.concepts], ["Metrics", output.metrics], ["Join paths", output.join_paths], ["Gotchas", output.gotchas]]
          .forEach(([label, items]) => {
            if (items && items.length) box.appendChild(field(label, items.map(item => item.name).join(", ")));
          });
        break;
      case "traverse_code_knowledge_graph":
        box.appendChild(renderCkg(output));
        break;
      case "read_source":
        box.appendChild(field("Source", `${output.file_path}:${output.start_line}-${output.end_line} @ ${output.commit_sha.slice(0, 7)}`));
        box.appendChild(field("Symbol", output.qualified_name));
        box.appendChild(codeBlock(output.text, languageOf(output.file_path)));
        break;
      case "app_primer":
        box.appendChild(renderMarkdown(output.primer));
        if (output.components.length) {
          box.appendChild(field("Components", output.components.map(c => `${c.id} — ${c.name}`).join("\n")));
        }
        break;
      case "search_business_docs": {
        const list = el("ul");
        output.hits.forEach(hit => {
          const li = el("li");
          li.appendChild(el("b", null, `${hit.title} › ${hit.heading}`));
          li.appendChild(el("span", "muted", ` (${hit.slug}, score ${hit.score})`));
          li.appendChild(el("div", null, hit.snippet));
          list.appendChild(li);
        });
        box.appendChild(list.children.length ? list : el("p", "hint", "No hits."));
        break;
      }
      case "check_execution":
        if (output.run_status) {
          box.appendChild(field("Ran", `${output.run_status.ran} · ${plural(output.run_status.count, "run")}`));
          box.appendChild(field("Detail", output.run_status.detail));
        }
        if (output.error_count !== null && output.error_count !== undefined) {
          box.appendChild(field("Errors", String(output.error_count)));
        }
        if (output.errors && output.errors.length) box.appendChild(rowsToTable(output.errors));
        break;
      case "answer_from_known_items":
        box.appendChild(output.matches.length
          ? rowsToTable(output.matches)
          : el("p", "hint", "No published unit matched."));
        break;
      case "app_capabilities":
        box.appendChild(el("p", null, output.capabilities));
        if (output.starter_prompts.length) box.appendChild(field("Starters", output.starter_prompts.join("\n")));
        break;
      default:
        box.appendChild(codeBlock(JSON.stringify(output, null, 2), "json"));
    }
    return box;
  }

  function nodeLine(node) {
    return `${node.kind} ${node.qualified_name} · ${node.file_path}:${node.start_line}-${node.end_line}`;
  }
  function renderCkg(output) {
    const box = el("div");
    const names = {};
    (output.nodes || []).forEach(node => { names[node.id] = node.qualified_name; });
    if (output.entry_node) {
      names[output.entry_node.id] = output.entry_node.qualified_name;
      box.appendChild(field("Entry", nodeLine(output.entry_node)));
    }
    if (output.entry_component) box.appendChild(field("Component", `${output.entry_component.id} — ${output.entry_component.name}`));
    if (output.nodes && output.nodes.length) {
      box.appendChild(el("h5", null, plural(output.nodes.length, "node")));
      const list = el("ul");
      output.nodes.forEach(node => {
        const li = el("li", null, nodeLine(node));
        if (node.signature) li.appendChild(codeBlock(node.signature, "python"));
        list.appendChild(li);
      });
      box.appendChild(list);
    }
    if (output.edges && output.edges.length) {
      box.appendChild(el("h5", null, plural(output.edges.length, "edge")));
      const list = el("ul");
      output.edges.forEach(edge => {
        const source = names[edge.source_id] || edge.source_id;
        const target = edge.target_table || names[edge.target_node_id] || edge.target_node_id;
        list.appendChild(el("li", null, `${source} —${edge.kind}→ ${target} (line ${edge.line})`));
      });
      box.appendChild(list);
    }
    if (output.conditionals && output.conditionals.length) {
      box.appendChild(el("h5", null, plural(output.conditionals.length, "conditional")));
      const list = el("ul");
      output.conditionals.forEach(conditional => {
        const li = el("li", null, `line ${conditional.line}: `);
        li.appendChild(el("code", null, conditional.condition_text));
        list.appendChild(li);
      });
      box.appendChild(list);
    }
    return box;
  }

  function renderDocSections(sections) {
    const box = el("div");
    box.appendChild(el("h5", null, `Matched sections (${sections.length})`));
    sections.forEach(section_ => {
      const item = el("details");
      item.appendChild(el("summary", null, `${section_.slug} › ${section_.heading}`));
      item.appendChild(el("div", "claimed-text", section_.text));
      box.appendChild(item);
    });
    return box;
  }

  // The status trail with per-step durations (start to finish of the
  // same node) and, on a protocol violation, the raw router text the
  // live trail line never shows.
  function renderTrail(events) {
    const list = el("ul", "trail inspector-trail");
    if (!events.length) { list.appendChild(el("li", "hint", "No trail recorded.")); return list; }
    const starts = {};
    events.forEach(event => {
      if (event.phase === "start") starts[event.node] = Date.parse(event.at);
      const li = el("li", event.phase);
      let text = `${event.node} — ${event.detail}`;
      if (event.phase === "finish" && starts[event.node] !== undefined) {
        text += ` (${((Date.parse(event.at) - starts[event.node]) / 1000).toFixed(1)}s)`;
      }
      if (event.phase === "finish" && /^(error|protocol violation)/.test(event.detail)) li.className = "error";
      li.textContent = text;
      if (event.raw_response) {
        li.appendChild(el("div", "label", "raw router response"));
        li.appendChild(el("pre", "raw", event.raw_response));
      }
      list.appendChild(li);
    });
    return list;
  }

  // ---- boot -------------------------------------------------------------
  async function boot() {
    try {
      const cfg = await api("GET", "/api/config");
      document.getElementById("app-name").textContent = cfg.app_name;
      document.title = cfg.app_name;
      document.getElementById("user-name").textContent = cfg.user || "";
      if (cfg.accent_color) document.documentElement.style.setProperty("--accent", cfg.accent_color);
      // Starter prompts live in the empty state, which every new
      // conversation (a fresh reload included) opens on; a click asks
      // directly rather than round-tripping through the form.
      (cfg.starter_prompts || []).forEach(text => {
        const button = el("button", "starter", text);
        button.type = "button";
        button.addEventListener("click", () => ask(text));
        startersBox.appendChild(button);
      });
      await loadWorkspaces();
      await selectWorkspace(state.workspaces[0].id);
    } catch (err) {
      note(err.message);
    }
  }

  document.getElementById("new-workspace").addEventListener("click", createWorkspace);
  document.getElementById("new-conversation").addEventListener("click", newConversation);
  composer.addEventListener("submit", ev => { ev.preventDefault(); ask(input.value); });
  boot();
})();
