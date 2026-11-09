import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { PageHead, Empty } from '../shared/components';
import { useDashboard } from '../shared/dashboard';
import { date } from '../shared/format';

type Message = { id: string; source: string; speaker_name: string; user_id: string;
  text: string; created: number; is_bot: boolean };
type Attachment = { id: string; message_id: string; name: string; status: string;
  error: string; bytes: number; created: number };
type Source = { source: string; name: string; count: number };

export function ChatsPage() {
  const { revision, setError } = useDashboard();
  const [sources, setSources] = useState<Source[]>([]);
  const [source, setSource] = useState('');
  const [keyword, setKeyword] = useState('');
  const [offset, setOffset] = useState(0);
  const [messages, setMessages] = useState<Message[]>([]);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  useEffect(() => {
    api<Source[]>('/operations/chat-sources').then(setSources).catch(e => setError(e.message));
  }, [revision]);
  useEffect(() => {
    let stopped = false;
    Promise.all([
      api<Message[]>(`/operations/chats?source=${encodeURIComponent(source)}&keyword=${encodeURIComponent(keyword)}&offset=${offset}`),
      source ? api<Attachment[]>(`/operations/attachments?source=${encodeURIComponent(source)}`) : Promise.resolve([]),
    ]).then(([rows, files]) => { if (!stopped) { setMessages(rows); setAttachments(files); } })
      .catch(e => { if (!stopped) setError(e.message); });
    return () => { stopped = true; };
  }, [source, keyword, offset, revision]);
  return <>
    <PageHead title="聊天与图片" />
    <div className="toolbar">
      <select value={source} onChange={e => { setSource(e.target.value); setOffset(0); }}>
        <option value="">全部会话</option>
        {sources.map(s => <option key={s.source} value={s.source}>{s.name || s.source} · {s.count} 条</option>)}
      </select>
      <input value={keyword} onChange={e => { setKeyword(e.target.value); setOffset(0); }} placeholder="搜索原话或文件名" />
    </div>
    <p>原始聊天独立保存。新收到的图片和表情包自动留存副本，单张最多 64 MiB；普通文件、语音和视频不自动留档。</p>
    <section className="panel">
      {!messages.length && <Empty text="没有匹配的聊天记录。" />}
      {messages.map(m => <article key={m.id} style={{ borderBottom: '1px solid var(--border)', padding: '12px 0' }}>
        <strong>{m.is_bot ? '石英' : m.speaker_name || m.user_id}</strong> · {date(m.created)}
        {!source && <small> · {m.source}</small>}
        <p style={{ whiteSpace: 'pre-wrap' }}>{m.text || '（附件消息）'}</p>
        {attachments.filter(a => a.message_id === m.id).map(a => <p key={a.id}>
          {a.status === 'saved'
            ? <a href={`/api/v1/operations/attachments/${a.id}/file`} download>{a.name} · {Math.ceil(a.bytes / 1024)} KiB</a>
            : <span>{a.name} · {a.status === 'failed' ? `保存失败：${a.error}` : '保存中'}</span>}
        </p>)}
      </article>)}
      <div className="toolbar">
        <button disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 100))}>较新记录</button>
        <button disabled={messages.length < 100} onClick={() => setOffset(offset + 100)}>更早记录</button>
      </div>
    </section>
  </>;
}
