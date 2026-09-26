import { randomUUID } from "node:crypto";
import type { CourseSourceRecord, PublicationTime } from "./pku-source.js";
import { DEFAULT_TIMEZONE } from "./core/time.js";

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
    raw: { title: string; body: string; publication_time: string | null };
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
    if (text.length > 100_000) throw new Error(`source ${source.input_id} exceeds the v0.2 text limit; text was not truncated`);
    return {
      input_id: source.input_id, text, text_quality: source.text_quality,
      platform: "teaching_web", published_at: { ...source.published_at }, collected_at: source.collected_at,
      timezone: DEFAULT_TIMEZONE, timezone_basis: "caller_default",
      source_url: source.source_url, source_label: source.title || null, source_item_id: source.source_item_id,
      course_or_project: source.course || null, context: [],
      upstream_hints: {
        source_kind: source.kind, section: source.section,
        references: source.references.map((reference) => ({ ...reference })), warnings: [...source.warnings],
        raw: { title: source.title, body: source.body, publication_time: source.publication_raw },
      },
    };
  });
  // The existing contract allows at most 100 records per request; preserve every source.
  const count = Math.max(1, Math.ceil(records.length / 100));
  return Array.from({ length: count }, (_, index) => ({
    contract_version: "0.2", request_id: count === 1 ? requestId : `${requestId}:${index + 1}`,
    records: records.slice(index * 100, (index + 1) * 100),
  }));
}
