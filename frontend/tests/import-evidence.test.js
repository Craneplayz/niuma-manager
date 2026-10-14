import test from "node:test";
import assert from "node:assert/strict";
import {
  candidateEvidenceMarkup,
  failureSourcesMarkup,
} from "../src/import-evidence.js";

const record = (input_id, overrides = {}) => ({
  input_id,
  text: "请提交报告。\n格式要求：PDF，附件中还有完整评分标准。",
  source_label: "第二次作业通知",
  source_url: "https://course.example.test/notice/2",
  platform: "teaching_web",
  course_or_project: "概率论",
  ...overrides,
});
const item = (overrides = {}) => ({
  candidate: {
    input_id: "notice-1",
    evidence: [{ quote: "请提交报告。" }],
    sources: [],
    ...overrides,
  },
  batch: { request: { records: [record("notice-1")] } },
});

test("candidate excerpts retain an independent expandable complete notification", () => {
  const candidate = item({ evidence: [{ quote: "请提交报告。" }, { quote: "请提交报告。" }] });
  candidate.batch.request.records[0].text += '\n<script>alert("original")</script>';
  const html = candidateEvidenceMarkup(candidate);
  assert.match(html, /识别摘录/);
  assert.equal((html.match(/<blockquote>/g) || []).length, 1);
  assert.match(html, /<details><summary>查看完整通知原文<\/summary><pre>/);
  assert.ok(html.includes("格式要求：PDF，附件中还有完整评分标准。"));
  assert.match(html, /&lt;script&gt;alert\(&quot;original&quot;\)&lt;\/script&gt;/);
  assert.doesNotMatch(html, /<script>/);
  assert.doesNotMatch(html, /<details[^>]*\bopen\b/);
});

test("candidate sources include record and candidate URLs once with safe labels", () => {
  const candidate = item({ sources: [
    { url: "https://course.example.test/notice/2", label: "重复来源", platform: "teaching_web" },
    { url: "https://mail.example.test/notice", label: '<img src="x"> 邮件', platform: "email" },
    ...["javascript:alert(1)", "data:text/html,test", "file:///private/input.txt", "https://user:secret@example.test/", "/relative"].map((url) => ({ url, label: "不安全来源" })),
  ] });
  const html = candidateEvidenceMarkup(candidate);
  assert.equal((html.match(/<a /g) || []).length, 2);
  assert.match(html, /打开教学网原页面/);
  assert.match(html, /打开来源页面/);
  assert.match(html, /&lt;img src=&quot;x&quot;&gt; 邮件/);
  assert.equal((html.match(/rel="noopener noreferrer"/g) || []).length, 2);
  assert.doesNotMatch(html, /javascript:|data:text|file:\/\/|user:secret|不安全来源|<img/);
});

test("full original is also available without excerpts, and missing text is explicit", () => {
  const candidate = item({ evidence: [] });
  assert.match(candidateEvidenceMarkup(candidate), /查看完整通知原文/);
  candidate.batch.request.records[0].text = "";
  assert.match(candidateEvidenceMarkup(candidate), /原文暂不可用/);
  assert.doesNotMatch(candidateEvidenceMarkup(candidate), /<pre>/);
});

test("deadline basis and review reasons remain escaped without duplicates", () => {
  const html = candidateEvidenceMarkup(item({
    deadline: { basis: "<日期有疑问>" },
    review_reasons: ["<日期有疑问>", "核对 & 年份", "核对 & 年份"],
  }));
  assert.equal((html.match(/&lt;日期有疑问&gt;/g) || []).length, 1);
  assert.equal((html.match(/核对 &amp; 年份/g) || []).length, 1);
});

test("partial failures show only affected source identities and safe links", () => {
  const html = failureSourcesMarkup({
    request: { records: [record("ok", { source_label: "已成功通知", course_or_project: "成功课程" }), record('failed-<1>', { source_label: "原通知 <报告>", course_or_project: "概率 & 统计" })] },
    result: {
      status: "partial_success",
      errors: [{ input_id: 'failed-<1>', message: "private-token /private/backend/path" }],
      record_results: [{ input_id: "ok", status: "success" }, { input_id: 'failed-<1>', status: "failed" }],
    },
  });
  assert.match(html, /概率 &amp; 统计 · 原通知 &lt;报告&gt;/);
  assert.match(html, /通知标识 <code>failed-&lt;1&gt;<\/code>/);
  assert.equal((html.match(/<a /g) || []).length, 1);
  assert.doesNotMatch(html, /已成功通知|成功课程|private-token|private\/backend/);
});

test("incomplete source rows retain course, notification identity, and URL", () => {
  const html = failureSourcesMarkup({
    request: { records: [record("incomplete")] },
    result: { status: "partial_success", errors: [{ input_id: "incomplete", code: "incomplete_source" }], record_results: [{ input_id: "incomplete", status: "failed" }] },
  });
  assert.match(html, /概率论 · 第二次作业通知/);
  assert.match(html, /<code>incomplete<\/code>/);
  assert.match(html, /href="https:\/\/course\.example\.test\/notice\/2"/);
});

test("batch failures without row IDs identify every original notification", () => {
  const html = failureSourcesMarkup({
    request: { records: [record("first"), record("second", { source_label: "另一份通知" })] },
    result: { status: "failed", errors: [{ input_id: null, message: "internal exception" }], record_results: [] },
  });
  assert.match(html, /<code>first<\/code>/);
  assert.match(html, /另一份通知/);
  assert.match(html, /<code>second<\/code>/);
  assert.doesNotMatch(html, /internal exception/);
});

test("unknown failed IDs use candidate source identity without leaking error objects", () => {
  const html = failureSourcesMarkup({
    request: { records: [] },
    result: { status: "failed", errors: [{ input_id: "unknown", message: "API_KEY=private" }], tasks: [] },
    items: [{ candidate: { input_id: "unknown", course_or_project: "算法", sources: [{ input_id: "unknown", label: "来源通知", url: "https://course.example.test/another", platform: "teaching_web" }] } }],
  });
  assert.match(html, /算法 · 来源通知/);
  assert.match(html, /<code>unknown<\/code>/);
  assert.match(html, /打开教学网原页面/);
  assert.doesNotMatch(html, /API_KEY|private|\[object Object\]/);
  assert.match(failureSourcesMarkup({ result: { status: "failed", errors: [{ input_id: "missing" }] } }), /通知<\/p>.*<code>missing<\/code>/);
});

test("fallback names are concise and ignore reference paths and upstream metadata", () => {
  const html = failureSourcesMarkup({
    request: { records: [record("fallback", {
      source_label: null,
      source_url: "file:///private/secret.json",
      text: "\n" + "通知".repeat(100) + "\n正文不应显示在失败身份卡",
      upstream_hints: { references: [{ file_ref: "/private/hidden.pdf" }], password: "private-password" },
    })] },
    result: { status: "failed" },
  });
  assert.ok(html.includes("通知".repeat(60)));
  assert.ok(!html.includes("通知".repeat(61)));
  assert.doesNotMatch(html, /正文不应|private|hidden\.pdf|<a /);
});

test("native teaching workspace projections retain body and attachment excerpts for notices", () => {
  const candidate = item({
    content_type: "notice",
    evidence: [
      { input_id: "notice-1", field: "body", quote: "课程安排见附件。", supports: ["description"] },
      { input_id: "notice-1", field: "reference:course-file", quote: "新教室：<实验楼> 201。", supports: ["description"] },
    ],
    sources: [{ input_id: "notice-1", platform: "teaching_web", url: "https://course.example.test/notice/2", label: "课程安排通知", source_item_id: "course-item" }],
  });
  candidate.batch.request.contract_version = "teaching-workspace/0.1";
  candidate.batch.request.records[0] = record("notice-1", {
    text: "课程安排见附件。\n请以最新教室安排为准。",
    source_label: "课程安排通知",
    upstream_hints: { references: [{ file_ref: "/private/course-file.pdf", text: "内部读取详情" }], course_term: null },
  });
  const html = candidateEvidenceMarkup(candidate);
  assert.match(html, /查看完整通知原文/);
  assert.ok(html.includes("请以最新教室安排为准。"));
  assert.match(html, /新教室：&lt;实验楼&gt; 201。/);
  assert.equal((html.match(/<blockquote>/g) || []).length, 2);
  assert.equal((html.match(/<a /g) || []).length, 1);
  assert.doesNotMatch(html, /private|course-file\.pdf|内部读取详情|<实验楼>/);
});
