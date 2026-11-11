import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import { commandResult, deviceCommand, requestId } from '../api/commands';
import { PageHead } from '../shared/components';
import { DeviceAccess, type Administrator } from './DeviceAccess';

type Device = { id: string; name: string; online: boolean; revoked: number; access?: 'owner' | 'delegated' };
type Session = { id: string; agent: string; cwd: string; state: string; mode: string; native_id: string; capabilities?: string[] };
type Watch = { id: string; session: string; active: number; checked: number };
const names: Record<string, string> = { running: '执行中', completed: '已完成', failed: '失败', unknown: '待核对', unverified: '未核实', interrupted: '已中断', waiting_confirmation: '等待确认', starting: '启动中' };

export function DevicesPage() {
  const [origins, setOrigins] = useState<Array<{session: string; label: string; account?: string}>>([]);
  const [administrators, setAdministrators] = useState<Administrator[]>([]);
  const [origin, setOrigin] = useState('');
  const [originReason, setOriginReason] = useState('正在读取管理员会话…');
  const [devices, setDevices] = useState<Device[]>([]);
  const [device, setDevice] = useState('');
  const [loadingDevices, setLoadingDevices] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selected, setSelected] = useState('');
  const [agent, setAgent] = useState('claude');
  const [cwd, setCwd] = useState('');
  const [prompt, setPrompt] = useState('');
  const [query, setQuery] = useState('');
  const [watches, setWatches] = useState<Watch[]>([]);
  const [timeline, setTimeline] = useState<any[]>([]);
  const [output, setOutput] = useState<any>(null);
  const [pending, setPending] = useState<{id: string; op: string; byRequest?: boolean} | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const generation = useRef(0);
  const active = sessions.find(s => s.id === selected);
  const currentDevice = devices.find(d => d.id === device);
  const online = currentDevice?.online;
  const supports = (cap: string) => !!online && !!active?.capabilities?.includes(cap);
  const reset = () => { generation.current++; setSessions([]); setSelected(''); setTimeline([]); setWatches([]); setPending(null); setOutput(null); setError(''); setBusy(false); setLoadingDevices(false); };
  const chooseOrigin = (value: string) => { reset(); setDevice(''); setDevices([]); setOrigin(value); };
  const chooseDevice = (value: string) => { reset(); setDevice(value); };
  useEffect(() => {
    let live = true;
    api<{items: Array<{session: string; label: string; account?: string}>; administrators?: Administrator[]; reason: string}>('/operations/admin-sessions')
      .then(r => { if (!live) return; setOrigins(r.items); setAdministrators(r.administrators || []); setOriginReason(r.reason || '请选择接收结果的管理员私聊'); if (r.items.length === 1) setOrigin(r.items[0].session); })
      .catch(e => { if (live) setOriginReason(e.message); });
    return () => { live = false; generation.current++; };
  }, []);
  const loadDevices = async () => {
    if (!origin) return;
    const version = generation.current;
    setLoadingDevices(true); setError('');
    try {
      const result = commandResult(await deviceCommand(origin, '', 'devices')).data;
      if (version !== generation.current) return;
      const list = (Array.isArray(result) ? result : []).filter((d: Device) => !d.revoked);
      setDevices(list); if (list.length === 1) setDevice(list[0].id);
    } catch (e) { if (version === generation.current) setError((e as Error).message); }
    finally { if (version === generation.current) setLoadingDevices(false); }
  };
  useEffect(() => { void loadDevices(); }, [origin]);
  const accept = (op: string, raw: any) => {
    setOutput(raw);
    if (raw?.result?.ok === false || raw?.ok === false) setPending(null);
    const result = commandResult(raw);
    if (result.pending) { setPending({ id: raw.command_id, op }); return; }
    setPending(null);
    const value = result.data;
    if (op === 'sessions' || op === 'discover') setSessions(Array.isArray(value) ? value : []);
    if (op === 'watches') setWatches(Array.isArray(value) ? value : []);
    if (op === 'timeline') setTimeline(Array.isArray(value) ? value : []);
    if (op === 'approve') setTimeline([]);
    if ((op === 'start' || op === 'resume') && value?.id) { setSelected(value.id); setSessions(old => [...old.filter(s => s.id !== value.id), value]); }
  };
  const run = async (op: string, args: Record<string, unknown> = {}) => {
    const version = generation.current;
    if (!origin || busy || (pending && op !== 'result')) return;
    const id = requestId();
    setBusy(true); setError('');
    try {
      const raw = await deviceCommand(origin, device, op, args, id);
      if (version !== generation.current) return;
      accept(op === 'result' && pending ? pending.op : op, raw);
      if (op === 'revoke') { chooseDevice(''); await loadDevices(); }
      if (op === 'unwatch') setWatches(old => old.map(w => w.id === args.watch_id ? {...w, active: 0} : w));
    } catch (e) {
      if (version === generation.current) {
        setError((e as Error).message);
        if (['start','send','interrupt','resume','approve','bind_window','window_action'].includes(op)) {
          setPending({id, op, byRequest: true});
          setOutput({state:'unknown', reason:'请求结果未确认，请查询原请求。'});
        }
      }
    }
    finally { if (version === generation.current) setBusy(false); }
  };
  useEffect(() => { if (device && origin) void run('sessions'); }, [device, origin]);
  const pendingApprovals = timeline.filter(e => e.kind === 'confirmation' && !timeline.some(later => later.seq > e.seq && ['confirmation_resolved', 'confirmation_expired'].includes(later.kind) && (later.detail === e.detail?.approval_id || later.detail?.approval_id === e.detail?.approval_id)));
  return <>
    <PageHead title="电脑与 Agent" />
    {error && <div role="alert" className="banner error">{error}</div>}
    <section className="panel stack"><h3>设备与回传会话</h3>
      <div className="form-grid"><label className="field"><span>当前管理员与回传会话</span><select value={origin} onChange={e => chooseOrigin(e.target.value)}>
        <option value="">{originReason}</option>{origins.map(o => <option key={o.session} value={o.session}>{o.label}</option>)}
        {origin && !origins.some(o => o.session === origin) && <option value={origin}>{origin}</option>}</select></label>
        <label className="field"><span>电脑</span><select disabled={!origin || loadingDevices} value={device} onChange={e => chooseDevice(e.target.value)}>
          <option value="">{loadingDevices ? '正在加载…' : devices.length ? '选择电脑' : origin ? '当前管理员没有可见设备' : '先选择回传会话'}</option>
          {devices.map(d => <option key={d.id} value={d.id}>{d.name} · {d.online ? '在线' : '离线'}{d.access === 'owner' ? ' · 自己的设备' : d.access === 'delegated' ? ' · 获授权' : ''}</option>)}</select></label></div>
      <div className="row"><button disabled={!origin || busy || loadingDevices} onClick={() => void loadDevices()}>刷新设备</button><button disabled={!origin || busy} onClick={() => void run('pair')}>生成配对码</button>
        <button disabled={currentDevice?.access !== 'owner' || busy} onClick={() => { if (confirm('撤销后需要重新配对，继续？')) void run('revoke'); }}>撤销设备</button></div>
      {device && !online && <p role="status">电脑离线，请启动电脑上的石英托盘程序。</p>}
      <details><summary>高级：手动指定管理员私聊</summary><label className="field"><span>会话标识</span><input value={origin} onChange={e => chooseOrigin(e.target.value)} placeholder="平台:FriendMessage:管理员 ID" /></label></details>
    </section>
    <DeviceAccess origin={origin} device={currentDevice} administrators={administrators}
      account={origins.find(o => o.session === origin)?.account || origin.split(':').at(-1) || ''} />
    <section className="panel stack"><h3>Agent 会话</h3><div className="form-grid"><label className="field"><span>Agent</span><select value={agent} onChange={e => setAgent(e.target.value)}><option value="claude">Claude Code</option><option value="codex">Codex</option><option value="dsh">DSH</option></select></label>
      <label className="field"><span>已有任务关键词</span><input value={query} onChange={e => setQuery(e.target.value)} /></label></div>
      <div className="row"><button disabled={!online || busy} onClick={() => void run('discover', {agent, query})}>发现已有会话</button><button disabled={!device || busy} onClick={() => void run('sessions')}>刷新会话</button></div>
      <div className="device-table"><table className="table"><thead><tr><th>会话</th><th>工作目录</th><th>控制方式</th><th>状态</th></tr></thead><tbody>{sessions.map(s => <tr key={s.id} aria-selected={selected === s.id}>
        <td><button disabled={busy || !!pending} className={selected === s.id ? 'primary' : 'ghost'} onClick={() => { generation.current++; setSelected(s.id); setTimeline([]); setOutput(null); void run('read', {session_id: s.id}); }}>{s.agent} · {(s.native_id || s.id).slice(0, 12)}</button></td><td>{s.cwd}</td><td>{s.mode === 'managed' ? '原生托管' : '仅观察'}</td><td>{names[s.state] || s.state}</td></tr>)}</tbody></table></div>
      {!sessions.length && <p>暂无会话。选择在线电脑后可发现已有会话，或在下方新建任务。</p>}
      {active && <small className="break-text">已选择 {active.agent}：{active.native_id || active.id}。{active.mode !== 'managed' && '仅观察不代表已接管。'}</small>}
    </section>
    <section className="panel stack"><h3>任务操作</h3><label className="field"><span>新任务工作目录</span><input value={cwd} onChange={e => setCwd(e.target.value)} /></label>
      <label className="field"><span>任务或追加指令</span><textarea value={prompt} onChange={e => setPrompt(e.target.value)} rows={4} /></label>
      <div className="row"><button className="primary" disabled={!online || !cwd || !prompt || busy || !!pending} onClick={() => void run('start', {agent, cwd, prompt})}>新建任务</button>
        <button disabled={!supports('send') || !prompt || busy || !!pending} onClick={() => void run('send', {session_id: selected, prompt})}>追加指令</button><button disabled={!supports('interrupt') || busy} onClick={() => void run('interrupt', {session_id: selected})}>中断</button>
        <button disabled={!supports('resume') || busy} onClick={() => { if (confirm('恢复历史会话不会接管正在运行的窗口，继续？')) void run('resume', {session_id: selected}); }}>恢复历史会话</button>
        <button disabled={!selected || busy} onClick={() => void run('timeline', {session_id: selected})}>刷新事件与确认</button></div>
    </section>
    <section className="panel stack"><h3>监工与确认</h3><div className="row"><button disabled={!online || !selected || busy} onClick={() => void run('watch', {session_id: selected})}>持续监工当前会话</button><button disabled={!device || busy} onClick={() => void run('watches')}>刷新监工列表</button></div>
      {!watches.length && <p>尚未加载到监工记录。</p>}{watches.map(w => <div className="row" key={w.id}><span className="break-text">{w.session} · {w.active ? '监工中' : '已停止'}</span><button disabled={!w.active || busy} onClick={() => void run('unwatch', {watch_id: w.id})}>取消监工</button></div>)}
      {pendingApprovals.map(e => <div className="proposal stack" key={e.seq}><b>待确认操作 · {active?.agent} · {devices.find(d => d.id === device)?.name}</b><pre className="device-output">{JSON.stringify(e.detail, null, 2)}</pre>
        <div className="row">{[false, true].map(allow => <button key={String(allow)} disabled={!supports('approve') || busy || !e.detail?.approval_id || Date.parse(e.detail?.expires || '') <= Date.now()} onClick={() => { if (!allow || confirm('批准上方所示的具体操作？')) void run('approve', {session_id: selected, approval_id: e.detail.approval_id, allow}); }}>{allow ? '批准本次' : '拒绝'}</button>)}</div></div>)}
      {!pendingApprovals.length && <small>没有已加载的待确认操作。选择会话后刷新事件查看。</small>}
    </section>
    {output !== null && <section className="panel stack"><h3>执行结果</h3><p role="status">{pending ? pending.byRequest ? '尚未确认服务器是否收到命令；请查询原请求，不要重新提交。' : '命令已接收，正在等待设备结果；请查询原命令，不要重复提交。' : output?.result?.ok === false ? '执行失败，请查看原因。' : '结果已返回。任务是否完成以实际状态为准。'}</p>
      {pending && <button disabled={busy} onClick={() => void run('result', pending.byRequest ? {request_id: pending.id} : {command_id: pending.id})}>查询原命令结果</button>}{output?.code && <p>配对码：<strong>{output.code}</strong>（五分钟有效）</p>}{output?.result?.output && <pre className="device-output">{output.result.output}</pre>}
      {timeline.map(e => <p key={e.seq}>{names[e.state] || e.state} · {e.kind} · {e.delivery_state || '暂无消息回执'}</p>)}
      <details><summary>原始返回数据</summary><pre className="device-output">{JSON.stringify(output, null, 2)}</pre></details></section>}
  </>;
}
