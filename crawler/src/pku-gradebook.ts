import { load } from "cheerio";
import { DateTime } from "luxon";
import type { APIRequestContext } from "playwright";
import { sha256 } from "./core/utils.js";
import { DEFAULT_TIMEZONE } from "./core/time.js";
import { courseItemInputId, makeBodyEvidence, makeSourceReference, type CourseSourceRecord } from "./pku-source.js";

export type GradebookItem = { id: string; title: string; due: string | null; submitted: boolean; contentId: string | null };

/** Read only assignment identity, deadline and submission signal. Grade cells are private. */
export function gradebookItems(html: string, url: URL): GradebookItem[] {
  const $ = load(html), items: GradebookItem[] = [];
  for (const el of $(".sortable_item_row[role=row]").toArray()) {
    const row = $(el), cell = row.find(".cell.gradable").first();
    if (!/^(?:作业|assignment)$/i.test(cell.find(".itemCat").text().trim())) continue;
    const label = cell.find("span[id], a[id]").first();
    const id = label.attr("id"), title = label.text().trim();
    if (!id || !/^_\d+_\d+$/.test(id) || !title || title.length > 512) continue;
    const epoch = Number(row.attr("duedate"));
    const date = DateTime.fromMillis(Number.isSafeInteger(epoch) ? epoch : 0, { zone: DEFAULT_TIMEZONE });
    const due = Number.isSafeInteger(epoch) && epoch > 0 && date.isValid && date.year < 2200 ? date.toISO({ suppressMilliseconds: true }) : null;
    const targets = new Set<string>();
    for (const link of cell.find("a[href]").toArray()) {
      const href = $(link).attr("href")!;
      if (!URL.canParse(href, url)) continue;
      const target = new URL(href, url);
      const id = target.searchParams.get("content_id");
      if (target.origin === url.origin && target.pathname === "/webapps/assignment/uploadAssignment"
          && target.searchParams.get("course_id") === url.searchParams.get("course_id") && id && /^_\d+_\d+$/.test(id)) {
        targets.add(id);
      }
    }
    const submitted = row.hasClass("submitted_item_row") || row.children(".activity, .status").find("[title],[alt]").toArray()
      .some(el => /^(已提交|submitted)$/i.test(($(el).attr("title") || $(el).attr("alt") || "").trim()));
    items.push({id, title, due, contentId: targets.size === 1 ? [...targets][0] : null, submitted});
  }
  return items;
}

/** Resolve Blackboard's explicit content → grade column relation without requesting scores. */
export async function resolveGradebookLinks(request: Pick<APIRequestContext, "get">,
  sources: CourseSourceRecord[], items: GradebookItem[], url: URL, concurrency: 1 | 4 = 4) {
  const courseId = url.searchParams.get("course_id");
  if (!courseId || !/^_\d+_\d+$/.test(courseId)) return;
  const missing = new Set(items.filter(item => !item.contentId).map(item => item.id));
  if (!missing.size) return;
  const known = new Set(items.map(item => item.contentId));
  const ids = [...new Set(sources.filter(source => source.kind === "assignment"
    && new URL(source.source_url).searchParams.get("course_id") === courseId)
    .map(source => source.source_item_id).filter((id): id is string => !!id && /^_\d+_\d+$/.test(id) && !known.has(id)))];
  const columns = new Map<string, string[]>();
  for (let i = 0; i < ids.length; i += concurrency) {
    await Promise.all(ids.slice(i, i + concurrency).map(async id => {
      const endpoint = new URL(`/learn/api/public/v1/courses/${courseId}/contents/${id}`, url.origin);
      endpoint.searchParams.set("fields", "id,contentHandler");
      try {
        const response = await request.get(endpoint.href, {timeout: 5000, maxRedirects: 0});
        try {
          if (!response.ok()) return;
          const data = await response.json();
          const column = data?.contentHandler?.gradeColumnId;
          if (data?.id === id && data.contentHandler?.id === "resource/x-bb-assignment" && missing.has(column)) {
            columns.set(column, [...(columns.get(column) ?? []), id]);
          }
        } finally { await response.dispose(); }
      } catch { /* Keep the independently reviewable grade row when metadata is unavailable. */ }
    }));
  }
  for (const item of items) {
    const matches = columns.get(item.id);
    if (!item.contentId && matches?.length === 1) item.contentId = matches[0];
  }
}

export function addGradebookItems(sources: CourseSourceRecord[], items: GradebookItem[], url: URL, course: string, collectedAt: string) {
  const assignments = sources.filter(s => s.kind === "assignment");
  const result: CourseSourceRecord[] = [];
  for (const item of items) {
    const linked = item.contentId && assignments.find(s => s.source_item_id === item.contentId);
    if (linked) { linked.gradebook = item; continue; }
    const courseId = url.searchParams.get("course_id")!;
    if (item.contentId) {
      // The row itself can be the only route to an assignment outside the scanned sections.
      const detail = new URL("/webapps/assignment/uploadAssignment", url);
      detail.searchParams.set("course_id", courseId);
      detail.searchParams.set("content_id", item.contentId);
      const source: CourseSourceRecord = {
        input_id: courseItemInputId(url.origin, courseId, "content", item.contentId), source_item_id: item.contentId,
        source_url: detail.href, course, section: "成绩明细", kind: "assignment", title: item.title, body: "",
        gradebook: item, body_evidence: makeBodyEvidence(item.title, "", "summary"), text_quality: "summary",
        published_at: {value: null, precision: "unknown", basis: "unknown"}, publication_raw: null,
        collected_at: collectedAt, references: [makeSourceReference(item.title, detail.href, "detail")], warnings: [],
      };
      result.push(source);
      assignments.push(source);
      continue;
    }
    // Same-name rows with different IDs remain independently reviewable.
    const text = item.submitted ? "成绩明细显示已提交。" : "作业来自课程成绩明细，提交情况以作业详情为准。";
    result.push({input_id: "pku:gradebook:" + sha256(JSON.stringify([courseId, item.id])), source_item_id: item.id,
      source_url: url.href, course, section: "成绩明细", kind: "assignment", title: item.title, body: text,
      gradebook: item, body_evidence: makeBodyEvidence(item.title, text, "summary"), text_quality: "summary",
      published_at: {value: null, precision: "unknown", basis: "unknown"}, publication_raw: null,
      collected_at: collectedAt, references: [], warnings: []});
  }
  return result;
}
