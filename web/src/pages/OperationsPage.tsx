import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { Empty, PageHead } from '../shared/components';
import { useDashboard } from '../shared/dashboard';
import { date } from '../shared/format';

type Task = { id: string; request: string; session: string; status: string;
  started: number; result: string; failures: number; provider: string;
  events?: { id: number; action: string; plugin: string; path: string; result: string }[] };
const labels: Record<string, string> = {
  queued: '排队', running: '执行中', testing: '测试中', deploying: '部署中',
  completed: '已部署', failed: '失败', cancelled: '已取消',
  interrupted: '已中断', timed_out: '已超时', needs_attention: '待处理',
  awaiting_deployment: '独立部署验证中',
  rolled_back: '失败后已恢复',
};

export function OperationsPage() {
  const { action, revision, setError } = useDashboard();
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selected, setSelected] = useState<Task | null>(null);
  const [request, setRequest] = useState('');
  const [session, setSession] = useState('');
  const [plugin, setPlugin] = useState('');
  const [path, setPath] = useState('main.py');
  const [content, setContent] = useState('');
  const [workResult, setWorkResult] = useState<any>(null);
  const [draft, setDraft] = useState<any>(null);
  const [deployments, setDeployments] = useState<any[]>([]);
  const work = (op: string) => void action(async () => {
    const result = await api<any>('/operations/workbench', 'POST', {
      action: op, name: plugin, path, content: op === 'write' ? content : '', session,
    });
    setWorkResult(result);
    if (result.ok === false) throw new Error(result.error || '操作未成功');
    if (op === 'read') setContent(result.text ?? JSON.stringify(result, null, 2));
    if (op === 'status') setDraft(result);
  }, '');
  useEffect(() => {
    let stopped = false;
    const load = () => api<Task[]>('/operations/tasks').then(data => {
      if(!stopped) setTasks(data);
    }).catch(e => { if(!stopped) setError(e.message); });
    void load();
    void api<any[]>('/operations/deployments').then(data => { if(!stopped) setDeployments(data); })
      .catch(e => { if(!stopped) setError(e.message); });
    const timer = setInterval(load, 5000);
    return () => { stopped = true; clearInterval(timer); };
  }, [revision]);
  const inspect = (id: string) => action(async () => {
    setSelected(await api<Task>(`/operations/tasks/${id}`));
  }, '');
  return <>
    <PageHead title="开发任务" />
    <section className="panel">
      <label>交给石英的任务<textarea rows={4} value={request}
        onChange={e => setRequest(e.target.value)} placeholder="说明要实现或修改什么，以及怎样算完成" /></label>
      <label>接收结果的管理员会话<input value={session} onChange={e => setSession(e.target.value)}
        placeholder="snowluma:FriendMessage:QQ号" /></label>
      <p>石英会自行编写、隔离测试和启用。每次最多30分钟、3轮修复。</p>
      <button disabled={!request.trim() || !session.trim()} onClick={() => void action(async () => {
        await api('/operations/tasks', 'POST', { request, session }); setRequest('');
      }, '已提交开发任务，完成后石英会回复')}>开始任务</button>
    </section>
    <section className="panel">
      {!tasks.length ? <Empty text="还没有开发任务。" /> : <table className="table">
        <thead><tr><th>任务</th><th>状态</th><th>开始</th><th>操作</th></tr></thead>
        <tbody>{tasks.map(t => <tr key={t.id}>
          <td><button className="ghost" onClick={() => void inspect(t.id)}>{t.request.slice(0, 90)}</button></td>
          <td>{labels[t.status] || t.status}</td><td>{date(t.started)}</td>
          <td>{['queued', 'running', 'testing', 'deploying'].includes(t.status)
            ? <button onClick={() => void action(() => api(`/operations/tasks/${t.id}/cancel`, 'POST'), '已取消')}>取消</button>
            : <button onClick={() => void action(() => api(`/operations/tasks/${t.id}/resume`, 'POST'), '已续做')}>续做</button>}</td>
        </tr>)}</tbody>
      </table>}
    </section>
    {selected && <section className="panel">
      <h2>任务详情 <button className="ghost" onClick={() => setSelected(null)}>关闭</button></h2>
      <p>{selected.request}</p>
      <p>{labels[selected.status]} · {selected.provider} · 失败测试 {selected.failures} 轮</p>
      <pre style={{ whiteSpace: 'pre-wrap' }}>{selected.result || '尚无最终结果'}</pre>
      <button onClick={() => void inspect(selected.id)}>刷新详情</button>
      {selected.events?.map(e => <details key={e.id}>
        <summary>{e.action} {e.plugin} {e.path}</summary>
        <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{e.result}</pre>
      </details>)}
    </section>}
    <section className="panel">
      <h2>核心部署版本</h2>
      {!deployments.length ? <Empty text="暂无独立部署记录。" /> : deployments.map(d => <details key={d.id}>
        <summary>{date(d.created)} · {d.action === 'rollback' ? '恢复上一版' : '部署更新'} · {labels[d.status] || d.status}</summary>
        <p>版本记录：<code>{d.id}</code> · {d.plugin}</p>
        <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(d.validation ?? { error: d.error, status: d.status }, null, 2)}</pre>
      </details>)}
    </section>
    <section className="panel">
      <h2>插件工作台</h2>
      <p>与石英开发子代理使用同一份草稿和测试证据。修改后需重新测试才能部署。</p>
      <label>插件目录名或新插件短名<input value={plugin} onChange={e => {
        setPlugin(e.target.value); setDraft(null); setWorkResult(null);
      }} placeholder="astrbot_plugin_shirnk_ext_reminder" /></label>
      <div className="toolbar">
        {Object.entries({ list: '查看插件', checkout: '检出到草稿', status: '草稿状态', diff: '查看差异',
          check: '语法检查', test: '隔离测试', deploy: '部署启用', rollback: '恢复上一版' }).map(([op, label]) =>
          <button key={op} disabled={!session.trim() || (op !== 'list' && !plugin.trim())} onClick={() => {
            if (!['deploy', 'rollback'].includes(op) || window.confirm(`${label}“${plugin}”？`)) work(op);
          }}>{label}</button>)}
      </div>
      {draft && <p>测试证据：{draft.test_current ? draft.test?.ok ? '当前草稿测试通过' : '当前草稿测试失败' : '尚未测试或草稿已改变'} ·
        {draft.rollback_available ? '有上一版备份' : '暂无备份'} · {draft.conflict ? '线上版本冲突' : '无已发现冲突'}</p>}
      <label>文件路径<input value={path} list="draft-files" onChange={e => setPath(e.target.value)} /></label>
      <datalist id="draft-files">{draft?.draft_files?.map((f: string) => <option key={f} value={f} />)}</datalist>
      <button disabled={!session || !plugin || !path} onClick={() => work('read')}>读取草稿</button>
      <textarea rows={14} value={content} onChange={e => setContent(e.target.value)} spellCheck={false} />
      <button disabled={!session || !plugin || !path} onClick={() => work('write')}>保存草稿</button>
      {workResult && <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>
        {workResult.text ?? JSON.stringify(workResult, null, 2)}</pre>}
    </section>
  </>;
}

export { CapabilitiesPage } from './CapabilitiesPage';
