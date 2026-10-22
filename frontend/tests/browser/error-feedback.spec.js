import { test, expect } from "@playwright/test";

const receipt = { contract_version: "error-report/0.1", saved: true, report_id: "abc123".repeat(4) };

async function openApp(page, { anonymous = false, workspaceFailure = false, failReportOnce = false, teachingFailure = false, withTrash = false, history = null } = {}) {
  const reports = [];
  await page.route("**/api/**", async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let value = {}, status = 200;
    if (path === "/api/auth/me") {
      value = anonymous ? { error: "登录已失效" } : { user: { id: "synthetic", name: "合成账号" } };
      if (anonymous) status = 401;
    } else if (path === "/api/workspace") {
      value = workspaceFailure ? { error: "Unexpected SQLite PRIVATE-CONTENT token=PRIVATE" } : { tasks: { version: 1, value: [{ id: "task", title: "合成任务", status: "not_started", due: null, plans: [], subtasks: [] }] } };
      if (workspaceFailure) status = 500;
      else if (withTrash) value.tasks.value.push({ id: "trash", title: "回收站任务", status: "cancelled", due: null, plans: [], subtasks: [], recycleBin: { status: "not_started", deletedAt: "2026-10-15T00:00:00Z" } });
    } else if (path === "/api/data/tasks" || path === "/api/auth/login") {
      status = 503; value = { error: "Unexpected failure PRIVATE-CONTENT password=PRIVATE" };
    } else if (path === "/api/error-reports") {
      reports.push(request.postDataJSON());
      if (failReportOnce && reports.length === 1) return route.abort("failed");
      value = receipt;
    } else if (path === "/api/version") value = { environment: "test", revision: "a7fe0a8", managed: true };
    else if (path === "/api/usage/visit") value = { offerRating: false };
    else if (path === "/api/usage/guest-visit") value = { saved: true };
    else if (path === "/api/recognition/batches") value = history ? history.batches : [];
    else if (path === "/api/recognition/retry" && history) value = history.retry(request.postDataJSON());
    else if (path === "/api/sources/sync-runs") {
      value = { contract_version: "source-sync/0.1", runs: [] };
      if (teachingFailure && request.method() === "POST") { status = 503; value = { error: typeof teachingFailure === 'object' ? teachingFailure : "教学网识别配置不可用 API_KEY=PRIVATE" }; }
    }
    else if (path === "/api/recognition/task-evidence") value = { evidence: [] };
    else if (path === "/api/sources/sync-schedule") value = { contract_version: "source-sync/0.1", enabled: false, available: true, revision: 0, interval_minutes: 60, sources: ["teaching_web"] };
    else if (path === "/api/sources/credentials") value = { configured: true, username: "synthetic" };
    else throw new Error("Unmocked API: " + path);
    await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(value) });
  });
  await page.goto("/");
  return reports;
}

test("unknown load errors keep visible guidance, a working report button and an acknowledgement", async ({ page }, info) => {
  const reports = await openApp(page, { workspaceFailure: true });
  const alert = page.locator("#sync-alert");
  await expect(alert.getByText("暂时无法读取任务，请稍后重试。")).toBeVisible();
  await expect(alert).not.toContainText("PRIVATE");
  const feedback = alert.getByRole("button", { name: "反馈此问题" });
  await expect(feedback).toBeVisible();
  await feedback.click();
  await expect(alert.getByText("已提交，感谢反馈。")).toBeVisible();
  await expect(alert.getByRole("button", { name: "已反馈" })).toBeDisabled();
  expect(reports).toHaveLength(1);
  expect(reports[0].action).toBe("tasks_load");
  expect(JSON.stringify(reports)).not.toMatch(/PRIVATE|password|token|SQLite/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("error-feedback.png"), fullPage: true });
});

test("a failed report remains retryable and retries the same incident", async ({ page }) => {
  const reports = await openApp(page, { workspaceFailure: true, failReportOnce: true });
  const alert = page.locator("#sync-alert");
  await alert.getByRole("button", { name: "反馈此问题" }).click();
  await expect(alert.getByText("无法确认反馈是否已提交，请稍后重试。")).toBeVisible();
  await expect(alert.getByText("已提交，感谢反馈。")).toHaveCount(0);
  await alert.getByRole("button", { name: "重试反馈" }).click();
  await expect(alert.getByText("已提交，感谢反馈。")).toBeVisible();
  expect(reports).toHaveLength(2);
  expect(reports[1]).toEqual(reports[0]);
});

test("save failures keep the report control after the task view redraws", async ({ page }) => {
  const reports = await openApp(page);
  await page.getByRole("button", { name: "完成任务：合成任务", exact: true }).click();
  const alert = page.locator("#sync-alert");
  await expect(alert.getByText("无法确认修改是否已保存，请重试保存。")).toBeVisible();
  await alert.getByRole("button", { name: "反馈此问题" }).click();
  await expect(alert.getByText("已提交，感谢反馈。")).toBeVisible();
  expect(reports[0].action).toBe("tasks_save");
});

test("feedback stays inside an open task dialog and remains clickable after delete fails", async ({ page }) => {
  const reports = await openApp(page);
  await page.getByRole("button", { name: "合成任务", exact: true }).click();
  const drawer = page.locator("#task-drawer");
  await drawer.getByRole("button", { name: "移入回收站" }).click();
  await expect(drawer.getByText("暂时无法删除任务，请稍后重试。")).toBeVisible();
  await drawer.getByRole("button", { name: "反馈此问题" }).click();
  await expect(drawer.getByText("已提交，感谢反馈。")).toBeVisible();
  expect(reports[0].action).toBe("tasks_delete");
});

test("restore failures keep feedback inside the recycle-bin dialog", async ({ page }) => {
  const reports = await openApp(page, { withTrash: true });
  await page.getByRole("button", { name: /回收站/ }).click();
  const dialog = page.locator("#dialog");
  await dialog.getByRole("button", { name: "恢复", exact: true }).click();
  await expect(dialog.getByText("暂时无法恢复任务，请稍后重试。")).toBeVisible();
  await dialog.getByRole("button", { name: "反馈此问题" }).click();
  await expect(dialog.getByText("已提交，感谢反馈。")).toBeVisible();
  expect(reports[0].action).toBe("tasks_restore");
});

test("login system errors can be reported without erasing the entered email", async ({ page }) => {
  const reports = await openApp(page, { anonymous: true });
  if (await page.locator("#sidebar-show").isVisible()) await page.locator("#sidebar-show").click();
  await page.getByRole("button", { name: "账号与数据", exact: true }).click();
  const dialog = page.locator("#dialog");
  await dialog.getByLabel("邮箱", { exact: true }).fill("synthetic@example.test");
  await dialog.getByLabel("密码", { exact: true }).fill("Synthetic-2026!");
  await dialog.getByRole("button", { name: "登录", exact: true }).click();
  await expect(dialog.getByText("账号操作未完成，请稍后重试。")).toBeVisible();
  await dialog.getByRole("button", { name: "反馈此问题" }).click();
  await expect(dialog.getByText("已提交，感谢反馈。")).toBeVisible();
  await expect(dialog.getByLabel("邮箱", { exact: true })).toHaveValue("synthetic@example.test");
  expect(JSON.stringify(reports)).not.toMatch(/synthetic@example|Synthetic-2026|PRIVATE/);
});

test("teaching failures offer user guidance and a report instead of maintenance commands", async ({ page }) => {
  const reports = await openApp(page, { teachingFailure: true });
  if (await page.locator("#sidebar-show").isVisible()) await page.locator("#sidebar-show").click();
  await page.getByRole("button", { name: "导入内容", exact: true }).click();
  await page.getByRole("button", { name: "教学网", exact: true }).click();
  await page.locator('[data-import-action="sync"]').click();
  const alert = page.getByRole("alert").filter({ hasText: "暂时无法整理教学网通知" });
  await expect(alert).toBeVisible();
  await expect(alert).not.toContainText(/PRIVATE|配置|安装|NIUMA_|pnpm/);
  await alert.getByRole("button", { name: "反馈此问题" }).click();
  await expect(alert.getByText("已提交，感谢反馈。")).toBeVisible();
  expect(reports[0].code).toBe("teaching_model_configuration");
  expect(reports[0].action).toBe("teaching_sync");
});

test("recognition diagnosis reaches the browser and feedback keeps only its identifier", async ({ page }) => {
  const diagnostic_id='a1'.repeat(12);
  const reports=await openApp(page,{teachingFailure:{code:'invalid_agent_output',failure:{reason:'draft_schema'},diagnostic_id,
    diagnostic:{schema_path:'properties/tasks/items/required',rule:'required',instance_path:'tasks/0',missing_fields:['prerequisites']},
    diagnostics:'{"stage":"validation","attempt":2,"escaped":"<script>PRIVATE-DETAIL</script>"}'}});
  if(await page.locator('#sidebar-show').isVisible()) await page.locator('#sidebar-show').click();
  await page.getByRole('button',{name:'导入内容',exact:true}).click();
  await page.getByRole('button',{name:'教学网',exact:true}).click();
  await page.locator('[data-import-action="sync"]').click();
  const alert=page.getByRole('alert').filter({hasText:'prerequisites'});
  await expect(alert).toBeVisible();
  await expect(alert).toContainText(diagnostic_id);
  await alert.getByText('识别诊断详情',{exact:true}).click();
  await expect(alert.locator('pre')).toContainText('validation');
  await expect(alert.locator('script')).toHaveCount(0);
  await alert.getByRole('button',{name:'反馈此问题'}).click();
  await expect(alert.getByText('已提交，感谢反馈。')).toBeVisible();
  expect(reports[0].diagnostic_id).toBe(diagnostic_id);
  expect(JSON.stringify(reports)).not.toMatch(/PRIVATE-DETAIL|diagnostics|missing_fields/);
});

test("retrying the same import history row reports the new diagnosis and retains same-attempt feedback", async ({ page }) => {
  const firstId = 'a1'.repeat(12), nextId = 'b2'.repeat(12);
  const request = { contract_version: '0.2', request_id: 'synthetic:retry-feedback', records: [{
    input_id: 'synthetic:input', platform: 'manual', text: '明天提交合成作业', source_label: '合成文本导入',
    collected_at: '2026-10-22T09:00:00+08:00', published_at: null, course_or_project: null, url: null,
  }] };
  const failedBatch = diagnostic_id => ({ request, importedAt: '2026-10-22T09:00:00+08:00', items: [],
    result: { status: 'failed', tasks: [], errors: [{ input_id: 'synthetic:input', code: 'invalid_agent_output',
      message: '合成校验失败', retryable: true, failure: { reason: 'draft_schema' }, diagnostic_id }] } });
  let retries = 0;
  const history = { batches: [failedBatch(firstId)], retry(body) {
    expect(body).toEqual(request); retries++;
    this.batches = [failedBatch(nextId)]; return this.batches;
  } };
  const reports = await openApp(page, { history });
  if (await page.locator('#sidebar-show').isVisible()) await page.locator('#sidebar-show').click();
  await page.getByRole('button', { name: '导入内容', exact: true }).click();
  await page.locator('[data-source-sync-history] > summary').click();
  const row = page.locator('[data-import-history-batch-entry="synthetic:retry-feedback"]');
  await row.getByRole('button', { name: '错误 1 条', exact: true }).click();
  await row.locator('[data-import-history-item="0"]').click();
  const alert = row.getByRole('alert');
  await expect(alert).toContainText(firstId);
  await alert.getByRole('button', { name: '反馈此问题' }).click();
  await expect(alert.getByRole('button', { name: '已反馈' })).toBeDisabled();
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  await expect(alert.getByRole('button', { name: '已反馈' })).toBeDisabled();
  await row.getByRole('button', { name: '处理未完成', exact: true }).click();
  await expect(alert).toContainText(nextId);
  await expect(alert).not.toContainText(firstId);
  await expect(alert.getByRole('button', { name: '反馈此问题' })).toBeEnabled();
  await alert.getByRole('button', { name: '反馈此问题' }).click();
  await expect(alert.getByRole('button', { name: '已反馈' })).toBeDisabled();
  expect(retries).toBe(1);
  expect(reports.map(report => report.diagnostic_id)).toEqual([firstId, nextId]);
  expect(reports[0].incident_id).not.toBe(reports[1].incident_id);
});

test("administrator dashboard displays received reports in its own section", async ({ page }) => {
  await page.route("**/api/admin/usage*", route => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({
      start: "2026-10-01", end: "2026-10-15", environment: "test", activeUsers: 0,
      metrics: [], daily: [], feedback: [], rating: { samples: 0, average: null }, methodology: "合成验收",
      errorReports: [{ report_id: receipt.report_id, received_at: "2026-10-15T14:30:00+08:00", code: "model_configuration", action: "text_import", status: 503, frontend_revision: "a7fe0a8", backend_revision: "a7fe0a8" }],
    }),
  }));
  await page.goto("/admin.html");
  const section = page.locator(".admin-error-reports");
  await expect(section.getByRole("heading", { name: "用户错误报告" })).toBeVisible();
  await expect(section.getByRole("cell", { name: "文本导入", exact: true })).toBeVisible();
  await expect(section.getByRole("cell", { name: receipt.report_id, exact: true })).toBeVisible();
});
