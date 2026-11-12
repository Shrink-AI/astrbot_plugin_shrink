import { Fragment, useEffect, useState } from 'react';
import { ChevronRightIcon, MoreHorizontalIcon, XIcon } from 'lucide-react';
import { cn } from 'cn';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { BulkActions } from '@/components/data-table/bulk-actions';
import { DataTableColumnHeader } from '@/components/data-table/column-header';
import { FacetedFilter } from '@/components/data-table/faceted-filter';
import { api } from '@/api/client';
import { useConfirm } from '@/components/confirm-dialog';
import type { Editing, Memory } from '@/api/types';
import { Badge, Empty, Facts, PageHead, Pagination, Panel, TableShell, TableToolbar } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';
import { certainty, date, memoryKinds, memoryStatus, origins, place, shortDate } from '@/shared/format';
import { EditContent } from '@/shared/EditContent';

const preClass = 'max-h-80 overflow-auto rounded-lg border bg-muted/40 p-2.5 font-mono text-xs whitespace-pre-wrap break-words';

const kindOptions = Object.entries(memoryKinds).map(([value, label]) => ({ label, value }));
const statusOptions = Object.entries(memoryStatus).map(([value, label]) => ({ label, value }));

type SortKey = 'kind' | 'status' | 'confidence' | 'updated';

const sortValue = (row: Memory, key: SortKey): string | number =>
  key === 'kind' ? memoryKinds[row.kind] ?? row.kind
    : key === 'status' ? memoryStatus[row.status] ?? row.status
      : key === 'confidence' ? row.confidence : row.updated;

function MaterialSources() {
  const { revision, action, busy, setError } = useDashboard();
  const ask = useConfirm();
  const [rows, setRows] = useState<any[]>([]);
  const [selected, setSelected] = useState<any>(null);
  useEffect(() => {
    let cancelled = false;
    api<any[]>('/operations/materials').then(data => { if(!cancelled) setRows(data); })
      .catch(e => { if(!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [revision]);
  const parsed = (raw: string) => { try { return JSON.parse(raw); } catch { return { error: raw }; } };
  return <Panel className="mb-4">
    <Collapsible className="group/collapsible">
      <CollapsibleTrigger asChild>
        <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
          <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
          资料来源与整理结果（最近 100 项）
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-2">
        <div className="mt-3 flex flex-col gap-2.5">
          {!rows.length ? <Empty text="尚未整理交付资料。" /> : rows.map(row => <div className="proposal flex flex-col gap-1.5 rounded-lg border p-2.5" key={row.id}>
            <div className="flex flex-wrap items-center gap-2">
              <b>{row.title}</b>
              <Badge>{row.undone ? '已撤销' :
                row.status === 'curated' ? '已整理' : row.status === 'failed' ? '整理失败' : '整理中'}</Badge>
              <small className="text-xs text-muted-foreground">{date(row.created)}</small>
            </div>
            <p className="text-sm text-muted-foreground">{row.uri} · {place(row.source)}</p>
            <p className="text-sm">{parsed(row.result).summary || parsed(row.result).error || '等待整理结果'}</p>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="outline" size="sm" disabled={busy} onClick={() => void action(async () => {
                setSelected(await api(`/operations/materials/${row.id}`));
              }, '')}>查看来源原文与关联记忆</Button>
              <Button variant="outline" size="sm" disabled={busy || row.status === 'processing' || !!row.undone}
                onClick={async () => {
                  if(!await ask({ title: '撤销此次整理？', desc: `撤销“${row.title}”此次整理产生的记忆变更？来源原文仍保留。`, destructive: true })) return;
                  void action(() => api(`/runs/${row.run_id}/undo`, 'POST'), '已撤销此次整理');
                }}>撤销此次整理</Button>
            </div>
          </div>)}
          {selected && <div className="proposal flex flex-col gap-1.5 rounded-lg border p-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <b>{selected.title}</b>
              <Button variant="outline" size="sm" onClick={() => setSelected(null)}>关闭原文</Button>
            </div>
            <p className="text-sm text-muted-foreground">{selected.uri}</p>
            <pre className={preClass}>{selected.content}</pre>
            <pre className={preClass}>{JSON.stringify(parsed(selected.result), null, 2)}</pre>
          </div>}
        </div>
      </CollapsibleContent>
    </Collapsible>
  </Panel>;
}

function statusBadge(m: Memory) {
  if(m.verified === 1) return <Badge tone="ok">已确认</Badge>;
  if(m.verified === -1) return <Badge tone="bad">判定错误</Badge>;
  const tone = m.status === 'active' ? undefined : m.status === 'candidate' ? 'warn' : 'bad';
  return <Badge tone={tone}>{memoryStatus[m.status] ?? m.status}</Badge>;
}

function evidenceText(raw: string) {
  try {
    const items = JSON.parse(raw) as unknown[];
    return items.map(i => typeof i === 'string' ? i : (i as { text?: string; message?: string }).text ?? (i as { message?: string }).message ?? JSON.stringify(i)).join('\n');
  } catch { return raw; }
}

export function MemoryPage() {
  const { action, busy, revision, setError } = useDashboard();
  const ask = useConfirm();
  const [rows, setRows] = useState<Memory[]>([]);
  const [q, setQ] = useState('');
  const [kind, setKind] = useState('');
  const [status, setStatus] = useState('');
  const [source, setSource] = useState('');
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [sort, setSort] = useState<{ key: SortKey; dir: 'asc' | 'desc' } | null>(null);
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      api<Memory[]>(`/memories?q=${encodeURIComponent(q)}&kind=${kind}&status=${status}&source=${encodeURIComponent(source)}&offset=${offset}`)
        .then(r => { if(!cancelled) { setRows(r); setSelected([]); } })
        .catch(e => { if(!cancelled) setError(e.message); });
    }, q || source ? 250 : 0);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [q, kind, status, source, offset, revision]);
  const toggle = (id: number) => setSelected(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id]);
  const batch = async (verb: 'confirm' | 'reject' | 'delete', label: string) => {
    if(verb === 'delete' && !await ask({ title: '批量删除记忆？', desc: `删除选中的 ${selected.length} 条记忆？`, destructive: true })) return;
    void action(() => api('/memories/batch', 'POST', { ids: selected, action: verb }), label);
  };
  const verify = (m: Memory, verdict: 'correct' | 'wrong') => void action(() => api(`/memories/${m.id}/verify`, 'POST', { verdict }), verdict === 'correct' ? '已确认' : '已判定为错误');
  const remove = async (m: Memory) => {
    if(!await ask({ title: '删除记忆？', desc: '删除这条记忆？', destructive: true })) return;
    void action(() => api(`/memories/${m.id}`, 'DELETE'), '已删除');
  };
  const directionOf = (key: SortKey) => sort?.key === key ? sort.dir : undefined;
  const toggleSort = (key: SortKey) => setSort(current => {
    if(!current || current.key !== key) return { key, dir: 'asc' as const };
    return current.dir === 'asc' ? { key, dir: 'desc' as const } : null;
  });
  const sorted = !sort ? rows : [...rows].sort((a, b) => {
    const left = sortValue(a, sort.key);
    const right = sortValue(b, sort.key);
    const diff = typeof left === 'number' && typeof right === 'number'
      ? left - right
      : String(left).localeCompare(String(right), 'zh');
    return sort.dir === 'asc' ? diff : -diff;
  });
  return <>
    <PageHead title="记忆" description="群里沉淀下来的事实、约定和黑话，可以在这里纠正和清理。" />
    <MaterialSources />
    <div className="flex flex-1 flex-col gap-4">
      <TableToolbar>
        <div className="flex flex-1 flex-col-reverse items-start gap-y-2 sm:flex-row sm:items-center sm:gap-2">
          <Input type="search" placeholder="搜索内容" className="h-8 w-[180px] lg:w-[250px]" value={q}
            onChange={e => { setQ(e.target.value); setOffset(0); }} />
          <div className="flex flex-wrap items-center gap-2">
            <FacetedFilter title="类型" value={kind} options={kindOptions}
              onChange={next => { setKind(next); setOffset(0); }} />
            <FacetedFilter title="状态" value={status} options={statusOptions}
              onChange={next => { setStatus(next); setOffset(0); }} />
            <Input placeholder="群号" className="h-8 w-[104px]" value={source}
              onChange={e => { setSource(e.target.value); setOffset(0); }} />
          </div>
        </div>
        {(q || kind || status || source) && <Button variant="ghost" size="sm" className="h-8 px-2"
          onClick={() => { setQ(''); setKind(''); setStatus(''); setSource(''); setOffset(0); }}>
          重置<XIcon data-icon="inline-end" />
        </Button>}
      </TableToolbar>
      <TableShell>
        <Table className="min-w-[880px]">
          <TableHeader>
            <TableRow>
              <TableHead className="w-8">
                <Checkbox aria-label="全选" checked={!!rows.length && selected.length === rows.length}
                  onCheckedChange={value => setSelected(value === true ? rows.map(r => r.id) : [])} />
              </TableHead>
              <TableHead className="w-[92px]">
                <DataTableColumnHeader title="类型" direction={directionOf('kind')} onSort={() => toggleSort('kind')} />
              </TableHead>
              <TableHead>内容</TableHead>
              <TableHead className="w-[96px]">
                <DataTableColumnHeader title="状态" direction={directionOf('status')} onSort={() => toggleSort('status')} />
              </TableHead>
              <TableHead className="w-[88px] text-right">
                <DataTableColumnHeader title="置信度" direction={directionOf('confidence')} onSort={() => toggleSort('confidence')}
                  className="-me-2.5" />
              </TableHead>
              <TableHead className="w-[140px]">来源</TableHead>
              <TableHead className="w-[96px] text-right">
                <DataTableColumnHeader title="更新" direction={directionOf('updated')} onSort={() => toggleSort('updated')}
                  className="-me-2.5" />
              </TableHead>
              <TableHead className="w-[44px]"><span className="sr-only">操作</span></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>{!sorted.length ? <TableRow>
            <TableCell colSpan={8} className="h-24 text-center text-muted-foreground">没有匹配的记忆。</TableCell>
          </TableRow> : sorted.map(m => <Fragment key={m.id}>
            <TableRow className={cn('clickable cursor-pointer', open === m.id && 'bg-muted/40')}
              data-state={selected.includes(m.id) ? 'selected' : undefined}
              onClick={() => setOpen(open === m.id ? null : m.id)}>
              <TableCell onClick={e => e.stopPropagation()}>
                <Checkbox aria-label="选择" checked={selected.includes(m.id)} onCheckedChange={() => toggle(m.id)} />
              </TableCell>
              <TableCell><Badge>{memoryKinds[m.kind] ?? m.kind}</Badge></TableCell>
              <TableCell className="whitespace-normal">{m.kind === 'glossary' && <b>{m.term}：</b>}{m.text}</TableCell>
              <TableCell>{statusBadge(m)}</TableCell>
              <TableCell className="text-right tabular-nums">{Math.round(m.confidence * 100)}%</TableCell>
              <TableCell className="text-muted-foreground">{place(m.source)}</TableCell>
              <TableCell className="text-right tabular-nums">{shortDate(m.updated)}</TableCell>
              <TableCell className="text-right" onClick={e => e.stopPropagation()}>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="icon-sm" aria-label="更多操作" title="更多操作"
                      onClick={e => e.stopPropagation()}>
                      <MoreHorizontalIcon />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-36">
                    <DropdownMenuItem disabled={busy} onSelect={() => verify(m, 'correct')}>确认正确</DropdownMenuItem>
                    <DropdownMenuItem disabled={busy} onSelect={() => verify(m, 'wrong')}>判定错误</DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => setEditing({ kind: 'memory', row: { ...m } })}>纠正</DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem variant="destructive" onSelect={() => void remove(m)}>删除</DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </TableCell>
            </TableRow>
            {open === m.id && <TableRow className="bg-muted/30 hover:bg-muted/30">
              <TableCell />
              <TableCell colSpan={7} className="whitespace-normal">
                <div className="flex flex-col gap-2.5 py-1">
                  <Facts>
                    <dt>关于</dt><dd>{m.subject || '—'}</dd>
                    {m.participants !== '[]' && <><dt>参与者</dt><dd>{JSON.parse(m.participants).join('、')}</dd></>}
                    <dt>原话引用</dt><dd>{m.quote || '—'}</dd>
                    <dt>确定程度</dt><dd>{certainty[m.certainty] ?? m.certainty}，重要度 {m.importance}</dd>
                    <dt>来自</dt><dd>{origins[m.origin] ?? m.origin}{m.run ? `（${m.run}）` : ''}</dd>
                    <dt>被用到</dt><dd>{m.recalls} 次{m.last_recalled ? `，最近 ${date(m.last_recalled)}` : ''}</dd>
                    {m.valid_until && <><dt>有效到</dt><dd>{shortDate(m.valid_until)}</dd></>}
                    {m.superseded_by && <><dt>被取代</dt><dd>#{m.superseded_by}</dd></>}
                  </Facts>
                  <Collapsible className="group/collapsible">
                    <CollapsibleTrigger asChild>
                      <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
                        <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
                        依据
                      </Button>
                    </CollapsibleTrigger>
                    <CollapsibleContent className="pt-2">
                      <pre className={cn(preClass, 'mt-1.5')}>{evidenceText(m.evidence)}</pre>
                    </CollapsibleContent>
                  </Collapsible>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button variant="outline" size="sm" disabled={busy} onClick={() => verify(m, 'correct')}>确认正确</Button>
                    <Button variant="outline" size="sm" disabled={busy} onClick={() => verify(m, 'wrong')}>判定错误</Button>
                    <Button variant="outline" size="sm" onClick={() => setEditing({ kind: 'memory', row: { ...m } })}>纠正</Button>
                    <span className="flex-1" />
                    <Button variant="destructive" size="sm" disabled={busy} onClick={() => void remove(m)}>删除</Button>
                  </div>
                </div>
              </TableCell>
            </TableRow>}
          </Fragment>)}</TableBody>
        </Table>
      </TableShell>
      <Pagination offset={offset} count={rows.length} size={50} change={setOffset} />
      <BulkActions count={selected.length} label="条记忆" onClear={() => setSelected([])}>
        <Button variant="outline" size="sm" disabled={busy} onClick={() => void batch('confirm', '已确认')}>确认正确</Button>
        <Button variant="outline" size="sm" disabled={busy} onClick={() => void batch('reject', '已判定为错误')}>判定错误</Button>
        <Button variant="destructive" size="sm" disabled={busy} onClick={() => void batch('delete', '已删除')}>删除</Button>
      </BulkActions>
    </div>
    {editing && <EditContent editing={editing} setEditing={setEditing} />}
  </>;
}