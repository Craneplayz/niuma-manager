import { test, expect } from '@playwright/test';

async function openRating(page, { failRatingOnce = false, failFeedbackOnce = false } = {}) {
  const writes = [];
  let ratingAttempts = 0, feedbackAttempts = 0;
  await page.route('**/api/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    let value = {}, status = 200;
    if (path === '/api/auth/me') value = { user: { id: 'rating-synthetic', name: '合成账号' } };
    else if (path === '/api/workspace') value = { tasks: { version: 1, value: [] } };
    else if (path === '/api/usage/visit') value = { offerRating: true };
    else if (path === '/api/usage/feedback') {
      const body = request.postDataJSON();
      writes.push(body);
      const fail = body.kind === 'rating'
        ? ++ratingAttempts === 1 && failRatingOnce
        : ++feedbackAttempts === 1 && failFeedbackOnce;
      if (fail) { status = 503; value = { error: 'Synthetic unavailable' }; }
      else value = { saved: true };
    } else if (path === '/api/version') value = { environment: 'test', revision: 'synthetic', managed: true };
    else if (path === '/api/recognition/task-evidence') value = { evidence: [] };
    else throw new Error('Unmocked API: ' + path);
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(value) });
  });
  await page.goto('/');
  await expect(page.locator('#usage-rating')).toBeVisible();
  return writes;
}

const prompts = [
  '抱歉没能让您满意，请问有哪些地方需要改进？',
  '感谢您的反馈，哪些体验还不够好？',
  '感谢您的评价，我们还可以做些什么，让您用得更顺手？',
  '很高兴您用得不错！还有哪些细节可以做得更好？',
  '感谢您的认可！最喜欢哪些功能，或有什么新的期待？',
];

for (const [index, prompt] of prompts.entries()) {
  const score = index + 1;
  test(`${score} points stays in one colored row and offers optional feedback without losing the score`, async ({ page }, info) => {
    await page.clock.install();
    const writes = await openRating(page);
    const panel = page.locator('#usage-rating');
    const buttons = panel.locator('[data-rating]');
    const boxes = await buttons.evaluateAll(elements => elements.map(element => {
      const box = element.getBoundingClientRect();
      return { x: box.x, y: box.y, width: box.width, height: box.height, color: getComputedStyle(element).backgroundColor };
    }));
    expect(boxes).toHaveLength(5);
    expect(new Set(boxes.map(box => box.y)).size).toBe(1);
    expect(new Set(boxes.map(box => box.color)).size).toBe(5);
    for (const box of boxes) {
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
      expect(Math.abs(box.width - boxes[0].width)).toBeLessThan(1);
      expect(box.x + box.width).toBeLessThanOrEqual(page.viewportSize().width);
    }
    if (score === 1) await panel.screenshot({ path: info.outputPath('rating-options.png') });
    await panel.getByRole('button', { name: `${score}分`, exact: true }).click();
    await expect(panel.getByText(prompt, { exact: true })).toBeVisible();
    await expect(panel.getByText(`已记录您的 ${score} 分评分。`)).toBeVisible();
    await expect(panel.getByRole('textbox', { name: '使用反馈（选填）' })).toBeVisible();
    await expect(panel.getByText('使用反馈（选填）', { exact: true })).toHaveCount(0);
    await expect(panel.locator('[data-rating]:enabled')).toHaveCount(5);
    await expect(panel.getByRole('button', { name: `${score}分`, exact: true })).toHaveAttribute('aria-pressed', 'true');
    if (score === 1) await panel.screenshot({ path: info.outputPath('rating-feedback.png') });
    await panel.getByRole('button', { name: '暂时跳过' }).click();
    await expect(panel.getByText('谢谢您的反馈，我们会继续努力！', { exact: true })).toBeVisible();
    await page.clock.fastForward(1280);
    await page.clock.fastForward(220);
    await expect(panel).toHaveCount(0);
    expect(writes).toEqual([{ kind: 'rating', score }]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

test('feedback failures preserve the draft and retry without resubmitting the rating', async ({ page }) => {
  const writes = await openRating(page, { failFeedbackOnce: true });
  const panel = page.locator('#usage-rating');
  await panel.getByRole('button', { name: '1分', exact: true }).click();
  const input = panel.getByRole('textbox', { name: '使用反馈（选填）' });
  await input.fill('希望手机上的操作更方便');
  await panel.getByRole('button', { name: '提交反馈', exact: true }).click();
  await expect(panel.getByText('反馈发送未完成，请稍后重试。')).toBeVisible();
  await expect(input).toHaveValue('希望手机上的操作更方便');
  await expect(panel.getByRole('button', { name: '暂时跳过' })).toBeEnabled();
  await panel.getByRole('button', { name: '提交反馈', exact: true }).click();
  await expect(panel.getByText('谢谢您的反馈，我们会继续努力！', { exact: true })).toBeVisible();
  await expect(input).not.toBeVisible();
  expect(writes).toEqual([
    { kind: 'rating', score: 1 },
    { kind: 'feedback', message: '希望手机上的操作更方便' },
    { kind: 'feedback', message: '希望手机上的操作更方便' },
  ]);
  await panel.getByRole('button', { name: '关闭评分' }).click();
  await expect(panel).toHaveCount(0);
});

test('blank optional feedback thanks the user without submitting an empty message', async ({ page }) => {
  const writes = await openRating(page);
  const panel = page.locator('#usage-rating');
  await panel.getByRole('button', { name: '5分', exact: true }).click();
  await panel.getByRole('textbox', { name: '使用反馈（选填）' }).fill('   ');
  await panel.getByRole('button', { name: '提交反馈', exact: true }).click();
  await expect(panel.getByText('谢谢您的反馈，我们会继续努力！', { exact: true })).toBeVisible();
  expect(writes).toEqual([{ kind: 'rating', score: 5 }]);
});

test('failed ratings keep the form and allow retry before submitting feedback', async ({ page }) => {
  const writes = await openRating(page, { failRatingOnce: true });
  const panel = page.locator('#usage-rating');
  await panel.getByRole('button', { name: '2分', exact: true }).click();
  await expect(panel.getByText('反馈发送未完成，请稍后重试。')).toBeVisible();
  await expect(panel.getByRole('textbox')).toBeVisible();
  await expect(panel.getByRole('button', { name: '提交反馈', exact: true })).toBeDisabled();
  await panel.getByRole('button', { name: '2分', exact: true }).click();
  await expect(panel.getByText(prompts[1], { exact: true })).toBeVisible();
  expect(writes).toEqual([{ kind: 'rating', score: 2 }, { kind: 'rating', score: 2 }]);
});

test('a pending rating prevents duplicates and stays closed after dismissal', async ({ page }) => {
  await openRating(page);
  let release, started;
  const gate = new Promise(resolve => { release = resolve; });
  const requested = new Promise(resolve => { started = resolve; });
  let requests = 0;
  await page.route('**/api/usage/feedback', async route => {
    requests++;
    started();
    await gate;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ saved: true }) });
  });
  const panel = page.locator('#usage-rating');
  await panel.getByRole('button', { name: '3分', exact: true }).click();
  await requested;
  await expect(panel.locator('[data-rating]:enabled')).toHaveCount(5);
  await panel.getByRole('button', { name: '3分', exact: true }).click();
  await panel.getByRole('button', { name: '关闭评分' }).click();
  const response = page.waitForResponse('**/api/usage/feedback');
  release();
  await response;
  await expect(panel).toHaveCount(0);
  expect(requests).toBe(1);
});

test('rapid score changes preserve the draft and save the final selection last', async ({ page }) => {
  await openRating(page);
  let release, started;
  const gate = new Promise(resolve => { release = resolve; });
  const requested = new Promise(resolve => { started = resolve; });
  const writes = [];
  await page.route('**/api/usage/feedback', async route => {
    const body = route.request().postDataJSON();
    writes.push(body);
    if (writes.length === 1) { started(); await gate; }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ saved: true }) });
  });
  const panel = page.locator('#usage-rating');
  await panel.getByRole('button', { name: '1分', exact: true }).click();
  await requested;
  const input = panel.getByRole('textbox');
  await input.fill('改分时保留这段建议');
  await panel.getByRole('button', { name: '5分', exact: true }).click();
  await panel.getByRole('button', { name: '3分', exact: true }).click();
  await expect(panel.getByText(prompts[2], { exact: true })).toBeVisible();
  await expect(panel.getByRole('button', { name: '3分', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect(input).toHaveValue('改分时保留这段建议');
  await expect(panel.getByRole('button', { name: '提交反馈', exact: true })).toBeDisabled();
  expect(writes).toEqual([{ kind: 'rating', score: 1 }]);
  release();
  await expect(panel.getByText('已记录您的 3 分评分。')).toBeVisible();
  expect(writes.map(body => body.score)).toEqual([1, 5, 3]);
  await expect(panel.getByRole('button', { name: '提交反馈', exact: true })).toBeEnabled();
});

test('skipping waits for the final score before thanking the user and closing', async ({ page }) => {
  await page.clock.install();
  await openRating(page);
  let release, started;
  const gate = new Promise(resolve => { release = resolve; });
  const requested = new Promise(resolve => { started = resolve; });
  const scores = [];
  await page.route('**/api/usage/feedback', async route => {
    scores.push(route.request().postDataJSON().score);
    if (scores.length === 1) { started(); await gate; }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify({ saved: true }) });
  });
  const panel = page.locator('#usage-rating');
  await panel.getByRole('button', { name: '1分', exact: true }).click();
  await requested;
  await panel.getByRole('button', { name: '5分', exact: true }).click();
  await panel.getByRole('button', { name: '暂时跳过' }).click();
  await expect(panel.getByRole('button', { name: '暂时跳过' })).toBeDisabled();
  await expect(panel.getByText('谢谢您的反馈，我们会继续努力！', { exact: true })).toHaveCount(0);
  release();
  await expect.poll(() => scores).toEqual([1, 5]);
  await expect(panel.getByText('谢谢您的反馈，我们会继续努力！', { exact: true })).toBeVisible();
  await page.clock.fastForward(1280);
  await page.clock.fastForward(220);
  await expect(panel).toHaveCount(0);
});

test('entry and automatic dismissal animate, with reduced motion supported', async ({ page }) => {
  await page.clock.install();
  await openRating(page);
  const panel = page.locator('#usage-rating');
  await expect(panel).toHaveCSS('animation-name', 'usage-rating-in');
  await panel.getByRole('button', { name: '4分', exact: true }).click();
  await panel.getByRole('button', { name: '提交反馈', exact: true }).click();
  await expect(panel.getByText('谢谢您的反馈，我们会继续努力！', { exact: true })).toBeVisible();
  await page.clock.fastForward(1000);
  await expect(panel).toBeVisible();
  await page.clock.fastForward(300);
  await expect(panel).toHaveClass(/is-closing/);
  await expect(panel).toHaveCSS('animation-name', 'usage-rating-out');
  await page.clock.fastForward(200);
  await expect(panel).toHaveCount(0);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.reload();
  await expect(panel).toHaveCSS('animation-name', 'none');
  await panel.getByRole('button', { name: '关闭评分' }).click();
  await expect(panel).toHaveCount(0);
});
