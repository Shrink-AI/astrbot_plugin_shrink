import { ChevronRightIcon, Plus } from 'lucide-react';
import { Fragment, useEffect, useRef, useState } from 'react';
import { cn } from 'cn';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Textarea } from '@/components/ui/textarea';
import { api } from '@/api/client';
import type { ChatLine, Proposal, ReviewItem, TeachSession, TeachThread } from '@/api/types';
import { Badge, Empty, PageHead, Panel } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';
import { memoryKinds, origins, place, reviewKinds } from '@/shared/format';

const kindLabels: Record<string, string> = {
  memory: '新增记忆', nickname: '新增称呼', glossary: '黑话', correct: '改正记忆', retire: '判定错误',
  confirm: '确认记忆', mark_bot: '机器人标记', question: '请你确认',
};
const statusLabels: Record<string, string> = { pending: '待处理', accepted: '已采纳', rejected: '已拒绝', invalid: '无效' };

function describe(p: Proposal) {
  const d = p.payload as Record<string, string | number | boolean | undefined>;
  switch(p.kind) {
    case 'memory': return `${d.subject_name}（${memoryKinds[String(d.kind)] ?? d.kind}）：${d.text}`;
    case 'nickname': return `${d.user_name} 也叫“${d.name}”（${d.scope ? place(String(d.scope)) : '所有群'}）`;
    case 'glossary': return `${d.term}：${d.meaning}`;
    case 'correct': return `“${d.old_text}” → “${d.text}”`;
    case 'retire': return `“${d.old_text}”${d.reason ? `（${d.reason}）` : ''}`;
    case 'confirm': return `“${d.old_text}”`;
    case 'mark_bot': return `${d.user_name} ${d.bot ? '是机器人' : '是真人'}`;
    case 'question': return String(d.text);
    default: return JSON.stringify(d);
  }
}

function ProposalCard({ p }: { p: Proposal }) {
  const { action, busy } = useDashboard();
  const editable = p.kind === 'memory' || p.kind === 'correct' ? 'text' : p.kind === 'nickname' ? 'name' : p.kind === 'glossary' ? 'meaning' : '';
  const [value, setValue] = useState(editable ? String((p.payload as Record<string, unknown>)[editable] ?? '') : '');
  const done = p.status !== 'pending';
  if(p.kind === 'question') {
    const queue = Number(p.payload.queue_id);
    const answer = (verdict: string, label: string) => void action(() => api(`/review/${queue}?proposal=${p.id}`, 'POST', { verdict }), label);
    return <Card className={cn('proposal gap-2 rounded-lg py-3 text-sm shadow-none', done && 'opacity-60')}>
      <div className="flex flex-wrap items-center gap-2"><Badge tone="info">{reviewKinds[String(p.payload.kind)] ?? '请你确认'}</Badge><span className="flex-1" />{done && <Badge>{p.result || statusLabels[p.status]}</Badge>}</div>
      <div className="break-words">{describe(p)}</div>
      {!done && <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" disabled={busy} onClick={() => answer('correct', '已确认')}>对</Button>
        <Button variant="outline" size="sm" disabled={busy} onClick={() => answer('wrong', '已判定为错误')}>不对</Button>
        <Button variant="ghost" size="sm" disabled={busy} onClick={() => answer('skip', '已跳过')}>跳过</Button></div>}
    </Card>;
  }
  return <Card className={cn('proposal gap-2 rounded-lg py-3 text-sm shadow-none', done && 'opacity-60')}>
    <div className="flex flex-wrap items-center gap-2"><Badge tone={p.status === 'invalid' ? 'bad' : undefined}>{kindLabels[p.kind] ?? p.kind}</Badge><span className="flex-1" />
      {done && <Badge tone={p.status === 'accepted' ? 'ok' : undefined}>{statusLabels[p.status]}</Badge>}</div>
    {!done && editable
      ? <Textarea rows={2} aria-label="内容" className="min-h-0" value={value} onChange={e => setValue(e.target.value)} />
      : <div className="break-words">{describe(p)}</div>}
    {p.status === 'invalid' && <p className="text-xs text-muted-foreground">{p.result}</p>}
    {p.status === 'accepted' && p.result && <p className="text-xs text-muted-foreground">{p.result}</p>}
    {!done && <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" disabled={busy || (!!editable && !value.trim())} onClick={() => void action(() =>
        api(`/teach/proposals/${p.id}/accept`, 'POST', editable ? { [editable]: value.trim() } : {}), '已采纳')}>采纳</Button>
      <Button variant="outline" size="sm" disabled={busy} onClick={() => void action(() => api(`/teach/proposals/${p.id}/reject`, 'POST'), '已拒绝')}>拒绝</Button>
    </div>}
  </Card>;
}

const two = (n: number) => String(n).padStart(2, '0');
const day = (v: number) => { const d = new Date(v * 1000); return `${d.getMonth() + 1}月${d.getDate()}日`; };
const host = (u: string) => { try { return new URL(u).hostname; } catch { return u; } };
const clock = (v: number) => { const d = new Date(v * 1000); return `${two(d.getHours())}:${two(d.getMinutes())}`; };

function ChatLog({ lines }: { lines: ChatLine[] }) {
  let header = '';
  return <div className="chatlog flex flex-col gap-1 rounded-lg border bg-muted/30 p-2.5 text-sm">{lines.map(l => {
    const key = `${l.source}|${day(l.created)}`;
    const head = key !== header;
    header = key;
    return <Fragment key={l.id}>
      {head && <div className="chatlog-head text-xs font-medium text-muted-foreground">{l.group || place(l.source)} · {day(l.created)}</div>}
      {l.gap && !head && <div className="chatlog-gap text-xs text-muted-foreground">……</div>}
      <div className={cn('chatline break-words', l.cited && 'cited rounded bg-accent px-1 font-medium')}>
        <span className="tabular-nums text-xs text-muted-foreground">{clock(l.created)}</span>{' '}
        <span className="speaker text-foreground">{l.speaker}</span>：{l.text || '［图片］'}
      </div>
    </Fragment>;
  })}</div>;
}

function ReviewCard({ item, disabled, answer }: {
  item: ReviewItem; disabled: boolean; answer: (verdict: 'correct' | 'wrong' | 'skip') => void;
}) {
  const p = item.person;
  const word = item.kind.startsWith('nickname') ? '这个称呼' : '这个词';
  const shown = item.memories.slice(0, 6), rest = item.memories.slice(6);
  const memory = (m: ReviewItem['memories'][number]) => <li key={m.id} className="break-words">{m.text}
    {m.verified === 1 ? <span className="text-xs text-muted-foreground">（已确认）</span>
      : m.status === 'candidate' ? <span className="text-xs text-muted-foreground">（待确认）</span>
        : <span className="text-xs text-muted-foreground">（{origins[m.origin] ?? m.origin}）</span>}</li>;
  return <div className="proposal flex flex-col gap-2 rounded-lg border p-3 text-sm">
    <div><Badge tone="info">{reviewKinds[item.kind] ?? '请你确认'}</Badge></div>
    <div className="break-words">{item.text || item.reason}</div>
    {item.old && <div className="text-xs text-muted-foreground">之前确认过的是：{item.old}</div>}
    {p && <div className="text-xs text-muted-foreground">说的是 {p.name}（{p.bot ? '机器人，' : ''}QQ {p.account}{p.names.length ? `，也叫 ${p.names.join('、')}` : ''}）</div>}
    {!!item.chat.length && <div className="flex flex-col gap-1">
      <span className="text-xs text-muted-foreground">{item.kind === 'suspected_bot' ? 'TA 最近说的话' : '当时的聊天（高亮的是依据）'}</span>
      <ChatLog lines={item.chat} />
    </div>}
    {!item.chat.length && !item.memories.length && item.kind !== 'profile_new' && (item.quote
      ? <div className="text-xs text-muted-foreground">原话摘录：“{item.quote}”（原始聊天已找不到）</div>
      : <div className="text-xs text-muted-foreground">找不到原始聊天。</div>)}
    {!!item.uses.length && <div className="flex flex-col gap-1">
      <span className="text-xs text-muted-foreground">群里其他用到{word}的地方</span>
      <ChatLog lines={item.uses} />
    </div>}
    {item.kind === 'profile_new' && <div className="flex flex-col gap-1">
      <span className="text-xs text-muted-foreground">小结是根据这些记忆写的（{item.memories.length}）</span>
      {item.memories.length ? <ul className="flex list-inside list-disc flex-col gap-1">{shown.map(memory)}</ul>
        : <div className="text-xs text-muted-foreground">TA 现在没有记忆。</div>}
      {!!rest.length && <Collapsible className="group/collapsible text-sm">
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
            <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
            其余 {rest.length} 条
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="pt-2">
          <ul className="mt-1 flex list-inside list-disc flex-col gap-1">{rest.map(memory)}</ul>
        </CollapsibleContent>
      </Collapsible>}
    </div>}
    {!!item.links.length && <div className="text-xs text-muted-foreground">网上来源：{item.links.map(u =>
      <a key={u} className="text-info underline underline-offset-2" href={u} target="_blank" rel="noreferrer">{host(u)}</a>)}</div>}
    <span className="text-xs text-muted-foreground">原因：{item.reason}</span>
    <div className="flex flex-wrap items-center gap-2">
      <Button variant="outline" size="sm" disabled={disabled} onClick={() => answer('correct')}>对</Button>
      <Button variant="outline" size="sm" disabled={disabled} onClick={() => answer('wrong')}>不对</Button>
      <Button variant="ghost" size="sm" disabled={disabled} onClick={() => answer('skip')}>跳过</Button>
    </div>
  </div>;
}

export function TeachPage() {
  const { action, busy, revision, setError, status, refresh } = useDashboard();
  const [sessions, setSessions] = useState<TeachSession[]>([]);
  const [current, setCurrent] = useState<number | null>(null);
  const [thread, setThread] = useState<TeachThread | null>(null);
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [reviews, setReviews] = useState<ReviewItem[]>([]);
  const [reviewPage, setReviewPage] = useState({ revision, count: status.counts.review_open, offset: 0 });
  const [reviewLoading, setReviewLoading] = useState(true);
  const reviewCount = status.counts.review_open;
  const reviewOffset = reviewPage.revision === revision && reviewPage.count === reviewCount ? reviewPage.offset : 0;
  const bottom = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let cancelled = false;
    setReviewLoading(true);
    api<ReviewItem[]>(`/review?offset=${reviewOffset}`).then(rows => {
      if(!cancelled) setReviews(prev => reviewOffset ? [...prev, ...rows] : rows);
    }).catch(e => { if(!cancelled) setError(e.message); })
      .finally(() => { if(!cancelled) setReviewLoading(false); });
    return () => { cancelled = true; };
  }, [revision, reviewCount, reviewOffset]);
  useEffect(() => {
    api<TeachSession[]>('/teach/sessions').then(rows => {
      setSessions(rows);
      if(current === null && rows[0]) setCurrent(rows[0].id);
    }).catch(e => setError(e.message));
  }, [revision]);
  useEffect(() => {
    if(current === null) { setThread(null); return; }
    let cancelled = false;
    api<TeachThread>(`/teach/sessions/${current}`).then(t => { if(!cancelled) setThread(t); }).catch(e => setError(e.message));
    return () => { cancelled = true; };
  }, [current, revision]);
  useEffect(() => { bottom.current?.scrollIntoView({ block: 'end' }); }, [thread?.messages.length]);
  const create = () => void action(async () => { const r = await api<{ id: number }>('/teach/sessions', 'POST'); setCurrent(r.id); }, '');
  const loadMore = () => setReviewPage({ revision, count: reviewCount, offset: reviewOffset + 30 });
  const answer = (id: number, verdict: 'correct' | 'wrong' | 'skip') => void action(
    () => api(`/review/${id}`, 'POST', { verdict }),
    { correct: '已确认', wrong: '已判定为错误', skip: '已跳过' }[verdict],
  );
  const ask = () => void action(async () => {
    setSending(true);
    try {
      const sessionId = current ?? (await api<{ id: number }>('/teach/sessions', 'POST')).id;
      setCurrent(sessionId);
      await api(`/teach/sessions/${sessionId}/review`, 'POST');
    } finally { setSending(false); }
  }, '');
  const send = async (path: string, body?: unknown) => {
    if(current === null) return;
    setSending(true); setError('');
    try { await api(path, 'POST', body); setText(''); refresh(); }
    catch(e) { setError((e as Error).message); }
    finally { setSending(false); }
  };
  const pending = thread?.proposals.filter(p => p.status === 'pending') ?? [];
  const finished = thread?.proposals.filter(p => p.status !== 'pending').slice(-20).reverse() ?? [];
  return <>
    <PageHead title="定向学习">
      <Button disabled={busy} onClick={create}><Plus data-icon="inline-start" />新对话</Button>
    </PageHead>
    <p className="mb-3 text-sm text-muted-foreground">告诉石英群友的事、称呼或黑话，它会拆成具体条目，你采纳后才写入；也可以让它把不确定的数据拿来问你。</p>
    <div className="grid items-start gap-3 lg:grid-cols-[220px_minmax(0,1fr)] xl:grid-cols-[240px_minmax(0,1fr)_330px]">
      <Card className="gap-0 py-0">
        {!sessions.length && <Empty text="还没有对话。" />}
        <div className="flex max-h-[70vh] flex-col gap-0.5 overflow-y-auto p-1.5">
          {sessions.map(s => <Button key={s.id} variant="ghost" onClick={() => setCurrent(s.id)}
            className={cn('list-item h-auto w-full flex-col items-start justify-start gap-0.5 px-2.5 py-2 text-left whitespace-normal',
              s.id === current && 'active bg-secondary')}>
            <span className="title text-sm font-medium">{s.title}</span>
            {!!s.pending && <span className="text-xs text-muted-foreground">{s.pending} 条待处理</span>}
          </Button>)}
        </div>
      </Card>
      <Panel>
        {current === null ? <Empty text="新建一个对话开始。" /> : <div className="flex flex-col gap-3">
          <div className="flex max-h-[60vh] flex-col gap-2.5 overflow-y-auto">
            {!thread?.messages.length && <Empty text="例如：小林以前在上海实习过，群里也叫 TA 林哥。" />}
            {thread?.messages.map(m => <div key={m.id}
              className={cn('max-w-[85%] rounded-lg px-3 py-2 text-sm whitespace-pre-wrap break-words',
                m.role === 'admin' ? 'self-end bg-secondary' : 'self-start bg-muted')}>{m.text}</div>)}
            {sending && <div className="max-w-[85%] self-start rounded-lg bg-muted px-3 py-2 text-sm whitespace-pre-wrap break-words">处理中…</div>}
            <div ref={bottom} />
          </div>
          <form className="flex flex-col gap-2" onSubmit={e => { e.preventDefault(); if(text.trim()) void send(`/teach/sessions/${current}/messages`, { text: text.trim() }); }}>
            <Textarea placeholder="说点石英应该知道的事" value={text} maxLength={2000} onChange={e => setText(e.target.value)}
              onKeyDown={e => { if(e.key === 'Enter' && (e.ctrlKey || e.metaKey) && text.trim()) void send(`/teach/sessions/${current}/messages`, { text: text.trim() }); }} />
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground">Ctrl + Enter 发送</span>
              <span className="flex-1" />
              <Button disabled={sending || !text.trim()}>发送</Button>
            </div>
          </form>
        </div>}
      </Panel>
      <div className="flex flex-col gap-3 lg:col-span-2 xl:col-span-1">
        <Panel title={`待你确认（${reviewCount}）`} actions={
          <Button variant="ghost" size="sm" disabled={busy || sending || reviewLoading || reviewCount === 0} onClick={ask}>让石英换个说法问</Button>
        }>
          {reviewLoading ? <Empty text="加载中…" /> : !reviews.length ? <Empty text="没有需要你确认的数据。" /> :
            <div className="flex flex-col gap-2.5">{reviews.map(item =>
              <ReviewCard key={item.id} item={item} disabled={busy || sending} answer={verdict => answer(item.id, verdict)} />)}</div>}
          {reviews.length < reviewCount && <div className="mt-3 flex justify-end">
            <Button variant="outline" size="sm" disabled={busy || sending || reviewLoading} onClick={loadMore}>加载更多</Button>
          </div>}
        </Panel>
        <Panel title={`待处理（${pending.length}）`}>
          {!pending.length && <Empty text="没有待处理的建议。" />}
          {!!pending.length && <div className="flex flex-col gap-2.5">{pending.map(p => <ProposalCard key={p.id} p={p} />)}</div>}
          {!!finished.length && <Collapsible className="group/collapsible mt-3 text-sm">
            <CollapsibleTrigger asChild>
              <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
                <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
                已处理（{finished.length}）
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent className="pt-2">
              <div className="mt-2 flex flex-col gap-2.5">{finished.map(p => <ProposalCard key={p.id} p={p} />)}</div>
            </CollapsibleContent>
          </Collapsible>}
        </Panel>
      </div>
    </div>
  </>;
}