import { createHash, randomUUID } from "node:crypto";
import { sourceTextFits, type CourseSourceRecord, type PublicationTime } from "./pku-source.js";
import { DEFAULT_TIMEZONE } from "./core/time.js";

// Both the business API and recognition HTTP bridge cap request bodies at this size.
const MAX_REQUEST_BYTES = 2_000_000;
const MAX_RECORDS = 100;

export interface RecognitionRecord {
  input_id: string;
  text: string;
  text_quality: CourseSourceRecord["text_quality"];
  platform: "teaching_web";
  published_at: PublicationTime;
  collected_at: string;
  timezone: string;
  timezone_basis: "caller_default";
  source_url: string;
  source_label: string | null;
  source_item_id: string | null;
  course_or_project: string | null;
  context: [];
  upstream_hints: {
    source_kind: CourseSourceRecord["kind"];
    section: string;
    references: CourseSourceRecord["references"];
    warnings: string[];
    provenance?: CourseSourceRecord["provenance"];
    body_evidence?: CourseSourceRecord["body_evidence"];
    snapshot_id?: string;
    assignment?: CourseSourceRecord["assignment"];
    raw: { title: string; body: string; publication_time: string | null; assignment_due_date?: string | null };
  };
}
export interface RecognitionRequest {
  contract_version: "0.2";
  request_id: string;
  records: RecognitionRecord[];
}

/** Pure conversion only: no task decisions, time inference, service calls or database writes. */
export function toRecognitionRequests(sources: CourseSourceRecord[], requestId = `pku:${randomUUID()}`): RecognitionRequest[] {
  if (!requestId.trim()) throw new Error("request_id must not be empty");
  const ids = new Set<string>();
  const records: RecognitionRecord[] = sources.map((source) => {
    if (!source.input_id.trim() || ids.has(source.input_id)) throw new Error("source input_id must be nonempty and unique");
    ids.add(source.input_id);
    const text = [source.title, source.body].filter(Boolean).join("\n\n");
    if (!sourceTextFits(source.title, source.body)) throw new Error(`source ${source.input_id} exceeds the v0.2 text limit; text was not truncated`);
    return {
      input_id: source.input_id, text, text_quality: source.text_quality,
      platform: "teaching_web", published_at: { ...source.published_at }, collected_at: source.collected_at,
      timezone: DEFAULT_TIMEZONE, timezone_basis: "caller_default",
      source_url: source.source_url, source_label: source.title || null, source_item_id: source.source_item_id,
      course_or_project: source.course || null, context: [],
      upstream_hints: {
        source_kind: source.kind, section: source.section,
        ...(source.body_evidence ? { body_evidence: { ...source.body_evidence, included_reference_urls: [...source.body_evidence.included_reference_urls] } } : {}),
        ...(source.provenance ? { provenance: { ...source.provenance }, snapshot_id: sourceSnapshotId(source) } : {}),
        references: source.references.map((reference) => ({ ...reference })), warnings: [...source.warnings],
        ...(source.assignment ? { assignment: { ...source.assignment } } : {}),
        raw: {
          title: source.title, body: source.body, publication_time: source.publication_raw,
          ...(source.assignment ? { assignment_due_date: source.assignment.due_raw } : {}),
        },
      },
    };
  });
  const byteLength = (value: unknown) => Buffer.byteLength(JSON.stringify(value), "utf8");
  const emptyRequest = (id: string): RecognitionRequest => ({ contract_version: "0.2", request_id: id, records: [] });
  const sizes = records.map(byteLength);
  const single = emptyRequest(requestId);
  // Count the envelope, JSON escaping, duplicated raw evidence and array commas.
  const singleBytes = byteLength(single) + sizes.reduce((sum, size) => sum + size, 0) + Math.max(0, records.length - 1);
  if (records.length <= MAX_RECORDS && singleBytes <= MAX_REQUEST_BYTES) return [{ ...single, records }];
  if (!records.length) throw new Error("request_id exceeds the recognition request byte limit");

  const requests: RecognitionRequest[] = [];
  let batch = emptyRequest(`${requestId}:1`);
  let batchBytes = byteLength(batch);
  for (const [index, record] of records.entries()) {
    let addedBytes = sizes[index] + (batch.records.length ? 1 : 0);
    if (batch.records.length && (batch.records.length === MAX_RECORDS || batchBytes + addedBytes > MAX_REQUEST_BYTES)) {
      requests.push(batch);
      batch = emptyRequest(`${requestId}:${requests.length + 1}`);
      batchBytes = byteLength(batch);
      addedBytes = sizes[index];
    }
    if (batchBytes + addedBytes > MAX_REQUEST_BYTES) {
      throw new Error(`source ${record.input_id} exceeds the recognition request byte limit; evidence was not truncated`);
    }
    batch.records.push(record);
    batchBytes += addedBytes;
  }
  requests.push(batch);
  return requests;
}

/** Content version only, never an authorization token or #124 verified_snapshot_digests. */
export function sourceSnapshotId(source: CourseSourceRecord): string {
  function canonical(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).filter(([, v]) => v !== undefined).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0).map(([k, v]) => [k, canonical(v)]));
    return value;
  }
  return `pku:snapshot:${createHash("sha256").update(JSON.stringify(canonical(source))).digest("hex")}`;
}
