import { X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { cn } from 'cn';
import { Badge as UiBadge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Field, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { api } from '@/api/client';
import { useConfirm } from '@/components/confirm-dialog';
import type { Group, Nickname, Person, PersonDetail } from '@/api/types';
import { Badge, Empty, Facts, PageHead, Pagination, Panel } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';
import { aliases, date, memoryKinds, memoryStatus, origins, place, shortDate } from '@/shared/format';

function NicknameChip({ n, remove }: { n: Nickname; remove: () => Promise<void> }) {
  const title = `${origins[n.origin] ?? n.origin} · ${n.scope ? place(n.scope) : '所有群'}${n.verified === 1 ? ' · 已确认' : ''}`;
  return <span className={cn('chip inline-flex items-center gap-0.5', n.status !== 'active' && n.status)} title={title}>
    <UiBadge variant="outline" className="gap-1">
      {n.name}{n.status === 'candidate' && <span className="text-xs text-muted-foreground">待确认</span>}
    </UiBadge>
    <Button variant="ghost" size="icon-xs" aria-label={`删除称呼 ${n.name}`} onClick={() => void remove()}><X /></Button>
  </span>;
}

function PersonPanel({ uid }: { uid: string }) {
  const { action, busy, revision, setError } = useDashboard();
  const ask = useConfirm();
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
  return <div className="flex flex-col gap-2.5">
    <Panel title={person.name} actions={<div className="flex flex-wrap items-center gap-2">
      {person.kind === 'bot' && <Badge tone="warn">机器人</Badge>}
      <Button variant="outline" size="sm" disabled={busy} onClick={() => void action(() =>
        api(`/people/${encodeURIComponent(uid)}/kind`, 'PUT', { kind: person.kind === 'bot' ? 'human' : 'bot' }),
      person.kind === 'bot' ? '已改为真人' : '已标为机器人，之后不再从 TA 的发言学习')}>
        {person.kind === 'bot' ? '改为真人' : '标为机器人'}</Button></div>}>
      <Facts>
        <dt>账号</dt><dd>{person.id}</dd>
        <dt>以前的昵称</dt><dd>{others.length ? others.join('、') : '—'}</dd>
        <dt>小结</dt><dd>{profile?.summary || '还没有'}{profile?.verified === 1 && <> <Badge tone="ok">已确认</Badge></>}</dd>
        <dt>和石英</dt><dd>{profile?.relation || '—'}（石英回复过 {detail.interactions.replies} 次{detail.interactions.last ? `，最近 ${shortDate(detail.interactions.last)}` : ''}）</dd>
        <dt>第一次出现</dt><dd>{date(person.first_seen)}</dd>
      </Facts>
    </Panel>
    <Panel title="称呼">
      <p className="text-sm text-muted-foreground">群友怎么叫 TA。石英靠这些判断一句话是在叫谁。</p>
      <div className="my-2.5 flex flex-wrap items-center gap-1.5">
        {!detail.nicknames.length && <span className="text-sm text-muted-foreground">还没有记录</span>}
        {detail.nicknames.map(n => <NicknameChip key={n.id} n={n} remove={async () => {
          if(!await ask({ title: '删除称呼？', desc: `删除称呼“${n.name}”？`, destructive: true })) return;
          void action(() => api(`/nicknames/${n.id}`, 'DELETE'), '已删除');
        }} />)}
      </div>
      <form className="flex flex-wrap items-center gap-2" onSubmit={e => {
        e.preventDefault();
        void action(async () => { await api(`/people/${encodeURIComponent(uid)}/nicknames`, 'POST', { name: name.trim(), scope }); setName(''); }, '已添加');
      }}>
        <Field orientation="horizontal" className="w-auto">
          <FieldLabel htmlFor="nickname-name">新称呼</FieldLabel>
          <Input id="nickname-name" className="w-40" placeholder="新称呼" maxLength={12} value={name} onChange={e => setName(e.target.value)} />
        </Field>
        <Select value={scope || 'global'} onValueChange={next => setScope(next === 'global' ? '' : next)}>
          <SelectTrigger className="w-[180px]" aria-label="范围">
            <SelectValue placeholder="所有群" />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectItem value="global">所有群</SelectItem>
              {detail.sources.filter(s => s.source.includes(':group:')).map(s => <SelectItem key={s.source} value={s.source}>只在 {place(s.source)}</SelectItem>)}
            </SelectGroup>
          </SelectContent>
        </Select>
        <Button type="submit" disabled={busy || !name.trim()}>添加</Button>
      </form>
    </Panel>
    <Panel title="出现过的地方">
      <Table>
        <TableBody>{detail.sources.map(s => <TableRow key={s.source}>
          <TableCell>{place(s.source)}</TableCell>
          <TableCell className="text-right tabular-nums">{s.n} 条消息</TableCell>
          <TableCell className="text-right tabular-nums">{date(s.last)}</TableCell>
        </TableRow>)}</TableBody>
      </Table>
    </Panel>
    <Panel title={`相关记忆（${detail.memories.length}）`}>
      {!detail.memories.length && <Empty text="还没有关于 TA 的记忆。" />}
      {!!detail.memories.length && <Table>
        <TableBody>
          {detail.memories.map(m => <TableRow key={m.id}>
            <TableCell><Badge>{memoryKinds[m.kind] ?? m.kind}</Badge></TableCell>
            <TableCell className="min-w-[260px] whitespace-normal">{m.text}</TableCell>
            <TableCell><Badge tone={m.status === 'active' ? (m.verified === 1 ? 'ok' : undefined) : m.status === 'candidate' ? 'warn' : 'bad'}>
              {m.verified === 1 ? '已确认' : memoryStatus[m.status] ?? m.status}</Badge></TableCell>
            <TableCell className="text-right tabular-nums">{place(m.source)}</TableCell>
          </TableRow>)}
        </TableBody>
      </Table>}
    </Panel>
  </div>;
}

function Groups() {
  const { revision, setError } = useDashboard();
  const [groups, setGroups] = useState<Group[]>([]);
  useEffect(() => { api<Group[]>('/groups').then(setGroups).catch(e => setError(e.message)); }, [revision]);
  if(!groups.length) return <Panel><Empty text="还没有群聊记录。" /></Panel>;
  return <Panel><Table>
    <TableHeader>
      <TableRow><TableHead>群</TableHead><TableHead>小结</TableHead><TableHead className="text-right">消息</TableHead><TableHead className="text-right">最近</TableHead></TableRow>
    </TableHeader>
    <TableBody>{groups.map(g => <TableRow key={g.source}>
      <TableCell>{place(g.source)}</TableCell>
      <TableCell className="min-w-[260px] whitespace-normal">{g.profile?.summary || <span className="text-muted-foreground">夜间整理后生成</span>}</TableCell>
      <TableCell className="text-right tabular-nums">{g.n}</TableCell>
      <TableCell className="text-right tabular-nums">{shortDate(g.last)}</TableCell>
    </TableRow>)}</TableBody>
  </Table></Panel>;
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
      <ToggleGroup type="single" variant="outline" size="sm" value={tab}
        onValueChange={value => { if(value === 'people' || value === 'groups') setTab(value); }}>
        <ToggleGroupItem value="people">群友</ToggleGroupItem>
        <ToggleGroupItem value="groups">群</ToggleGroupItem>
      </ToggleGroup>
    </PageHead>
    {tab === 'groups' ? <Groups /> : <div className="grid grid-cols-1 gap-3 md:grid-cols-[280px_minmax(0,1fr)]">
      <div className="flex flex-col gap-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <Input type="search" placeholder="搜索名字、称呼或账号" className="min-w-0 flex-1" value={q}
            onChange={e => { setQ(e.target.value); setOffset(0); }} />
          <Select value={kind || 'all'} onValueChange={next => { setKind(next === 'all' ? '' : next); setOffset(0); }}>
            <SelectTrigger className="w-[120px]" aria-label="类型">
              <SelectValue placeholder="全部" />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectItem value="all">全部</SelectItem>
                <SelectItem value="human">真人</SelectItem>
                <SelectItem value="bot">机器人</SelectItem>
              </SelectGroup>
            </SelectContent>
          </Select>
        </div>
        <Panel>
          {!people.length && <Empty text="没有匹配的人。" />}
          <div className="flex flex-col gap-0.5">
            {people.map(p => <Button key={p.id} variant="ghost"
              className={cn('list-item h-auto w-full flex-col items-start justify-start gap-0.5 text-left whitespace-normal', selected === p.id && 'active')}
              onClick={() => setSelected(p.id)}>
              <span className="title flex items-center gap-1.5 font-medium">{p.name}{p.kind === 'bot' && <Badge tone="warn">机器人</Badge>}</span>
              <small className="text-xs text-muted-foreground">{(p.nicknames ?? []).filter(n => n.status === 'active').map(n => n.name).join('、') || p.id} · {p.memory_count ?? 0} 条记忆</small>
            </Button>)}
          </div>
        </Panel>
        <Pagination offset={offset} count={people.length} size={50} change={setOffset} />
      </div>
      {selected ? <PersonPanel uid={selected} /> : <Panel><Empty text="选择左侧的人查看详情。" /></Panel>}
    </div>}
  </>;
}