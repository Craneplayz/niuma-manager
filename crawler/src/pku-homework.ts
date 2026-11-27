import { extractCourseSourcesFromHtml, deduplicateCourseSources, type CourseSourceRecord } from "./pku-source.js";
import { addGradebookItems, gradebookItems, resolveGradebookLinks } from "./pku-gradebook.js";
import { load } from "cheerio";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { DateTime } from "luxon";
import { chromium, type Browser, type Page } from "playwright";
import { DEFAULT_TIMEZONE, parseEventTime } from "./core/time.js";
import { enrichAssignmentSources, enrichListedAssignment, parseAssignmentDetailHtml, readAssignmentDetail, validateAssignmentDestination } from "./pku-assignment.js";
import { authenticatedAttachmentReader, enrichSourceAttachments } from "./pku-attachments.js";
import type { DownloadObserver } from "./producer-observation.js";
import { CollectionFailure, collectionStage } from "./collection-failure.js";

import { PKU_ORIGIN, PKU_PORTAL_URL, readTeachingPage, pageAnchors, mapPages, allAnnouncements, courseAnnouncementTool, courseEntryId, UnrenderedTeachingPage, type TeachingPage } from "./pku-teaching-overview.js";
const PKU_LOGIN_URL = `${PKU_ORIGIN}/webapps/bb-sso-BBLEARN/login.html`;
const SECTION_PATH_PATTERN = /\/content\/listContent(?:Editable)?\.jsp$/i;

export type HomeworkStatus = "upcoming" | "past" | "no_deadline";

export interface HomeworkRecord {
  source_item_id?: string;
  course: string;
  section: string;
  title: string;
  description: string | null;
  source_url: string;
  deadline_raw: string | null;
  deadline: string | null;
  status: HomeworkStatus;
}

export interface HomeworkScrapeResult {
  checked_at: string;
  timezone: string;
  term: string | null;
  courses_checked: number;
  sections_checked: number;
  records_found: number;
  past_records: number;
  undated_records: number;
  source_records: CourseSourceRecord[];
  source_records_found: number;
  coverage_warnings: string[];
  records: HomeworkRecord[];
  upcoming: HomeworkRecord[];
}

export interface ScrapeOptions {
  downloadObserver?: DownloadObserver;
  pageOnly?: boolean;
  secretPath?: string;
  term?: string | null;
  asOf?: DateTime;
  executablePath?: string;
  allSections?: boolean;
  teachingSections?: boolean;
  /** Read-only diagnosis of one saved source, without traversing other courses. */
  targetSource?: { url: string; itemId: string; course: string; section: string };
}

export interface AnchorInfo {
  text: string;
  href: string;
}

export interface CourseCollectionPlan {
  collectAnnouncements: boolean;
  sections: AnchorInfo[];
  warnings: string[];
}

interface CourseLink {
  entryText: string;
  name: string;
  href: string;
  termLabel?: string;
}

/**
 * Returns the current PKU academic-term label used by the course list.
 * PKU's first semester starts in September; March-August is the second semester.
 */
export function currentAcademicTerm(asOf: DateTime<boolean> = DateTime.now().setZone(DEFAULT_TIMEZONE)): string {
  const startYear = asOf.month >= 9 ? asOf.year : asOf.year - 1;
  const semester = asOf.month >= 3 && asOf.month <= 8 ? 2 : 1;
  return `${String(startYear).slice(-2)}-${String(startYear + 1).slice(-2)}学年第${semester}学期`;
}

/**
 * Parses a deadline in the context of the scrape date.
 * Date-only deadlines mean the end of that day; 24:00 means the next day's midnight.
 */
export function parseHomeworkDeadline(raw: string, asOf: DateTime<boolean> = DateTime.now().setZone(DEFAULT_TIMEZONE)): DateTime | null {
  if (!containsExplicitDate(raw)) return null;
  const hadMidnightEnd = /(?:24\s*[:：]\s*00|24\s*点)/.test(raw);
  const normalized = raw.replace(/24\s*[:：]\s*00/g, "23:59").replace(/24\s*点/g, "23:59");
  const parsed = parseEventTime(normalized, asOf.toFormat("yyyy-MM-dd"));
  if (!parsed.start) return null;
  let deadline = DateTime.fromISO(parsed.start).setZone(DEFAULT_TIMEZONE);
  if (!deadline.isValid) return null;
  if (hadMidnightEnd) deadline = deadline.plus({ minutes: 1 });
  else if (parsed.allDay) deadline = deadline.endOf("day").startOf("second");
  return deadline;
}

/** Keeps only records with an explicitly parsed deadline strictly after asOf. */
export function filterUpcomingHomeworks(records: HomeworkRecord[], asOf: DateTime<boolean> = DateTime.now().setZone(DEFAULT_TIMEZONE)): HomeworkRecord[] {
  return records.filter((record) => record.deadline !== null && DateTime.fromISO(record.deadline).setZone(DEFAULT_TIMEZONE) > asOf);
}

export async function scrapePkuHomeworks(options: ScrapeOptions = {}): Promise<HomeworkScrapeResult> {
  if (options.targetSource) validateSourceTarget(options.targetSource.url, options.targetSource.itemId);
  const asOf = (options.asOf ?? DateTime.now()).setZone(DEFAULT_TIMEZONE);
  const term = options.term === undefined ? currentAcademicTerm(asOf) : options.term;
  const credentials = await collectionStage("credentials", () => readCredentials(options.secretPath ?? process.env.PKU_SECRET_PATH ?? "secret"));
  const browser = await collectionStage("browser", () => launchBrowser(options.executablePath ?? process.env.PLAYWRIGHT_EXECUTABLE_PATH));

  try {
    const context = await collectionStage("browser", () => browser.newContext({
      locale: "zh-CN", timezoneId: DEFAULT_TIMEZONE, ...(options.pageOnly ? {serviceWorkers: "block" as const} : {}),
    }));
    const page = await collectionStage("browser", () => context.newPage());
    if (options.pageOnly) {
      // Intercept only file requests. A catch-all Playwright route disables
      // Chromium's HTTP cache for the repeated course-page navigations.
      const session = await context.newCDPSession(page);
      session.on("Fetch.requestPaused", ({requestId}) => {
        void session.send("Fetch.failRequest", {requestId, errorReason: "BlockedByClient"}).catch(() => {
          // Navigation or browser shutdown can already have cancelled this request.
        });
      });
      await session.send("Fetch.enable", {patterns: [
        {resourceType: "Image"}, {resourceType: "Media"}, {urlPattern: "*/bbcswebdav/*"},
        ...["pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "zip"].map(ext => ({urlPattern: `*.${ext}*`})),
      ]});
    }
    await collectionStage("login", () => login(page, credentials.username, credentials.password));
    if (options.targetSource) {
      const target = options.targetSource;
      await navigateCoursePage(page, target.url);
      const selected = selectSourceTarget(await page.content(), target, asOf.toISO()!);
      const detailed = await enrichAssignmentSources(page, selected);
      const sourceRecords = options.pageOnly ? detailed : await enrichSourceAttachments(detailed, authenticatedAttachmentReader(context), process.env.PKU_ATTACHMENT_DIR, options.downloadObserver);
      return buildHomeworkScrapeResult(homeworkView(sourceRecords, asOf), {
        asOf, term: null, coursesChecked: 1, sectionsChecked: 1, sourceRecords,
        scopeWarning: "Only the requested source item and its references were inspected.",
      });
    }
    if (options.pageOnly && options.teachingSections && !options.allSections) {
      try {
        return await collectCourseSources(page, options, asOf, term, true);
      } catch (error) {
        // Older or client-rendered Blackboard layouts still need the browser.
        if (!(error instanceof UnrenderedTeachingPage)) throw error;
      }
    }
    return await collectCourseSources(page, options, asOf, term, false);
  } finally {
    await closeBrowser(browser);
  }
}

export async function collectCourseSources(page: Page, options: ScrapeOptions, asOf: DateTime, term: string | null, direct: boolean) {
  const request = page.context().request;
  const readPage = async (href: string, settleMs: number): Promise<TeachingPage> => {
    if (direct) return collectionStage("navigation", () => readTeachingPage(request, href));
    await navigateCoursePage(page, href);
    await page.waitForTimeout(settleMs);
    return { url: page.url(), html: await page.content() };
  };

  const portal = await readPage(PKU_PORTAL_URL, 2000);
  const $ = load(portal.html);
  const links = pageAnchors($, $.root(), portal.url);
  const current = $(".portlet").filter((_i, el) => /^(?:当前学期课程|current (?:term|semester) courses)$/i.test($(el).find("h2").first().text().trim()));
  const useCurrent = options.teachingSections && options.term === undefined && current.length === 1;
  const courses = discoverCourses(useCurrent ? pageAnchors($, current, portal.url) : links, useCurrent ? null : term);
  if (direct && (!courses.length || (options.term === undefined && !useCurrent))) throw new UnrenderedTeachingPage();
  const announcements = direct ? await allAnnouncements(portal, discoverCourses(links, null), href => readPage(href, 0)) : null;
  const collectedAt = asOf.toISO()!;
  const byCourse = await mapPages(courses, direct ? 3 : 1, async course => {
    const landing = await readPage(course.href, 700);
    const courseId = new URL(landing.url).searchParams.get("course_id") ?? courseEntryId(course.href);
    const expectedId = courseEntryId(course.href);
    if (direct && expectedId && new URL(landing.url).searchParams.get("course_id") !== expectedId)
      throw new CollectionFailure("navigation", "http_error", "course launcher redirected to an unidentified course");
    const h = load(landing.html);
    const menu = h("#courseMenuPalette_contents");
    if (direct && !menu.length) throw new UnrenderedTeachingPage();
    const plan = planCourseCollection(course.name, pageAnchors(h, direct ? menu : h.root(), landing.url),
      options.allSections ?? false, options.teachingSections ?? false);
    const context = { course: course.name, courseEntry: { text: course.entryText, url: course.href }, collectedAt };
    const sources: CourseSourceRecord[] = [];
    if (plan.collectAnnouncements) {
      const listed = extractCourseSourcesFromHtml(landing.html, { ...context, section: "课程通知", sourceUrl: landing.url });
      sources.push(...(options.teachingSections ? listed.filter(s => s.kind === "announcement") : listed));
    }
    // Reuse the historical course-scoped URL and evidence even when reading the global list.
    // If an overview omitted a visible course announcement its coverage is uncertain.
    const rows = announcements?.get(course.href);
    const scopedLanding = /\/execute\/announcement$/i.test(new URL(landing.url).pathname);
    let covered = false;
    if (rows && scopedLanding) {
      const notices = extractCourseSourcesFromHtml('<ul id="announcementList">' + rows.join("") + "</ul>",
        { ...context, section: "课程通知", sourceUrl: landing.url });
      covered = sources.every(old => notices.some(n => n.source_item_id === old.source_item_id));
      sources.push(...notices);
    }
    const sections = plan.sections.filter(s => !(covered && courseAnnouncementTool(s.href)));
    for (const section of sections) {
      const listing = await readPage(section.href, 500);
      if (direct) {
        const target = new URL(section.href), actual = new URL(listing.url);
        const contentId = target.searchParams.get("content_id");
        if (actual.searchParams.get("course_id") !== courseId || (contentId && SECTION_PATH_PATTERN.test(target.pathname)
          && actual.searchParams.get("content_id") !== contentId))
          throw new CollectionFailure("navigation", "http_error", "course section redirected to another listing");
      }
      if (direct && !/id=["'](?:content_listContainer|announcementList|noItems)[^"']*["']/i.test(listing.html))
        throw new UnrenderedTeachingPage();
      sources.push(...extractCourseSourcesFromHtml(listing.html, { ...context, section: section.text, sourceUrl: listing.url }));
    }
    if (options.teachingSections && courseId && /^_\d+_\d+$/.test(courseId)) {
      const url = new URL("/webapps/bb-mygrades-BBLEARN/myGrades", PKU_ORIGIN);
      url.searchParams.set("course_id", courseId);
      url.searchParams.set("stream_name", "mygrades");
      try {
        const response = await request.get(url.href, { timeout: 15000, maxRedirects: 0 });
        try {
          if (!response.ok()) throw new Error("gradebook unavailable");
          const html = await response.text();
          if (!html.includes("sortable_item_row")) plan.warnings.push(course.name + "：成绩明细为空或未识别，已保留课程栏目。");
          const items = gradebookItems(html, url);
          await resolveGradebookLinks(request, sources, items, url, direct ? 1 : 4);
          sources.push(...addGradebookItems(sources, items, url, course.name, collectedAt));
        } finally { await response.dispose(); }
      } catch {
        plan.warnings.push(course.name + "：成绩明细不可用，已保留课程栏目。");
      }
    }
    for (const source of sources) source.course_term_label = course.termLabel;
    return { sources, warnings: plan.warnings, sections: sections.length };
  });

  const unique = deduplicateCourseSources(byCourse.flatMap(c => c.sources));
  const detailed = direct ? await mapPages(unique, 3, source => enrichListedAssignment(source, async url => {
    const detail = await readTeachingPage(request, url);
    validateAssignmentDestination(url, new URL(detail.url));
    return parseAssignmentDetailHtml(detail.html, url);
  })) : await enrichAssignmentSources(page, unique);
  if (direct) {
    // A client-rendered detail can have a valid URL and an empty initial body.
    // Use the shared browser page sequentially only for those unrecognized details.
    for (const [i, source] of detailed.entries()) {
      if (source.assignment?.state === "unknown")
        detailed[i] = await enrichListedAssignment(unique[i], url => readAssignmentDetail(page, url));
    }
  }
  const sourceRecords = options.pageOnly ? detailed : await enrichSourceAttachments(detailed,
    authenticatedAttachmentReader(page.context()), process.env.PKU_ATTACHMENT_DIR, options.downloadObserver);
  return buildHomeworkScrapeResult(homeworkView(sourceRecords, asOf), {
    asOf, term, coursesChecked: courses.length, sectionsChecked: byCourse.reduce((n, c) => n + c.sections, 0), sourceRecords,
    allSections: options.allSections ?? false, coverageWarnings: byCourse.flatMap(c => c.warnings),
    ...(options.teachingSections ? {scopeWarning: "Inspected course assignment, announcement and teaching-content sections, native assignment details and gradebook assignment rows when available. Nested folders are outside this scan."} : {}),
  });
}

export function validateSourceTarget(value: string, itemId: string): void {
  const url = new URL(value);
  if (url.origin !== PKU_ORIGIN || url.username || url.password || !/^_\d+_\d+$/.test(itemId)
    || !/^_\d+_\d+$/.test(url.searchParams.get("course_id") ?? "")
    || (url.pathname.endsWith("/announcement") && url.searchParams.get("method") !== "search")
    || !/^\/webapps\/blackboard\/(?:execute\/announcement|content\/listContent(?:Editable)?\.jsp)$/i.test(url.pathname)) {
    throw new Error("target source must be a PKU course announcement/content page and an exact item ID");
  }
}

export function selectSourceTarget(html: string, target: NonNullable<ScrapeOptions["targetSource"]>, collectedAt: string): CourseSourceRecord[] {
  validateSourceTarget(target.url, target.itemId);
  const selected = extractCourseSourcesFromHtml(html, {
    course: target.course, section: target.section, sourceUrl: target.url, collectedAt,
  }).filter(source => source.source_item_id === target.itemId);
  if (selected.length !== 1) throw new Error("requested source item was missing or ambiguous; no other sources were read");
  return selected;
}

/** Source records are the primary output; legacy homework fields remain for compatibility. */
export function buildHomeworkScrapeResult(records: HomeworkRecord[], context: {
  asOf: DateTime;
  term: string | null;
  coursesChecked: number;
  sectionsChecked: number;
  sourceRecords?: CourseSourceRecord[];
  allSections?: boolean;
  coverageWarnings?: string[];
  scopeWarning?: string;
}): HomeworkScrapeResult {
  const deduplicated = deduplicateRecords(records);
  const scopeWarning = context.scopeWarning ?? (context.allSections
    ? "Course landing pages, linked content lists and native assignment details were inspected when available; nested folders and other detail pages were not read. Only same-origin teacher PDF/UTF-8 text attachments are supported; unread references retain their status."
    : "Only assignment sections and native assignment details were inspected; course announcements, other sections and nested folders were not read. Only same-origin teacher PDF/UTF-8 text attachments are supported; unread references retain their status.");
  return {
    checked_at: context.asOf.toISO({ suppressMilliseconds: true })!,
    timezone: DEFAULT_TIMEZONE,
    term: context.term,
    courses_checked: context.coursesChecked,
    sections_checked: context.sectionsChecked,
    records_found: deduplicated.length,
    past_records: deduplicated.filter((record) => record.status === "past").length,
    undated_records: deduplicated.filter((record) => record.status === "no_deadline").length,
    source_records: context.sourceRecords ?? [],
    source_records_found: context.sourceRecords?.length ?? 0,
    coverage_warnings: [...new Set([scopeWarning, ...(context.coverageWarnings ?? [])])],
    records: deduplicated,
    upcoming: filterUpcomingHomeworks(deduplicated, context.asOf),
  };
}

type HomeworkContext = { course: string; section: string; sourceUrl: string; asOf: DateTime };
const HOMEWORK_TITLE = /作业|assignment|homework|报告|练习|测验|quiz|\blab\s*\d/i;
const DEADLINE_LABEL = /截止|到期|deadline|\bdue\b|提交期限|提交时间/i;

/** Blackboard item boundaries, not keywords inside prose, define each record. */
export function extractHomeworkRecordsFromHtml(html: string, context: HomeworkContext): HomeworkRecord[] {
  return homeworkView(extractCourseSourcesFromHtml(html, { ...context, collectedAt: context.asOf.toISO()! }), context.asOf);
}

/** Optional legacy view only. Recognition exports always use the full source records. */
function homeworkView(sources: CourseSourceRecord[], asOf: DateTime): HomeworkRecord[] {
  return sources.filter((source) => source.kind === "assignment" || HOMEWORK_TITLE.test(source.title)
    || (source.kind === "announcement" && HOMEWORK_TITLE.test(source.body))).map((source) => {
    const record = makeHomeworkRecord(source.title, source.body ? source.body.split("\n") : [], {
      course: source.course, section: source.section, sourceUrl: source.source_url, asOf,
    });
    if (source.source_item_id) record.source_item_id = source.source_item_id;
    return record;
  });
}

function makeHomeworkRecord(title: string, lines: string[], context: HomeworkContext): HomeworkRecord {
  const window = [title, ...lines];
  const deadlines = window.flatMap((line, position) => {
    if (!DEADLINE_LABEL.test(line)) return [];
    const raw = /^(?:截止(?:日期|时间)?|到期(?:日期|时间)?|deadline|due(?: date)?|提交期限|提交时间)\s*[:：]?$/i.test(line)
      && window[position + 1] ? `${line}\n${window[position + 1]}` : line;
    return [{ raw, at: parseHomeworkDeadline(raw, context.asOf) }];
  });
  const distinctDeadlines = new Set(deadlines.map(({ at }) => at?.toMillis() ?? null));
  const deadlineAt = distinctDeadlines.size === 1 ? deadlines[0].at : null;
  return {
    course: context.course,
    section: context.section,
    title,
    description: lines.length ? window.join("\n") : null,
    source_url: context.sourceUrl,
    deadline_raw: deadlines.length ? deadlines.map(({ raw }) => raw).join("\n") : null,
    deadline: deadlineAt?.toISO({ suppressMilliseconds: true }) ?? null,
    status: deadlineAt ? (deadlineAt > context.asOf ? "upcoming" : "past") : "no_deadline",
  };
}

/** Text-only input has weaker boundaries; live pages use the HTML extractor above. */
export function extractHomeworkRecords(text: string, context: { course: string; section: string; sourceUrl: string; asOf: DateTime }): HomeworkRecord[] {
  // Repeated deadline lines belong to their respective records; do not deduplicate page text.
  const lines = text.split(/\r?\n/).map((line) => line.replace(/\s+/g, " ").trim()).filter(Boolean);
  const records: HomeworkRecord[] = [];
  const homeworkHeading = HOMEWORK_TITLE;
  const deadlineLabel = /截止|到期|deadline|\bdue\b|提交期限|提交时间/i;
  const genericHeading = /^(?:课程)?(?:作业|报告|练习|测验)$|^(?:assignments?|homework|quizzes)$/i;
  const isHeading = (line: string) => homeworkHeading.test(line) && line.length <= 100
    && !/[。；;]|\.(?:pdf|docx?|zip|pptx?)\b/i.test(line)
    && !/^(?:截止|到期|deadline|due\b|提交期限|提交时间|提交要求|本次|请|上第|第\d+章课后)/i.test(line);

  for (let index = 0; index < lines.length; index += 1) {
    if (!isHeading(lines[index])) continue;
    const nextHeading = lines.findIndex((line, position) => position > index && isHeading(line));
    const window = lines.slice(index, nextHeading < 0 ? lines.length : nextHeading);
    // Skip navigation/category headings, but retain templates with a separate title line.
    if (genericHeading.test(lines[index]) && (window.length === 1 || deadlineLabel.test(window[1]))) continue;
    const title = genericHeading.test(window[0]) ? window[1] : window[0];
    records.push(makeHomeworkRecord(title, window.slice(1), context));
  }
  return records;
}

/** An error page or expired login must not masquerade as a successful empty course. */
export async function navigateCoursePage(page: Pick<Page, "goto" | "url" | "locator">, url: string, timeout = 45_000): Promise<void> {
  const response = await collectionStage("navigation", () => page.goto(url, { waitUntil: "domcontentloaded", timeout }));
  if (!response || !response.ok()) throw new CollectionFailure("navigation", "http_error", `course page request failed (HTTP ${response?.status() ?? "unknown"})`);
  const destination = new URL(page.url());
  if (destination.origin !== PKU_ORIGIN || !/^\/webapps\/(?:portal|blackboard|announcement)\//i.test(destination.pathname)
    || await page.locator('input[type="password"]').count()) {
    throw new CollectionFailure("navigation", "session_expired", "course page left the authenticated area; login may have expired");
  }
}

async function login(page: Page, username: string, password: string): Promise<void> {
  await page.goto(PKU_LOGIN_URL, { waitUntil: "domcontentloaded", timeout: 45_000 });
  await page.waitForSelector('input[type="password"]', { state: "visible", timeout: 30_000 }).catch(() => undefined);
  const userField = await firstVisible(page, ['input[name="userName"]', 'input[name="username"]', 'input[id*="user" i]', 'input[placeholder*="User" i]', 'input[type="text"]']);
  const passwordField = await firstVisible(page, ['input[type="password"]']);
  if (!userField || !passwordField) {
    const current = new URL(page.url());
    throw new Error(`PKU campus-card login form was not found at ${current.hostname}${current.pathname}`);
  }
  await userField.fill(username);
  await passwordField.fill(password);
  const submit = await firstVisible(page, ['button[type="submit"]', 'input[type="submit"]', 'button:has-text("Login")', 'button:has-text("登录")', '[role="button"]:has-text("Login")']);
  if (!submit) throw new Error("PKU login submit control was not found");
  await Promise.all([
    page.waitForLoadState("domcontentloaded", { timeout: 30_000 }).catch(() => undefined),
    submit.click(),
  ]);
  await page.waitForURL((url) => {
    const destination = new URL(url);
    return destination.hostname === new URL(PKU_ORIGIN).hostname && /\/webapps\/(?:portal|announcement|blackboard)/i.test(destination.pathname);
  }, { timeout: 30_000 }).catch(() => undefined);
  await page.waitForTimeout(1_000);
  const current = new URL(page.url());
  if (/iaaa\.pku\.edu\.cn$/i.test(current.hostname) || await page.locator('input[type="password"]').count()) throw new Error(`PKU campus-card login did not finish at ${current.hostname}${current.pathname}`);
}

async function firstVisible(page: Page, selectors: string[]) {
  for (const selector of selectors) {
    const candidates = page.locator(selector);
    for (let index = 0; index < await candidates.count(); index += 1) {
      const candidate = candidates.nth(index);
      if (await candidate.isVisible().catch(() => false)) return candidate;
    }
  }
  return null;
}

function discoverCourses(anchors: AnchorInfo[], term: string | null): CourseLink[] {
  const courses = new Map<string, CourseLink>();
  for (const anchor of anchors) {
    const url = new URL(anchor.href, PKU_ORIGIN);
    if (url.origin !== PKU_ORIGIN || !url.pathname.endsWith("/launcher") || !url.searchParams.has("id") || !url.searchParams.has("url")) continue;
    if (term && !anchor.text.includes(term)) continue;
    const name = anchor.text.replace(/^[^:]+:\s*/, "");
    if (name && !courses.has(anchor.href)) courses.set(anchor.href, { name, href: anchor.href, entryText: anchor.text, termLabel: anchor.text.match(/\d{2}(?:\d{2})?-\d{2}(?:\d{2})?学年第[12]学期/)?.[0] });
  }
  return [...courses.values()];
}

function discoverSections(anchors: AnchorInfo[], includeAnnouncements = false): AnchorInfo[] {
  const sections = new Map<string, AnchorInfo>();
  for (const anchor of anchors) {
    const url = new URL(anchor.href, PKU_ORIGIN);
    const announcement = includeAnnouncements && /通知|公告|\bannouncements?\b/i.test(anchor.text)
      && courseAnnouncementTool(url.href);
    if (url.origin !== PKU_ORIGIN || !(SECTION_PATH_PATTERN.test(url.pathname) || announcement)) continue;
    if (!sections.has(anchor.href)) sections.set(anchor.href, anchor);
  }
  return [...sections.values()];
}

function isAssignmentSection(text: string): boolean {
  return /作业|\bassignments?\b|\bhomework\b/i.test(text);
}

/** Decide which pages a course crawl may open before any section navigation occurs. */
export function planCourseCollection(courseName: string, anchors: AnchorInfo[], allSections = false, teachingSections = false): CourseCollectionPlan {
  const available = discoverSections(anchors, teachingSections);
  const assignments = available.filter((section) => isAssignmentSection(section.text));
  return {
    collectAnnouncements: allSections || teachingSections,
    sections: allSections ? available : teachingSections ? available.filter((section) => isAssignmentSection(section.text) || /教学内容|通知|公告|\bannouncements?\b|\bcourse content\b/i.test(section.text)) : assignments,
    warnings: assignments.length ? [] : [`No assignment section was found for course "${courseName}".`],
  };
}

function containsExplicitDate(value: string): boolean {
  return /20\d{2}\s*[年./-]\s*\d{1,2}\s*[月./-]\s*\d{1,2}|20\d{2}[./-]\d{1,2}[./-]\d{1,2}|\d{1,2}\s*月\s*\d{1,2}\s*日?|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}/i.test(value);
}

function deduplicateRecords(records: HomeworkRecord[]): HomeworkRecord[] {
  const result = new Map<string, HomeworkRecord>();
  for (const record of records) {
    const key = record.source_item_id
      ? JSON.stringify([record.course, record.source_item_id])
      : JSON.stringify([record.course, record.title, record.deadline, ...(record.deadline === null ? [record.source_url, record.section, record.deadline_raw, record.description] : [])]);
    const previous = result.get(key);
    if (!previous || (record.description?.length ?? 0) > (previous.description?.length ?? 0)) result.set(key, record);
  }
  return [...result.values()];
}

async function readCredentials(path: string): Promise<{ username: string; password: string }> {
  const lines = (await readFile(path, "utf8")).split(/\r?\n/);
  const username = lines[0]?.trim();
  const password = lines[1];
  if (!username || !password) throw new Error(`credential file ${path} must contain username and password on its first two lines`);
  return { username, password };
}

async function launchBrowser(executablePath?: string): Promise<Browser> {
  const candidates = [
    executablePath,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
  ].filter((candidate): candidate is string => Boolean(candidate));
  const installed = candidates.find((candidate) => existsSync(candidate));
  return chromium.launch({ headless: true, ...(installed ? { executablePath: installed } : {}) });
}

async function closeBrowser(browser: Browser): Promise<void> {
  await browser.close().catch(() => undefined);
}
