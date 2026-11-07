import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Digest, Run } from '../api/types';
import { Badge, Empty, PageHead, Pagination } from '../shared/components';
import { useDashboard } from '../shared/dashboard';
import { date, place, runKinds, runLabels } from '../shared/format';
import { RunDetail } from '../shared/RunDetail';

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
  const tone = (s: string) => s === 'success' ? 'ok' : s === 'failed' ? 'bad' : s === 'running' ? 'info' : undefined;
  return <>
    <PageHead title="整理记录">
      <div className="segmented">
        <button className={tab === 'runs' ? 'on' : ''} onClick={() => { setTab('runs'); setOffset(0); }}>记录</button>
        <button className={tab === 'digests' ? 'on' : ''} onClick={() => { setTab('digests'); setOffset(0); }}>聊天摘要</button>
      </div>
    </PageHead>
    {tab === 'runs' ? <>
      <div className="toolbar"><select aria-label="类型" value={kind} onChange={e => { setKind(e.target.value); setOffset(0); }}>
        <option value="">全部类型</option>{Object.entries(runKinds).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select></div>
      <section className="panel" style={{ padding: 0 }}>
        {!runs.length ? <Empty text="还没有记录。" /> : <div className="table-wrap"><table className="table">
          <thead><tr><th>#</th><th>类型</th><th>来源</th><th>状态</th><th>结果</th><th>开始时间</th></tr></thead>
          <tbody>{runs.map(r => <tr key={r.id} className="clickable" onClick={() => void action(async () => setOpen(await api<Run>(`/runs/${r.id}`)), '')}>
            <td>{r.id}</td><td>{runKinds[r.kind] ?? r.kind}</td><td>{place(r.source)}</td>
            <td><Badge tone={tone(r.status)}>{r.undone ? '已撤销' : runLabels[r.status] ?? r.status}</Badge></td>
            <td className="text">{summary(r)}</td><td className="num">{date(r.started)}</td></tr>)}</tbody>
        </table></div>}
      </section>
      <Pagination offset={offset} count={runs.length} size={30} change={setOffset} />
    </> : <>
      <section className="panel" style={{ padding: 0 }}>
        {!digests.length ? <Empty text="还没有聊天摘要。" /> : <div className="table-wrap"><table className="table">
          <thead><tr><th>群</th><th>时间段</th><th>消息</th><th>摘要</th></tr></thead>
          <tbody>{digests.map(d => <tr key={d.id}><td>{place(d.source)}</td><td className="num">{date(d.start)}<br />{date(d.end)}</td>
            <td className="num">{d.count}</td><td className="text">{d.summary}</td></tr>)}</tbody>
        </table></div>}
      </section>
      <Pagination offset={offset} count={digests.length} size={30} change={setOffset} />
    </>}
    {open && <RunDetail run={open} close={() => setOpen(null)} />}
  </>;
}
