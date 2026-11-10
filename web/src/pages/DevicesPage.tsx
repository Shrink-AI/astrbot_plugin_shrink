import { useState } from 'react';
import { api } from '../api/client';
import { PageHead } from '../shared/components';
import { useDashboard } from '../shared/dashboard';

type Device = { id: string; name: string; online: boolean; revoked: number };
type Session = { id: string; agent: string; cwd: string; state: string; mode: string; native_id: string; capabilities?: string[] };

export function DevicesPage() {
  const { action } = useDashboard();
  const [origin, setOrigin] = useState('');
  const [devices, setDevices] = useState<Device[]>([]);
  const [device, setDevice] = useState('');
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selected, setSelected] = useState('');
  const [agent, setAgent] = useState('claude');
  const [cwd, setCwd] = useState('');
  const [prompt, setPrompt] = useState('');
  const [query, setQuery] = useState('');
  const [approval, setApproval] = useState('');
  const [watch, setWatch] = useState('');
  const [watches, setWatches] = useState<Array<{ id: string; session: string; active: number; checked: number }>>([]);
  const [output, setOutput] = useState<any>(null);
  const [commandId, setCommandId] = useState('');
  const invoke = async (op: string, args: Record<string, unknown> = {}) => {
    const value = await api<any>('/operations/plugin-capabilities', 'POST', {
      capability: 'computer.control', action: op, session: origin,
      arguments: { device_id: device, request_id: crypto.randomUUID(), ...args },
    });
    setOutput(value);
    if (value?.command_id) setCommandId(value.command_id);
    if (value?.result?.ok === false || value?.ok === false) throw new Error(value?.result?.error || value?.error);
    return value;
  };
  const run = (op: string, args: Record<string, unknown> = {}) => void action(() => invoke(op, args), '');
  return <>
    <PageHead title="电脑与 Agent" />
    <section className="panel">
      <label>管理员原会话<input value={origin} onChange={e => setOrigin(e.target.value)} placeholder="平台:FriendMessage:管理员 ID" /></label>
      <p>配对、控制和结果回传使用这个会话。仅观察的会话不代表已经接管。</p>
      <button disabled={!origin} onClick={() => void action(async () => setDevices(await invoke('devices')), '')}>刷新设备</button>
      <button disabled={!origin} onClick={() => run('pair')}>生成配对码</button>
      <select value={device} onChange={e => { setDevice(e.target.value); setSessions([]); setSelected(''); }}>
        <option value="">选择电脑</option>
        {devices.filter(d => !d.revoked).map(d => <option key={d.id} value={d.id}>{d.name} · {d.online ? '在线' : '离线'}</option>)}
      </select>
      <button disabled={!device} onClick={() => {
        if (window.confirm('撤销此电脑后，需要重新配对才能控制。继续？')) run('revoke');
      }}>撤销设备</button>
    </section>
    <section className="panel">
      <label>Agent<select value={agent} onChange={e => setAgent(e.target.value)}>
        <option value="claude">Claude Code</option><option value="codex">Codex</option><option value="dsh">DSH</option>
      </select></label>
      <label>已有任务关键词<input value={query} onChange={e => setQuery(e.target.value)} /></label>
      <button disabled={!device} onClick={() => void action(async () => {
        const r = await invoke('discover', { agent, query }); setSessions(r.result || []);
      }, '')}>发现已有会话</button>
      <button disabled={!device} onClick={() => void action(async () => {
        const r = await invoke('sessions'); setSessions(r.result || []);
      }, '')}>托管会话</button>
      <table className="table"><thead><tr><th>Agent / 原生会话</th><th>目录</th><th>控制方式</th><th>状态</th></tr></thead>
        <tbody>{sessions.map(s => <tr key={s.id}>
          <td><button className="ghost" onClick={() => { setSelected(s.id); run('read', { session_id: s.id }); }}>{s.agent} / {s.native_id}</button></td>
          <td>{s.cwd}</td><td>{s.mode === 'managed' ? '托管' : '仅观察'}<br />{s.capabilities?.join(' · ')}</td><td>{s.state}</td>
        </tr>)}</tbody></table>
      <label>当前会话 ID<input value={selected} onChange={e => setSelected(e.target.value)} /></label>
      <label>新任务工作目录<input value={cwd} onChange={e => setCwd(e.target.value)} /></label>
      <label>任务或追加指令<textarea value={prompt} onChange={e => setPrompt(e.target.value)} rows={4} /></label>
      <button disabled={!device || !cwd || !prompt} onClick={() => run('start', { agent, cwd, prompt })}>新建任务</button>
      <button disabled={!selected || !prompt} onClick={() => run('send', { session_id: selected, prompt })}>向当前会话追加</button>
      <button disabled={!selected} onClick={() => run('interrupt', { session_id: selected })}>中断</button>
      <button disabled={!selected} onClick={() => {
        if (window.confirm('仅恢复已退出的原托管进程。不会接管仍在运行的原窗口，也不会重发旧指令。继续？'))
          void action(async () => { const r = await invoke('resume', { session_id: selected }); if (r.result?.id) setSelected(r.result.id); }, '');
      }}>恢复历史会话</button>
      <button disabled={!selected} onClick={() => run('timeline', { session_id: selected })}>事件时间线</button>
      <button disabled={!selected} onClick={() => void action(async () => {
        const r = await invoke('watch', { session_id: selected }); if (r.watch_id) setWatch(r.watch_id);
      }, '')}>持续监工</button>
      <label>监工 ID<input value={watch} onChange={e => setWatch(e.target.value)} /></label>
      <button disabled={!device} onClick={() => void action(async () => setWatches(await invoke('watches')), '')}>查看已有监工</button>
      {watches.map(w => <p key={w.id}><button className="ghost" onClick={() => { setWatch(w.id); setSelected(w.session); }}>{w.id}</button>
        {' · '}{w.active ? '监工中' : '已停止'}{' · '}{w.checked ? new Date(w.checked * 1000).toLocaleString() : '等待首次检查'}</p>)}
      <button disabled={!watch} onClick={() => run('unwatch', { watch_id: watch })}>取消监工</button>
      <label>待确认操作 ID<input value={approval} onChange={e => setApproval(e.target.value)} /></label>
      <button disabled={!selected || !approval} onClick={() => run('approve', { session_id: selected, approval_id: approval, allow: false })}>拒绝</button>
      <button disabled={!selected || !approval} onClick={() => {
        if (window.confirm('请先核对事件中待确认的具体操作。批准执行？'))
          run('approve', { session_id: selected, approval_id: approval, allow: true });
      }}>批准本次</button>
    </section>
    {output !== null && <section className="panel"><h3>实际返回</h3>
      {output.state === 'pending' && <p>命令已保存，正在等待设备结果。请查询原命令，不要重复提交。</p>}
      <button disabled={!commandId} onClick={() => run('result', { command_id: commandId })}>查询原命令结果</button>
      <pre>{JSON.stringify(output, null, 2)}</pre></section>}
  </>;
}
