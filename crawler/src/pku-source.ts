import { load, type Cheerio, type CheerioAPI } from "cheerio";
import type { AnyNode } from "domhandler";
import { DateTime } from "luxon";
import { DEFAULT_TIMEZONE } from "./core/time.js";
import { sha256 } from "./core/utils.js";

export interface PublicationTime {
  value: string | null;
  precision: "datetime" | "date" | "unknown";
  basis: "source" | "unknown";
}
export interface SourceReference {
  label: string;
  url: string;
  content_read: boolean;
  kind: "detail" | "attachment" | "image" | "link";
  read_status: "unread" | "complete" | "partial" | "failed" | "unsupported";
  reason: string | null;
  text?: string;
}
export const MAX_SOURCE_TEXT_LENGTH = 100_000;
export function sourceTextFits(title: string, body: string): boolean {
  return [title, body].filter(Boolean).join("\n\n").length <= MAX_SOURCE_TEXT_LENGTH;
}

export const UNREAD_REFERENCE_WARNING = "linked pages, attachments and images were not read";

export interface CourseSourceRecord {
  input_id: string;
  source_item_id: string | null;
  source_url: string;
  course: string;
  section: string;
  kind: "announcement" | "assignment" | "content";
  title: string;
  body: string;
  /** Original teacher body, kept separate before attachment text is appended for legacy output. */
  teacher_body?: string;
  course_term_label?: string;
  text_quality: "complete" | "partial" | "summary" | "missing";
  published_at: PublicationTime;
  publication_raw: string | null;
  collected_at: string;
  references: SourceReference[];
  assignment?: { state: "active" | "submitted" | "unknown"; due_raw: string | null };
  warnings: string[];
  provenance?: CourseProvenance;
  body_evidence?: BodyEvidence;
}
export interface BodyEvidence {
  version: "teaching-body-v1";
  text: string;
  status: CourseSourceRecord["text_quality"];
  included_reference_urls: string[];
}
export function makeBodyEvidence(title: string, body: string, status: BodyEvidence["status"], included: string[] = []): BodyEvidence {
  return { version: "teaching-body-v1", text: [title, body].filter(Boolean).join("\n\n"), status, included_reference_urls: included };
}
export interface CourseProvenance {
  version: "course-provenance-v1";
  course_id: string | null;
  course_entry_url: string | null;
  source_metadata_text: string;
  term_label: string | null;
  term_basis: "course_entry" | "unknown";
  applies_to: "unknown";
}
export interface SourceContext { course: string; section: string; sourceUrl: string; collectedAt: string; courseEntry?: { text: string; url: string }; }

/** Observed course-list evidence only: a label cannot prove a material is a current assignment. */
export function collectCourseProvenance(context: SourceContext): CourseProvenance {
  const pageUrl = cleanSourceUrl(context.sourceUrl);
  const courseId = pageUrl ? new URL(pageUrl).searchParams.get("course_id") : null;
  const entryUrl = context.courseEntry ? cleanSourceUrl(context.courseEntry.url) : null;
  const entry = entryUrl ? new URL(entryUrl) : null;
  const entryId = entry?.pathname.endsWith("/launcher") ? entry.searchParams.get("id") : null;
  const matches = Boolean(courseId && entryId === courseId && entry?.origin === new URL(pageUrl!).origin);
  // Preserve the observed entry text; never use the caller's term filter or current date as evidence.
  const text = matches && context.courseEntry!.text.length <= 4096 ? context.courseEntry!.text : "";
  const labels = [...text.matchAll(/(?:20\d{2}|\d{2})\s*[-–]\s*(?:20\d{2}|\d{2})学年第[12]学期/g)].map((m) => m[0]);
  const unique = [...new Set(labels)];
  const termLabel = unique.length === 1 ? unique[0] : null;
  return { version: "course-provenance-v1", course_id: courseId, course_entry_url: matches ? entryUrl : null,
    source_metadata_text: text, term_label: termLabel, term_basis: termLabel ? "course_entry" : "unknown", applies_to: "unknown" };
}

/** Only transport metadata is cleaned; source text remains evidence, never instructions. */
export function cleanSourceUrl(value: string, base?: string): string | null {
  try {
    const url = new URL(value, base);
    if (!["http:", "https:"].includes(url.protocol)) return null;
    url.username = "";
    url.password = "";
    for (const key of [...url.searchParams.keys()]) {
      if (/token|password|secret|session|ticket|auth|credential|api.?key|^code$|^sso$/i.test(key)) url.searchParams.delete(key);
    }
    url.hash = "";
    return url.href;
  } catch { return null; }
}

/** Preserve source precision; collection time never supplies a missing publication date. */
export function parseSourcePublication(raw: string | null): PublicationTime {
  const unknown: PublicationTime = { value: null, precision: "unknown", basis: "unknown" };
  if (!raw) return unknown;
  const date = raw.match(/(20\d{2})[年./-]\s*(\d{1,2})[月./-]\s*(\d{1,2})(?:日)?/);
  if (!date) return unknown;
  const tail = raw.slice(date.index! + date[0].length).replace(/星期[一二三四五六日天]/g, "").trim();
  const clock = tail.match(/^(上午|下午)?\s*(\d{1,2})(?:[:：时点])(\d{1,2})(?:分)?(?:(?:[:：])(\d{2})|(\d{1,2})秒)?\s*(?:CST)?$/i);
  if (tail && !clock) return unknown;
  let hour = clock ? Number(clock[2]) : 0;
  if (clock?.[1] === "下午" && hour < 12) hour += 12;
  if (clock?.[1] === "上午" && hour === 12) hour = 0;
  const dt = DateTime.fromObject({ year: Number(date[1]), month: Number(date[2]), day: Number(date[3]), hour, minute: Number(clock?.[3] ?? 0), second: Number(clock?.[4] ?? clock?.[5] ?? 0) }, { zone: DEFAULT_TIMEZONE });
  if (!dt.isValid) return unknown;
  return { value: clock ? dt.toISO({ suppressMilliseconds: true }) : dt.toISODate(), precision: clock ? "datetime" : "date", basis: "source" };
}

/** Collect every substantive listing item, without deciding whether it is a task. */
export function extractCourseSourcesFromHtml(html: string, context: SourceContext): CourseSourceRecord[] {
  const $ = load(html);
  const records: CourseSourceRecord[] = [];
  const cleanPageUrl = cleanSourceUrl(context.sourceUrl);
  if (!cleanPageUrl) throw new Error("invalid source page URL");
  $("#content_listContainer > li, #announcementList > li").each((_index, element) => {
    const item = $(element);
    const announcement = item.parent().attr("id") === "announcementList";
    const heading = announcement ? item.children("h3").first() : item.children(".item").find("h3").first();
    if (!heading.length) return;
    const title = heading.text().replace(/\s+/g, " ").trim();
    const href = heading.find("a[href]").first().attr("href") ?? "";
    if (/\/content\/listContent(?:Editable)?\.jsp/i.test(href)) return;
    const details = item.children(".details").first();
    const publicationRaw = announcement
      ? details.children("p").map((_i, p) => $(p).text().trim()).get().find((text) => /^(?:发布时间|Posted on)\s*[:：]/i.test(text)) ?? null
      : null;
    const content = announcement ? details.find(".vtbegenerated") : details;
    const parsed = parseTeacherContent($, content, cleanPageUrl);
    const references = parsed.references;
    if (href) {
      const url = cleanSourceUrl(href, cleanPageUrl);
      if (url && !references.some((ref) => ref.url === url)) references.unshift(makeSourceReference(title, url, /\/webapps\/assignment\//i.test(href) ? "detail" : referenceKind(url)));
    }
    const body = parsed.text;
    const id = announcement ? item.attr("id") : item.children(".item").attr("id") ?? item.attr("id");
    const url = new URL(cleanPageUrl);
    if (item.attr("id")) url.hash = item.attr("id")!;
    const kind = announcement ? "announcement" : /\/webapps\/assignment\//i.test(href) ? "assignment" : "content";
    const identity = id
      ? [url.origin, url.searchParams.get("course_id") ?? context.course, kind === "announcement" ? "announcement" : "content", id]
      : [cleanPageUrl, title, body];
    const textQuality = !body ? (title ? "summary" : "missing") : references.length ? "partial" : "complete";
    records.push({
      input_id: `pku:source:${sha256(JSON.stringify(identity))}`,
      source_item_id: id ?? null, source_url: url.href, course: context.course, section: context.section,
      kind, title, body, text_quality: textQuality,
      body_evidence: makeBodyEvidence(title, body, !body ? (title ? "summary" : "missing") : kind === "assignment" ? "partial" : "complete"),
      ...(context.courseEntry ? { provenance: collectCourseProvenance(context) } : {}),
      published_at: parseSourcePublication(publicationRaw), publication_raw: publicationRaw,
      collected_at: context.collectedAt, references,
      warnings: [
        ...(!body ? ["listing body is missing; only the title was collected"] : []),
        ...(references.length ? [UNREAD_REFERENCE_WARNING] : []),
        ...(publicationRaw && parseSourcePublication(publicationRaw).value === null ? ["publication time could not be parsed; raw value retained"] : []),
      ],
    });
  });
  return records;
}

export function deduplicateCourseSources(records: CourseSourceRecord[]): CourseSourceRecord[] {
  const unique = new Map<string, CourseSourceRecord>();
  for (const record of records) {
    const previous = unique.get(record.input_id);
    if (!previous || record.body.length > previous.body.length) unique.set(record.input_id, record);
  }
  return [...unique.values()];
}

export function referenceKind(url: string): SourceReference["kind"] {
  return /\/bbcswebdav\/|\.(?:pdf|txt|zip|docx?|pptx?|xlsx?)(?:[?#]|$)/i.test(url) ? "attachment" : "link";
}

export function makeSourceReference(label: string, url: string, kind: SourceReference["kind"]): SourceReference {
  return { label, url, kind, content_read: false, read_status: "unread", reason: kind === "image" ? "image_not_read" : "not_read" };
}

const EXCLUDED_CONTENT = "script, style, input, textarea, button, nav, [role=navigation], .announcementInfo, #assignmentSubmission, .submission, .attempt, .grade";

/** Only the known assignment page wrapper may contain teacher information and private controls. */
export function cloneTeacherContent($: CheerioAPI, content: Cheerio<AnyNode>, allowAssignmentForm = false): Cheerio<AnyNode> {
  const excluded = `${EXCLUDED_CONTENT}, ${allowAssignmentForm ? "form:not(#uploadAssignmentForm):not(#uploadAssignmentFormId), #step2, #step3" : "form"}`;
  const safe = content.filter((_i, node) => {
    const element = $(node);
    // Blackboard's read-only announcement list is itself inside a form.
    // Exempt only that outer wrapper for the known teacher body, never nested
    // forms, arbitrary form text or student submission/grade/navigation areas.
    const announcementForm = element.is("#announcementList > li > .details .vtbegenerated")
      ? element.closest("#announcementList").parent("form#announcementForm").get(0) : undefined;
    return !element.is(excluded) && element.parents(excluded).toArray().every(parent => parent === announcementForm);
  }).clone();
  safe.find(excluded).remove();
  return safe;
}

/** Scan URL tokens separately from descriptors; commas can be part of a URL/data URI. */
function imageCandidates(srcset: string): string[] {
  const urls: string[] = [];
  let position = 0;
  const whitespace = /[\t\n\f\r ]/;
  while (position < srcset.length) {
    while (position < srcset.length && (whitespace.test(srcset[position]) || srcset[position] === ",")) position++;
    const start = position;
    while (position < srcset.length && !whitespace.test(srcset[position])) position++;
    const token = srcset.slice(start, position);
    if (!token) break;
    urls.push(token.replace(/,+$/, ""));
    if (token.endsWith(",")) continue;
    // Descriptor commas delimit candidates, except inside parentheses.
    let depth = 0;
    while (position < srcset.length) {
      const char = srcset[position++];
      if (char === "(") depth++;
      else if (char === ")") depth = Math.max(0, depth - 1);
      else if (char === "," && depth === 0) break;
    }
  }
  return urls;
}

/** Work only inside the teacher-authored container; never inspect submission widgets. */
export function parseTeacherContent($: CheerioAPI, content: Cheerio<AnyNode>, baseUrl: string): { text: string; references: SourceReference[] } {
  const safe = cloneTeacherContent($, content);
  const references: SourceReference[] = [];
  const add = (label: string, value: string, kind: SourceReference["kind"]) => {
    const url = cleanSourceUrl(value, baseUrl);
    if (!references.some((ref) => ref.url === (url ?? "") && ref.kind === kind)) references.push(makeSourceReference(label, url ?? "", kind));
  };
  safe.find("a[href]").each((_i, node) => {
    const href = $(node).attr("href")!;
    // In-page anchors contain no unread document; substantive external links do.
    if (!href.trim().startsWith("#")) add($(node).text().trim(), href, referenceKind(href));
  });
  const addImage = (value: string, alt: string, presentation: boolean) => {
    const url = cleanSourceUrl(value, baseUrl);
    const knownIcon = url && ["/images/ci/icons/cmlink_generic.gif", "/images/ci/ng/cal_year_event.gif"].includes(new URL(url).pathname)
      && /^(?:文件|File|单击获得更多选项|Click for more options)?$/i.test(alt);
    const decorative = url && new URL(url).origin === new URL(baseUrl).origin
      && /^\/images\/ci\//i.test(new URL(url).pathname)
      && (presentation || knownIcon);
    if (!decorative) add(alt, value, "image");
  };
  safe.find("img[src], img[srcset], picture > source[srcset]").each((_i, node) => {
    const image = $(node);
    const fallback = image.is("source") ? image.parent().children("img").first() : image;
    const alt = fallback.attr("alt") ?? "";
    const presentation = fallback.attr("role") === "presentation" || fallback.attr("aria-hidden") === "true";
    if (image.is("img") && image.attr("src") !== undefined) addImage(image.attr("src")!, alt, presentation);
    for (const candidate of imageCandidates(image.attr("srcset") ?? "")) addImage(candidate, alt, presentation);
  });
  safe.find("br").replaceWith("\n");
  safe.find("p, div, li, tr, dt, dd").after("\n");
  safe.find("td, th").after(" ");
  const text = safe.text().split(/\r?\n/).map((line) => line.replace(/\s+/g, " ").trim()).filter(Boolean).join("\n");
  return { text, references };
}
