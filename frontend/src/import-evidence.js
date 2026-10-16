import { sourceLink } from "./task-evidence.js";

const text = (value) => (typeof value === "string" ? value : "");
const list = (value) => (Array.isArray(value) ? value : []);
const esc = (value) =>
  text(value).replace(/[&<>"']/g, (character) =>
    ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[character],
  );

function sourceLinks(record, sources, showLabels = true) {
  const seen = new Set();
  return [
    {
      url: record?.source_url,
      label: record?.source_label,
      platform: record?.platform,
    },
    ...list(sources),
  ]
    .map((source) => {
      const url = text(source?.url);
      const link = sourceLink(
        url,
        (source?.platform || record?.platform) === "teaching_web"
          ? "打开教学网原页面"
          : "打开来源页面",
      );
      if (!link) return "";
      const key = new URL(url).href;
      if (seen.has(key)) return "";
      seen.add(key);
      const label = text(source?.label);
      return `${showLabels && label ? `${esc(label)} · ` : ""}${link}`;
    })
    .filter(Boolean)
    .join("<br>");
}

export function createEvidenceState() {
  const opened = new Set();
  return {
    reset: () => opened.clear(),
    has: (key) => opened.has(key),
    toggle(event) {
      const node = event.target, key = node?.dataset?.candidateEvidence;
      if (!key || node.isConnected === false) return false;
      if (node.open) opened.add(key); else opened.delete(key);
      return true;
    },
  };
}

export function candidateEvidenceMarkup(item, state) {
  const candidate = item?.candidate || {};
  const record = list(item?.batch?.request?.records).find(
    (record) => record.input_id === candidate.input_id,
  );
  const quotes = [
    ...new Set(
      list(candidate.evidence)
        .map((evidence) => text(evidence?.quote))
        .filter((quote) => quote.trim()),
    ),
  ];
  const original = text(record?.text);
  const key = text(item?.id);
  const attributes = (part) => key ? ` data-candidate-evidence="${esc(key + ':' + part)}"${state?.has(key + ':' + part) ? ' open' : ''}` : '';
  const links = sourceLinks(record, candidate.sources);
  const basis = text(candidate.deadline?.basis);
  const reasons = [
    ...new Set([basis, ...list(candidate.review_reasons).map(text)]),
  ].filter(Boolean);
  return `<details class="candidate-evidence"${attributes('evidence')}><summary>查看原文</summary><div class="task-evidence-entry">${links ? `<div class="task-evidence-links">${links}</div>` : ""}${quotes.length ? `<section class="task-evidence-quotes"><p class="status-note">识别摘录</p>${quotes.map((quote) => `<blockquote>${esc(quote)}</blockquote>`).join("")}</section>` : ""}${original.trim() ? `<details${attributes('original')}><summary>查看完整通知原文</summary><pre>${esc(original)}</pre></details>` : '<p class="status-note">原文暂不可用</p>'}${reasons.map((reason) => `<p class="status-note">${esc(reason)}</p>`).join("")}</div></details>`;
}

export function failureSourcesMarkup(batch) {
  const records = list(batch?.request?.records);
  const result = batch?.result || {};
  const failedStatuses = new Set(["failed", "incomplete", "partial_success"]);
  const ids = new Set(
    [
      ...list(result.errors).map((error) => text(error?.input_id)),
      ...list(result.record_results)
        .filter((row) => failedStatuses.has(row?.status))
        .map((row) => text(row.input_id)),
    ].filter(Boolean),
  );
  if (!ids.size && result.status === "failed") {
    for (const record of records) {
      if (text(record?.input_id)) ids.add(record.input_id);
    }
  }
  const candidates = [
    ...list(batch?.items).map((item) => item?.candidate),
    ...list(result.tasks),
  ].filter(Boolean);
  return [...ids]
    .map((id) => {
      const record = records.find((record) => record.input_id === id);
      const candidate = candidates.find((candidate) => candidate.input_id === id);
      const sources = candidates.flatMap((candidate) =>
        list(candidate.sources).filter((source) => source?.input_id === id),
      );
      const label =
        text(record?.source_label) ||
        text(sources[0]?.label) ||
        text(record?.text).split(/\r?\n/).find((line) => line.trim())?.trim().slice(0, 120) ||
        "通知";
      const course = text(record?.course_or_project) || text(candidate?.course_or_project);
      const links = sourceLinks(record, sources, false);
      return `<div class="task-evidence-entry"><p class="status-note">${course ? `${esc(course)} · ` : ""}${esc(label)}</p><p class="status-note">通知标识 <code>${esc(id)}</code>${links ? `<br>${links}` : ""}</p></div>`;
    })
    .join("");
}
