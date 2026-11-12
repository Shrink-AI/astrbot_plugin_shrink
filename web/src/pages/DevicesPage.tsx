import { useEffect, useRef, useState } from 'react';
import { ChevronRightIcon } from 'lucide-react';
import { Alert, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { api } from '@/api/client';
import { commandResult, deviceCommand, requestId } from '@/api/commands';
import { useConfirm } from '@/components/confirm-dialog';
import { PageHead, Panel } from '@/shared/components';
import { DeviceAccess, type Administrator } from './DeviceAccess';

type Device = { id: string; name: string; online: boolean; revoked: number; access?: 'owner' | 'delegated' };
type Session = { id: string; agent: string; cwd: string; state: string; mode: string; native_id: string; capabilities?: string[] };
type Watch = { id: string; session: string; active: number; checked: number };
const names: Record<string, string> = { running: '执行中', completed: '已完成', failed: '失败', unknown: '待核对', unverified: '未核实', interrupted: '已中断', waiting_confirmation: '等待确认', starting: '启动中' };
const preClass = 'max-h-80 overflow-auto rounded-lg border bg-muted/40 p-2.5 font-mono text-xs whitespace-pre-wrap break-words';

export function DevicesPage() {
  const ask = useConfirm();
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
    {error && <Alert variant="destructive" className="mb-4"><AlertTitle>{error}</AlertTitle></Alert>}
    <Panel title="设备与回传会话" className="mb-3">
      <div className="flex flex-col gap-2.5">
        <FieldGroup className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field>
            <FieldLabel htmlFor="device-origin">当前管理员与回传会话</FieldLabel>
            <Select value={origin || 'none-origin'} onValueChange={next => chooseOrigin(next === 'none-origin' ? '' : next)}>
              <SelectTrigger id="device-origin" className="w-full">
                <SelectValue placeholder={originReason} />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectItem value="none-origin">{originReason}</SelectItem>
                  {origins.map(o => <SelectItem key={o.session} value={o.session}>{o.label}</SelectItem>)}
                  {origin && !origins.some(o => o.session === origin) && <SelectItem value={origin}>{origin}</SelectItem>}
                </SelectGroup>
              </SelectContent>
            </Select>
          </Field>
          <Field>
            <FieldLabel htmlFor="device-pick">电脑</FieldLabel>
            <Select value={device || 'none-device'} disabled={!origin || loadingDevices} onValueChange={next => chooseDevice(next === 'none-device' ? '' : next)}>
              <SelectTrigger id="device-pick" className="w-full">
                <SelectValue placeholder={loadingDevices ? '正在加载…' : devices.length ? '选择电脑' : origin ? '当前管理员没有可见设备' : '先选择回传会话'} />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectItem value="none-device">{loadingDevices ? '正在加载…' : devices.length ? '选择电脑' : origin ? '当前管理员没有可见设备' : '先选择回传会话'}</SelectItem>
                  {devices.map(d => <SelectItem key={d.id} value={d.id}>{d.name} · {d.online ? '在线' : '离线'}{d.access === 'owner' ? ' · 自己的设备' : d.access === 'delegated' ? ' · 获授权' : ''}</SelectItem>)}
                </SelectGroup>
              </SelectContent>
            </Select>
          </Field>
        </FieldGroup>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" disabled={!origin || busy || loadingDevices} onClick={() => void loadDevices()}>刷新设备</Button>
          <Button variant="outline" disabled={!origin || busy} onClick={() => void run('pair')}>生成配对码</Button>
          <Button variant="destructive" disabled={currentDevice?.access !== 'owner' || busy} onClick={async () => {
            if(!await ask({ title: '撤销设备？', desc: '撤销后需要重新配对，继续？', destructive: true })) return;
            void run('revoke');
          }}>撤销设备</Button>
        </div>
        {device && !online && <p role="status" className="text-sm text-muted-foreground">电脑离线，请启动电脑上的石英托盘程序。</p>}
        <Collapsible className="group/collapsible text-sm">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
              <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
              高级：手动指定管理员私聊
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-2">
            <Field className="mt-2">
              <FieldLabel htmlFor="device-origin-manual">会话标识</FieldLabel>
              <Input id="device-origin-manual" value={origin} onChange={e => chooseOrigin(e.target.value)} placeholder="平台:FriendMessage:管理员 ID" />
            </Field>
          </CollapsibleContent>
        </Collapsible>
      </div>
    </Panel>
    <DeviceAccess origin={origin} device={currentDevice} administrators={administrators}
      account={origins.find(o => o.session === origin)?.account || origin.split(':').at(-1) || ''} />
    <Panel title="Agent 会话" className="mb-3">
      <div className="flex flex-col gap-2.5">
        <FieldGroup className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field>
            <FieldLabel htmlFor="agent-kind">Agent</FieldLabel>
            <Select value={agent} onValueChange={next => setAgent(next)}>
              <SelectTrigger id="agent-kind" className="w-full">
                <SelectValue placeholder="Claude Code" />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectItem value="claude">Claude Code</SelectItem>
                  <SelectItem value="codex">Codex</SelectItem>
                  <SelectItem value="dsh">DSH</SelectItem>
                </SelectGroup>
              </SelectContent>
            </Select>
          </Field>
          <Field>
            <FieldLabel htmlFor="agent-query">已有任务关键词</FieldLabel>
            <Input id="agent-query" value={query} onChange={e => setQuery(e.target.value)} />
          </Field>
        </FieldGroup>
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" disabled={!online || busy} onClick={() => void run('discover', {agent, query})}>发现已有会话</Button>
          <Button variant="outline" disabled={!device || busy} onClick={() => void run('sessions')}>刷新会话</Button>
        </div>
        <Table>
          <TableHeader><TableRow>
            <TableHead>会话</TableHead>
            <TableHead>工作目录</TableHead>
            <TableHead>控制方式</TableHead>
            <TableHead>状态</TableHead>
          </TableRow></TableHeader>
          <TableBody>{sessions.map(s => <TableRow key={s.id} aria-selected={selected === s.id}>
            <TableCell><Button variant={selected === s.id ? 'default' : 'ghost'} disabled={busy || !!pending}
              onClick={() => { generation.current++; setSelected(s.id); setTimeline([]); setOutput(null); void run('read', {session_id: s.id}); }}>
              {s.agent} · {(s.native_id || s.id).slice(0, 12)}</Button></TableCell>
            <TableCell className="min-w-[260px] whitespace-normal">{s.cwd}</TableCell>
            <TableCell>{s.mode === 'managed' ? '原生托管' : '仅观察'}</TableCell>
            <TableCell>{names[s.state] || s.state}</TableCell>
          </TableRow>)}</TableBody>
        </Table>
        {!sessions.length && <p className="text-sm text-muted-foreground">暂无会话。选择在线电脑后可发现已有会话，或在下方新建任务。</p>}
        {active && <p className="text-xs text-muted-foreground break-words">已选择 {active.agent}：{active.native_id || active.id}。{active.mode !== 'managed' && '仅观察不代表已接管。'}</p>}
      </div>
    </Panel>
    <Panel title="任务操作" className="mb-3">
      <div className="flex flex-col gap-2.5">
        <Field>
          <FieldLabel htmlFor="task-cwd">新任务工作目录</FieldLabel>
          <Input id="task-cwd" value={cwd} onChange={e => setCwd(e.target.value)} />
        </Field>
        <Field>
          <FieldLabel htmlFor="task-prompt">任务或追加指令</FieldLabel>
          <Textarea id="task-prompt" value={prompt} onChange={e => setPrompt(e.target.value)} rows={4} />
        </Field>
        <div className="flex flex-wrap items-center gap-2">
          <Button disabled={!online || !cwd || !prompt || busy || !!pending} onClick={() => void run('start', {agent, cwd, prompt})}>新建任务</Button>
          <Button variant="outline" disabled={!supports('send') || !prompt || busy || !!pending} onClick={() => void run('send', {session_id: selected, prompt})}>追加指令</Button>
          <Button variant="outline" disabled={!supports('interrupt') || busy} onClick={() => void run('interrupt', {session_id: selected})}>中断</Button>
          <Button variant="outline" disabled={!supports('resume') || busy} onClick={async () => {
            if(!await ask({ title: '恢复历史会话？', desc: '恢复历史会话不会接管正在运行的窗口，继续？' })) return;
            void run('resume', {session_id: selected});
          }}>恢复历史会话</Button>
          <Button variant="outline" disabled={!selected || busy} onClick={() => void run('timeline', {session_id: selected})}>刷新事件与确认</Button>
        </div>
      </div>
    </Panel>
    <Panel title="监工与确认" className="mb-3">
      <div className="flex flex-col gap-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" disabled={!online || !selected || busy} onClick={() => void run('watch', {session_id: selected})}>持续监工当前会话</Button>
          <Button variant="outline" disabled={!device || busy} onClick={() => void run('watches')}>刷新监工列表</Button>
        </div>
        {!watches.length && <p className="text-sm text-muted-foreground">尚未加载到监工记录。</p>}
        {watches.map(w => <div className="flex flex-wrap items-center gap-2" key={w.id}>
          <span className="text-sm break-words">{w.session} · {w.active ? '监工中' : '已停止'}</span>
          <Button variant="outline" size="sm" disabled={!w.active || busy} onClick={() => void run('unwatch', {watch_id: w.id})}>取消监工</Button>
        </div>)}
        {pendingApprovals.map(e => <div className="proposal flex flex-col gap-2 rounded-lg border bg-card p-2.5" key={e.seq}>
          <p className="text-sm font-semibold">待确认操作 · {active?.agent} · {devices.find(d => d.id === device)?.name}</p>
          <pre className={preClass}>{JSON.stringify(e.detail, null, 2)}</pre>
          <div className="flex flex-wrap items-center gap-2">
            {[false, true].map(allow => <Button key={String(allow)} variant={allow ? 'outline' : 'destructive'}
              disabled={!supports('approve') || busy || !e.detail?.approval_id || Date.parse(e.detail?.expires || '') <= Date.now()}
              onClick={async () => {
                if(allow && !await ask({ title: '批准这次操作？', desc: '批准上方所示的具体操作？' })) return;
                void run('approve', {session_id: selected, approval_id: e.detail.approval_id, allow});
              }}>
              {allow ? '批准本次' : '拒绝'}</Button>)}
          </div>
        </div>)}
        {!pendingApprovals.length && <p className="text-xs text-muted-foreground">没有已加载的待确认操作。选择会话后刷新事件查看。</p>}
      </div>
    </Panel>
    {output !== null && <Panel title="执行结果">
      <div className="flex flex-col gap-2.5">
        <p role="status" className="text-sm text-muted-foreground">{pending ? pending.byRequest ? '尚未确认服务器是否收到命令；请查询原请求，不要重新提交。' : '命令已接收，正在等待设备结果；请查询原命令，不要重复提交。' : output?.result?.ok === false ? '执行失败，请查看原因。' : '结果已返回。任务是否完成以实际状态为准。'}</p>
        {pending && <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" disabled={busy} onClick={() => void run('result', pending.byRequest ? {request_id: pending.id} : {command_id: pending.id})}>查询原命令结果</Button>
        </div>}
        {output?.code && <p className="text-sm">配对码：<strong className="font-semibold">{output.code}</strong>（五分钟有效）</p>}
        {output?.result?.output && <pre className={preClass}>{output.result.output}</pre>}
        {timeline.map(e => <p key={e.seq} className="text-sm text-muted-foreground">{names[e.state] || e.state} · {e.kind} · {e.delivery_state || '暂无消息回执'}</p>)}
        <Collapsible className="group/collapsible text-sm">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
              <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
              原始返回数据
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-2">
            <pre className="mt-2 max-h-80 overflow-auto rounded-lg border bg-muted/40 p-2.5 font-mono text-xs whitespace-pre-wrap break-words">{JSON.stringify(output, null, 2)}</pre>
          </CollapsibleContent>
        </Collapsible>
      </div>
    </Panel>}
  </>;
}