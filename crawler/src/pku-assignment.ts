import { load, type Cheerio, type CheerioAPI } from "cheerio";
import type { AnyNode } from "domhandler";
import type { Page } from "playwright";
import { makeBodyEvidence, cloneTeacherContent, parseTeacherContent, sourceTextFits, makeSourceReference, UNREAD_REFERENCE_WARNING, type CourseSourceRecord, type SourceReference } from "./pku-source.js";

const PKU_ORIGIN = "https://course.pku.edu.cn";
const ASSIGNMENT_PATH = /^\/webapps\/assignment\//i;
const ACTIVE_HEADING = /^(?:上载作业|Upload Assignment)\s*[:：]?\s*/i;
const SUBMITTED_HEADING = /^(?:复查提交历史记录|Review Submission History)\s*[:：]?\s*/i;
const DUE_LABELS = ["到期日期", "Due Date"];
const GENERIC_UNREAD_WARNING = UNREAD_REFERENCE_WARNING;
const ASSIGNMENT_INFO = "#assignmentInfo, .assignmentInfo, [data-role=assignment-info]";
const DESCRIPTION_SELECTORS = "[data-assignment-description], #assignmentDescription, .assignment-description, #assignmentInfo .vtbegenerated, .assignmentInfo .vtbegenerated, [data-role=assignment-info] .vtbegenerated";
const OTHER_FIELDS = /(?:满分|Points Possible|Full Points|作业提交|Assignment Submission)/i;
const FIELD_LABEL = ".label, [data-role=label], label, th, [role=rowheader], [role=columnheader]";
const FIELD_ROW = ".metaField, tr, li";

export type AssignmentPageState = "active" | "submitted" | "unknown";

export interface AssignmentDetail {
  state: AssignmentPageState;
  heading_raw?: string;
  title: string | null;
  description: string;
  due_raw: string | null;
  references?: SourceReference[];
  due_unread?: boolean;
}

type AssignmentPage = Pick<Page, "goto" | "url" | "locator" | "content">;

function normalize(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function ownText($: CheerioAPI, node: AnyNode): string {
  return normalize($(node).clone().children().remove().end().text());
}

function headingTexts($: CheerioAPI, excluded = ""): string[] {
  return $("h1, h2, h3, [role=heading], #pageTitleText, .pageTitle")
    .filter((_index, node) => !excluded || (!$(node).is(excluded) && !$(node).parents(excluded).length))
    .map((_index, node) => normalize($(node).text()))
    .get()
    .filter(Boolean);
}

function findExactTexts($: CheerioAPI, values: string[], scope: Cheerio<AnyNode>): Cheerio<AnyNode> {
  const lowered = new Set(values.map((value) => value.toLocaleLowerCase("en-US")));
  return scope.find("*").filter((_i, candidate) => lowered.has(ownText($, candidate).toLocaleLowerCase("en-US")));
}

function dueCandidate(value: string): string | null {
  const text = normalize(value);
  if (!text || text.length > 200 || OTHER_FIELDS.test(text)) return null;
  if (/^(?:未设置|未指定|无|Not set|None|No due date|N\/A|[-–—]+)$/i.test(text)) return "";
  return /\d/.test(text) ? text : null;
}

function readableText($: CheerioAPI, node: Cheerio<AnyNode>): string {
  const safe = node.clone();
  safe.find("br").replaceWith(" ");
  return normalize(safe.text());
}

/** Read only the prefix owned by this field, including boundaries nested in value nodes. */
function readDueNodes($: CheerioAPI, nodes: AnyNode[], tableCell = false) {
  const parts: string[] = [];
  let bounded = false;
  let hasValue = false;
  function visit(node: AnyNode, insideValue = false): void {
    if (bounded || node.type === "comment") return;
    const next = $(node);
    if (node.type === "text") {
      const text = normalize(next.text());
      if (OTHER_FIELDS.test(text) || DUE_LABELS.some((value) => text.toLowerCase() === value.toLowerCase())) {
        bounded = true;
        return;
      }
      if (text) { parts.push(next.text()); hasValue = true; }
      return;
    }
    if (next.is(`${FIELD_LABEL}, ${FIELD_ROW}, ${ASSIGNMENT_INFO}, ${DESCRIPTION_SELECTORS}`)
      || (!insideValue && !next.is(`span, strong, b, em, i, time, br, .value, [data-role=value]${tableCell ? ", td" : ""}`))) {
      bounded = true;
      return;
    }
    // An empty value node still belongs to this field; do not climb past it.
    if (!next.is("br")) hasValue = true;
    if (next.is("br")) parts.push(" ");
    const valueContainer = insideValue || next.is(".value, [data-role=value], td");
    for (const child of next.contents().toArray()) visit(child, valueContainer);
  }
  for (const node of nodes) {
    visit(node);
    parts.push(" ");
    if (bounded) break;
  }
  return { value: dueCandidate(parts.join("")), bounded, hasValue };
}

function dueValueFromLabel($: CheerioAPI, label: Cheerio<AnyNode>): string | null {
  // An exact own-text label may also contain its value, e.g. <td>Due Date<span>...</span></td>.
  const contents = label.contents().toArray();
  const lastLabelText = contents.reduce((last, node, index) => node.type === "text" && normalize($(node).text()) ? index : last, -1);
  const inside = readDueNodes($, contents.slice(lastLabelText + 1));
  if (inside.value !== null || inside.bounded || inside.hasValue) return inside.value;
  const labelText = normalize(label.text());
  let candidate = label;
  for (let depth = 0; depth < 5; depth += 1) {
    if (candidate.is(`${ASSIGNMENT_INFO}, ${FIELD_ROW}`)) break;
    const inTableRow = candidate.parent().is("tr");
    const following = followingNodes(candidate);
    // A table field owns one adjacent value cell, even when later headers have no class.
    const nodes = inTableRow
      ? following.filter((node) => node.type !== "comment" && (node.type !== "text" || normalize($(node).text()))).slice(0, 1)
      : following;
    const adjacent = readDueNodes($, nodes, inTableRow);
    if (adjacent.value !== null || adjacent.bounded || adjacent.hasValue) return adjacent.value;
    const parent = candidate.parent();
    // Only unwrap a pure label. Never retry by concatenating a containing field/region.
    if (!parent.length || parent.is(`body, html, ${ASSIGNMENT_INFO}, ${FIELD_ROW}`)
      || readableText($, parent) !== labelText) break;
    candidate = parent;
  }
  return null;
}

function followingNodes(node: Cheerio<AnyNode>): AnyNode[] {
  const nodes: AnyNode[] = [];
  for (let next = node[0]?.nextSibling; next; next = next.nextSibling) nodes.push(next);
  return nodes;
}

function assignmentDescription($: CheerioAPI, baseUrl: string) {
  const texts: string[] = [];
  const references: SourceReference[] = [];
  const roots = $(DESCRIPTION_SELECTORS).filter((_i, node) => !$(node).parents(DESCRIPTION_SELECTORS).length);
  for (const node of roots.toArray()) {
    const parsed = parseTeacherContent($, $(node), baseUrl);
    if (parsed.text && !texts.some((text) => normalize(text) === normalize(parsed.text))) texts.push(parsed.text);
    for (const reference of parsed.references) {
      if (!references.some((item) => item.url === reference.url && item.kind === reference.kind)) references.push(reference);
    }
  }
  return { text: texts.join("\n\n"), references };
}

/** Parse only stable, teacher-authored assignment information. Submission history stays private. */
export function parseAssignmentDetailHtml(html: string, baseUrl = PKU_ORIGIN): AssignmentDetail {
  const raw = load(html);
  const $ = load(cloneTeacherContent(raw, raw("body"), true).html() ?? "");
  $("form#uploadAssignmentForm").each((_i, node) => { $(node).replaceWith($(node).contents()); });
  const headings = headingTexts($);
  const submittedHeading = headingTexts($, `${DESCRIPTION_SELECTORS}, ${ASSIGNMENT_INFO}, #content_listContainer, #announcementList`)
    .find((heading) => SUBMITTED_HEADING.test(heading));
  if (submittedHeading) {
    return {
      state: "submitted",
      heading_raw: submittedHeading,
      title: normalize(submittedHeading.replace(SUBMITTED_HEADING, "")) || null,
      description: "",
      due_raw: null,
    };
  }

  const information = $(ASSIGNMENT_INFO);
  const dueLabels = findExactTexts($, DUE_LABELS, information)
    .filter((_i, node) => !$(node).is(DESCRIPTION_SELECTORS) && !$(node).parents(DESCRIPTION_SELECTORS).length);
  const activeHeading = headings.find((heading) => ACTIVE_HEADING.test(heading));
  const assignmentInfo = findExactTexts($, ["作业信息", "Assignment Information"], $("body"));
  if (!activeHeading && !assignmentInfo.length && !dueLabels.length) {
    return { state: "unknown", title: null, description: "", due_raw: null };
  }

  const description = assignmentDescription($, baseUrl);
  const dueValues = dueLabels.toArray().map((node) => dueValueFromLabel($, $(node)));
  const readableDueValues = [...new Set(dueValues.filter((value): value is string => value !== null))];
  const dueUnread = dueValues.some((value) => value === null) || readableDueValues.length > 1;
  return {
    state: "active",
    title: activeHeading ? normalize(activeHeading.replace(ACTIVE_HEADING, "")) || null : null,
    description: description.text,
    ...(description.references.length ? { references: description.references } : {}),
    due_raw: readableDueValues.filter(Boolean).join(" / ") || null,
    ...(dueUnread ? { due_unread: true } : {}),
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
  const requested = new URL(url);
  for (const key of ["course_id", "content_id"]) {
    const expected = requested.searchParams.get(key);
    if (expected && destination.searchParams.get(key) !== expected) {
      throw new Error("assignment detail redirected to a different or unidentified assignment");
    }
  }
  return parseAssignmentDetailHtml(await page.content(), url);
}

function markDetailReferenceRead(source: CourseSourceRecord, url: string): CourseSourceRecord["references"] {
  return source.references.map((reference) => reference.url === url ? { ...reference, content_read: true, read_status: "complete", reason: null } : { ...reference });
}

function warningsAfterRead(source: CourseSourceRecord, references: CourseSourceRecord["references"], extra: string[]): string[] {
  const warnings = source.warnings.filter((warning) => warning !== GENERIC_UNREAD_WARNING);
  if (references.some((reference) => !reference.content_read)) warnings.push(GENERIC_UNREAD_WARNING);
  return [...new Set([...warnings, ...extra])];
}

function appendFact(body: string, fact: string): string {
  return body ? `${body}\n${fact}` : fact;
}

/** Keep a whole listing as fallback, with the omitted detail explicitly unread. */
function retainListingOnOverflow(source: CourseSourceRecord, detailUrl: string, references: SourceReference[], assignment: NonNullable<CourseSourceRecord["assignment"]>): CourseSourceRecord {
  const unread = references.map((ref) => ref.url === detailUrl
    ? { ...ref, text: undefined, content_read: false, read_status: "failed" as const, reason: "too_large" } : ref);
  if (!unread.some((ref) => ref.url === detailUrl)) unread.push({ ...makeSourceReference(source.title, detailUrl, "detail"), read_status: "failed", reason: "too_large" });
  return {
    ...source, references: unread, assignment,
    ...(source.body_evidence ? { body_evidence: { ...source.body_evidence, status: source.body_evidence.text ? "partial" as const : "missing" as const } } : {}),
    text_quality: source.body ? "partial" : source.title ? "summary" : "missing",
    warnings: warningsAfterRead(source, unread, ["assignment detail exceeds the v0.2 input limit; whole listing text retained for manual review"]),
  };
}

/** Merge stable assignment facts without changing the listing-derived identity. */
export function enrichAssignmentSource(source: CourseSourceRecord, detailUrl: string, detail: AssignmentDetail): CourseSourceRecord {
  const descriptionMissing = detail.state === "active" && !detail.description && !detail.references?.length;
  const references = detail.state === "submitted"
    ? source.references.map((reference) => reference.url === detailUrl ? { ...reference, content_read: false, read_status: "partial" as const, reason: "submission_history_only", ...(detail.heading_raw ? { text: detail.heading_raw } : {}) } : { ...reference })
    : detail.state === "unknown" || descriptionMissing || detail.due_unread
      ? source.references.map((reference) => reference.url === detailUrl ? { ...reference, content_read: false, read_status: "failed" as const, reason: "detail_unrecognized" } : { ...reference })
      : markDetailReferenceRead(source, detailUrl);
  for (const reference of detail.references ?? []) {
    if (!references.some((item) => item.url === reference.url)) references.push({ ...reference });
  }
  if (detail.state === "unknown") {
    return {
      ...source,
      references,
      text_quality: source.body ? "partial" : source.text_quality,
      ...(source.body_evidence ? { body_evidence: { ...source.body_evidence, status: source.body_evidence.text ? "partial" as const : "missing" as const } } : {}),
      assignment: { state: "unknown", due_raw: null },
      warnings: warningsAfterRead(source, references, ["assignment detail page structure was not recognized"]),
    };
  }

  if (detail.state === "submitted") {
    const body = /(?:提交状态|Submission status)\s*[:：]?\s*(?:已提交|Submitted)/i.test(source.body)
      ? source.body : appendFact(source.body, "提交状态：已提交");
    if (!sourceTextFits(detail.title ?? source.title, body)) return retainListingOnOverflow(source, detailUrl, references, { state: "submitted", due_raw: null });
    return {
      ...source,
      title: detail.title ?? source.title,
      body,
      text_quality: "partial",
      ...(source.body_evidence ? { body_evidence: { ...source.body_evidence, status: source.body_evidence.text ? "partial" as const : "missing" as const } } : {}),
      references,
      assignment: { state: "submitted", due_raw: null },
      warnings: warningsAfterRead(source, references, ["submission history heading indicates submitted; private submission, file and grading content was not read"]),
    };
  }

  let body = detail.description || source.body;
  if (detail.description && source.body && !normalize(detail.description).includes(normalize(source.body))) {
    body = `列表正文：${source.body}\n\n作业详情：${detail.description}`;
  }
  if (detail.due_raw && !normalize(body).includes(normalize(detail.due_raw))) body = appendFact(body, `到期日期：${detail.due_raw}`);
  if (!sourceTextFits(detail.title ?? source.title, body)) return retainListingOnOverflow(source, detailUrl, references, { state: "active", due_raw: detail.due_raw });
  const unread = references.some((reference) => !reference.content_read);
  return {
    ...source,
    title: detail.title ?? source.title,
    body,
    ...(source.body_evidence ? { body_evidence: makeBodyEvidence(detail.title ?? source.title, body,
      descriptionMissing || detail.due_unread ? "partial" : body ? "complete" : (detail.title ?? source.title) ? "summary" : "missing",
      references.filter((ref) => ref.url === detailUrl && ref.content_read).map((ref) => ref.url)) } : {}),
    text_quality: unread || descriptionMissing || detail.due_unread ? (body ? "partial" : "summary") : body ? "complete" : "summary",
    references,
    assignment: { state: "active", due_raw: detail.due_raw },
    warnings: warningsAfterRead(source, references, [
      ...(descriptionMissing ? ["assignment teacher description was not found; listing text retained for manual review"] : []),
      ...(detail.due_unread ? ["assignment due-date field could not be read consistently; teacher text retained for manual review"] : []),
    ]),
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
        ...(source.body_evidence ? { body_evidence: { ...source.body_evidence, status: source.body_evidence.text ? "partial" as const : "missing" as const } } : {}),
        references: source.references.map((item) => item.url === reference.url ? { ...item, read_status: "failed", reason: "detail_failed" } : { ...item } ),
        warnings: [...new Set([...source.warnings, "assignment detail could not be read; listing text was retained"])],
      });
    }
  }
  return enriched;
}
