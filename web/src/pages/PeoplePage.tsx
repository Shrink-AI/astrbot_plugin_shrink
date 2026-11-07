import { X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Group, Nickname, Person, PersonDetail } from '../api/types';
import { Badge, Empty, PageHead, Pagination, Panel } from '../shared/components';
import { useDashboard } from '../shared/dashboard';
import { aliases, date, memoryKinds, memoryStatus, origins, place, shortDate } from '../shared/format';

function NicknameChip({ n, remove }: { n: Nickname; remove: () => void }) {
  const title = `${origins[n.origin] ?? n.origin} · ${n.scope ? place(n.scope) : '所有群'}${n.verified === 1 ? ' · 已确认' : ''}`;
  return <span className={'chip ' + (n.status !== 'active' ? n.status : '')} title={title}>
    {n.name}{n.status === 'candidate' && <small>待确认</small>}
    <button aria-label={`删除称呼 ${n.name}`} onClick={remove}><X size={12} /></button>
  </span>;
}

function PersonPanel({ uid }: { uid: string }) {
  const { action, busy, revision, setError } = useDashboard();
  const [detail, setDetail] = useState<PersonDetail | null>(null);
  const [name, setName] = useState('');
  const [scope, setScope] = useState('');
  useEffect(() => {
    let cancelled = false;
    api<PersonDetail>(`/people/${encodeURIComponent(uid)}`).then(d => { if(!cancelled) setDetail(d); })
      .catch(e => { if(!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [uid, revision]);
  if(!detail) return <Panel><Empty text="加载中…" /></Panel>;
  const { person, profile } = detail;
  const others = aliases(person.aliases).filter(a => a !== person.name);
  return <div className="stack">
    <Panel title={person.name} actions={<div className="row">
      {person.kind === 'bot' && <Badge tone="warn">机器人</Badge>}
      <button className="small" disabled={busy} onClick={() => void action(() =>
        api(`/people/${encodeURIComponent(uid)}/kind`, 'PUT', { kind: person.kind === 'bot' ? 'human' : 'bot' }),
      person.kind === 'bot' ? '已改为真人' : '已标为机器人，之后不再从 TA 的发言学习')}>
        {person.kind === 'bot' ? '改为真人' : '标为机器人'}</button></div>}>
      <dl className="facts">
        <dt>账号</dt><dd>{person.id}</dd>
        <dt>以前的昵称</dt><dd>{others.length ? others.join('、') : '—'}</dd>
        <dt>小结</dt><dd>{profile?.summary || '还没有'}{profile?.verified === 1 && <> <Badge tone="ok">已确认</Badge></>}</dd>
        <dt>和石英</dt><dd>{profile?.relation || '—'}（石英回复过 {detail.interactions.replies} 次{detail.interactions.last ? `，最近 ${shortDate(detail.interactions.last)}` : ''}）</dd>
        <dt>第一次出现</dt><dd>{date(person.first_seen)}</dd>
      </dl>
    </Panel>
    <Panel title="称呼">
      <p className="muted">群友怎么叫 TA。石英靠这些判断一句话是在叫谁。</p>
      <div className="chips" style={{ margin: '10px 0' }}>
        {!detail.nicknames.length && <span className="muted">还没有记录</span>}
        {detail.nicknames.map(n => <NicknameChip key={n.id} n={n} remove={() => {
          if(confirm(`删除称呼“${n.name}”？`)) void action(() => api(`/nicknames/${n.id}`, 'DELETE'), '已删除');
        }} />)}
      </div>
      <form className="row" onSubmit={e => {
        e.preventDefault();
        void action(async () => { await api(`/people/${encodeURIComponent(uid)}/nicknames`, 'POST', { name: name.trim(), scope }); setName(''); }, '已添加');
      }}>
        <input style={{ width: 160 }} placeholder="新称呼" maxLength={12} value={name} onChange={e => setName(e.target.value)} />
        <select style={{ width: 'auto' }} value={scope} onChange={e => setScope(e.target.value)}>
          <option value="">所有群</option>
          {detail.sources.filter(s => s.source.includes(':group:')).map(s => <option key={s.source} value={s.source}>只在 {place(s.source)}</option>)}
        </select>
        <button disabled={busy || !name.trim()}>添加</button>
      </form>
    </Panel>
    <Panel title="出现过的地方">
      <table className="table"><tbody>{detail.sources.map(s => <tr key={s.source}>
        <td>{place(s.source)}</td><td className="num">{s.n} 条消息</td><td className="num">{date(s.last)}</td></tr>)}</tbody></table>
    </Panel>
    <Panel title={`相关记忆（${detail.memories.length}）`}>
      {!detail.memories.length && <Empty text="还没有关于 TA 的记忆。" />}
      {!!detail.memories.length && <div className="table-wrap"><table className="table"><tbody>
        {detail.memories.map(m => <tr key={m.id}>
          <td><Badge>{memoryKinds[m.kind] ?? m.kind}</Badge></td>
          <td className="text">{m.text}</td>
          <td><Badge tone={m.status === 'active' ? (m.verified === 1 ? 'ok' : undefined) : m.status === 'candidate' ? 'warn' : 'bad'}>
            {m.verified === 1 ? '已确认' : memoryStatus[m.status] ?? m.status}</Badge></td>
          <td className="num">{place(m.source)}</td></tr>)}
      </tbody></table></div>}
    </Panel>
  </div>;
}

function Groups() {
  const { revision, setError } = useDashboard();
  const [groups, setGroups] = useState<Group[]>([]);
  useEffect(() => { api<Group[]>('/groups').then(setGroups).catch(e => setError(e.message)); }, [revision]);
  if(!groups.length) return <Panel><Empty text="还没有群聊记录。" /></Panel>;
  return <Panel><div className="table-wrap"><table className="table">
    <thead><tr><th>群</th><th>小结</th><th>消息</th><th>最近</th></tr></thead>
    <tbody>{groups.map(g => <tr key={g.source}><td>{place(g.source)}</td><td className="text">{g.profile?.summary || <span className="muted">夜间整理后生成</span>}</td>
      <td className="num">{g.n}</td><td className="num">{shortDate(g.last)}</td></tr>)}</tbody>
  </table></div></Panel>;
}

export function PeoplePage() {
  const { revision, setError } = useDashboard();
  const [tab, setTab] = useState<'people' | 'groups'>('people');
  const [people, setPeople] = useState<Person[]>([]);
  const [q, setQ] = useState('');
  const [kind, setKind] = useState('');
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState('');
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      api<Person[]>(`/people?q=${encodeURIComponent(q)}&kind=${kind}&offset=${offset}`)
        .then(rows => { if(!cancelled) { setPeople(rows); if(!selected && rows[0]) setSelected(rows[0].id); } })
        .catch(e => { if(!cancelled) setError(e.message); });
    }, q ? 250 : 0);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [q, kind, offset, revision]);
  return <>
    <PageHead title="人物">
      <div className="segmented">
        <button className={tab === 'people' ? 'on' : ''} onClick={() => setTab('people')}>群友</button>
        <button className={tab === 'groups' ? 'on' : ''} onClick={() => setTab('groups')}>群</button>
      </div>
    </PageHead>
    {tab === 'groups' ? <Groups /> : <div className="split">
      <div className="stack">
        <div className="toolbar" style={{ margin: 0 }}>
          <input type="search" placeholder="搜索名字、称呼或账号" value={q} onChange={e => { setQ(e.target.value); setOffset(0); }} />
          <select value={kind} onChange={e => { setKind(e.target.value); setOffset(0); }}>
            <option value="">全部</option><option value="human">真人</option><option value="bot">机器人</option>
          </select>
        </div>
        <section className="panel" style={{ padding: 0 }}>
          {!people.length && <Empty text="没有匹配的人。" />}
          <div className="list">{people.map(p => <button key={p.id} className={'list-item' + (selected === p.id ? ' active' : '')} onClick={() => setSelected(p.id)}>
            <span className="title">{p.name}{p.kind === 'bot' && <Badge tone="warn">机器人</Badge>}</span>
            <small>{(p.nicknames ?? []).filter(n => n.status === 'active').map(n => n.name).join('、') || p.id} · {p.memory_count ?? 0} 条记忆</small>
          </button>)}</div>
        </section>
        <Pagination offset={offset} count={people.length} size={50} change={setOffset} />
      </div>
      {selected ? <PersonPanel uid={selected} /> : <Panel><Empty text="选择左侧的人查看详情。" /></Panel>}
    </div>}
  </>;
}
