import { extractCourseSourcesFromHtml, deduplicateCourseSources, type CourseSourceRecord } from "./pku-source.js";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { DateTime } from "luxon";
import { chromium, type Browser, type Page } from "playwright";
import { DEFAULT_TIMEZONE, parseEventTime } from "./core/time.js";
import { enrichAssignmentSources } from "./pku-assignment.js";
import { authenticatedAttachmentReader, enrichSourceAttachments } from "./pku-attachments.js";

const PKU_ORIGIN = "https://course.pku.edu.cn";
const PKU_LOGIN_URL = `${PKU_ORIGIN}/webapps/bb-sso-BBLEARN/login.html`;
const PKU_PORTAL_URL = `${PKU_ORIGIN}/webapps/portal/execute/tabs/tabAction?tab_tab_group_id=_1_1`;
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
  const credentials = await readCredentials(options.secretPath ?? process.env.PKU_SECRET_PATH ?? "secret");
  const browser = await launchBrowser(options.executablePath ?? process.env.PLAYWRIGHT_EXECUTABLE_PATH);
  const context = await browser.newContext({ locale: "zh-CN", timezoneId: DEFAULT_TIMEZONE });
  const page = await context.newPage();

  try {
    await login(page, credentials.username, credentials.password);
    if (options.targetSource) {
      const target = options.targetSource;
      await navigateCoursePage(page, target.url);
      const selected = selectSourceTarget(await page.content(), target, asOf.toISO()!);
      const detailed = await enrichAssignmentSources(page, selected);
      const sourceRecords = await enrichSourceAttachments(detailed, authenticatedAttachmentReader(context), process.env.PKU_ATTACHMENT_DIR);
      return buildHomeworkScrapeResult(homeworkView(sourceRecords, asOf), {
        asOf, term: null, coursesChecked: 1, sectionsChecked: 1, sourceRecords,
        scopeWarning: "Only the requested source item and its references were inspected.",
      });
    }
    await navigateCoursePage(page, PKU_PORTAL_URL);
    await page.waitForTimeout(2_000);

    const courses = discoverCourses(await collectAnchors(page), term);
    const sources: CourseSourceRecord[] = [];
    const coverageWarnings: string[] = [];
    let sectionsChecked = 0;

    for (const course of courses) {
      await navigateCoursePage(page, course.href);
      await page.waitForTimeout(700);
      const coursePageUrl = page.url();
      const firstSource = sources.length;
      const plan = planCourseCollection(course.name, await collectAnchors(page), options.allSections ?? false, options.teachingSections ?? false);
      coverageWarnings.push(...plan.warnings);
      if (plan.collectAnnouncements) {
        const landingSources = extractCourseSourcesFromHtml(await page.content(), {
          course: course.name,
          courseEntry: { text: course.entryText, url: course.href },
          section: "课程通知",
          sourceUrl: coursePageUrl,
          collectedAt: DateTime.now().setZone(DEFAULT_TIMEZONE).toISO()!,
        });
        sources.push(...(options.teachingSections ? landingSources.filter(source => source.kind === "announcement") : landingSources));
      }

      for (const section of plan.sections) {
        sectionsChecked += 1;
        await navigateCoursePage(page, section.href, 30_000);
        await page.waitForTimeout(500);
        sources.push(...extractCourseSourcesFromHtml(await page.content(), {
          course: course.name,
          courseEntry: { text: course.entryText, url: course.href },
          section: section.text,
          sourceUrl: page.url(),
          collectedAt: DateTime.now().setZone(DEFAULT_TIMEZONE).toISO()!,
        }));
      }
      for (const source of sources.slice(firstSource)) source.course_term_label = course.termLabel;
    }

    const detailed = await enrichAssignmentSources(page, deduplicateCourseSources(sources));
    const sourceRecords = await enrichSourceAttachments(detailed, authenticatedAttachmentReader(context), process.env.PKU_ATTACHMENT_DIR);
    return buildHomeworkScrapeResult(homeworkView(sourceRecords, asOf), {
      asOf, term, coursesChecked: courses.length, sectionsChecked, sourceRecords,
      allSections: options.allSections ?? false, coverageWarnings,
    });
  } finally {
    await closeBrowser(browser);
  }
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
  const response = await page.goto(url, { waitUntil: "domcontentloaded", timeout });
  if (!response || !response.ok()) throw new Error(`course page request failed (HTTP ${response?.status() ?? "unknown"})`);
  const destination = new URL(page.url());
  if (destination.origin !== PKU_ORIGIN || !/^\/webapps\/(?:portal|blackboard|announcement)\//i.test(destination.pathname)
    || await page.locator('input[type="password"]').count()) {
    throw new Error("course page left the authenticated area; login may have expired");
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

async function collectAnchors(page: Page): Promise<AnchorInfo[]> {
  return page.locator("a").evaluateAll((elements) => elements.map((element) => ({
    text: (element.textContent ?? "").replace(/\s+/g, " ").trim(),
    href: (element as HTMLAnchorElement).href,
  })).filter((item) => item.text && item.href));
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
      && /\/webapps\/(?:blackboard|announcement)\/execute\/announcement$/i.test(url.pathname);
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
    sections: allSections ? available : teachingSections ? available.filter((section) => isAssignmentSection(section.text) || /教学内容|课程通知|公告|\bannouncements?\b|\bcourse content\b/i.test(section.text)) : assignments,
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
