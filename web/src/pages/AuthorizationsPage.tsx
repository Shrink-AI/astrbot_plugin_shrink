import { useEffect, useState } from 'react';
import { api } from '@/api/client';
import { commandResult, deviceCommand } from '@/api/commands';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { PageHead, Panel } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';
import { DeviceAccess, type Administrator } from './DeviceAccess';

type Policy = { id: string; device: string; execution_session: string; administrator: string; categories: string[]; state: string; version: number; future: boolean; expires: number | null };
type Confirmation = { id: string; state: string; reason: string; operation_id: string | null; body: { task: string; approval_id: string; device: string; category: string; origin: string }; receipt: unknown };
type Data = { policies: Policy[]; confirmations: Confirmation[]; audit: unknown[] };
export function AuthorizationsPage() {
  const { action, busy, revision, setError } = useDashboard();
  const [origins, setOrigins] = useState<Array<{session: string; label: string; account?: string}>>([]);
  const [admins, setAdmins] = useState<Administrator[]>([]);
  const [session, setSession] = useState('');
  const [data, setData] = useState<Data | null>(null);
  const [devices, setDevices] = useState<Array<{id: string; name: string; access?: 'owner' | 'delegated'}>>([]);
  const [device, setDevice] = useState('');
  const [provider, setProvider] = useState('');
  const [execution, setExecution] = useState('');
  const [task, setTask] = useState('');
  const [categories, setCategories] = useState('read,execute');
  const [future, setFuture] = useState(false);
  const [expires, setExpires] = useState('');
  useEffect(() => {
    api<{items: typeof origins; administrators: Administrator[]}>('/operations/admin-sessions')
      .then(r => { setOrigins(r.items); setAdmins(r.administrators || []); if (r.items.length === 1) setSession(r.items[0].session); })
      .catch(e => setError(e.message));
    api<Array<{name: string; provider: string}>>('/operations/plugin-capabilities')
      .then(rows => { const found = rows.filter(r => r.name === 'computer.control'); if (found.length === 1) setProvider(found[0].provider); })
      .catch(e => setError(e.message));
  }, []);
  useEffect(() => {
    if (!session) return;
    let live = true;
    setData(null); setDevices([]); setDevice('');
    api<Data>('/authorizations?session=' + encodeURIComponent(session)).then(r => { if (live) setData(r); }).catch(e => setError(e.message));
    deviceCommand(session, '', 'devices').then(r => { if (live) setDevices(commandResult(r).data || []); }).catch(e => setError(e.message));
    return () => { live = false; };
  }, [session, revision]);
  const run = (op: string, args: Record<string, unknown>) => void action(() => api('/authorizations/action', 'POST', {
    session, action: op, arguments: args, ...(future && op === 'grant' ? { future_instruction: '未来同类任务' } : {}),
  }));
  return <>
    <PageHead title="授权管理" description="设备访问权与自动批准分别管理；批准始终返回原任务会话。" />
    <Panel title="管理员与设备">
      <label>原会话<select className="m-2 rounded border p-2" value={session} onChange={e => setSession(e.target.value)}>
        <option value="">选择管理员会话</option>{origins.map(o => <option key={o.session} value={o.session}>{o.label}</option>)}
      </select></label>
      <label>设备<select className="m-2 rounded border p-2" value={device} onChange={e => setDevice(e.target.value)}>
        <option value="">选择设备</option>{devices.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
      </select></label>
    </Panel>
    <DeviceAccess origin={session} device={devices.find(d => d.id === device)} administrators={admins}
      account={origins.find(o => o.session === session)?.account || session.split(':').at(-1) || ''} />
    <Panel title="设置自动批准">
      <div className="grid gap-3">
        <label>电脑能力提供者<Input value={provider} onChange={e => setProvider(e.target.value)} /></label>
        <label>执行会话<Input value={execution} onChange={e => setExecution(e.target.value)} /></label>
        <label>核心任务（可选）<Input value={task} onChange={e => setTask(e.target.value)} /></label>
        <label>允许类别（read / execute / write / delete_data / grant_permission，逗号分隔）
          <Input value={categories} onChange={e => setCategories(e.target.value)} /></label>
        <label>有效期（留空表示持续至撤销）<Input type="datetime-local" value={expires} onChange={e => setExpires(e.target.value)} /></label>
        <label><input type="checkbox" checked={future} onChange={e => setFuture(e.target.checked)} /> 明确授权这台设备、当前管理员的未来同类任务</label>
        <Button disabled={busy || !session || !device || !provider || !execution} onClick={() => run('grant', {
          device, provider, execution_session: execution, task, categories: categories.split(',').map(s => s.trim()),
          future, expires: expires ? Date.parse(expires) / 1000 : null,
        })}>保存明确范围的授权</Button>
      </div>
    </Panel>
    <Panel title="持续策略">
      {data?.policies.map(p => <div key={p.id} className="my-3 rounded border p-3">
        <p>{p.device} · {p.execution_session} · 管理员 {p.administrator} · {p.categories.join(', ')} · {p.state} · v{p.version}</p>
        <p>{p.future ? '含未来同类任务' : '仅当前任务／执行会话'} · {p.expires ? new Date(p.expires * 1000).toLocaleString() : '持续至撤销'}</p>
        <Button variant="outline" disabled={busy || p.state !== 'active'} onClick={() => run('pause', {policy_id: p.id})}>暂停</Button>
        <Button variant="destructive" disabled={busy || p.state === 'revoked'} onClick={() => run('revoke', {policy_id: p.id})}>撤销</Button>
      </div>)}
    </Panel>
    <Panel title="逐次批准记录">
      {data?.confirmations.map(c => <div key={c.id} className="my-3 rounded border p-3">
        <p>{c.body.device} · {c.body.task || '执行会话'} · {c.body.approval_id} · {c.body.category} · {c.state}</p>
        <p>{c.reason}</p>
        <Button disabled={busy || !['waiting_user', 'held'].includes(c.state)} onClick={() => run('answer', {confirmation_id: c.id, task: c.body.task})}>同意此任务的这项操作</Button>
        <Button variant="destructive" disabled={busy || !['waiting_user', 'held'].includes(c.state)} onClick={() => run('answer', {confirmation_id: c.id, task: c.body.task, allow: false})}>拒绝此操作</Button>
        <Button variant="outline" disabled={busy || c.state !== 'unknown'} onClick={() => run('reconcile', {confirmation_id: c.id})}>核对原审批回执</Button>
        <details><summary>审计详情</summary><pre className="whitespace-pre-wrap break-all text-xs">{JSON.stringify(c, null, 2)}</pre></details>
      </div>)}
    </Panel>
  </>;
}
