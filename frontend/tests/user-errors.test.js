import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { describeUserError, userErrorMarkup, submitErrorFeedback, resetErrorFeedback, pruneErrorFeedback } from "../src/user-errors.js";
import { renderErrorReports } from "../src/error-reports-view.js";

const schema = JSON.parse(await readFile(new URL("../../contracts/schemas/error-report-v0.1.schema.json", import.meta.url)));
const examples = JSON.parse(await readFile(new URL("../../contracts/examples/error-reports-v01.json", import.meta.url))).reports;
const recognitionErrors = JSON.parse(await readFile(new URL("../../contracts/recognition-errors.json", import.meta.url)));

test('every recognition reason has safe consistent messaging and a reportable code', () => {
  for (const [reason, spec] of Object.entries(recognitionErrors)) {
    const report = describeUserError({code:'invalid_agent_output', message:'PRIVATE-SENTINEL', failure:{reason}});
    assert.equal(report.kind, reason);
    assert.equal(report.message, spec.message);
    assert.equal(report.details, spec.action);
    assert.ok(schema.properties.code.enum.includes(report.kind));
    assert.ok(!JSON.stringify(report).includes('PRIVATE-SENTINEL'));
  }
  const diagnostic = {schema_path:'properties/tasks/items/required', rule:'required'};
  assert.match(describeUserError({failure:{reason:'draft_schema'}, diagnostic}).details, /校验位置/);
  assert.ok(!describeUserError({failure:{reason:'draft_schema'}, diagnostic:{...diagnostic,schema_path:'<script>PRIVATE'}}).details.includes('PRIVATE'));
});
const receipt = { contract_version: "error-report/0.1", saved: true, report_id: "abc123".repeat(4) };

test('test diagnostics survive friendly error mapping and are escaped without entering feedback payloads', () => {
  const diagnostics = '{"stage":"pdf_transcription","page_start":7,"code":"agent_timeout","test":"<script>"}';
  const report = describeUserError({code:'agent_timeout',message:'Timeout',diagnostics},'teaching_sync');
  assert.equal(report.diagnostics, diagnostics);
  const markup = userErrorMarkup(report,'teaching_sync');
  assert.match(markup,/测试环境诊断详情/);assert.match(markup,/page_start/);
  assert.ok(!markup.includes('<script>'));
  assert.ok(!userErrorMarkup({code:'agent_timeout'},'teaching_sync').includes('测试环境诊断详情'));
});

function fixture(error = { status: 503, message: "文本识别配置不可用，API_KEY=PRIVATE" }, action = "text_import") {
  resetErrorFeedback();
  const html = userErrorMarkup(error, action);
  const id = html.match(/data-error-feedback="([^"]+)"/)?.[1];
  const button = { dataset: { errorFeedback: id }, disabled: false, textContent: "反馈此问题" };
  const label = { dataset: { errorFeedbackStatus: id }, textContent: "" };
  globalThis.document = { querySelectorAll: selector => selector === "[data-error-feedback]" ? [button] : [label] };
  return { error, action, html, button, label, redraw: () => userErrorMarkup(error, action) };
}

test("system failures explain user actions and unknown errors never echo internals", () => {
  const cases = [
    ["文本识别配置不可用，密钥 PRIVATE", /维护人员处理.*手动添加任务/],
    ["教学网采集依赖未安装 crawler/ pnpm install", /暂时无法从教学网获取通知/],
    ["凭据加密依赖未安装 server/requirements.txt", /无法保存教学网连接/],
    ["unknown crash <script> PRIVATE-CONTENT password=PRIVATE", /操作未完成/],
  ];
  for (const [message, expected] of cases) {
    const html = userErrorMarkup({ status: 503, message });
    assert.match(html, expected);
    assert.match(html, /反馈此问题/);
    assert.doesNotMatch(html, /PRIVATE|pnpm|requirements|<script>|请安装|配置模型/);
  }
  const validation = userErrorMarkup("请填写任务名称");
  assert.match(validation, /请填写任务名称/);
  assert.doesNotMatch(validation, /data-error-feedback/);
});

test("every contract category keeps a user-visible error and report fallback", () => {
  for (const code of schema.properties.code.enum) {
    const html = userErrorMarkup({ code, status: 500, message: "PRIVATE-RAW" }, "general");
    assert.match(html, /class="user-error-message">[^<]+/);
    assert.match(html, /data-error-feedback=/);
    assert.doesNotMatch(html, /PRIVATE-RAW/);
    assert.ok(schema.properties.code.enum.includes(describeUserError({ code, status: 500 }).kind));
  }
});

test("clicking submits only the shared contract fields and keeps success after redraw", async () => {
  const f = fixture(), calls = [];
  await submitErrorFeedback(f.button, { send: async payload => { calls.push(structuredClone(payload)); return receipt; }, frontendRevision: "a7fe0a8", backendRevision: "a7fe0a8" });
  assert.equal(calls.length, 1);
  assert.deepEqual(Object.keys(calls[0]).sort(), Object.keys(examples[0]).sort());
  assert.equal(calls[0].code, examples[0].code);
  assert.equal(calls[0].action, examples[0].action);
  assert.doesNotMatch(JSON.stringify(calls), /PRIVATE|message|password|stack|url/);
  assert.equal(f.button.disabled, true);
  assert.equal(f.label.textContent, "已提交，感谢反馈。");
  assert.match(f.redraw(), /已反馈/);
  assert.match(f.redraw(), /data-error-feedback="[^"]+" disabled/);
});

test("concurrent clicks share one pending submission and preserve the incident on retry", async () => {
  const f = fixture(); let release, calls = [];
  const waiting = new Promise(resolve => release = resolve);
  const options = { send: async payload => { calls.push(structuredClone(payload)); await waiting; throw new TypeError("offline"); }, frontendRevision: "invalid/private", backendRevision: undefined };
  const first = submitErrorFeedback(f.button, options);
  assert.equal(f.button.disabled, true);
  await submitErrorFeedback(f.button, options);
  assert.equal(calls.length, 1);
  release(); await first;
  assert.equal(f.button.disabled, false);
  assert.match(f.label.textContent, /无法确认反馈是否已提交/);
  assert.doesNotMatch(f.redraw(), /已提交，感谢/);
  await submitErrorFeedback(f.button, { send: async payload => { calls.push(structuredClone(payload)); return receipt; }, frontendRevision: "deadbee" });
  assert.deepEqual(calls[1], calls[0]);
  assert.equal(calls[0].frontend_revision, "unknown");
  assert.equal(calls[0].backend_revision, "unknown");
});

test("missing or malformed acknowledgements never display submission success", async () => {
  for (const result of [{}, { saved: false }, { ...receipt, report_id: "invalid" }, { ...receipt, contract_version: "unsupported" }]) {
    const f = fixture();
    await submitErrorFeedback(f.button, { send: async () => result });
    assert.match(f.label.textContent, /无法确认反馈是否已提交/);
    assert.equal(f.button.disabled, false);
  }
});

test("account reset discards report UI state and old acknowledgements cannot change new errors", async () => {
  const f = fixture(); let release;
  const request = submitErrorFeedback(f.button, { send: () => new Promise(resolve => release = resolve) });
  const next = fixture();
  assert.notEqual(f.button.dataset.errorFeedback, next.button.dataset.errorFeedback);
  release(receipt); await request;
  assert.equal(next.button.disabled, false);
  assert.equal(next.label.textContent, "");
});

test("a later failure remains reportable while historical redraws retain their acknowledgement", async () => {
  const f = fixture();
  await submitErrorFeedback(f.button, { send: async () => receipt });
  const next = userErrorMarkup({ ...f.error }, f.action);
  assert.doesNotMatch(next, /已反馈/);
  assert.match(f.redraw(), /已反馈/);
  const stable = userErrorMarkup(f.error, f.action, "synthetic-run-record");
  const id = stable.match(/data-error-feedback="([^"]+)"/)[1];
  await submitErrorFeedback({ dataset: { errorFeedback: id } }, { send: async () => receipt });
  assert.match(userErrorMarkup({ ...f.error }, f.action, "synthetic-run-record"), /已反馈/);
});

test("large error lists keep every mounted feedback control usable", async () => {
  resetErrorFeedback();
  const buttons = Array.from({ length: 600 }, () => {
    const html = userErrorMarkup(new Error("synthetic failure"), "general");
    return { dataset: { errorFeedback: html.match(/data-error-feedback="([^"]+)"/)[1] } };
  });
  globalThis.document = { querySelectorAll: selector => selector === "[data-error-feedback]" ? buttons : [] };
  pruneErrorFeedback();
  let sent = 0;
  for (const button of [buttons[0], buttons.at(-1)])
    await submitErrorFeedback(button, { send: async () => { sent++; return receipt; } });
  assert.equal(sent, 2);
});

test("administrator reports render an empty state and escape received fields", () => {
  assert.match(renderErrorReports([]), /暂无错误报告/);
  const html = renderErrorReports([{ ...examples[0], report_id: '<img src=x onerror="alert(1)">', received_at: "2026-10-15T14:30:00+08:00" }]);
  assert.match(html, /用户错误报告|文本导入/);
  assert.match(html, /&lt;img/);
  assert.doesNotMatch(html, /<img/);
});

test("email errors use safe messages and service failures retain valid report controls", () => {
  for (const code of ["email_not_configured", "email_send_failed", "email_code_invalid", "email_code_expired", "email_code_rate_limited", "email_already_verified", "email_invalid", "email_registered"]) {
    const error = { code, status: code === "email_code_rate_limited" ? 429 : 503, message: "PRIVATE password=PRIVATE" }, html = userErrorMarkup(error, "account");
    assert.doesNotMatch(html, /PRIVATE/);
    if (["email_not_configured", "email_send_failed"].includes(code)) {
      assert.match(html, /data-error-feedback/);
      assert.ok(schema.properties.code.enum.includes(describeUserError(error, "account").kind));
    } else assert.doesNotMatch(html, /data-error-feedback/);
  }
});
