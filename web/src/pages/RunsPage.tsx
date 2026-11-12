import { useEffect, useState } from 'react';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { api } from '@/api/client';
import { FacetedFilter } from '@/components/data-table/faceted-filter';
import type { Digest, Run } from '@/api/types';
import { Badge, PageHead, Pagination, TableShell, TableToolbar } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';
import { date, place, runKinds, runLabels } from '@/shared/format';
import { RunDetail } from '@/shared/RunDetail';

function summary(r: Run) {
  const s = r.stats ?? {};
  const accepted = Object.values(s.accepted ?? {}).reduce((a, b) => a + b, 0);
  const rejected = Object.values(s.rejected ?? {}).reduce((a, b) => a + b, 0);
  if(r.kind === 'digest') return `${s.messages ?? 0} 条消息，写入 ${accepted}，拦下 ${rejected}`;
  return r.detail || '—';
}

export function RunsPage() {
  const { action, revision, setError } = useDashboard();
  const [tab, setTab] = useState<'runs' | 'digests'>('runs');
  const [kind, setKind] = useState('');
  const [offset, setOffset] = useState(0);
  const [runs, setRuns] = useState<Run[]>([]);
  const [digests, setDigests] = useState<Digest[]>([]);
  const [open, setOpen] = useState<Run | null>(null);
  useEffect(() => {
    let cancelled = false;
    const load = tab === 'runs'
      ? api<Run[]>(`/runs?kind=${kind}&offset=${offset}`).then(r => { if(!cancelled) setRuns(r); })
      : api<Digest[]>(`/digests?offset=${offset}`).then(r => { if(!cancelled) setDigests(r); });
    load.catch(e => { if(!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [tab, kind, offset, revision]);
  const tone = (s: string): 'ok' | 'bad' | 'info' | undefined => s === 'success' ? 'ok' : s === 'failed' ? 'bad' : s === 'running' ? 'info' : undefined;
  const changeTab = (value: string) => {
    if(value !== 'runs' && value !== 'digests') return;
    setTab(value); setOffset(0);
  };
  const size = 30;
  return <>
    <PageHead title="整理记录" description="每一轮整理、学习和摘要的结果，可以逐条回看和撤销。">
      <ToggleGroup type="single" variant="outline" size="sm" value={tab} onValueChange={changeTab}>
        <ToggleGroupItem value="runs" role="button" aria-pressed={tab === 'runs'}>记录</ToggleGroupItem>
        <ToggleGroupItem value="digests" role="button" aria-pressed={tab === 'digests'}>聊天摘要</ToggleGroupItem>
      </ToggleGroup>
    </PageHead>
    <div className="flex flex-1 flex-col gap-4">
      {tab === 'runs' ? <>
        <TableToolbar>
          <div className="flex flex-1 flex-wrap items-center gap-2">
            <FacetedFilter title="类型" value={kind}
              options={Object.entries(runKinds).map(([value, label]) => ({ label, value }))}
              onChange={next => { setKind(next); setOffset(0); }} />
          </div>
        </TableToolbar>
        <TableShell>
          <Table className="min-w-[720px]">
            <TableHeader><TableRow>
              <TableHead className="w-[64px]">#</TableHead>
              <TableHead className="w-[104px]">类型</TableHead>
              <TableHead className="w-[150px]">来源</TableHead>
              <TableHead className="w-[96px]">状态</TableHead>
              <TableHead>结果</TableHead>
              <TableHead className="w-[150px] text-right">开始时间</TableHead>
            </TableRow></TableHeader>
            <TableBody>{!runs.length ? <TableRow>
              <TableCell colSpan={6} className="h-24 text-center text-muted-foreground">还没有记录。</TableCell>
            </TableRow> : runs.map(r => <TableRow key={r.id} className="clickable cursor-pointer"
              onClick={() => void action(async () => setOpen(await api<Run>(`/runs/${r.id}`)), '')}>
              <TableCell className="tabular-nums text-muted-foreground">{r.id}</TableCell>
              <TableCell>{runKinds[r.kind] ?? r.kind}</TableCell>
              <TableCell className="text-muted-foreground">{place(r.source)}</TableCell>
              <TableCell><Badge tone={tone(r.status)}>{r.undone ? '已撤销' : runLabels[r.status] ?? r.status}</Badge></TableCell>
              <TableCell className="whitespace-normal">{summary(r)}</TableCell>
              <TableCell className="text-right tabular-nums">{date(r.started)}</TableCell>
            </TableRow>)}</TableBody>
          </Table>
        </TableShell>
        <Pagination offset={offset} count={runs.length} size={size} change={setOffset} />
      </> : <>
        <TableShell>
          <Table className="min-w-[720px]">
            <TableHeader><TableRow>
              <TableHead className="w-[150px]">群</TableHead>
              <TableHead className="w-[150px] text-right">时间段</TableHead>
              <TableHead className="w-[80px] text-right">消息</TableHead>
              <TableHead>摘要</TableHead>
            </TableRow></TableHeader>
            <TableBody>{!digests.length ? <TableRow>
              <TableCell colSpan={4} className="h-24 text-center text-muted-foreground">还没有聊天摘要。</TableCell>
            </TableRow> : digests.map(d => <TableRow key={d.id}>
              <TableCell className="text-muted-foreground">{place(d.source)}</TableCell>
              <TableCell className="text-right tabular-nums">{date(d.start)}<br />{date(d.end)}</TableCell>
              <TableCell className="text-right tabular-nums">{d.count}</TableCell>
              <TableCell className="whitespace-normal">{d.summary}</TableCell>
            </TableRow>)}</TableBody>
          </Table>
        </TableShell>
        <Pagination offset={offset} count={digests.length} size={size} change={setOffset} />
      </>}
    </div>
    {open && <RunDetail run={open} close={() => setOpen(null)} />}
  </>;
}