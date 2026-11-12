import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { api } from '@/api/client';
import { Empty, PageHead, Panel } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';
import { date } from '@/shared/format';

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
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <Select value={source || 'all'} onValueChange={next => { setSource(next === 'all' ? '' : next); setOffset(0); }}>
        <SelectTrigger className="w-full sm:w-[280px]" aria-label="会话">
          <SelectValue placeholder="全部会话" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            <SelectItem value="all">全部会话</SelectItem>
            {sources.map(s => <SelectItem key={s.source} value={s.source}>{s.name || s.source} · {s.count} 条</SelectItem>)}
          </SelectGroup>
        </SelectContent>
      </Select>
      <Input className="w-full sm:w-[280px]" value={keyword} onChange={e => { setKeyword(e.target.value); setOffset(0); }} placeholder="搜索原话或文件名" />
    </div>
    <p className="mb-3 text-sm text-muted-foreground">原始聊天独立保存。新收到的图片和表情包自动留存副本，单张最多 64 MiB；普通文件、语音和视频不自动留档。</p>
    <Panel>
      {!messages.length && <Empty text="没有匹配的聊天记录。" />}
      {messages.map(m => <article key={m.id} className="border-b py-3 last:border-0">
        <div className="text-sm">
          <strong className="font-semibold">{m.is_bot ? '石英' : m.speaker_name || m.user_id}</strong> · {date(m.created)}
          {!source && <span className="text-xs text-muted-foreground"> · {m.source}</span>}
        </div>
        <p className="mt-1 whitespace-pre-wrap break-words">{m.text || '（附件消息）'}</p>
        {attachments.filter(a => a.message_id === m.id).map(a => <p key={a.id} className="mt-1 text-sm">
          {a.status === 'saved'
            ? <a className="text-info underline underline-offset-4" href={`/api/v1/operations/attachments/${a.id}/file`} download>{a.name} · {Math.ceil(a.bytes / 1024)} KiB</a>
            : <span className="text-muted-foreground">{a.name} · {a.status === 'failed' ? `保存失败：${a.error}` : '保存中'}</span>}
        </p>)}
      </article>)}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button variant="outline" size="sm" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 100))}>较新记录</Button>
        <Button variant="outline" size="sm" disabled={messages.length < 100} onClick={() => setOffset(offset + 100)}>更早记录</Button>
      </div>
    </Panel>
  </>;
}