import { test, expect } from "@playwright/test";

const link = "https://mp.weixin.qq.com/s/example-id";
async function workspace(page) {
  const requests = [], reads = [];
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
        if (rejected) { rejected = false; status = 503; json = { error: "请重试" }; }
      }
    }
    if (path === "/api/quick/article") { reads.push(req.postDataJSON().url); json = { text: "" }; }
    if (path === "/api/recognition/text-jobs") {
      json = { contract_version: "text-import-jobs/0.1", jobs: [] };
      if (req.method() === "POST") requests.push(req.postDataJSON());
    }
    await route.fulfill({ status, json });
  });
  await page.goto("/"); await page.locator(".add-task").click();
  return { requests, reads };
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
  await page.getByRole("button", { name: "上传并识别" }).click();
  await expect(page.locator("#task-edit-error")).not.toBeEmpty();
  await expect(page.locator("#quick-text")).toHaveValue("请记录讲座时间");
  await page.getByRole("button", { name: "上传并识别" }).click();
  await expect(page).toHaveURL(/importSource=files/);
  expect(requests.length).toBe(3);
  expect(requests[0]).toBe("asset");
  expect(requests[1]).toEqual(requests[2]);
  expect(requests[2].records[0].text).toBe("请记录讲座时间");
});

test("multi-action notice uses the batch importer and optional publication time", async ({ page }) => {
  const { requests } = await workspace(page);
  const text = "下周一提交报名表；下周三前把访谈提纲发给组长。";
  await page.locator("#quick-text").fill(text);
  await page.getByRole("button", { name: "设置通知发布时间" }).click();
  await page.locator("#quick-published").fill("2026-11-20T08:00");
  await page.getByRole("button", { name: "识别通知" }).click();
  await expect(page).toHaveURL(/importSource=text|page=import/);
  expect(requests[0].records[0]).toMatchObject({ text, published_at: { value: "2026-11-20T08:00:00+08:00" } });
});

test("a bare link is read on submit, and only an unreadable page asks for details", async ({ page }) => {
  const { reads } = await workspace(page);
  await page.locator("#quick-text").fill("我想参加这个活动：" + link);
  await expect(page.locator("#quick-link-extra")).toBeHidden();
  expect(reads).toEqual([]);
  await page.getByRole("button", { name: "读取链接" }).click();
  await expect(page.locator("#quick-link-text")).toBeFocused();
  expect(reads).toEqual([link]);
  await page.locator("#quick-link-text").fill("明天上午10点半有数算考试，你帮我记一下");
  await expect(page.locator("#quick-summary")).toHaveText("数算考试");
  await expect(page.locator(".quick-submit")).toHaveText("添加");
});

test("a readable link goes straight to notice recognition with its source", async ({ page }) => {
  const { requests } = await workspace(page);
  const text = "学生读书会将于周五在图书馆举行，请提前一天报名。";
  await page.route("**/api/quick/article", route => route.fulfill({ json: { text } }));
  await page.locator("#quick-text").fill(link);
  await page.getByRole("button", { name: "读取链接" }).click();
  await expect(page.locator("#quick-task-form")).toHaveCount(0);
  expect(requests[0].records[0]).toMatchObject({ text, source_url: link });
});
