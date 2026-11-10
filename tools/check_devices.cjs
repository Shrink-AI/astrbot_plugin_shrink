// Run against built static assets served on 127.0.0.1:6187. All API traffic is isolated fixtures.
const { chromium } = require('playwright-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

(async () => {
  const browser = await chromium.launch({ channel: 'msedge', headless: true,
    args: ['--no-proxy-server', '--host-resolver-rules=MAP dashboard.test 127.0.0.1'] });
  const context = await browser.newContext({ viewport: {width: 1440, height: 1000} });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('dialog', dialog => dialog.accept());
  let candidates = [{session:'fixture:FriendMessage:owner',label:'测试管理员'}];
  let devices = [{id:'fixture-device',name:'测试电脑',online:true,revoked:0}];
  let starts = 0, queries = 0, approved = 0;
  let deviceError = false, dropStart = false, originalRequest;
  const writable = {id:'managed',native_id:'native-fixture',agent:'claude',
    cwd:'D:\\Workspace\\' + 'long-path-'.repeat(12),state:'running',mode:'managed',
    capabilities:['read','send','interrupt','approve']};
  const sessionRows = [{...writable,id:'observed',mode:'observe',capabilities:['read']},writable];
  const watches = [{id:'watch-fixture',session:'managed',active:1}];
  await page.route('**/api/v1/**', async route => {
    const endpoint = new URL(route.request().url()).pathname;
    let data = {};
    if (endpoint.endsWith('/session')) data = {csrf:'fixture'};
    else if (endpoint.endsWith('/status')) data = {counts:{},runs:[],learning_runs:[]};
    else if (endpoint.endsWith('/settings')) data = {settings:{}};
    else if (endpoint.endsWith('/admin-sessions')) data = {items:candidates,reason:'未配置管理员或平台'};
    else if (endpoint.endsWith('/plugin-capabilities')) {
      const request = route.request().postDataJSON();
      const args = request.arguments;
      assert.match(args.request_id, /^[0-9a-f]{32}$/);
      switch(request.action) {
        case 'devices':
          if (deviceError) return route.fulfill({status:503,json:{detail:'电脑控制插件未启用'}});
          data = devices; break;
        case 'sessions': case 'discover': data = {command_id:'read-fixture',result:sessionRows}; break;
        case 'read': data = {command_id:'read-fixture',result:writable}; break;
        case 'start':
          starts++; originalRequest = args.request_id;
          if (dropStart) return route.abort('failed');
          data = {command_id:'command-fixture',state:'pending'}; break;
        case 'result':
          queries++;
          assert.ok(args.command_id === 'command-fixture' || args.request_id === originalRequest);
          data = {command_id:'command-fixture',result:writable}; break;
        case 'watches': data = watches; break;
        case 'unwatch':
          assert.equal(args.watch_id,'watch-fixture');
          watches[0].active = 0; data = {ok:true}; break;
        case 'timeline':
          data = [{seq:1,kind:'confirmation',state:'waiting_confirmation',detail:{
            approval_id:'approval-fixture',expires:new Date(Date.now()+60000).toISOString(),
            request:{operation:'只读测试目录'}}}]; break;
        case 'approve':
          assert.equal(args.session_id,'managed');
          assert.equal(args.approval_id,'approval-fixture');
          approved++; data={ok:true}; break;
        default: throw new Error('Unexpected fixture operation '+request.action);
      }
    }
    await route.fulfill({json:data});
  });
  const open = async () => {
    await page.goto('http://dashboard.test:6187/');
    await page.getByRole('button',{name:'电脑与 Agent',exact:true}).click();
  };
  try {
    await open();
    assert.equal(await page.evaluate(() => isSecureContext), false);
    assert.equal(await page.evaluate(() => typeof crypto.getRandomValues), 'function');
    await page.getByRole('button',{name:'claude · native-fixtu'}).first().waitFor();
    await page.getByRole('button',{name:'claude · native-fixtu'}).first().click();
    assert.equal(await page.getByRole('button',{name:'追加指令',exact:true}).isEnabled(),false);
    await page.getByRole('button',{name:'claude · native-fixtu'}).last().click();
    await page.getByLabel('任务或追加指令').fill('仅执行测试');
    await page.getByLabel('新任务工作目录').fill('D:\\Fixture');
    await page.getByRole('button',{name:'新建任务',exact:true}).click();
    await page.getByRole('button',{name:'查询原命令结果'}).click();
    assert.equal(starts,1); assert.equal(queries,1);
    dropStart = true;
    await page.getByRole('button',{name:'新建任务',exact:true}).click();
    await page.getByText('尚未确认服务器是否收到命令', {exact:false}).waitFor();
    await page.getByRole('button',{name:'查询原命令结果'}).click();
    assert.equal(starts,2); assert.equal(queries,2);
    await page.getByRole('button',{name:'刷新监工列表'}).click();
    await page.getByRole('button',{name:'取消监工',exact:true}).click();
    await page.getByText('managed · 已停止').waitFor();
    await page.getByRole('button',{name:'刷新事件与确认'}).click();
    await page.getByRole('button',{name:'批准本次'}).click();
    assert.equal(approved,1);
    fs.mkdirSync(path.resolve('runtime'), {recursive:true});
    await page.evaluate(() => scrollTo(0,0));
    await page.screenshot({path:'runtime/devices-desktop.png',fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:'runtime/devices-mobile.png',fullPage:true});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    candidates.push({session:'fixture:FriendMessage:second',label:'另一位测试管理员'});
    await open();
    assert.equal(await page.getByLabel('结果发送到').inputValue(),'');
    await page.getByLabel('结果发送到').selectOption(candidates[0].session);
    await page.getByRole('button',{name:'claude · native-fixtu'}).first().waitFor();
    devices = [];
    await page.getByLabel('结果发送到').selectOption(candidates[1].session);
    await page.getByRole('option',{name:'暂无已配对设备'}).waitFor({state:'attached'});
    assert.equal(await page.getByRole('button',{name:'claude · native-fixtu'}).count(),0);
    deviceError = true;
    await page.getByRole('button',{name:'刷新设备',exact:true}).click();
    await page.getByText('电脑控制插件未启用',{exact:true}).waitFor();
    assert.deepEqual(errors,[]);
    console.log('PASS insecure HTTP IDs, pending/unknown query-only recovery, capabilities, approvals, monitor cancellation, session reset, empty/error states, desktop/mobile layout');
  } finally { await browser.close(); }
})().catch(error => {console.error(error);process.exitCode=1;});
