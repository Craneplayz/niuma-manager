import { test, expect } from "@playwright/test";

async function workspace(page) {
  const requests = [];
  let rejected = true;
  await page.route("**/api/**", async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    let json = {}, status = 200;
    if (path === "/api/auth/me") json = { user: { id: "student", name: "学生", emailVerified: true } };
    if (path === "/api/workspace") json = { tasks: { value: [], version: 0 }, taskWriteOwner: "student", taskWriteGeneration: 0 };
    if (path === "/api/recognition/batches") json = [];
    if (path === "/api/materials/limits") json = { limits: { asset_bytes: 20 * 1048576 } };
    if (path === "/api/materials/assets") {
      requests.push("asset");
      json = { contract_version: "manual-materials/0.1", file_ref: "asset:abc", file_name: "讲座.png", mime_type: "image/png", size_bytes: 3, sha256: "a".repeat(64) };
    }
    if (path === "/api/materials/imports") {
      json = { jobs: [] };
      if (req.method() === "POST") {
        requests.push(req.postDataJSON());
        json = { job_id: "job-synthetic" };
        if (rejected) { rejected = false; status = 503; json = { error: "请重试" }; }
      }
    }
    if (path === "/api/recognition/text-jobs") {
      json = { contract_version: "text-import-jobs/0.1", jobs: [] };
      if (req.method() === "POST") requests.push(req.postDataJSON());
    }
    await route.fulfill({ status, json });
  });
  await page.goto("/"); await page.locator(".add-task").click();
  return { requests };
}

test("drop anywhere in the dialog, retry the same upload and keep the typed note", async ({ page }) => {
  const { requests } = await workspace(page);
  await page.locator("#quick-text").fill("请记录讲座时间");
  await page.locator("#quick-course-button").evaluate(el => {
    const dt = new DataTransfer(); dt.items.add(new File(["abc"], "讲座.png", { type: "image/png" }));
    el.dispatchEvent(new DragEvent("drop", { bubbles: true, cancelable: true, dataTransfer: dt }));
  });
  await expect(page.locator("#quick-selected-files")).toContainText("讲座.png");
  expect(requests).toEqual([]);
  await page.getByRole("button", { name: "加入" }).click();
  await expect(page.locator("#task-edit-error")).not.toBeEmpty();
  await expect(page.locator("#quick-text")).toHaveValue("请记录讲座时间");
  await page.getByRole("button", { name: "加入" }).click();
  await expect(page.locator("#quick-task-form")).toHaveCount(0);
  await expect(page).not.toHaveURL(/page=import/);
  expect(requests.length).toBe(3);
  expect(requests[0]).toBe("asset");
  expect(requests[1]).toEqual(requests[2]);
  expect(requests[2].records[0].text).toBe("请记录讲座时间");
});

test("multi-action notice uses the batch importer and optional publication time", async ({ page }) => {
  const { requests } = await workspace(page);
  const text = "下周一提交报名表；下周三前把访谈提纲发给组长。报名地址 https://example.org/register";
  await page.locator("#quick-text").fill(text);
  await page.getByRole("button", { name: "设置通知发布时间" }).click();
  await page.locator("#quick-published").fill("2026-11-20T08:00");
  await page.getByRole("button", { name: "加入" }).click();
  await expect(page.locator("#quick-task-form")).toHaveCount(0);
  await expect(page).not.toHaveURL(/page=import/);
  expect(requests[0].records[0]).toMatchObject({ text, source_url: null, published_at: { value: "2026-11-20T08:00:00+08:00" } });
});

test("pasting only a URL uses the ordinary text input", async ({ page }) => {
  const { requests } = await workspace(page);
  const input = page.locator("#quick-text");
  const text = "https://example.org/events";
  await input.dispatchEvent("paste");
  await input.fill(text);
  await page.getByRole("button", { name: "加入", exact: true }).click();
  await expect(page.locator("#quick-task-form")).toHaveCount(0);
  expect(requests).toHaveLength(1);
  expect(requests[0].records[0]).toMatchObject({ text, source_url: null });
});

test("a submitted notice survives reload and appears as a saved task on the list", async ({ page }) => {
  await workspace(page);
  let requestId, finished = false;
  await page.route("**/api/recognition/text-jobs", async route => {
    if (route.request().method() === "POST") requestId = route.request().postDataJSON().request_id;
    await route.fulfill({ json: { jobs: [{ request_id: requestId, state: finished ? "success" : "recognizing" }] } });
  });
  await page.route("**/api/workspace", route => route.fulfill({ json: {
    tasks: { value: finished ? [{ id: "created", title: "访谈提纲", status: "not_started", recognitionHistory: [{requestId}] }] : [], version: finished ? 1 : 0 },
    taskWriteOwner: "student", taskWriteGeneration: 0,
  } }));
  await page.locator("#quick-text").fill("下周一提交报名表；下周三前把访谈提纲发给组长。");
  await page.locator(".quick-submit").click();
  await expect(page.locator(".task-page")).toContainText("完成后将自动加入清单");
  await page.reload();
  await expect(page.locator(".task-page")).toContainText("完成后将自动加入清单");
  finished = true;
  await expect(page.locator('.task-row[data-task-id="created"]')).toContainText("访谈提纲");
  await expect(page.locator(".task-row.is-pending-review")).toHaveCount(0);
  await expect(page).not.toHaveURL(/page=import/);
});
