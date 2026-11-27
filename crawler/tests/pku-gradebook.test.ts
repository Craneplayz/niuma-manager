import type { APIRequestContext } from "playwright";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { addGradebookItems, gradebookItems, resolveGradebookLinks } from "../src/pku-gradebook.js";
import { toTeachingCaptures } from "../src/pku-teaching.js";
import { extractCourseSourcesFromHtml } from "../src/pku-source.js";
import { enrichAssignmentSource } from "../src/pku-assignment.js";

const gradeUrl = new URL("https://course.pku.edu.cn/webapps/bb-mygrades-BBLEARN/myGrades?course_id=_1_1");
const contentUrl = "https://course.pku.edu.cn/webapps/blackboard/content/listContent.jsp?course_id=_1_1&content_id=_2_1";
const detailUrl = "https://course.pku.edu.cn/webapps/assignment/uploadAssignment?course_id=_1_1&content_id=_3_1";
const collectedAt = "2026-11-01T08:00:00+08:00";
function gradeRow(id: number, title: string, category: string, stamp: number, cls = "", href = "") {
  return `<div role="row" class="sortable_item_row ${cls}" duedate="${stamp}">
    <div class="cell gradable"><span id="_${id}_1">${title}</span><div class="itemCat">${category}</div>
    ${href && `<a href="${href}">详情</a>`}</div><div class="grade">PRIVATE_SCORE</div></div>`;
}
describe("teaching gradebook capture", () => {
  it("reads only native assignment fields and keeps undated items", () => {
    const html = gradeRow(5,"Lab 0","作业",0) + gradeRow(6,"报告","作业",Date.UTC(2026,11,1,17,30),"submitted_item_row")
      + gradeRow(7,"总计","",0) + gradeRow(8,"测验","测试",0);
    const rows = gradebookItems(html,gradeUrl);
    const sources = addGradebookItems([], rows, gradeUrl, "合成课", collectedAt);
    const captures = toTeachingCaptures(sources);
    expect(captures.map(c => c.request.records[0].platform_item)).toEqual([
      {kind:"gradebook_assignment", due:null, submitted:false},
      {kind:"gradebook_assignment", due:"2026-12-02T01:30:00+08:00", submitted:true}]);
    expect(JSON.stringify(captures)).not.toContain("PRIVATE_SCORE");
  });
  it("joins an unlinked grade row by its course content metadata even with different titles", async () => {
    const listing = extractCourseSourcesFromHtml(`<ul id="content_listContainer"><li><div class="item" id="_3_1"><h3><a href="${detailUrl}">实验说明</a></h3></div><div class="details">提交报告</div></li></ul>`,
      {course:"合成课", section:"课程作业", sourceUrl:contentUrl, collectedAt});
    const rows = gradebookItems(gradeRow(5,"Lab 0","作业",0),gradeUrl);
    const metadata = {id:"_3_1", contentHandler:{id:"resource/x-bb-assignment", gradeColumnId:"_5_1"}};
    const calls: string[] = [];
    const request = {get: async (url: string) => {
      calls.push(url);
      return {ok:()=>true, json:async()=>metadata, dispose:async()=>{}};
    }} as Pick<APIRequestContext, "get">;
    metadata.id="_4_1";
    await resolveGradebookLinks(request, listing, rows, gradeUrl);
    expect(rows[0].contentId).toBeNull();
    metadata.id="_3_1";
    await resolveGradebookLinks(request, listing, rows, gradeUrl);
    expect(calls[0]).toBe("https://course.pku.edu.cn/learn/api/public/v1/courses/_1_1/contents/_3_1?fields=id%2CcontentHandler");
    expect(addGradebookItems(listing,rows,gradeUrl,"合成课",collectedAt)).toEqual([]);
    expect(listing[0].gradebook?.id).toBe("_5_1");
  });
  it("uses a detail's own facts when equal names do not prove equal IDs", () => {
    const listing = extractCourseSourcesFromHtml(`<ul id="content_listContainer"><li id="item"><div class="item" id="_3_1"><h3><a href="${detailUrl}">第四章作业</a></h3></div><div class="details">提交报告</div></li></ul>`,
      {course:"合成课", section:"课程作业", sourceUrl:contentUrl,collectedAt});
    const unrelated = gradebookItems(gradeRow(5,"第四章作业","作业",0,"submitted_item_row"),gradeUrl);
    const unlinked = addGradebookItems(listing,unrelated,gradeUrl,"合成课",collectedAt);
    expect(unlinked.map(source => source.source_item_id)).toEqual(["_5_1"]);
    expect(listing[0].gradebook).toBeUndefined();
    const detailed = enrichAssignmentSource(listing[0],detailUrl,{state:"active",title:"第四章作业",description:"请提交报告。",due_raw:"2026年12月2日 星期三 上午1:30"});
    const capture = toTeachingCaptures([detailed], "synthetic-native")[0];
    const fixture = new URL("../../contracts/examples/teaching-platform-native.json", import.meta.url);
    expect(capture).toEqual(JSON.parse(readFileSync(fixture, "utf8")));
    expect(capture.request.records[0].platform_item).toEqual({kind:"native_assignment",due:"2026-12-02T01:30:00+08:00",submitted:false});
    expect(capture.assignment?.state).toBe("active");
    const exact = gradebookItems(gradeRow(5,"新标题","作业",0,"",detailUrl),gradeUrl);
    expect(addGradebookItems(listing,exact,gradeUrl,"合成课",collectedAt)).toEqual([]);
    expect(listing[0].gradebook?.id).toBe("_5_1");
    const [gradeOnlyLink] = addGradebookItems([], exact, gradeUrl, "合成课", collectedAt);
    expect(gradeOnlyLink.input_id).toBe(listing[0].input_id);
    expect(gradeOnlyLink.references[0].url).toBe(detailUrl);
    const fromGradebook = enrichAssignmentSource(gradeOnlyLink, detailUrl, {state:"active", title:"第四章作业",description:"请提交报告。",due_raw:null});
    expect(toTeachingCaptures([fromGradebook])[0].request.records[0].platform_item).toMatchObject({
      kind:"native_assignment", gradebook_id:"_5_1",
    });
  });
});
