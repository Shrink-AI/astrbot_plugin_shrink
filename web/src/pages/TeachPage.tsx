import { Plus } from 'lucide-react';
import { Fragment, useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { ChatLine, Proposal, ReviewItem, TeachSession, TeachThread } from '../api/types';
import { Badge, Empty, PageHead, Panel } from '../shared/components';
import { useDashboard } from '../shared/dashboard';
import { memoryKinds, origins, place, reviewKinds } from '../shared/format';

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
    return <div className={'proposal' + (done ? ' done' : '')}>
      <div className="row"><Badge tone="info">{reviewKinds[String(p.payload.kind)] ?? '请你确认'}</Badge><span className="spacer" />{done && <Badge>{p.result || statusLabels[p.status]}</Badge>}</div>
      <div>{describe(p)}</div>
      {!done && <div className="row">
        <button className="small" disabled={busy} onClick={() => answer('correct', '已确认')}>对</button>
        <button className="small" disabled={busy} onClick={() => answer('wrong', '已判定为错误')}>不对</button>
        <button className="small ghost" disabled={busy} onClick={() => answer('skip', '已跳过')}>跳过</button></div>}
    </div>;
  }
  return <div className={'proposal' + (done ? ' done' : '')}>
    <div className="row"><Badge tone={p.status === 'invalid' ? 'bad' : undefined}>{kindLabels[p.kind] ?? p.kind}</Badge><span className="spacer" />
      {done && <Badge tone={p.status === 'accepted' ? 'ok' : undefined}>{statusLabels[p.status]}</Badge>}</div>
    {!done && editable ? <textarea rows={2} aria-label="内容" value={value} onChange={e => setValue(e.target.value)} /> : <div>{describe(p)}</div>}
    {p.status === 'invalid' && <small>{p.result}</small>}
    {p.status === 'accepted' && p.result && <small>{p.result}</small>}
    {!done && <div className="row">
      <button className="small primary" disabled={busy || (!!editable && !value.trim())} onClick={() => void action(() =>
        api(`/teach/proposals/${p.id}/accept`, 'POST', editable ? { [editable]: value.trim() } : {}), '已采纳')}>采纳</button>
      <button className="small" disabled={busy} onClick={() => void action(() => api(`/teach/proposals/${p.id}/reject`, 'POST'), '已拒绝')}>拒绝</button>
    </div>}
  </div>;
}

const two = (n: number) => String(n).padStart(2, '0');
const day = (v: number) => { const d = new Date(v * 1000); return `${d.getMonth() + 1}月${d.getDate()}日`; };
const host = (u: string) => { try { return new URL(u).hostname; } catch { return u; } };
const clock = (v: number) => { const d = new Date(v * 1000); return `${two(d.getHours())}:${two(d.getMinutes())}`; };

function ChatLog({ lines }: { lines: ChatLine[] }) {
  let header = '';
  return <div className="chatlog">{lines.map(l => {
    const key = `${l.source}|${day(l.created)}`;
    const head = key !== header;
    header = key;
    return <Fragment key={l.id}>
      {head && <div className="chatlog-head">{l.group || place(l.source)} · {day(l.created)}</div>}
      {l.gap && !head && <div className="chatlog-gap">……</div>}
      <div className={'chatline' + (l.cited ? ' cited' : '')}>
        <small>{clock(l.created)}</small> <span className="speaker">{l.speaker}</span>：{l.text || '［图片］'}
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
  const memory = (m: ReviewItem['memories'][number]) => <li key={m.id}>{m.text}
    {m.verified === 1 ? <small>（已确认）</small> : m.status === 'candidate' ? <small>（待确认）</small> : <small>（{origins[m.origin] ?? m.origin}）</small>}</li>;
  return <div className="proposal">
    <div><Badge tone="info">{reviewKinds[item.kind] ?? '请你确认'}</Badge></div>
    <div>{item.text || item.reason}</div>
    {item.old && <div className="review-note">之前确认过的是：{item.old}</div>}
    {p && <div className="review-note">说的是 {p.name}（{p.bot ? '机器人，' : ''}QQ {p.account}{p.names.length ? `，也叫 ${p.names.join('、')}` : ''}）</div>}
    {!!item.chat.length && <div className="review-block">
      <small>{item.kind === 'suspected_bot' ? 'TA 最近说的话' : '当时的聊天（高亮的是依据）'}</small>
      <ChatLog lines={item.chat} />
    </div>}
    {!item.chat.length && !item.memories.length && item.kind !== 'profile_new' && (item.quote
      ? <div className="review-note">原话摘录：“{item.quote}”（原始聊天已找不到）</div>
      : <div className="review-note">找不到原始聊天。</div>)}
    {!!item.uses.length && <div className="review-block">
      <small>群里其他用到{word}的地方</small>
      <ChatLog lines={item.uses} />
    </div>}
    {item.kind === 'profile_new' && <div className="review-block">
      <small>小结是根据这些记忆写的（{item.memories.length}）</small>
      {item.memories.length ? <ul className="review-list">{shown.map(memory)}</ul> : <div className="review-note">TA 现在没有记忆。</div>}
      {!!rest.length && <details><summary>其余 {rest.length} 条</summary><ul className="review-list">{rest.map(memory)}</ul></details>}
    </div>}
    {!!item.links.length && <div className="review-note">网上来源：{item.links.map(u => <a key={u} href={u} target="_blank" rel="noreferrer">{host(u)}</a>)}</div>}
    <small>原因：{item.reason}</small>
    <div className="row">
      <button className="small" disabled={disabled} onClick={() => answer('correct')}>对</button>
      <button className="small" disabled={disabled} onClick={() => answer('wrong')}>不对</button>
      <button className="small ghost" disabled={disabled} onClick={() => answer('skip')}>跳过</button>
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
      <button className="primary" disabled={busy} onClick={create}><Plus size={15} />新对话</button>
    </PageHead>
    <p className="muted" style={{ marginBottom: 12 }}>告诉石英群友的事、称呼或黑话，它会拆成具体条目，你采纳后才写入；也可以让它把不确定的数据拿来问你。</p>
    <div className="teach">
      <section className="panel" style={{ padding: 0 }}>
        {!sessions.length && <Empty text="还没有对话。" />}
        <div className="list">{sessions.map(s => <button key={s.id} className={'list-item' + (s.id === current ? ' active' : '')} onClick={() => setCurrent(s.id)}>
          <span className="title">{s.title}</span>{!!s.pending && <small>{s.pending} 条待处理</small>}</button>)}</div>
      </section>
      <Panel>
        {current === null ? <Empty text="新建一个对话开始。" /> : <>
          <div className="thread">
            {!thread?.messages.length && <Empty text="例如：小林以前在上海实习过，群里也叫 TA 林哥。" />}
            {thread?.messages.map(m => <div key={m.id} className={'msg ' + m.role}>{m.text}</div>)}
            {sending && <div className="msg assistant">处理中…</div>}
            <div ref={bottom} />
          </div>
          <form className="composer" onSubmit={e => { e.preventDefault(); if(text.trim()) void send(`/teach/sessions/${current}/messages`, { text: text.trim() }); }}>
            <textarea placeholder="说点石英应该知道的事" value={text} maxLength={2000} onChange={e => setText(e.target.value)}
              onKeyDown={e => { if(e.key === 'Enter' && (e.ctrlKey || e.metaKey) && text.trim()) void send(`/teach/sessions/${current}/messages`, { text: text.trim() }); }} />
            <button className="primary" disabled={sending || !text.trim()}>发送</button>
          </form>
          <small>Ctrl + Enter 发送</small>
        </>}
      </Panel>
      <div>
        <Panel title={`待你确认（${reviewCount}）`} actions={
          <button className="small ghost" disabled={busy || sending || reviewLoading || reviewCount === 0} onClick={ask}>让石英换个说法问</button>
        }>
          {reviewLoading ? <Empty text="加载中…" /> : !reviews.length ? <Empty text="没有需要你确认的数据。" /> : reviews.map(item =>
            <ReviewCard key={item.id} item={item} disabled={busy || sending} answer={verdict => answer(item.id, verdict)} />)}
          {reviews.length < reviewCount && <div className="pagination">
            <button className="small" disabled={busy || sending || reviewLoading} onClick={loadMore}>加载更多</button>
          </div>}
        </Panel>
        <Panel title={`待处理（${pending.length}）`}>
          {!pending.length && <Empty text="没有待处理的建议。" />}
          {pending.map(p => <ProposalCard key={p.id} p={p} />)}
          {!!finished.length && <details style={{ marginTop: 12 }}><summary>已处理（{finished.length}）</summary>
            <div style={{ marginTop: 8 }}>{finished.map(p => <ProposalCard key={p.id} p={p} />)}</div></details>}
        </Panel>
      </div>
    </div>
  </>;
}
