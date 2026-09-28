import { load } from "cheerio";
import { DateTime } from "luxon";
import { DEFAULT_TIMEZONE } from "./core/time.js";
import { sha256 } from "./core/utils.js";

export interface PublicationTime {
  value: string | null;
  precision: "datetime" | "date" | "unknown";
  basis: "source" | "unknown";
}
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
  references: { label: string; url: string; content_read: false }[];
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
    const references: CourseSourceRecord["references"] = [];
    const addReference = (label: string, value: string) => {
      const url = cleanSourceUrl(value, cleanPageUrl);
      if (url && !references.some((ref) => ref.url === url)) references.push({ label, url, content_read: false });
    };
    if (href) addReference(title, href);
    content.find("a[href]").each((_i, a) => addReference($(a).text().trim(), $(a).attr("href")!));
    content.find("img[src]").each((_i, img) => addReference($(img).attr("alt") ?? "", $(img).attr("src")!));
    const bodyNode = content.clone();
    bodyNode.find("script, style, .announcementInfo").remove();
    bodyNode.find("br").replaceWith("\n");
    bodyNode.find("p, div, li, tr, dt, dd").after("\n");
    bodyNode.find("td, th").after(" ");
    const body = bodyNode.text().split(/\r?\n/).map((line) => line.replace(/\s+/g, " ").trim()).filter(Boolean).join("\n");
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
        ...(references.length ? ["linked pages, attachments and images were not read"] : []),
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
