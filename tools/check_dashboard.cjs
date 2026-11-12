// Isolated browser regression against the local disposable preview (tools/preview.py --seed).
// Usage: node tools/check_dashboard.cjs <data-dir> [port]
const { chromium } = require('playwright-core');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const PINYIN = /shi\s*ying/i;
const DECORATIVE = ['SHRINK', 'PERSONAL COMPANION', 'WELCOME BACK', '记得人，也记得来处', '一张表情就够了', '好好接住', 'QUARTZ', '打开石英的房间'];

(async () => {
  const data = path.resolve(process.argv[2] || path.join(__dirname, '../runtime/preview'));
  const port = process.argv[3] || '6187';
  const shots = path.resolve(__dirname, '../runtime');
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const nav = async name => {
    if (!(await page.locator('.nav').count())) await page.locator('[data-slot="sidebar-trigger"]').first().click();
    await page.locator('.nav').getByRole('button', { name: new RegExp('^' + name) }).click();
  };
  const clean = async label => {
    const text = await page.locator('body').innerText();
    assert.ok(!PINYIN.test(text), `pinyin on ${label}`);
    for (const word of DECORATIVE) assert.ok(!text.includes(word), `decorative text "${word}" on ${label}`);
  };
  try {
    await page.goto(`http://127.0.0.1:${port}/`);
    await clean('login');
    await page.getByLabel('管理员密码').fill(fs.readFileSync(path.join(data, 'initial-password.txt'), 'utf8').trim());
    await page.getByRole('button', { name: '登录' }).click();
    await page.getByRole('heading', { name: '概览' }).waitFor();
    await page.getByText('运行状态').waitFor();
    await clean('overview');
    await page.screenshot({ path: path.join(shots, 'v4-overview.png'), fullPage: true });

    await nav('人物');
    await page.getByPlaceholder('搜索名字、称呼或账号').fill('白宝');
    await page.locator('.list-item').filter({ hasText: '阿白' }).first().click();
    await page.getByText('在做rust项目，常聊技术。').waitFor();
    await page.getByPlaceholder('新称呼').fill('小E');
    await page.getByRole('button', { name: '添加' }).click();
    await page.locator('.chip').filter({ hasText: '小E' }).waitFor();
    await clean('people');
    await page.screenshot({ path: path.join(shots, 'v4-people.png'), fullPage: true });

    await nav('记忆');
    await page.locator('tr.clickable').filter({ hasText: '阿白在做一个rust项目' }).click();
    await page.getByRole('button', { name: '纠正' }).click();
    await page.getByLabel('记忆内容').fill('阿白在做一个rust项目，已经上线');
    await page.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByRole('dialog').waitFor({ state: 'detached' });
    await page.locator('#content td').filter({ hasText: '阿白在做一个rust项目，已经上线' }).first().waitFor();
    await page.locator('[data-slot="popover-trigger"]').filter({ hasText: '状态' }).click();
    await page.getByRole('option', { name: '候选' }).click();
    await page.keyboard.press('Escape');
    await page.locator('tr.clickable').first().waitFor();
    await clean('memory');
    await page.screenshot({ path: path.join(shots, 'v4-memory.png'), fullPage: true });

    await nav('定向学习');
    await page.getByRole('heading', { name: /^待你确认（\d+）$/ }).waitFor();
    await page.getByRole('button', { name: '不对', exact: true }).first().waitFor();
    // Every question shows what it is based on: the chat lines it came from, or a summary's memories.
    await page.locator('.proposal').filter({ hasText: '开杀' }).locator('.chatline.cited').first().waitFor();
    await page.getByText('小结是根据这些记忆写的').waitFor();
    assert.equal(await page.locator('.proposal').filter({ hasText: '黑话“开杀”' }).count(), 1, 'one card per question');
    await page.screenshot({ path: path.join(shots, 'v5-review.png'), fullPage: true });
    await page.getByRole('button', { name: '新对话' }).click();
    await page.getByPlaceholder('说点石英应该知道的事').fill('阿白 在学吉他');
    await page.getByRole('button', { name: '发送' }).click();
    await page.getByText('我拆成了一条记忆，确认后写入。').waitFor();
    await page.getByRole('button', { name: '采纳' }).first().click();
    await page.getByRole('button', { name: '让石英换个说法问' }).click();
    await page.getByRole('button', { name: '不对' }).first().waitFor();
    await page.getByRole('button', { name: '不对' }).first().click();
    await clean('teach');
    await page.screenshot({ path: path.join(shots, 'v4-teach.png'), fullPage: true });

    await nav('整理记录');
    await page.locator('tr.clickable').filter({ hasText: '整理群聊' }).first().click();
    await page.getByText('被拦下的条目').waitFor();
    await page.getByRole('button', { name: '关闭' }).click();
    await page.getByRole('button', { name: '聊天摘要' }).click();
    await page.getByText(/小周下周去上海出差/).waitFor();
    await clean('runs');

    await nav('人格');
    await page.getByLabel('说话习惯').fill('说话随和，常常只回几个字。');
    await page.getByRole('button', { name: '保存为新版本' }).click();
    await page.getByText('好耶').waitFor();
    await clean('persona');

    await nav('表情');
    const sample = path.join(shots, 'engineering-test.png');
    if (fs.existsSync(sample)) {
      await page.locator('input[type=file]').setInputFiles(sample);
      await page.getByRole('button', { name: /上传/ }).click();
      await page.locator('.meme').first().waitFor();
      await page.locator('.meme').first().getByRole('button', { name: '禁用' }).click();
      await page.locator('.meme.off').first().waitFor();
      await page.getByRole('button', { name: '全选本页' }).click();
      await page.locator('.selection-bar').getByRole('button', { name: '启用' }).click();
      await page.locator('.meme:not(.off)').first().waitFor();
    }
    await clean('memes');

    await nav('设置');
    await page.getByLabel('主动插话概率').fill('0.05');
    await page.getByRole('button', { name: '保存设置' }).click();
    await page.getByRole('status').filter({ hasText: '已保存' }).waitFor();
    await clean('settings');
    await page.screenshot({ path: path.join(shots, 'v4-desktop.png'), fullPage: true });

    await page.setViewportSize({ width: 390, height: 844 });
    for (const name of ['概览', '聊天与图片', '人物', '记忆', '表情', '人格', '提示词', '定向学习', '经验', '整理记录', '夜间能力维护', '开发任务', '电脑与 Agent', '插件与能力', '设置']) {
      await nav(name);
      await page.waitForTimeout(250);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, `overflow on ${name}`);
    }
    await nav('概览');
    await page.screenshot({ path: path.join(shots, 'v4-mobile.png'), fullPage: true });
    await page.getByRole('button', { name: '切换主题' }).click();
    await page.getByRole('menuitem', { name: '深色' }).click();
    assert.equal(await page.evaluate(() => document.documentElement.classList.contains('dark')), true, 'dark theme');
    await page.getByRole('button', { name: '切换主题' }).click();
    await page.getByRole('menuitem', { name: '浅色' }).click();
    assert.equal(await page.evaluate(() => document.documentElement.classList.contains('light')), true, 'light theme');
    await page.getByRole('button', { name: '管理员菜单' }).click();
    await page.getByRole('menuitem', { name: '退出' }).click();
    await page.getByRole('button', { name: '登录' }).waitFor();
    assert.deepEqual(errors, []);
    console.log('DASHBOARD_OK all pages at desktop 1440 and mobile 390; nickname edit, memory correction, teach accept and review, run detail, persona, meme toggle and batch, settings, theme menu, logout; no pinyin or decorative copy');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
