/** Synthetic HTML -> production crawler projection; no campus login or private data. */
import { extractCourseSourcesFromHtml } from "../src/pku-source.js";
import { enrichAssignmentSource, parseAssignmentDetailHtml } from "../src/pku-assignment.js";
import { toTeachingRequests } from "../src/pku-teaching.js";

const detailUrl = "https://course.pku.edu.cn/webapps/assignment/uploadAssignment?course_id=synthetic&content_id=synthetic";
function source(id: string, body: string, native = false) {
  const title = native ? `<a href="${detailUrl}">第二章书面作业</a>` : "课程作业通知";
  return extractCourseSourcesFromHtml(`<ul id="content_listContainer"><li id="${id}"><div class="item" id="${id}"><h3>${title}</h3></div><div class="details">${body}</div></li></ul>`, {
    course: "离散数学（合成测试）", section: "课程作业",
    sourceUrl: "https://course.pku.edu.cn/webapps/blackboard/content/listContent.jsp?course_id=synthetic",
    collectedAt: "2026-10-09T12:00:00+08:00",
  })[0];
}
const urgency=source('urgency','请于2026.9.29 9:00前交一份课程内容回顾，内容范围为目前学过的全部内容（包括当次课），请大家尽快完成，会按比例计入总评。');
urgency.collected_at='2026-09-25T12:00:00+08:00';
const detail = enrichAssignmentSource(source('detail','提交第二章书面作业的 PDF。',true),detailUrl,
  parseAssignmentDetailHtml(`<h1>上载作业：合成第二章作业</h1><form id="uploadAssignmentFormId"><div id="step1">
    <h3>作业信息</h3><div class="metaSection"><div id="due" class="metaLabel">到期日期</div>
    <div class="metaField" aria-describedby="due">2026年10月25日 星期日<span>下午11:59</span></div></div>
    <div class="vtbegenerated">请完成第二章书面作业并提交 PDF。</div></div>
    <div id="step2"><textarea>合成私人草稿，不得读取。</textarea></div></form>`,detailUrl));
// This entry is a plain assignment-section item, with no native submission page.
const yearless = source('yearless','将作业命名为 HW2-姓名-学号.pdf，通过邮件提交至 test@example.test，本次作业 deadline 为 10.23。');
yearless.course_term_label='26-27学年第1学期';
const supplement=source('supplement','请于2026年10月23日前提交课程报告。附件仅供参考。<a href="/bbcswebdav/courses/synthetic/reference.docx">参考资料.docx</a>');
const required=source('required','请提交课程报告，具体提交日期以附件为准。<a href="/bbcswebdav/courses/synthetic/requirements.docx">提交要求.docx</a>');
console.log(JSON.stringify(toTeachingRequests([urgency,detail,yearless,supplement,required],'synthetic-live')));
