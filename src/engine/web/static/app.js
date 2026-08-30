/* The Block 1 transcript: one question in, a live status trail, one
   verified outcome out. Vanilla JS; no framework, no build, no
   browser storage (CLAUDE.md). Rendering mirrors the engine's rules —
   money cells format exactly as harness/render.py does. */
(function () {
  "use strict";

  const transcript = document.getElementById("transcript");
  const emptyState = document.getElementById("empty-state");
  const composer = document.getElementById("composer");
  const input = document.getElementById("question");
  const send = document.getElementById("send");
  let conversationId = null;

  // ---- config / branding (all from the pack) -------------------------
  fetch("/api/config").then(r => r.json()).then(cfg => {
    document.getElementById("app-name").textContent = cfg.app_name;
    document.title = cfg.app_name;
    document.getElementById("user-name").textContent = cfg.user || "";
    if (cfg.accent_color) document.documentElement.style.setProperty("--accent", cfg.accent_color);
    const starters = document.getElementById("starters");
    (cfg.starter_prompts || []).forEach(text => {
      const b = document.createElement("button");
      b.type = "button"; b.className = "starter"; b.textContent = text;
      b.addEventListener("click", () => { input.value = text; composer.requestSubmit(); });
      starters.appendChild(b);
    });
  });

  // ---- rendering ------------------------------------------------------
  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }

  // Same rule as engine/harness/render.py format_money: sign, symbol,
  // thousands separators, two decimals.
  function formatMoney(value, symbol) {
    const sign = value < 0 ? "-" : "";
    const fixed = Math.abs(value).toFixed(2);
    const [whole, frac] = fixed.split(".");
    return sign + symbol + whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",") + "." + frac;
  }

  function formatCell(value, hint) {
    if (value === null || value === undefined) return "";
    if (typeof value === "number" && hint && hint.kind === "money") return formatMoney(value, hint.symbol);
    if (typeof value === "boolean") return value ? "true" : "false";
    return String(value);
  }

  function renderMarkdown(text) {
    const box = el("div", "markdown");
    box.innerHTML = marked.parse(text);
    box.querySelectorAll("pre code").forEach(block => {
      if (window.hljs) hljs.highlightElement(block);
    });
    return box;
  }

  function renderTable(table, caption) {
    const wrap = el("div", "table");
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
    if (outcome.kind === "refuse") {
      return renderCard("refuse", "This can't be answered", [
        ["Why", outcome.reason], ["What would work", outcome.what_would_work]]);
    }
    if (outcome.kind === "clarify") {
      return renderCard("clarify", "One thing to clarify first", [["Question", outcome.question]]);
    }
    return renderCard("escalate", "This needs a person", [["Why", outcome.reason]]);
  }

  function chipFor(payload) {
    const result = payload.result;
    const outcome = result.outcome;
    const events = result.events || [];
    const seconds = events.length > 1
      ? Math.max(1, Math.round((Date.parse(events[events.length - 1].at) - Date.parse(events[0].at)) / 1000))
      : 0;
    const tools = (result.tools_used || []).length;
    let cls, label;
    if (outcome.kind === "answer" && outcome.verification === "verified") { cls = "verified"; label = "✓ Verified"; }
    else if (outcome.kind === "answer") { cls = "unverified"; label = "⚠ Unverified"; }
    else if (outcome.kind === "refuse") { cls = "refuse"; label = "⊘ Refused"; }
    else if (outcome.kind === "clarify") { cls = "clarify"; label = "? Clarify"; }
    else { cls = "escalate"; label = "↑ Escalated"; }
    const parts = [label, `${tools} tool${tools === 1 ? "" : "s"}`];
    if (seconds) parts.push(`${seconds}s`);
    const chip = el("button", "chip " + cls, parts.join(" · "));
    chip.type = "button";
    chip.setAttribute("aria-expanded", "false");
    chip.title = "Show the progress trail";
    chip.addEventListener("click", () => {
      chip.setAttribute("aria-expanded", chip.getAttribute("aria-expanded") === "true" ? "false" : "true");
    });
    return chip;
  }

  // ---- SSE over fetch (POST; EventSource is GET-only) -----------------
  async function streamAsk(question, onFrame) {
    const response = await fetch("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, conversation_id: conversationId }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.message || `HTTP ${response.status}`);
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
        if (data) onFrame(event, JSON.parse(data));
      }
    }
  }

  // ---- a turn ---------------------------------------------------------
  composer.addEventListener("submit", async ev => {
    ev.preventDefault();
    const question = input.value.trim();
    if (!question) return;
    input.value = "";
    send.disabled = true;
    emptyState.hidden = true;

    const turn = el("div", "turn");
    turn.appendChild(el("div", "question", question));
    const trail = el("ul", "trail");
    turn.appendChild(trail);
    transcript.appendChild(turn);
    turn.scrollIntoView({ block: "end" });

    try {
      await streamAsk(question, (event, payload) => {
        if (event === "status") {
          const li = el("li", payload.phase, payload.detail);
          if (payload.phase === "finish" && /^(error|protocol violation)/.test(payload.detail)) li.className = "error";
          trail.appendChild(li);
        } else if (event === "result") {
          conversationId = payload.result.conversation_id;
          const chip = chipFor(payload);
          turn.insertBefore(chip, trail);        // trail collapses behind the chip
          turn.appendChild(renderOutcome(payload.result.outcome));
        } else if (event === "error") {
          throw new Error(payload.message);
        }
        turn.scrollIntoView({ block: "end" });
      });
    } catch (err) {
      const chip = el("span", "chip error", "✗ Error");
      turn.insertBefore(chip, trail);
      turn.appendChild(renderCard("error", "The engine hit an error", [["Detail", String(err.message || err)]]));
    } finally {
      send.disabled = false;
      input.focus();
    }
  });
})();
