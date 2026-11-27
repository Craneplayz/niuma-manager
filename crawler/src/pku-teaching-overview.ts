import { load, type Cheerio, type CheerioAPI } from "cheerio";
import type { AnyNode } from "domhandler";
import type { APIRequestContext } from "playwright";
import { CollectionFailure } from "./collection-failure.js";

export const PKU_ORIGIN = "https://course.pku.edu.cn";
export const PKU_PORTAL_URL = PKU_ORIGIN + "/webapps/portal/execute/tabs/tabAction?tab_tab_group_id=_1_1";
export type TeachingPage = { html: string; url: string };
export class UnrenderedTeachingPage extends Error {}

/** Keep account requests bounded and keep source order independent of response order. */
export async function mapPages<T, U>(items: T[], concurrency: 1 | 3, read: (item: T) => Promise<U>): Promise<U[]> {
  const result: U[] = [];
  for (let start = 0; start < items.length; start += concurrency) {
    // Finish the batch before propagating a failure or switching to the browser.
    const batch = await Promise.allSettled(items.slice(start, start + concurrency).map(read));
    for (const item of batch) {
      if (item.status === "rejected") throw item.reason;
      result.push(item.value);
    }
  }
  return result;
}

const readOnlyPage = /^\/webapps\/(?:portal\/execute\/tabs\/tabAction|(?:blackboard|announcement)\/execute\/(?:launcher|announcement|courseMain)|blackboard\/content\/(?:launchLink|listContent(?:Editable)?)\.jsp|bb-mygrades-BBLEARN\/myGrades|assignment\/(?:uploadAssignment|viewSubmissionHistory))$/i;

/** Follow authenticated read-only page redirects explicitly, never forwarding the request to a file or another host. */
export async function readTeachingPage(request: Pick<APIRequestContext, "get">, href: string): Promise<TeachingPage> {
  let url = new URL(href);
  for (let hop = 0; hop <= 6; hop++) {
    if (url.origin !== PKU_ORIGIN || url.username || url.password || !readOnlyPage.test(url.pathname)
      || (isAnnouncement(url.href) && ![null, "search"].includes(url.searchParams.get("method"))))
      throw new CollectionFailure("navigation", "session_expired");
    const response = await request.get(url.href, { maxRedirects: 0, timeout: 15_000 });
    try {
      const headers = response.headers();
      if ([301, 302, 303, 307, 308].includes(response.status()) && headers.location) {
        url = new URL(headers.location, url);
        continue;
      }
      if (!response.ok()) throw new CollectionFailure("navigation", "http_error");
      if (!headers["content-type"]?.includes("text/html") || headers["content-disposition"]?.includes("attachment"))
        throw new CollectionFailure("navigation", "http_error");
      const html = await response.text();
      if (/<input\b[^>]*\btype\s*=\s*["']?password\b/i.test(html)) throw new CollectionFailure("navigation", "session_expired");
      return { html, url: url.href };
    } finally {
      await response.dispose();
    }
  }
  throw new CollectionFailure("navigation", "http_error");
}

export function pageAnchors($: CheerioAPI, root: Cheerio<AnyNode>, base: string) {
  const documentBase = $("base[href]").first().attr("href");
  const baseUrl = documentBase && URL.canParse(documentBase, base) ? new URL(documentBase, base).href : base;
  const anchors = [];
  for (const node of root.find("a[href]").toArray()) {
    const href = $(node).attr("href")!;
    const text = $(node).text().replace(/\s+/g, " ").trim();
    if (text && URL.canParse(href, baseUrl)) anchors.push({ text, href: new URL(href, baseUrl).href });
  }
  return anchors;
}

export function courseEntryId(href: string) {
  const id = new URL(href).searchParams.get("id") ?? "";
  // Some Blackboard versions put a serialized PkId into the launcher URL.
  return /^_\d+_\d+$/.test(id) ? id : id.match(/^PkId\{key=(_\d+_\d+),/)?.[1];
}

type Course = { href: string; name: string; entryText: string };
type AnnouncementIndex = Map<string, string[]>;
const isAnnouncement = (url: string) => /^\/webapps\/(?:blackboard|announcement)\/execute\/announcement$/i.test(new URL(url).pathname);

export function courseAnnouncementTool(href: string) {
  const url = new URL(href);
  return isAnnouncement(href) || (url.pathname === "/webapps/blackboard/content/launchLink.jsp" && url.searchParams.get("tool_type") === "TOOL");
}

/** Only a confirmed all-announcements view can replace the course announcement tool. */
export async function allAnnouncements(portal: TeachingPage, roster: Course[], read: (url: string) => Promise<TeachingPage>): Promise<AnnouncementIndex | null> {
  const $ = load(portal.html);
  const allView = (url: URL) => url.origin === PKU_ORIGIN && isAnnouncement(url.href)
    && url.searchParams.get("viewChoice") === "3" && !url.searchParams.has("course_id")
    && !url.searchParams.has("searchSelect");
  const href = pageAnchors($, $.root(), portal.url).find(link => {
    const url = new URL(link.href);
    return allView(url) && /更多|more|查看所有|view all/i.test(link.text);
  })?.href;
  if (!href) return null;
  let page: TeachingPage;
  try { page = await read(href); } catch { return null; }
  if (!allView(new URL(page.url))) return null;
  const a = load(page.html);
  if (!a("#announcementList").length || a('[rel=next], .paging a, .pagingcontrols a, .pagination a').length) return null;
  const index: AnnouncementIndex = new Map();
  for (const course of roster) index.set(course.href, []);
  for (const li of a("#announcementList > li").toArray()) {
    const row = a(li);
    const owner = row.find(".announcementInfo p").last().clone();
    owner.children("span").remove();
    const label = owner.text().trim();
    if (!label || !row.attr("id") || !row.children("h3").length) return null;
    const matches = roster.filter(c => c.name === label || c.entryText === label);
    // An unfamiliar label might be a current class under a different display name.
    // Let its course-scoped listing establish ownership instead of guessing.
    if (matches.length !== 1) return null;
    index.get(matches[0].href)!.push(a.html(li));
  }
  return index;
}
