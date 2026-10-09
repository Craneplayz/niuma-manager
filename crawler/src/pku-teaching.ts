import { createHash, randomUUID } from "node:crypto";
import { DateTime } from "luxon";
import type { CourseSourceRecord } from "./pku-source.js";

const digest = (value: unknown) => createHash("sha256").update(JSON.stringify(value)).digest("hex");
export function teachingPriority(source: CourseSourceRecord): number {
  if (/作业|\bassignments?\b|\bhomework\b/i.test(source.section)) return 0;
  if (source.kind === "announcement" || /通知|公告|\bannouncements?\b/i.test(source.section)) return 1;
  return 2;
}

function assignmentScope(source: CourseSourceRecord): boolean {
  // An email/upload-elsewhere assignment has no native submission page. Its
  // authenticated assignment-section placement is still an assignment context.
  // A known native state always takes precedence over the section name.
  if (source.assignment) return source.assignment.state === "active";
  return /^(?:课程作业|作业|assignments?|homework)$/i.test(source.section.trim());
}

/** A native v0.4 projection; successful detail text is already part of teacher_body. */
export function toTeachingRequests(sources: CourseSourceRecord[], requestId: string = randomUUID()) {
  const entries = [...sources].sort((a, b) => teachingPriority(a) - teachingPriority(b)).map((source) => {
    const publicUrl = new URL(source.source_url);
    publicUrl.hash = "";
    const body = source.teacher_body ?? source.body;
    const text = [source.title, body].filter(Boolean).join("\n\n");
    const detailUnread = source.references.some((ref) => ref.kind === "detail" && ref.read_status !== "complete");
    const bodyStatus = !body ? (text ? "summary" : "missing") : detailUnread ? "partial" : "complete";
    const sourceMetadata = `课程：${source.course}\n栏目：${source.section}${source.course_term_label ? `\n课程列表学期：${source.course_term_label}` : ""}`;
    const references = source.references.filter((ref) => !(ref.kind === "detail" && ref.read_status === "complete")).map((ref, index) => ({
      ref_id: `ref:${index}`, kind: ref.kind === "image" ? "content_image" : ref.kind === "attachment" ? "attachment" : "linked_detail",
      label: [...(ref.label || "未命名引用")].map(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127 ? " " : char).join("").slice(0, 512),
      read_state: ref.read_status === "complete" && !ref.text ? "unread" : ref.read_status,
      text: ["complete", "partial"].includes(ref.read_status) && ref.text ? ref.text : null,
    }));
    // A detail reference may be partial without captured text; it remains unread.
    for (const ref of references) if (ref.read_state === "partial" && !ref.text) ref.read_state = "unread";
    let term = null;
    const match = source.course_term_label?.match(/^(\d{2}|\d{4})-(\d{2}|\d{4})学年第([12])学期$/);
    if (match) {
      const year = Number(match[1]) + (match[1].length === 2 ? 2000 : 0);
      const next = Number(match[2]) + (match[2].length === 2 ? 2000 : 0);
      if (next === year + 1) {
        const start = match[3] === "1" ? `${year}-09-01` : `${next}-03-01`;
        const end = match[3] === "1" ? DateTime.fromISO(`${next}-02-01`).endOf("month").toISODate()! : `${next}-08-31`;
        const current = source.collected_at.slice(0, 10) >= start && source.collected_at.slice(0, 10) <= end;
        term = { term_id: source.course_term_label!, starts_on: start, ends_on: end,
          applies_to: assignmentScope(source) && current ? "current_assignment" : "course_material",
          evidence: [{ part_id: "source_metadata", quote: `课程列表学期：${source.course_term_label}` },
            { part_id: "source_metadata", quote: `栏目：${source.section}` }] };
      }
    }
    const record = {
      input_id: source.input_id, source_item_id: source.source_item_id || source.input_id,
      course_id: new URL(source.source_url).searchParams.get("course_id") || `course:${digest(source.course)}`,
      snapshot_id: `capture:${digest(source)}`, platform: "teaching_web", text, body_status: bodyStatus,
      source_metadata_text: sourceMetadata, references,
      published_at: source.published_at.basis === "source" && source.published_at.value ? { value: source.published_at.value, basis: "source" } : null,
      collected_at: source.collected_at, timezone: "Asia/Shanghai", course_term: term,
    };
    return { record, source: { label: `${source.course} · ${source.section}`, url: publicUrl.href, course_or_project: source.course || null } };
  });
  // One bounded item per request: a slow record never spends the next record's budget.
  return entries.map((entry, index) => ({ contract_version: "0.4", request_id: `${requestId}:${index}`, records: [entry] }));
}
