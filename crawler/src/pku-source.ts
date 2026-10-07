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
  text_quality: "complete" | "partial" | "summary" | "missing";
  published_at: PublicationTime;
  publication_raw: string | null;
  collected_at: string;
  references: SourceReference[];
  assignment?: { state: "active" | "submitted" | "unknown"; due_raw: string | null };
  warnings: string[];
}
export interface SourceContext { course: string; section: string; sourceUrl: string; collectedAt: string; }

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

const EXCLUDED_CONTENT = "script, style, input, textarea, button, nav, [role=navigation], .announcementInfo, .submission, .attempt, .grade";

/** Only the known assignment page wrapper may contain teacher information and private controls. */
export function cloneTeacherContent($: CheerioAPI, content: Cheerio<AnyNode>, allowAssignmentForm = false): Cheerio<AnyNode> {
  const excluded = `${EXCLUDED_CONTENT}, ${allowAssignmentForm ? "form:not(#uploadAssignmentForm)" : "form"}`;
  const safe = content.filter((_i, node) => !$(node).is(excluded) && !$(node).parents(excluded).length).clone();
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
