// Run explicitly against the dashboard; creates one non-applying learning preview.
const { chromium } = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const runtime = path.resolve(__dirname, '../runtime');
  const secretFile = fs.readdirSync(runtime).find(n => n.startsWith('admin-') && n.endsWith('.txt'));
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto('http://127.0.0.1:6186/');
    await page.getByLabel('管理员密码').fill(fs.readFileSync(path.join(runtime, secretFile), 'utf8').trim());
    await page.getByRole('button', { name: '进入后台' }).click();
    await page.getByRole('heading', { name: '成长节奏' }).waitFor();
    const before = await (await page.request.get('http://127.0.0.1:6186/api/v1/status')).json();
    await page.getByRole('button', { name: /人格与成长/ }).click();
    await page.getByRole('heading', { name: '学习记录与预演' }).waitFor();
    const previewResponse = page.waitForResponse(r => r.url().endsWith('/learning/preview') && r.request().method() === 'POST');
    await page.getByRole('button', { name: '预演一次' }).click();
    assert.equal((await (await previewResponse).json()).status, 'queued');
    let after, run;
    for (let i = 0; i < 60; i++) {
      after = await (await page.request.get('http://127.0.0.1:6186/api/v1/status')).json();
      run = after.learning_runs.find(r => r.diagnostics.mode === 'preview' && r.id > (before.learning_runs[0]?.id || 0));
      if (run && run.status !== 'running') break;
      await new Promise(resolve => setTimeout(resolve, 2000));
    }
    assert.ok(run && run.status !== 'running', 'Preview did not finish');
    assert.equal(after.active_persona, before.active_persona);
    assert.equal(after.counts.memories, before.counts.memories);
    assert.equal(after.last_learning, before.last_learning);
    assert.equal(after.next_learning_check, before.next_learning_check);
    await page.getByRole('button', { name: '刷新记录', exact: true }).click();
    await page.getByRole('button', { name: new RegExp(`第 ${run.id} 轮`) }).filter({ hasText: run.diagnostics.reason }).waitFor();
    await page.screenshot({ path: path.join(runtime, 'growth-desktop.png'), fullPage: true });
    await page.getByRole('button', { name: new RegExp(`第 ${run.id} 轮`) }).click();
    await page.getByRole('heading', { name: `第 ${run.id} 轮学习` }).waitFor();
    await page.screenshot({ path: path.join(runtime, 'growth-detail.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: path.join(runtime, 'growth-mobile.png'), fullPage: true });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth), false);
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({
      run_id: run.id, status: run.status, decision: run.diagnostics.decision,
      reason: run.diagnostics.reason, candidate: run.diagnostics.candidate,
      evidence_count: run.diagnostics.evidence.length,
      eligible_expressions: run.diagnostics.expressions_eligible,
      error_type: run.diagnostics.error_type,
      state_unchanged: true, desktop_mobile: 'rendered_without_overflow', page_errors: errors,
    }));
    const session = await (await page.request.get('http://127.0.0.1:6186/api/v1/session')).json();
    await page.request.post('http://127.0.0.1:6186/api/v1/logout', {
      headers: { Origin: 'http://127.0.0.1:6186', 'X-CSRF-Token': session.csrf },
    });
  } finally {
    await context.close();
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
