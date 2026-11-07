import { Fragment, useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Editing, Memory } from '../api/types';
import { Badge, Empty, PageHead, Pagination } from '../shared/components';
import { useDashboard } from '../shared/dashboard';
import { certainty, date, memoryKinds, memoryStatus, origins, place, shortDate } from '../shared/format';
import { EditContent } from '../shared/EditContent';

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
  const [rows, setRows] = useState<Memory[]>([]);
  const [q, setQ] = useState('');
  const [kind, setKind] = useState('');
  const [status, setStatus] = useState('');
  const [source, setSource] = useState('');
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<number | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [editing, setEditing] = useState<Editing | null>(null);
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
  const batch = (verb: 'confirm' | 'reject' | 'delete', label: string) => {
    if(verb === 'delete' && !confirm(`删除选中的 ${selected.length} 条记忆？`)) return;
    void action(() => api('/memories/batch', 'POST', { ids: selected, action: verb }), label);
  };
  const verify = (m: Memory, verdict: 'correct' | 'wrong') => void action(() => api(`/memories/${m.id}/verify`, 'POST', { verdict }), verdict === 'correct' ? '已确认' : '已判定为错误');
  return <>
    <PageHead title="记忆" />
    <div className="toolbar">
      <input type="search" placeholder="搜索内容" value={q} onChange={e => { setQ(e.target.value); setOffset(0); }} />
      <select aria-label="类型" value={kind} onChange={e => { setKind(e.target.value); setOffset(0); }}>
        <option value="">全部类型</option>{Object.entries(memoryKinds).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select>
      <select aria-label="状态" value={status} onChange={e => { setStatus(e.target.value); setOffset(0); }}>
        <option value="">全部状态</option>{Object.entries(memoryStatus).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select>
      <input style={{ width: 160 }} placeholder="群号" value={source} onChange={e => { setSource(e.target.value); setOffset(0); }} />
    </div>
    {!!selected.length && <div className="selection-bar">已选 {selected.length} 条
      <button className="small" disabled={busy} onClick={() => batch('confirm', '已确认')}>确认正确</button>
      <button className="small" disabled={busy} onClick={() => batch('reject', '已判定为错误')}>判定错误</button>
      <button className="small danger" disabled={busy} onClick={() => batch('delete', '已删除')}>删除</button>
      <span className="spacer" /><button className="small ghost" onClick={() => setSelected([])}>取消选择</button></div>}
    <section className="panel" style={{ padding: 0 }}>
      {!rows.length ? <Empty text="没有匹配的记忆。" /> : <div className="table-wrap"><table className="table">
        <thead><tr><th><input type="checkbox" aria-label="全选" checked={selected.length === rows.length}
          onChange={e => setSelected(e.target.checked ? rows.map(r => r.id) : [])} /></th>
          <th>类型</th><th>内容</th><th>状态</th><th>置信度</th><th>来源</th><th>更新</th></tr></thead>
        <tbody>{rows.map(m => <Fragment key={m.id}>
          <tr className={'clickable' + (open === m.id ? ' open' : '')} onClick={() => setOpen(open === m.id ? null : m.id)}>
            <td onClick={e => e.stopPropagation()}><input type="checkbox" aria-label="选择" checked={selected.includes(m.id)} onChange={() => toggle(m.id)} /></td>
            <td><Badge>{memoryKinds[m.kind] ?? m.kind}</Badge></td>
            <td className="text">{m.kind === 'glossary' && <b>{m.term}：</b>}{m.text}</td>
            <td>{statusBadge(m)}</td>
            <td className="num">{Math.round(m.confidence * 100)}%</td>
            <td>{place(m.source)}</td>
            <td className="num">{shortDate(m.updated)}</td>
          </tr>
          {open === m.id && <tr className="detail-row"><td /><td colSpan={6}><div className="detail">
            <dl className="facts">
              <dt>关于</dt><dd>{m.subject || '—'}</dd>
              {m.participants !== '[]' && <><dt>参与者</dt><dd>{JSON.parse(m.participants).join('、')}</dd></>}
              <dt>原话引用</dt><dd>{m.quote || '—'}</dd>
              <dt>确定程度</dt><dd>{certainty[m.certainty] ?? m.certainty}，重要度 {m.importance}</dd>
              <dt>来自</dt><dd>{origins[m.origin] ?? m.origin}{m.run ? `（${m.run}）` : ''}</dd>
              <dt>被用到</dt><dd>{m.recalls} 次{m.last_recalled ? `，最近 ${date(m.last_recalled)}` : ''}</dd>
              {m.valid_until && <><dt>有效到</dt><dd>{shortDate(m.valid_until)}</dd></>}
              {m.superseded_by && <><dt>被取代</dt><dd>#{m.superseded_by}</dd></>}
            </dl>
            <details><summary>依据</summary><pre>{evidenceText(m.evidence)}</pre></details>
            <div className="row">
              <button className="small" disabled={busy} onClick={() => verify(m, 'correct')}>确认正确</button>
              <button className="small" disabled={busy} onClick={() => verify(m, 'wrong')}>判定错误</button>
              <button className="small" onClick={() => setEditing({ kind: 'memory', row: { ...m } })}>纠正</button>
              <span className="spacer" />
              <button className="small danger" disabled={busy} onClick={() => { if(confirm('删除这条记忆？')) void action(() => api(`/memories/${m.id}`, 'DELETE'), '已删除'); }}>删除</button>
            </div>
          </div></td></tr>}
        </Fragment>)}</tbody>
      </table></div>}
    </section>
    <Pagination offset={offset} count={rows.length} size={50} change={setOffset} />
    {editing && <EditContent editing={editing} setEditing={setEditing} />}
  </>;
}
