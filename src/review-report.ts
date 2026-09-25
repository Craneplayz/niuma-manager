import { mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { normalizedEventSchema, type NormalizedEvent } from "./core/schemas.js";
import { readJsonl } from "./storage/jsonl-store.js";

export interface ReviewReportOptions {
  normalizedRoot?: string;
  outputRoot?: string;
}

export async function writeReviewReport(runId: string, options: ReviewReportOptions = {}): Promise<{ outputPath: string; eventCount: number }> {
  if (!/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(runId)) throw new Error(`invalid run ID: ${runId}`);
  const normalizedPath = join(options.normalizedRoot ?? "data/normalized", `${runId}.jsonl`);
  const outputPath = join(options.outputRoot ?? "data/review", `${runId}.html`);
  const values = await readJsonl<unknown>(normalizedPath);
  const events = values.map((value, index) => {
    const result = normalizedEventSchema.safeParse(value);
    if (!result.success) throw new Error(`invalid normalized event at line ${index + 1}: ${result.error.issues[0]?.message ?? "schema mismatch"}`);
    return result.data;
  });
  await mkdir(options.outputRoot ?? "data/review", { recursive: true });
  await writeFile(outputPath, renderReviewReport(runId, events), "utf8");
  return { outputPath, eventCount: events.length };
}

function renderReviewReport(runId: string, events: NormalizedEvent[]): string {
  const payload = JSON.stringify({ runId, generatedAt: new Date().toISOString(), events })
    .replace(/</g, "\\u003c")
    .replace(/\u2028/g, "\\u2028")
    .replace(/\u2029/g, "\\u2029");

  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Scrape review · ${escapeHtml(runId)}</title>
  <style>
    :root {
      color-scheme: light;
      --paper: #f3f5f0;
      --paper-deep: #e6ebe5;
      --ink: #17211d;
      --muted: #68736e;
      --line: #cfd8d1;
      --blue: #125b78;
      --blue-soft: #d9edf2;
      --orange: #e66b4e;
      --orange-soft: #fbe0d5;
      --white: #ffffff;
      --shadow: 0 18px 40px rgba(23, 33, 29, 0.08);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--paper);
      color: var(--ink);
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.45;
    }
    body::before {
      content: "";
      display: block;
      height: 8px;
      background: repeating-linear-gradient(135deg, var(--orange) 0 16px, #f4a27d 16px 24px, var(--blue) 24px 40px);
    }
    main { max-width: 1180px; margin: 0 auto; padding: 50px 28px 80px; }
    .eyebrow { margin: 0 0 12px; color: var(--blue); font-size: 0.72rem; font-weight: 800; letter-spacing: 0.16em; text-transform: uppercase; }
    h1, h2, h3 { margin: 0; font-family: Georgia, "Times New Roman", serif; font-weight: 500; letter-spacing: -0.035em; }
    h1 { max-width: 720px; font-size: clamp(2.7rem, 7vw, 5.6rem); line-height: 0.94; }
    .intro { display: flex; align-items: end; justify-content: space-between; gap: 28px; margin-bottom: 34px; }
    .intro p { max-width: 380px; margin: 0 0 4px; color: var(--muted); font-size: 0.98rem; }
    .run-stamp { min-width: 180px; padding: 14px 16px; border: 1px solid var(--line); border-left: 4px solid var(--orange); background: var(--white); box-shadow: 8px 8px 0 var(--paper-deep); }
    .run-stamp span { display: block; color: var(--muted); font-size: 0.68rem; font-weight: 800; letter-spacing: 0.12em; text-transform: uppercase; }
    .run-stamp code { display: block; margin-top: 5px; overflow-wrap: anywhere; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.78rem; }
    .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 26px; }
    .stat { padding: 18px 18px 16px; border-top: 3px solid var(--blue); background: var(--white); }
    .stat:nth-child(2) { border-top-color: var(--orange); }
    .stat:nth-child(3) { border-top-color: #6c8c59; }
    .stat:nth-child(4) { border-top-color: #8a6fb6; }
    .stat strong { display: block; font-size: 1.9rem; font-weight: 750; letter-spacing: -0.05em; }
    .stat span { color: var(--muted); font-size: 0.75rem; font-weight: 750; letter-spacing: 0.08em; text-transform: uppercase; }
    .controls { position: sticky; top: 14px; z-index: 2; display: flex; flex-wrap: wrap; align-items: center; gap: 10px; margin-bottom: 28px; padding: 12px; border: 1px solid var(--line); background: rgba(243, 245, 240, 0.94); box-shadow: var(--shadow); backdrop-filter: blur(10px); }
    .controls label { color: var(--muted); font-size: 0.75rem; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; }
    select, input[type="search"] { min-height: 40px; border: 1px solid var(--line); border-radius: 0; background: var(--white); color: var(--ink); font: inherit; }
    select { min-width: 190px; padding: 0 34px 0 10px; }
    input[type="search"] { flex: 1 1 240px; padding: 0 12px; }
    select:focus, input:focus { outline: 3px solid rgba(18, 91, 120, 0.22); outline-offset: 1px; border-color: var(--blue); }
    .check { display: inline-flex; align-items: center; gap: 8px; min-height: 40px; padding: 0 8px; color: var(--ink); font-size: 0.86rem; }
    .check input { accent-color: var(--orange); }
    .results-line { display: flex; justify-content: space-between; gap: 16px; margin: 0 0 12px; color: var(--muted); font-size: 0.8rem; }
    .results-line strong { color: var(--ink); }
    #event-list { display: grid; gap: 14px; }
    .event-card { display: grid; grid-template-columns: minmax(0, 1fr) 220px; gap: 28px; padding: 24px; border: 1px solid var(--line); background: var(--white); box-shadow: 10px 10px 0 var(--paper-deep); }
    .card-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 12px; }
    .badge { display: inline-flex; align-items: center; min-height: 24px; padding: 3px 8px; background: var(--blue-soft); color: var(--blue); font-size: 0.68rem; font-weight: 850; letter-spacing: 0.08em; text-transform: uppercase; }
    .badge.orange { background: var(--orange-soft); color: #9b422c; }
    .event-card h2 { font-size: clamp(1.45rem, 3vw, 2.15rem); line-height: 1.08; }
    .description { margin: 14px 0 0; color: #46534d; white-space: pre-wrap; }
    .facts { display: grid; gap: 15px; align-content: start; padding-left: 20px; border-left: 1px solid var(--line); }
    .fact dt { color: var(--muted); font-size: 0.67rem; font-weight: 850; letter-spacing: 0.12em; text-transform: uppercase; }
    .fact dd { margin: 3px 0 0; overflow-wrap: anywhere; font-size: 0.9rem; }
    .fact dd a { color: var(--blue); }
    details { grid-column: 1 / -1; border-top: 1px solid var(--line); padding-top: 14px; }
    summary { cursor: pointer; color: var(--blue); font-size: 0.76rem; font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; }
    .provenance { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 13px 24px; margin-top: 15px; font-size: 0.82rem; }
    .provenance p { margin: 0; overflow-wrap: anywhere; }
    .provenance strong { display: block; margin-bottom: 3px; color: var(--muted); font-size: 0.65rem; letter-spacing: 0.1em; text-transform: uppercase; }
    .warning { color: #9b422c; }
    .empty { padding: 46px 24px; border: 1px dashed var(--line); color: var(--muted); text-align: center; }
    footer { margin-top: 42px; color: var(--muted); font-size: 0.75rem; }
    @media (max-width: 760px) {
      main { padding: 34px 16px 56px; }
      .intro { display: block; }
      .intro p { margin-top: 22px; }
      .run-stamp { margin-top: 22px; }
      .stats { grid-template-columns: repeat(2, 1fr); }
      .controls { position: static; }
      .event-card { display: block; padding: 18px; }
      .facts { margin-top: 22px; padding: 18px 0 0; border-top: 1px solid var(--line); border-left: 0; }
      .provenance { grid-template-columns: 1fr; }
    }
    @media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
  </style>
</head>
<body>
  <main>
    <section class="intro" aria-labelledby="page-title">
      <div>
        <p class="eyebrow">Public event scrape · inspection copy</p>
        <h1 id="page-title">A closer look at the catch.</h1>
        <p style="margin-top: 22px">A quiet review surface for checking what the crawler found before anything is considered application data.</p>
      </div>
      <div class="run-stamp"><span>Run ID</span><code id="run-id"></code></div>
    </section>
    <section class="stats" aria-label="Run summary">
      <div class="stat"><strong id="stat-events">0</strong><span>events in file</span></div>
      <div class="stat"><strong id="stat-sources">0</strong><span>source sites</span></div>
      <div class="stat"><strong id="stat-unresolved">0</strong><span>unresolved times</span></div>
      <div class="stat"><strong id="stat-partial">0</strong><span>partial parses</span></div>
    </section>
    <section class="controls" aria-label="Review filters">
      <label for="source-filter">Source</label>
      <select id="source-filter"><option value="">All sources</option></select>
      <input id="search" type="search" placeholder="Search title, speaker, location…" aria-label="Search events">
      <label class="check"><input id="unresolved-only" type="checkbox"> only unresolved</label>
    </section>
    <p class="results-line"><span id="result-count"></span><span>Generated <time id="generated-at"></time></span></p>
    <section id="event-list" aria-live="polite"></section>
    <footer>This is a generated inspection artifact. It is not wired into the real app frontend.</footer>
  </main>
  <script id="review-data" type="application/json">${payload}</script>
  <script>
    (() => {
      const data = JSON.parse(document.getElementById("review-data").textContent || "{}");
      const events = Array.isArray(data.events) ? data.events : [];
      const $ = (id) => document.getElementById(id);
      const formatDate = (value) => {
        if (!value) return "Not resolved";
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
      };
      const text = (value) => value == null || value === "" ? "Not recorded" : String(value);
      const append = (parent, tag, value, className) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        node.textContent = text(value);
        parent.appendChild(node);
        return node;
      };
      const fact = (parent, label, value, link) => {
        const wrapper = document.createElement("div");
        wrapper.className = "fact";
        const dt = document.createElement("dt"); dt.textContent = label;
        const dd = document.createElement("dd");
        if (link && value) { const a = document.createElement("a"); a.href = value; a.target = "_blank"; a.rel = "noreferrer"; a.textContent = value; dd.appendChild(a); }
        else dd.textContent = text(value);
        wrapper.append(dt, dd); parent.appendChild(wrapper);
      };
      const card = (event) => {
        const article = document.createElement("article"); article.className = "event-card";
        const main = document.createElement("div");
        const head = document.createElement("div"); head.className = "card-head";
        append(head, "span", event.event_type || "unknown", "badge");
        append(head, "span", event.source.source_id, "badge orange");
        main.appendChild(head);
        append(main, "h2", event.title);
        if (event.description) append(main, "p", event.description, "description");
        const facts = document.createElement("dl"); facts.className = "facts";
        fact(facts, "When", event.start_time ? formatDate(event.start_time) + (event.end_time ? " → " + formatDate(event.end_time) : "") : "Not resolved");
        fact(facts, "Where", event.location);
        fact(facts, "People", [...(event.speakers || []), ...(event.organizers || [])].join(" · "));
        fact(facts, "Affiliation", event.speaker_affiliation);
        article.append(main, facts);
        const details = document.createElement("details");
        const summary = document.createElement("summary"); summary.textContent = "Show provenance and raw fields";
        const provenance = document.createElement("div"); provenance.className = "provenance";
        const addProvenance = (label, value, link) => {
          const p = document.createElement("p");
          const strong = document.createElement("strong"); strong.textContent = label; p.appendChild(strong);
          if (link && value) { const a = document.createElement("a"); a.href = value; a.target = "_blank"; a.rel = "noreferrer"; a.textContent = value; p.appendChild(a); }
          else p.appendChild(document.createTextNode(text(value)));
          provenance.appendChild(p);
        };
        addProvenance("Source URL", event.source.source_url, true);
        addProvenance("Source item", event.source.source_item_id);
        addProvenance("Parse status", event.parse.status + " · " + event.parse.time_resolution);
        addProvenance("Raw time", event.raw.time);
        addProvenance("Raw location", event.raw.location);
        addProvenance("Raw speaker", event.raw.speaker);
        addProvenance("Raw organizer", event.raw.organizer);
        if (event.parse.warnings.length) addProvenance("Warnings", event.parse.warnings.join(" · "));
        details.append(summary, provenance); article.appendChild(details);
        return article;
      };
      const searchable = (event) => [event.title, event.description, event.location, ...(event.speakers || []), ...(event.organizers || []), event.source.source_id].filter(Boolean).join(" ").toLocaleLowerCase();
      const render = () => {
        const source = $("source-filter").value;
        const query = $("search").value.trim().toLocaleLowerCase();
        const unresolvedOnly = $("unresolved-only").checked;
        const filtered = events.filter((event) => (!source || event.source.source_id === source) && (!query || searchable(event).includes(query)) && (!unresolvedOnly || event.parse.time_resolution === "unresolved"));
        const list = $("event-list"); list.replaceChildren();
        if (!filtered.length) { const empty = document.createElement("p"); empty.className = "empty"; empty.textContent = "No events match these filters."; list.appendChild(empty); }
        else filtered.forEach((event) => list.appendChild(card(event)));
        $("result-count").innerHTML = "Showing <strong>" + filtered.length + "</strong> of <strong>" + events.length + "</strong> events";
      };
      const sources = [...new Set(events.map((event) => event.source.source_id))].sort();
      sources.forEach((source) => { const option = document.createElement("option"); option.value = source; option.textContent = source; $("source-filter").appendChild(option); });
      $("run-id").textContent = data.runId || "unknown";
      $("generated-at").textContent = formatDate(data.generatedAt);
      $("stat-events").textContent = events.length;
      $("stat-sources").textContent = sources.length;
      $("stat-unresolved").textContent = events.filter((event) => event.parse.time_resolution === "unresolved").length;
      $("stat-partial").textContent = events.filter((event) => event.parse.status !== "ok").length;
      $("source-filter").addEventListener("change", render);
      $("search").addEventListener("input", render);
      $("unresolved-only").addEventListener("change", render);
      render();
    })();
  </script>
</body>
</html>
`;
}

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character] ?? character);
}
