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

function sourceLinks(record, sources) {
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
      return link;
    })
    .filter(Boolean)
    .join("<br>");
}

export function failureSources(batch) {
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
      const links = sourceLinks(record, sources);
      return { input_id: id, label, course, links };
    });
}

export function failureSourcesMarkup(batch) {
  return failureSources(batch).map(({input_id, label, course, links}) =>
    `<div class="task-evidence-entry"><p class="status-note">${course ? `${esc(course)} · ` : ""}${esc(label)}</p><p class="status-note">通知标识 <code>${esc(input_id)}</code>${links ? `<br>${links}` : ""}</p></div>`).join("");
}
