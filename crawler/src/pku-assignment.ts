import { load, type Cheerio, type CheerioAPI } from "cheerio";
import type { AnyNode } from "domhandler";
import type { Page } from "playwright";
import type { CourseSourceRecord } from "./pku-source.js";

const PKU_ORIGIN = "https://course.pku.edu.cn";
const ASSIGNMENT_PATH = /^\/webapps\/assignment\//i;
const ACTIVE_HEADING = /^(?:上载作业|Upload Assignment)\s*[:：]?\s*/i;
const SUBMITTED_HEADING = /^(?:复查提交历史记录|Review Submission History)\s*[:：]?\s*/i;
const DUE_LABELS = ["到期日期", "Due Date"];
const GENERIC_UNREAD_WARNING = "linked pages, attachments and images were not read";

export type AssignmentPageState = "active" | "submitted" | "unknown";

export interface AssignmentDetail {
  state: AssignmentPageState;
  title: string | null;
  description: string;
  due_raw: string | null;
}

type AssignmentPage = Pick<Page, "goto" | "url" | "locator" | "content">;

function normalize(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function ownText($: CheerioAPI, node: AnyNode): string {
  return normalize($(node).clone().children().remove().end().text());
}

function headingTexts($: CheerioAPI): string[] {
  return $("h1, h2, h3, [role=heading], #pageTitleText, .pageTitle")
    .map((_index, node) => normalize($(node).text()))
    .get()
    .filter(Boolean);
}

function findExactText($: CheerioAPI, values: string[]): Cheerio<AnyNode> | null {
  const lowered = new Set(values.map((value) => value.toLocaleLowerCase("en-US")));
  const node = $("body *").toArray().find((candidate) => lowered.has(ownText($, candidate).toLocaleLowerCase("en-US")));
  return node ? $(node) : null;
}

function dueValueFromLabel($: CheerioAPI, label: Cheerio<AnyNode>): string | null {
  const labelText = normalize(label.text());
  let candidate = label;
  for (let depth = 0; depth < 5; depth += 1) {
    const readable = candidate.clone();
    readable.find("br").replaceWith(" ");
    const text = normalize(readable.text());
    const remainder = normalize(text.replace(labelText, ""))
      .split(/(?:满分|Points Possible|Full Points|作业提交|Assignment Submission)/i)[0]
      .trim();
    if (remainder && remainder.length <= 200 && /\d/.test(remainder)) return remainder;
    const parent = candidate.parent();
    if (!parent.length || parent.is("body, html")) break;
    candidate = parent;
  }
  return null;
}

function assignmentDescription($: CheerioAPI): string {
  const selectors = [
    "[data-assignment-description]",
    "#assignmentDescription",
    ".assignment-description",
    "#assignmentInfo .vtbegenerated",
    ".assignmentInfo .vtbegenerated",
    "[data-role=assignment-info] .vtbegenerated",
  ];
  for (const selector of selectors) {
    const node = $(selector).first();
    if (!node.length) continue;
    const safe = node.clone();
    safe.find("script, style, form, input, textarea, button, .submission, .attempt, .grade").remove();
    safe.find("br").replaceWith("\n");
    safe.find("p, div, li, tr, dt, dd").after("\n");
    const text = safe.text().split(/\r?\n/).map(normalize).filter(Boolean).join("\n");
    if (text) return text;
  }
  return "";
}

/** Parse only stable, teacher-authored assignment information. Submission history stays private. */
export function parseAssignmentDetailHtml(html: string): AssignmentDetail {
  const $ = load(html);
  const headings = headingTexts($);
  const submittedHeading = headings.find((heading) => SUBMITTED_HEADING.test(heading));
  if (submittedHeading) {
    return {
      state: "submitted",
      title: normalize(submittedHeading.replace(SUBMITTED_HEADING, "")) || null,
      description: "",
      due_raw: null,
    };
  }

  const dueLabel = findExactText($, DUE_LABELS);
  const activeHeading = headings.find((heading) => ACTIVE_HEADING.test(heading));
  const assignmentInfo = findExactText($, ["作业信息", "Assignment Information"]);
  if (!activeHeading && !assignmentInfo && !dueLabel) {
    return { state: "unknown", title: null, description: "", due_raw: null };
  }

  return {
    state: "active",
    title: activeHeading ? normalize(activeHeading.replace(ACTIVE_HEADING, "")) || null : null,
    description: assignmentDescription($),
    due_raw: dueLabel ? dueValueFromLabel($, dueLabel) : null,
  };
}

export function isPkuAssignmentUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.origin === PKU_ORIGIN && ASSIGNMENT_PATH.test(url.pathname);
  } catch {
    return false;
  }
}

/** Navigate and read only. The restricted Page type deliberately exposes no click or fill API. */
export async function readAssignmentDetail(page: AssignmentPage, url: string, timeout = 15_000): Promise<AssignmentDetail> {
  if (!isPkuAssignmentUrl(url)) throw new Error("assignment detail URL is outside the authenticated PKU assignment area");
  const response = await page.goto(url, { waitUntil: "domcontentloaded", timeout });
  if (!response || !response.ok()) throw new Error(`assignment detail request failed (HTTP ${response?.status() ?? "unknown"})`);
  const destination = new URL(page.url());
  if (destination.origin !== PKU_ORIGIN || !ASSIGNMENT_PATH.test(destination.pathname)
    || await page.locator('input[type="password"]').count()) {
    throw new Error("assignment detail left the authenticated area; login may have expired");
  }
  return parseAssignmentDetailHtml(await page.content());
}

function markDetailReferenceRead(source: CourseSourceRecord, url: string): CourseSourceRecord["references"] {
  return source.references.map((reference) => reference.url === url ? { ...reference, content_read: true } : { ...reference });
}

function warningsAfterRead(source: CourseSourceRecord, references: CourseSourceRecord["references"], extra: string[]): string[] {
  const warnings = source.warnings.filter((warning) => warning !== GENERIC_UNREAD_WARNING);
  if (references.some((reference) => !reference.content_read)) warnings.push(GENERIC_UNREAD_WARNING);
  return [...new Set([...warnings, ...extra])];
}

function appendFact(body: string, fact: string): string {
  return body ? `${body}\n${fact}` : fact;
}

/** Merge stable assignment facts without changing the listing-derived identity. */
export function enrichAssignmentSource(source: CourseSourceRecord, detailUrl: string, detail: AssignmentDetail): CourseSourceRecord {
  const references = detail.state === "unknown" ? source.references.map((reference) => ({ ...reference })) : markDetailReferenceRead(source, detailUrl);
  if (detail.state === "unknown") {
    return {
      ...source,
      references,
      assignment: { state: "unknown", due_raw: null },
      warnings: warningsAfterRead(source, references, ["assignment detail page structure was not recognized"]),
    };
  }

  if (detail.state === "submitted") {
    const body = /(?:提交状态|Submission status)\s*[:：]?\s*(?:已提交|Submitted)/i.test(source.body)
      ? source.body : appendFact(source.body, "提交状态：已提交");
    return {
      ...source,
      title: detail.title ?? source.title,
      body,
      text_quality: "partial",
      references,
      assignment: { state: "submitted", due_raw: null },
      warnings: warningsAfterRead(source, references, ["submission history detected; private submission, file and grading content was not read"]),
    };
  }

  let body = detail.description || source.body;
  if (detail.due_raw && !normalize(body).includes(normalize(detail.due_raw))) body = appendFact(body, `到期日期：${detail.due_raw}`);
  const unread = references.some((reference) => !reference.content_read);
  return {
    ...source,
    title: detail.title ?? source.title,
    body,
    text_quality: unread ? "partial" : body ? "complete" : "summary",
    references,
    assignment: { state: "active", due_raw: detail.due_raw },
    warnings: warningsAfterRead(source, references, []),
  };
}

/** Read native assignment links sequentially so the authenticated session is not flooded. */
export async function enrichAssignmentSources(page: AssignmentPage, sources: CourseSourceRecord[]): Promise<CourseSourceRecord[]> {
  const enriched: CourseSourceRecord[] = [];
  for (const source of sources) {
    const reference = source.kind === "assignment" ? source.references.find((item) => isPkuAssignmentUrl(item.url)) : undefined;
    if (!reference) {
      enriched.push(source);
      continue;
    }
    try {
      enriched.push(enrichAssignmentSource(source, reference.url, await readAssignmentDetail(page, reference.url)));
    } catch {
      enriched.push({
        ...source,
        references: source.references.map((item) => ({ ...item })),
        warnings: [...new Set([...source.warnings, "assignment detail could not be read; listing text was retained"])],
      });
    }
  }
  return enriched;
}
