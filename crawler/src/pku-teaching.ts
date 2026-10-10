import { randomUUID } from "node:crypto";
import { DateTime } from "luxon";
import type { CourseSourceRecord } from "./pku-source.js";
import { sourceSnapshotId } from "./pku-recognition.js";
import { sha256 } from "./core/utils.js";
import { DEFAULT_TIMEZONE } from "./core/time.js";

// Shape owned by contracts/schemas/teaching-request-v0.4.schema.json (#124).
export interface TeachingEntry {
  record: {
    input_id: string; source_item_id: string; course_id: string; snapshot_id: string;
    platform: "teaching_web"; text: string; body_status: CourseSourceRecord["text_quality"];
    source_metadata_text: string;
    references: { ref_id: string; kind: "attachment" | "content_image" | "linked_detail";
      label: string; read_state: "complete" | "partial" | "unread" | "failed" | "unsupported"; text: string | null }[];
    published_at: { value: string; basis: "source" } | null;
    collected_at: string; timezone: string; course_term: null;
  };
  source: { label: string | null; url: string | null; course_or_project: string | null };
}
export interface TeachingRequest { contract_version: "0.4"; request_id: string; records: TeachingEntry[]; }
export interface TeachingExport { requests: TeachingRequest[]; skipped: { input_id: string; reason: string }[]; }
function hasControl(value: string): boolean {
  return [...value].some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127);
}
function identifier(value: string | null | undefined): value is string {
  return typeof value === "string" && Boolean(value.trim()) && value.length <= 256 && !hasControl(value);
}
function check(ok: unknown, reason: string): asserts ok { if (!ok) throw new Error(reason); }
function time(value: string): boolean { return /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && DateTime.fromISO(value).isValid; }
function label(value: string): string | null {
  check(value.length <= 512 && !hasControl(value), "source_label_invalid");
  return value.trim() ? value : null;
}
function navigationUrl(original: URL): string | null {
  // #124's public display URL cannot carry item fragments, private parameters or non-public hosts.
  if (original.origin !== "https://course.pku.edu.cn") return null;
  const url = new URL(original.href); url.hash = "";
  const allowed = new Set(["id", "page", "view", "mode", "method", "context", "courseid", "handle", "contentid", "announcementid", "p", "lang"]);
  const used = new Set<string>();
  for (const [key, value] of [...url.searchParams]) {
    const normalized = key.toLowerCase().replace(/[_-]/g, "");
    if (!allowed.has(normalized) || used.has(normalized) || value.length > 256 || /[\p{Cc}\p{Cf}\p{Cs}]/u.test(value)) url.searchParams.delete(key);
    else used.add(normalized);
  }
  return url.href.length <= 2048 && !/%(?:0[0-9a-f]|1[0-9a-f]|7f)/i.test(url.href) ? url.href : null;
}
function entry(source: CourseSourceRecord): TeachingEntry {
  check(identifier(source.input_id) && identifier(source.source_item_id), "stable_source_identity_missing");
  const url = new URL(source.source_url);
  check(["http:", "https:"].includes(url.protocol) && !url.username && !url.password && source.source_url.length <= 2048, "source_url_invalid");
  const courseIds = url.searchParams.getAll("course_id");
  check(courseIds.length === 1 && identifier(courseIds[0]), "stable_course_identity_missing");
  const courseId = courseIds[0];
  check(!source.provenance || source.provenance.course_id === courseId, "course_identity_mismatch");
  const body = source.body_evidence;
  check(body?.version === "teaching-body-v1", "separated_body_evidence_missing");
  check(body.text.length <= 100_000 && (body.status === "missing" ? !body.text : body.text.trim()), "body_evidence_invalid");
  const included = new Set(body.included_reference_urls);
  check(included.size === body.included_reference_urls.length && [...included].every((value) => source.references.some((ref) => ref.url === value && ref.kind === "detail" && ref.content_read && ref.read_status === "complete")), "included_detail_invalid");
  const references = source.references.filter((ref) => !included.has(ref.url)).map((ref, index) => {
    check(ref.label.length <= 512 && !hasControl(ref.label), "reference_label_invalid");
    const read = ref.read_status === "complete" || ref.read_status === "partial";
    check(!read || Boolean(ref.text?.trim()), "reference_text_missing");
    check(ref.content_read === (ref.read_status === "complete"), "reference_read_state_invalid");
    return { ref_id: `pku:ref:${sha256(JSON.stringify([source.input_id, ref.kind, ref.url, index]))}`,
      kind: ref.kind === "attachment" ? "attachment" as const : ref.kind === "image" ? "content_image" as const : "linked_detail" as const,
      label: ref.label.trim() ? ref.label : ref.kind, read_state: ref.read_status, text: read ? ref.text! : null };
  });
  check(references.length <= 50, "reference_count_exceeded");
  const metadata = source.provenance?.source_metadata_text ?? "";
  check(metadata.length <= 4096, "source_metadata_too_large");
  check(body.text.length + metadata.length + references.reduce((total, ref) => total + (ref.text?.length ?? 0), 0) <= 100_000, "aggregate_evidence_too_large");
  check(time(source.collected_at), "collection_time_invalid");
  const publication = source.published_at.value;
  check(!publication || source.published_at.basis === "source" && (time(publication) || /^\d{4}-\d{2}-\d{2}$/.test(publication) && DateTime.fromISO(publication).isValid), "publication_time_invalid");
  return { record: { input_id: source.input_id, source_item_id: source.source_item_id, course_id: courseId,
      snapshot_id: sourceSnapshotId(source), platform: "teaching_web", text: body.text, body_status: body.status,
      source_metadata_text: metadata, references, published_at: publication ? { value: publication, basis: "source" } : null,
      collected_at: source.collected_at, timezone: DEFAULT_TIMEZONE,
      // A course label cannot establish semester dates or current-assignment applicability.
      course_term: null },
    source: { label: label(source.title), url: navigationUrl(url), course_or_project: label(source.course) } };
}
/** Export only; account trust and the v0.4 entry digest belong to the controlled server consumer. */
export function toTeachingRequests(sources: CourseSourceRecord[], requestId = `pku:${randomUUID()}`): TeachingExport {
  check(identifier(requestId) && requestId.length <= 240, "request_id_invalid");
  const requests: TeachingRequest[] = []; const skipped: TeachingExport["skipped"] = [];
  const seen = new Set<string>(); const duplicate = new Set<string>();
  for (const source of sources) { if (seen.has(source.input_id)) duplicate.add(source.input_id); seen.add(source.input_id); }
  let batch: TeachingRequest = { contract_version: "0.4", request_id: `${requestId}:1`, records: [] };
  for (const source of sources) {
    try {
      check(!duplicate.has(source.input_id), "duplicate_input_id");
      const value = entry(source);
      const next = () => ({ ...batch, records: [...batch.records, value] });
      if (batch.records.length === 20 || Buffer.byteLength(JSON.stringify(next()), "utf8") > 2_000_000) {
        if (batch.records.length) requests.push(batch);
        batch = { contract_version: "0.4", request_id: `${requestId}:${requests.length + 1}`, records: [] };
      }
      check(Buffer.byteLength(JSON.stringify(next()), "utf8") <= 2_000_000, "entry_too_large");
      batch.records.push(value);
    } catch (error) { skipped.push({ input_id: source.input_id, reason: error instanceof Error ? error.message : "invalid_source_record" }); }
  }
  if (batch.records.length) requests.push(batch);
  return { requests, skipped };
}


/** Acceptance adapter: preserve #127 separated evidence in the controlled #139 envelope. */
export function toTeachingCaptures(sources: CourseSourceRecord[], requestId = `pku:${randomUUID()}`) {
  return sources.map((source, index) => {
    const output = toTeachingRequests([source], `${requestId}:${index}`);
    if (output.skipped.length || output.requests.length !== 1) throw new Error("teaching capture export rejected");
    const request = output.requests[0];
    const record = request.records[0].record;
    const native = source.references.find(ref => ref.kind === "detail" && ref.url.includes("/webapps/assignment/"));
    const url = native ? new URL(native.url) : null;
    const bound = source.kind === "assignment" && source.assignment && url
      && url.searchParams.get("course_id") === record.course_id
      && url.searchParams.get("content_id") === record.source_item_id;
    const state = source.assignment?.state;
    const basis = state === "submitted" ? "review_submission_history_title" : state === "active" ? "active_assignment_page" : "unknown";
    return { contract_version: "teaching-capture/0.1", request, assignment: bound
      ? { course_id: record.course_id, content_id: record.source_item_id, state, basis } : null };
  });
}
