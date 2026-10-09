import { describe, expect, it } from "vitest";
import { toTeachingRequests, toTeachingCaptures } from "../src/pku-teaching.js";
import { extractCourseSourcesFromHtml } from "../src/pku-source.js";
import { planCourseCollection } from "../src/pku-homework.js";

const source = () => extractCourseSourcesFromHtml('<ul id="content_listContainer"><li id="item"><div class="item" id="_1"><h3>作业 2</h3></div><div class="details">10月23日前提交作业。参考资料见附件。<a href="/bbcswebdav/courses/test/extra.pdf">参考.pdf</a></div></li></ul>',
  { course: "测试课程", section: "课程作业", sourceUrl: "https://course.pku.edu.cn/webapps/blackboard/content/listContent.jsp?course_id=_1", collectedAt: "2026-10-09T10:00:00+08:00" })[0];

describe("native teaching integration", () => {
  it("projects announcementForm teacher text as complete instead of a heading-only summary", () => {
    const html='<form id="announcementForm"><ul id="announcementList"><li id="notice"><h3>作业已发布，截止时间为2026-11-17 23:30</h3><div class="details"><div class="vtbegenerated"><p>请同学们按时完成。</p></div></div></li></ul></form>';
    const input=extractCourseSourcesFromHtml(html, {course:'测试课程',section:'课程通知',sourceUrl:'https://course.pku.edu.cn/webapps/blackboard/execute/announcement?course_id=_1',collectedAt:'2026-10-09T10:00:00+08:00'})[0];
    const record=toTeachingRequests([input])[0].records[0].record;
    expect(record.body_status).toBe('complete');
    expect(record.text).toContain('请同学们按时完成。');
    expect(record.text).toContain('2026-11-17 23:30');
    expect(record.references).toEqual([]);
  });
  it("retains submitted state only for a bound native assignment", () => {
    const input=source();input.kind='assignment';input.assignment={state:'submitted',due_raw:null};
    input.references.unshift({kind:'detail',label:'作业',url:'https://course.pku.edu.cn/webapps/assignment/uploadAssignment?course_id=_1&content_id=_1',content_read:false,read_status:'partial',reason:'submission_history_only'});
    const capture=toTeachingCaptures([input])[0];
    expect(capture.contract_version).toBe('teaching-capture/0.1');
    expect(capture.assignment).toEqual({course_id:'_1',content_id:'_1',state:'submitted',basis:'review_submission_history_title'});
    expect(capture.request.contract_version).toBe('0.4');
    expect(capture.request.records[0].record).not.toHaveProperty('assignment');
    for(const kind of ['announcement','content'] as const) expect(toTeachingCaptures([{...input,kind}])[0].assignment).toBeNull();
    input.references[0].url=input.references[0].url.replace('content_id=_1','content_id=_2');
    expect(toTeachingCaptures([input])[0].assignment).toBeNull();
  });
  it("never infers submitted from teacher text", () => {
    const input=source();input.body='已提交，请等待批改。';
    expect(toTeachingCaptures([input])[0].assignment).toBeNull();
  });
  it("keeps complete body separate from unread and extracted attachments", () => {
    const input=source(); input.teacher_body=input.body;input.body+='\n附件正文：合成全文';
    const record=toTeachingRequests([input])[0].records[0].record;
    expect(record.body_status).toBe('complete');expect(record.text).not.toContain('附件正文');
    expect(record.references[0].read_state).toBe('unread');expect(record.references[0].text).toBeNull();
  });
  it("binds an active native assignment in its observed current term", () => {
    const input=source();input.course_term_label='26-27学年第1学期';input.assignment={state:'active',due_raw:'10月23日'};
    expect(toTeachingRequests([input])[0].records[0].record.course_term?.applies_to).toBe('current_assignment');
    input.collected_at='2028-10-09T10:00:00+08:00';
    expect(toTeachingRequests([input])[0].records[0].record.course_term?.applies_to).toBe('course_material');
  });
  it("binds current-term assignment-section entries even when submission is by email", () => {
    const input=source();input.course_term_label='26-27学年第1学期';
    const term=toTeachingRequests([input])[0].records[0].record.course_term;
    expect(term?.applies_to).toBe('current_assignment');
    expect(term?.evidence).toContainEqual({part_id:'source_metadata',quote:'栏目：课程作业'});
    for(const section of ['教学内容','课程通知','历年作业','课程介绍']) {
      expect(toTeachingRequests([{...input,section}])[0].records[0].record.course_term?.applies_to).toBe('course_material');
    }
    for(const state of ['submitted','unknown'] as const) {
      expect(toTeachingRequests([{...input,assignment:{state,due_raw:null}}])[0].records[0].record.course_term?.applies_to).toBe('course_material');
    }
  });
  it("selects only the three requested sections and prioritizes across courses", () => {
    const anchors=['课程作业','课程通知','教学内容','课程介绍'].map((text,i)=>({text,href:`https://course.pku.edu.cn/webapps/blackboard/content/listContent.jsp?id=${i}`}));
    expect(planCourseCollection('课程',anchors,false,true).sections.map(s=>s.text)).toEqual(['课程作业','课程通知','教学内容']);
    const records=['教学内容','课程通知','课程作业'].map(section=>({...source(),section}));
    expect(toTeachingRequests(records).map(r=>r.records[0].source.label)).toEqual(['测试课程 · 课程作业','测试课程 · 课程通知','测试课程 · 教学内容']);
  });
  it("unread native detail remains incomplete even with listing text", () => {
    const input=source();input.references[0].kind='detail';
    expect(toTeachingRequests([input])[0].records[0].record.body_status).toBe('partial');
  });
  it("opens the dedicated announcements route even when it is not the course landing page", () => {
    const url='https://course.pku.edu.cn/webapps/blackboard/execute/announcement?course_id=_1&method=search';
    const plan=planCourseCollection('课程',[{text:'课程通知',href:url},{text:'无关页面',href:'https://example.test/content/listContent.jsp'}],false,true);
    expect(plan.sections).toEqual([{text:'课程通知',href:url}]);
  });
});
