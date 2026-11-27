import type { APIRequestContext, Page } from "playwright";
import { DateTime } from "luxon";
import { describe, expect, it, vi } from "vitest";
import { allAnnouncements, courseEntryId, PKU_ORIGIN, PKU_PORTAL_URL, readTeachingPage } from "../src/pku-teaching-overview.js";
import { collectCourseSources } from "../src/pku-homework.js";
import { courseItemInputId } from "../src/pku-source.js";

const globalUrl = PKU_ORIGIN + "/webapps/blackboard/execute/announcement?method=search&viewChoice=3";
const link = (path: string, label: string) => `<a href="${path}">${label}</a>`;
const courseUrl = (id: string) => PKU_ORIGIN + "/webapps/blackboard/execute/launcher?id=" + id + "&url=";
const course = (id: string, name: string) => ({ href: courseUrl(id), name, entryText: "2026: " + name });
const current = course("_1_1", "English seminar");
const archived = course("_2_1", "Archived seminar");
const portal = {
  url: PKU_PORTAL_URL,
  html: '<div class="portlet"><h2>当前学期课程</h2>' + link(current.href, current.entryText) + "</div>"
    + link(archived.href, archived.entryText) + link(globalUrl, "更多公告"),
};
const notice = (id: string, owner: string) => `<li id="${id}"><h3>Room change</h3><div class="details"><p>Posted on: 2026-09-17</p><div class="vtbegenerated">Use room 417.</div></div><div class="announcementInfo"><p><span>Course: </span>${owner}</p></div></li>`;
const notices = (...rows: string[]) => '<ul id="announcementList">' + rows.join("") + "</ul>";
const feed = (body: string) => async () => ({ html: body, url: globalUrl });

describe("all-course announcements", () => {
  it("maps the same notice title in two terms through exact course ownership", async () => {
    const index = await allAnnouncements(portal, [current, archived],
      feed(notices(notice("_11_1", current.name), notice("_22_1", archived.name))));
    expect(index?.get(current.href)).toEqual([expect.stringContaining('_11_1')]);
    expect(index?.get(archived.href)).toEqual([expect.stringContaining('_22_1')]);
  });
  it("does not substitute a recent or course-filtered page for the full feed", async () => {
    for (const filtered of [globalUrl.replace("viewChoice=3", "viewChoice=1"), globalUrl + "&searchSelect=_1_1"]) {
      const read = vi.fn(feed(notices()));
      expect(await allAnnouncements({ ...portal, html: link(filtered, "更多公告") }, [current], read)).toBeNull();
      expect(read).not.toHaveBeenCalled();
      expect(await allAnnouncements(portal, [current], async () => ({html: notices(), url: filtered}))).toBeNull();
    }
  });
  it.each([
    ["unknown class", notices(notice("_1_1", "Not resolved")), [current, archived]],
    ["same display name across terms", notices(notice("_1_1", current.name)), [current, { ...archived, name: current.name }]],
    ["page link to more rows", notices() + '<a rel="next" href="?page=2">Next</a>', [current]],
    ["unrecognized markup", "<h2>No data</h2>", [current]],
  ] as const)("falls back to course listings on %s", async (_label, markup, courses) => {
    expect(await allAnnouncements(portal, [...courses], feed(markup))).toBeNull();
  });
});

function fakeResponse(html: string, location?: string) {
  return { status: () => location ? 302 : 200, ok: () => !location,
    headers: () => ({ "content-type": "text/html", location }), text: async () => html, dispose: vi.fn() };
}

describe("authenticated overview navigation", () => {
  it.each(["https://example.org/logout", "/bbcswebdav/pid-1/private.pdf", "/webapps/blackboard/execute/announcement?method=delete", "/webapps/bb-sso-BBLEARN/login.html"])(
    "blocks redirect before requesting %s", async location => {
      const get = vi.fn().mockResolvedValue(fakeResponse("", location));
      await expect(readTeachingPage({ get } as unknown as APIRequestContext, PKU_PORTAL_URL)).rejects.toMatchObject({stage: "navigation"});
      expect(get).toHaveBeenCalledTimes(1);
    });
  it("recognizes both launcher ID formats", () => {
    expect(courseEntryId(current.href)).toBe("_1_1");
    expect(courseEntryId(courseUrl(encodeURIComponent("PkId{key=_99_1, dataType=blackboard.data.course.Course}")))).toBe("_99_1");
  });

  it("combines scoped notices, gradebook-only and teacher-posted assignments, and a client-rendered native detail", async () => {
    const landingUrl = PKU_ORIGIN + "/webapps/blackboard/execute/announcement?method=search&course_id=_1_1";
    const assignmentList = PKU_ORIGIN + "/webapps/blackboard/content/listContent.jsp?course_id=_1_1&content_id=_8_1";
    const announcementMenu = PKU_ORIGIN + "/webapps/blackboard/content/launchLink.jsp?course_id=_1_1&tool_type=TOOL";
    const landing = '<ul id="courseMenuPalette_contents"><li>' + link(announcementMenu, "课程通知") + '</li><li>' + link(assignmentList, "Assignments") + "</li></ul>" + notices();
    const grade = '<div class="sortable_item_row" role="row"><div class="cell gradable"><div class="itemCat">Assignment</div><span id="_40_1">Gradebook-only</span></div></div>';
    const detailUrl = PKU_ORIGIN + "/webapps/assignment/uploadAssignment?course_id=_1_1&content_id=_81_1";
    const listed = '<ul id="content_listContainer"><li id="contentListItem:_80_1"><div class="item" id="_80_1"><h3>Essay</h3></div><div class="details">Deliver in person.</div></li>'
      + '<li id="contentListItem:_81_1"><div class="item" id="_81_1"><h3>' + link(detailUrl, "Lab") + '</h3></div><div class="details">Read and submit.</div></li></ul>';
    const get = vi.fn(async (url: string) => {
      if (url === PKU_PORTAL_URL) return fakeResponse(portal.html);
      if (url === globalUrl) return fakeResponse(notices(notice("_10_1", current.name)));
      if (url === current.href) return fakeResponse("", landingUrl);
      if (url === landingUrl) return fakeResponse(landing);
      if (url === assignmentList) return fakeResponse(listed);
      if (url === detailUrl) return fakeResponse("<main></main>");
      if (new URL(url).pathname.endsWith("/myGrades")) return fakeResponse(grade);
      throw Error("unexpected page: " + url);
    });
    const goto = vi.fn().mockResolvedValue(fakeResponse(""));
    const page = {
      context: () => ({ request: { get } }), goto, url: () => detailUrl, locator: () => ({count: async () => 0}),
      content: async () => "<h1>Review Submission History: Lab</h1>",
    } as unknown as Page;
    const run = await collectCourseSources(page, {pageOnly:true, teachingSections:true}, DateTime.fromISO("2026-09-20"), "26-27学年第1学期", true);
    expect(run.courses_checked).toBe(1);
    expect(run.sections_checked).toBe(1);
    expect(run.source_records.map(r => r.title)).toEqual(["Room change", "Essay", "Lab", "Gradebook-only"]);
    expect(run.source_records[0]).toMatchObject({ source_url: landingUrl + "#_10_1", course: current.name, input_id: courseItemInputId(PKU_ORIGIN,"_1_1","announcement","_10_1"), body: "Use room 417." });
    expect(run.source_records.find(r => r.title === "Lab")?.assignment?.state).toBe("submitted");
    expect(goto).toHaveBeenCalledTimes(1);
    expect(get.mock.calls.map(([url]) => url)).not.toContain(announcementMenu);
  });
});
