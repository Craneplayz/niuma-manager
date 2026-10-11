import { createHash, randomUUID } from "node:crypto";
import { DateTime } from "luxon";
import type { CourseSourceRecord } from "./pku-source.js";
import { toTeachingRequests as exportEvidence } from "./pku-teaching-evidence.js";
import { isPkuAssignmentUrl } from "./pku-assignment.js";

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
  if (new Set(sources.map(source => source.input_id)).size !== sources.length) throw new Error("teaching_export_rejected:duplicate_input_id");
  const entries = [...sources].sort((a, b) => teachingPriority(a) - teachingPriority(b)).map((source) => {
    const output = exportEvidence([source], `capture:${digest(source.input_id)}`);
    if (output.skipped.length || output.requests.length !== 1) {
      throw new Error(`teaching_export_rejected:${output.skipped[0]?.reason ?? "empty_request"}`);
    }
    const entry = output.requests[0].records[0];
    const sourceMetadata = `课程：${source.course}\n栏目：${source.section}${source.course_term_label ? `\n课程列表学期：${source.course_term_label}` : ""}\n${entry.record.source_metadata_text}`.trim();
    if (sourceMetadata.length > 4096 || entry.record.text.length + sourceMetadata.length + entry.record.references.reduce((total, ref) => total + (ref.text?.length ?? 0), 0) > 100_000) throw new Error("teaching_export_rejected:aggregate_evidence_too_large");
    const detailUnread = source.references.some(ref => ref.kind === "detail" && ref.read_status !== "complete");
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
    return { record: { ...entry.record, source_metadata_text: sourceMetadata, body_status: detailUnread && entry.record.body_status === "complete" ? "partial" : entry.record.body_status, course_term: term }, source: entry.source };
  });
  // One bounded item per request: a slow record never spends the next record's budget.
  return entries.map((entry, index) => ({ contract_version: "0.4", request_id: `${requestId}:${index}`, records: [entry] }));
}

/** Trusted process output only: the public v0.4 model request stays unchanged. */
export function toTeachingCaptures(sources: CourseSourceRecord[], requestId?: string) {
  const ordered = [...sources].sort((a, b) => teachingPriority(a) - teachingPriority(b));
  return toTeachingRequests(ordered, requestId).map((request, index) => {
    const record = request.records[0].record;
    const source = ordered[index];
    const native = source.references.find(ref => ref.kind === 'detail' && isPkuAssignmentUrl(ref.url));
    const url = native ? new URL(native.url) : null;
    const bound = source.kind === 'assignment' && source.assignment && url
      && url.searchParams.get('course_id') === record.course_id
      && url.searchParams.get('content_id') === record.source_item_id;
    const state = source.assignment?.state;
    const basis = state === 'submitted' ? 'review_submission_history_title' : state === 'active' ? 'active_assignment_page' : 'unknown';
    return { contract_version: 'teaching-capture/0.1', request, assignment: bound
      ? { course_id: record.course_id, content_id: record.source_item_id, state, basis } : null };
  });
}
