// Built dashboard only; all API calls use disposable fixtures, never a real device.
// Run with playwright-core available (or SHRINK_PLAYWRIGHT set to its module path).
const { chromium } = require(process.env.SHRINK_PLAYWRIGHT || 'playwright-core');
const assert = require('node:assert/strict');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const root = path.resolve(__dirname, '../static');
  const server = http.createServer((req, res) => {
    const file = path.resolve(root, '.' + (req.url === '/' ? '/index.html' : req.url.split('?')[0]));
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) { res.writeHead(404).end(); return; }
    res.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html');
    fs.createReadStream(file).pipe(res);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ channel: 'msedge', headless: true,
    args: ['--no-proxy-server', '--host-resolver-rules=MAP dashboard.test 127.0.0.1'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    page.on('dialog', dialog => dialog.accept());
    const administrators = [{ id: 'owner', label: '测试所有者' }, { id: 'guest', label: '测试管理员' }];
    const origins = administrators.map(a => ({ account: a.id, label: a.label, session: `fixture:FriendMessage:${a.id}` }));
    let grants = [], mutations = 0, failGrant = false, slowList = false, releaseList;
    await page.route('**/api/v1/**', async route => {
      const endpoint = new URL(route.request().url()).pathname;
      let data = {};
      if (endpoint.endsWith('/session')) data = { csrf: 'fixture' };
      else if (endpoint.endsWith('/status')) data = { counts: {}, runs: [], learning_runs: [] };
      else if (endpoint.endsWith('/settings')) data = { settings: {} };
      else if (endpoint.endsWith('/admin-sessions')) data = { items: origins, administrators, reason: '' };
      else if (endpoint.endsWith('/plugin-capabilities')) {
        const req = route.request().postDataJSON();
        assert.match(req.arguments.request_id, /^[0-9a-f]{32}$/);
        const owner = req.session === origins[0].session;
        switch (req.action) {
          case 'devices': data = [{ id: 'device', name: '测试电脑', online: false, revoked: 0, access: owner ? 'owner' : 'delegated' }]; break;
          case 'sessions': data = { result: [] }; break;
          case 'grants':
            assert.ok(owner, 'delegates must not request the owner-only grant list');
            if (slowList) await new Promise(resolve => { releaseList = resolve; });
            data = grants; break;
          case 'grant':
            assert.ok(owner); assert.equal(req.arguments.grantee, 'guest'); mutations++;
            if (failGrant) return route.abort('failed');
            grants = [{ grantee: 'guest', created: Date.now() / 1000 }]; data = { ok: true }; break;
          case 'ungrant':
            assert.ok(owner); assert.equal(req.arguments.grantee, 'guest'); mutations++;
            grants = []; data = { ok: true }; break;
          default: throw new Error(`Unexpected operation: ${req.action}`);
        }
      }
      await route.fulfill({ json: data });
    });
    await page.goto(`http://dashboard.test:${server.address().port}/`);
    await page.getByRole('button', { name: '电脑与 Agent', exact: true }).click();
    assert.equal(await page.evaluate(() => isSecureContext), false);
    const origin = page.getByLabel('当前管理员与回传会话');
    const access = page.getByRole('region', { name: '设备授权' });
    await origin.selectOption(origins[0].session);
    await access.getByText('尚未授权给其他管理员。').waitFor();
    assert.equal(await access.getByRole('option', { name: '测试所有者', exact: true }).count(), 0);
    await access.getByLabel('授权给').selectOption('guest');
    assert.ok(await access.getByRole('button', { name: '授予权限' }).isEnabled(), 'offline devices still allow grants');
    await access.getByRole('button', { name: '授予权限' }).click();
    await access.getByRole('button', { name: '撤销授权' }).waitFor();
    assert.equal(mutations, 1);
    assert.equal(await access.getByLabel('授权给').isEnabled(), false);
    fs.mkdirSync(path.resolve('runtime'), { recursive: true });
    await page.screenshot({ path: 'runtime/device-access-desktop.png', fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: 'runtime/device-access-mobile.png', fullPage: true });
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    await access.getByRole('button', { name: '撤销授权' }).click();
    await access.getByText('尚未授权给其他管理员。').waitFor();
    assert.equal(mutations, 2);
    failGrant = true;
    await access.getByLabel('授权给').selectOption('guest');
    await access.getByRole('button', { name: '授予权限' }).click();
    await access.getByRole('alert').waitFor();
    assert.equal(mutations, 3);
    assert.equal(await access.getByRole('button', { name: '授予权限' }).isEnabled(), false);
    await access.getByRole('button', { name: '刷新授权' }).click();
    await access.getByText('尚未授权给其他管理员。').waitFor();
    assert.equal(mutations, 3, 'refresh must not repeat the grant operation');
    slowList = true;
    await access.getByRole('button', { name: '刷新授权' }).click();
    await page.waitForFunction(() => document.body.textContent.includes('正在读取授权名单'));
    await origin.selectOption(origins[1].session);
    await access.getByText('你通过授权使用这台电脑。', { exact: false }).waitFor();
    releaseList();
    await page.waitForLoadState('networkidle');
    assert.equal(await access.getByRole('button', { name: '撤销授权' }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '撤销设备', exact: true }).isEnabled(), false);
    assert.deepEqual(errors, []);
    console.log('PASS HTTP grant/revoke, owner-only UI, offline management, failure recovery without repeat, stale responses, desktop/mobile layout');
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
