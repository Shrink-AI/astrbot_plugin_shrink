import { useEffect, useState } from 'react';
import { CheckIcon, ChevronRightIcon, ChevronsUpDownIcon } from 'lucide-react';
import { cn } from 'cn';
import { Button } from '@/components/ui/button';
import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from '@/components/ui/command';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Field, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { api } from '@/api/client';
import { useConfirm } from '@/components/confirm-dialog';
import { Badge, Empty, PageHead, Panel } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';
import { date } from '@/shared/format';

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
const statusTone = (status: string): 'ok' | 'warn' | 'bad' | 'info' | undefined =>
  status === 'completed' ? 'ok'
    : ['failed', 'interrupted', 'timed_out'].includes(status) ? 'bad'
      : ['running', 'testing', 'deploying'].includes(status) ? 'info'
        : status ? 'warn' : undefined;

export function OperationsPage() {
  const { action, revision, setError } = useDashboard();
  const ask = useConfirm();
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
    <div className="flex flex-col gap-4">
      <Panel>
        <div className="flex flex-col gap-3">
          <Field>
            <FieldLabel htmlFor="operation-request">交给石英的任务</FieldLabel>
            <Textarea id="operation-request" rows={4} value={request}
              onChange={e => setRequest(e.target.value)} placeholder="说明要实现或修改什么，以及怎样算完成" />
          </Field>
          <Field>
            <FieldLabel htmlFor="operation-session">接收结果的管理员会话</FieldLabel>
            <Input id="operation-session" value={session} onChange={e => setSession(e.target.value)}
              placeholder="snowluma:FriendMessage:QQ号" />
          </Field>
          <p className="text-sm text-muted-foreground">石英会自行编写、隔离测试和启用。每次最多30分钟、3轮修复。</p>
          <div className="flex flex-wrap items-center gap-2">
            <Button disabled={!request.trim() || !session.trim()} onClick={() => void action(async () => {
              await api('/operations/tasks', 'POST', { request, session }); setRequest('');
            }, '已提交开发任务，完成后石英会回复')}>开始任务</Button>
          </div>
        </div>
      </Panel>
      <Panel>
        {!tasks.length ? <Empty text="还没有开发任务。" /> : <Table>
          <TableHeader><TableRow><TableHead>任务</TableHead><TableHead>状态</TableHead><TableHead>开始</TableHead><TableHead>操作</TableHead></TableRow></TableHeader>
          <TableBody>{tasks.map(t => <TableRow key={t.id}>
            <TableCell className="min-w-[260px] whitespace-normal">
              <Button variant="ghost" size="sm" className="h-auto min-w-0 justify-start py-1 text-left font-normal whitespace-normal"
                onClick={() => void inspect(t.id)}>{t.request.slice(0, 90)}</Button>
            </TableCell>
            <TableCell><Badge tone={statusTone(t.status)}>{labels[t.status] || t.status}</Badge></TableCell>
            <TableCell className="text-right tabular-nums">{date(t.started)}</TableCell>
            <TableCell>{['queued', 'running', 'testing', 'deploying'].includes(t.status)
              ? <Button variant="outline" size="sm" onClick={() => void action(() => api(`/operations/tasks/${t.id}/cancel`, 'POST'), '已取消')}>取消</Button>
              : <Button variant="outline" size="sm" onClick={() => void action(() => api(`/operations/tasks/${t.id}/resume`, 'POST'), '已续做')}>续做</Button>}</TableCell>
          </TableRow>)}</TableBody>
        </Table>}
      </Panel>
      {selected && <Panel title="任务详情" actions={<Button variant="ghost" size="sm" onClick={() => setSelected(null)}>关闭</Button>}>
        <div className="flex flex-col gap-3">
          <p className="text-sm break-words">{selected.request}</p>
          <p className="text-sm text-muted-foreground">{labels[selected.status]} · {selected.provider} · 失败测试 {selected.failures} 轮</p>
          <pre className="max-h-80 overflow-auto rounded-lg border bg-muted/40 p-2.5 font-mono text-xs whitespace-pre-wrap break-words">{selected.result || '尚无最终结果'}</pre>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => void inspect(selected.id)}>刷新详情</Button>
          </div>
          <div className="flex flex-col gap-2">
            {selected.events?.map(e => <Collapsible key={e.id} className="group/collapsible text-sm">
              <CollapsibleTrigger asChild>
                <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
                  <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
                  {e.action} {e.plugin} {e.path}
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent className="pt-2">
                <pre className="mt-2 max-h-80 overflow-auto rounded-lg border bg-muted/40 p-2.5 font-mono text-xs whitespace-pre-wrap break-words">{e.result}</pre>
              </CollapsibleContent>
            </Collapsible>)}
          </div>
        </div>
      </Panel>}
      <Panel title="核心部署版本">
        {!deployments.length ? <Empty text="暂无独立部署记录。" /> : <div className="flex flex-col gap-2">
          {deployments.map(d => <Collapsible key={d.id} className="group/collapsible text-sm">
            <CollapsibleTrigger asChild>
              <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
                <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
                {date(d.created)} · {d.action === 'rollback' ? '恢复上一版' : '部署更新'} · {labels[d.status] || d.status}
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent className="pt-2">
              <div className="mt-2 flex flex-col gap-2">
                <p className="text-sm">版本记录：<code className="font-mono text-xs">{d.id}</code> · {d.plugin}</p>
                <pre className="max-h-80 overflow-auto rounded-lg border bg-muted/40 p-2.5 font-mono text-xs whitespace-pre-wrap break-words">{JSON.stringify(d.validation ?? { error: d.error, status: d.status }, null, 2)}</pre>
              </div>
            </CollapsibleContent>
          </Collapsible>)}
        </div>}
      </Panel>
      <Panel title="插件工作台">
        <div className="flex flex-col gap-3">
          <p className="text-sm text-muted-foreground">与石英开发子代理使用同一份草稿和测试证据。修改后需重新测试才能部署。</p>
          <Field>
            <FieldLabel htmlFor="workbench-plugin">插件目录名或新插件短名</FieldLabel>
            <Input id="workbench-plugin" value={plugin} onChange={e => {
              setPlugin(e.target.value); setDraft(null); setWorkResult(null);
            }} placeholder="astrbot_plugin_shirnk_ext_reminder" />
          </Field>
          <div className="flex flex-wrap items-center gap-2">
            {Object.entries({ list: '查看插件', checkout: '检出到草稿', status: '草稿状态', diff: '查看差异',
              check: '语法检查', test: '隔离测试', deploy: '部署启用', rollback: '恢复上一版' }).map(([op, label]) =>
              <Button key={op} variant="outline" size="sm" disabled={!session.trim() || (op !== 'list' && !plugin.trim())} onClick={async () => {
                if(['deploy', 'rollback'].includes(op) && !await ask({ title: `${label}？`, desc: `${label}“${plugin}”？` })) return;
                work(op);
              }}>{label}</Button>)}
          </div>
          {draft && <p className="text-sm text-muted-foreground">测试证据：{draft.test_current ? draft.test?.ok ? '当前草稿测试通过' : '当前草稿测试失败' : '尚未测试或草稿已改变'} ·
            {draft.rollback_available ? '有上一版备份' : '暂无备份'} · {draft.conflict ? '线上版本冲突' : '无已发现冲突'}</p>}
          <Field>
            <FieldLabel htmlFor="workbench-path">文件路径</FieldLabel>
            <div className="flex items-center gap-2">
              <Input id="workbench-path" value={path} onChange={e => setPath(e.target.value)} placeholder="草稿里的文件名" />
              <DraftFilesPicker files={draft?.draft_files ?? []} value={path} onSelect={setPath} />
            </div>
          </Field>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" disabled={!session || !plugin || !path} onClick={() => work('read')}>读取草稿</Button>
          </div>
          <Textarea rows={14} className="min-h-72 font-mono text-xs" value={content} onChange={e => setContent(e.target.value)} spellCheck={false} />
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" disabled={!session || !plugin || !path} onClick={() => work('write')}>保存草稿</Button>
          </div>
          {workResult && <pre className="max-h-80 overflow-auto rounded-lg border bg-muted/40 p-2.5 font-mono text-xs whitespace-pre-wrap break-words">
            {workResult.text ?? JSON.stringify(workResult, null, 2)}</pre>}
        </div>
      </Panel>
    </div>
  </>;
}

/** 草稿文件选择器：Popover + Command 组合的 shadcn Combobox，替代原生 datalist。 */
function DraftFilesPicker({ files, value, onSelect }: {
  files: string[]; value: string; onSelect: (next: string) => void;
}) {
  const [open, setOpen] = useState(false);
  return <Popover open={open} onOpenChange={setOpen}>
    <PopoverTrigger asChild>
      <Button type="button" variant="outline" size="icon" aria-label="从草稿文件中选择"
        title="从草稿文件中选择" disabled={!files.length}>
        <ChevronsUpDownIcon />
      </Button>
    </PopoverTrigger>
    <PopoverContent className="w-[320px] p-0" align="end">
      <Command>
        <CommandInput placeholder="搜索草稿文件" />
        <CommandList>
          <CommandEmpty>没有匹配的文件</CommandEmpty>
          <CommandGroup>
            {files.map(file => <CommandItem key={file} value={file} onSelect={() => { onSelect(file); setOpen(false); }}>
              <CheckIcon className={cn('size-4', file === value ? 'opacity-100' : 'opacity-0')} />
              <span className="truncate">{file}</span>
            </CommandItem>)}
          </CommandGroup>
        </CommandList>
      </Command>
    </PopoverContent>
  </Popover>;
}

export { CapabilitiesPage } from './CapabilitiesPage';