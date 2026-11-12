import { useEffect, useMemo, useState } from 'react';
import { ChevronRightIcon } from 'lucide-react';
import { cn } from 'cn';
import { Alert, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { api } from '@/api/client';
import { useConfirm } from '@/components/confirm-dialog';
import type { PromptEntry, PromptTrialRow, PromptVersion } from '@/api/types';
import { Badge, Empty, PageHead, Panel } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';
import { date, place } from '@/shared/format';

const PLACEHOLDER = /\{\{(\w+)\}\}/g;
const preClass = 'max-h-80 overflow-auto rounded-lg border bg-muted/40 p-2.5 font-mono text-xs whitespace-pre-wrap break-words';
const textCell = 'min-w-[260px] whitespace-normal';

function placeholdersOf(text: string) {
  return [...new Set([...text.matchAll(PLACEHOLDER)].map(m => m[1]))];
}

function problemOf(entry: PromptEntry, text: string) {
  if(!text.trim()) return '内容不能为空';
  const found = placeholdersOf(text);
  const missing = entry.placeholders.filter(p => !found.includes(p));
  const extra = found.filter(p => !entry.placeholders.includes(p));
  return [
    missing.length ? '缺少 ' + missing.map(p => `{{${p}}}`).join('、') : '',
    extra.length ? '多了程序不认识的 ' + extra.map(p => `{{${p}}}`).join('、') : '',
  ].filter(Boolean).join('；');
}

type Source = { source: string; name: string; count: number; latest: number };

function Trial({ entry, draft }: { entry: PromptEntry; draft: string }) {
  const { setError } = useDashboard();
  const [sources, setSources] = useState<Source[]>([]);
  const [source, setSource] = useState('');
  const [rows, setRows] = useState<PromptTrialRow[] | null>(null);
  const [running, setRunning] = useState(false);
  useEffect(() => {
    api<Source[]>('/prompts/sources').then(list => {
      const groups = list;
      setSources(groups);
      setSource(s => s || groups[0]?.source || '');
    }).catch(() => setSources([]));
  }, []);
  const changed = draft !== entry.text;
  const run = async () => {
    setRunning(true); setRows(null);
    try {
      setRows(await api<PromptTrialRow[]>('/prompts/trial', 'POST', { source, drafts: changed ? { [entry.key]: draft } : {} }));
    } catch(e) { setError((e as Error).message); }
    finally { setRunning(false); }
  };
  return <Panel title="试一下" actions={<div className="flex flex-wrap items-center gap-2">
    <Select value={source || undefined} onValueChange={setSource}>
      <SelectTrigger className="w-[180px]" aria-label="会话">
        <SelectValue placeholder="选择会话" />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          {sources.map(s => <SelectItem key={s.source} value={s.source}>{place(s.source)}</SelectItem>)}
        </SelectGroup>
      </SelectContent>
    </Select>
    <Button disabled={running || !source} onClick={() => void run()}>{running ? '生成中…' : '用最近的消息试'}</Button>
  </div>}>
    <p className="mb-2 text-sm text-muted-foreground">
      拿这个群里石英最近回过的几条消息，按当时的聊天记录重新生成回复，把现在的版本和{changed ? '你改的版本' : '现在的版本（还没改）'}放在一起对比。不会发到群里，也不会保存。
    </p>
    {rows && (rows.length ? <Table>
      <TableHeader>
        <TableRow>
          <TableHead>群友说的</TableHead><TableHead>当时石英说的</TableHead><TableHead>现在的版本</TableHead>
          {changed && <TableHead>改后</TableHead>}
        </TableRow>
      </TableHeader>
      <TableBody>{rows.map((r, i) => <TableRow key={i}>
        <TableCell className={textCell}>
          <small className="text-xs text-muted-foreground">{r.speaker} · {date(r.time)}</small>
          <div>{r.said}</div>
        </TableCell>
        <TableCell className={textCell}>{r.original.join(' ／ ')}</TableCell>
        <TableCell className={textCell}>{r.saved.error ? <span className="text-muted-foreground">出错：{r.saved.error}</span> : r.saved.text}</TableCell>
        {changed && <TableCell className={textCell}>{r.draft.error ? <span className="text-muted-foreground">出错：{r.draft.error}</span> : r.draft.text}</TableCell>}
      </TableRow>)}</TableBody>
    </Table> : <Empty text="没有可以试的消息" />)}
  </Panel>;
}

function History({ entry, pick }: { entry: PromptEntry; pick: (text: string) => void }) {
  const { revision } = useDashboard();
  const [rows, setRows] = useState<PromptVersion[]>([]);
  useEffect(() => {
    api<PromptVersion[]>(`/prompts/${entry.key}/history`).then(setRows).catch(() => setRows([]));
  }, [entry.key, revision]);
  if(!rows.length) return null;
  return <Panel title="修改记录">
    <div className="flex flex-col gap-2.5">{rows.map(r => <div key={r.id} className="list-item flex flex-col gap-1.5 rounded-lg border p-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span>{date(r.created)}</span>
        {r.reset ? <Badge>恢复默认</Badge> : <Badge tone="info">修改</Badge>}
        {r.note && <small className="text-xs text-muted-foreground">{r.note}</small>}
        <span className="flex-1" />
        <Button variant="outline" size="sm" onClick={() => pick(r.text)}>放进编辑框</Button>
      </div>
      <Collapsible className="group/collapsible">
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
            <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
            看内容
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="pt-2">
          <pre className={cn(preClass, 'mt-1.5')}>{r.text}</pre>
        </CollapsibleContent>
      </Collapsible>
    </div>)}</div>
  </Panel>;
}

function Editor({ entry }: { entry: PromptEntry }) {
  const { busy, action } = useDashboard();
  const ask = useConfirm();
  const [draft, setDraft] = useState(entry.text);
  const [note, setNote] = useState('');
  useEffect(() => { setDraft(entry.text); setNote(''); }, [entry.key, entry.text]);
  const problem = problemOf(entry, draft);
  const dirty = draft !== entry.text;
  const showsChat = entry.key.startsWith('chat.');
  return <div className="flex flex-col gap-2.5">
    <Panel title={entry.title} actions={<div className="flex flex-wrap items-center gap-2">
      {entry.modified ? <Badge tone="info">已修改 · {date(entry.updated!)}</Badge> : <Badge>默认</Badge>}
    </div>}>
      <div className="flex flex-col gap-2.5">
        {entry.note && <p className="text-sm text-muted-foreground">{entry.note}</p>}
        {entry.placeholders.length > 0 && <p className="text-sm text-muted-foreground">
          必须保留：{entry.placeholders.map(p => <code key={p} className="rounded bg-muted px-1 font-mono text-xs">{`{{${p}}}`}</code>)}，程序会把实际内容填进去。
        </p>}
        <Textarea className="min-h-[300px] font-mono text-[13px] leading-relaxed" rows={Math.min(28, Math.max(4, draft.split('\n').length + 1))}
          value={draft} onChange={e => setDraft(e.target.value)} spellCheck={false} />
        {problem && <Alert variant="destructive"><AlertTitle>{problem}</AlertTitle></Alert>}
        <div className="flex flex-wrap items-center gap-2">
          <Input placeholder="这次改了什么（可不填）" maxLength={200} value={note} onChange={e => setNote(e.target.value)}
            className="min-w-[180px] flex-1" />
          <Button disabled={busy || !dirty || !!problem}
            onClick={() => void action(() => api(`/prompts/${entry.key}`, 'PUT', { text: draft, note }), '已保存，马上生效')}>保存</Button>
          <Button variant="outline" disabled={busy || !dirty} onClick={() => setDraft(entry.text)}>撤销修改</Button>
          <Button variant="outline" disabled={busy || !entry.modified} onClick={async () => {
            if(!await ask({ title: '恢复默认内容？', desc: `把「${entry.title}」恢复成默认内容？` })) return;
            void action(() => api(`/prompts/${entry.key}`, 'DELETE'), '已恢复默认');
          }}>恢复默认</Button>
        </div>
        {entry.modified && <Collapsible className="group/collapsible">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
              <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
              看默认内容
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-2">
            <pre className={cn(preClass, 'mt-1.5')}>{entry.default}</pre>
          </CollapsibleContent>
        </Collapsible>}
      </div>
    </Panel>
    {showsChat && <Trial entry={entry} draft={draft} />}
    <History entry={entry} pick={setDraft} />
  </div>;
}

export function PromptsPage() {
  const { revision, setError } = useDashboard();
  const [entries, setEntries] = useState<PromptEntry[]>([]);
  const [selected, setSelected] = useState('chat.persona');
  const [query, setQuery] = useState('');
  const [archived, setArchived] = useState<{ key: string; history: PromptVersion[] }[]>([]);
  useEffect(() => {
    api<PromptEntry[]>('/prompts').then(setEntries).catch(e => setError(e.message));
    api<{ key: string; history: PromptVersion[] }[]>('/prompts-archive')
      .then(setArchived).catch(e => setError(e.message));
  }, [revision]);
  const groups = useMemo(() => {
    const q = query.trim();
    const shown = entries.filter(e => !q || e.title.includes(q) || e.text.includes(q) || e.key.includes(q));
    const map = new Map<string, PromptEntry[]>();
    for(const e of shown) map.set(e.group, [...(map.get(e.group) ?? []), e]);
    return [...map.entries()];
  }, [entries, query]);
  const current = entries.find(e => e.key === selected) ?? entries[0];
  const modified = entries.filter(e => e.modified).length;
  return <>
    <PageHead title="提示词">{modified > 0 && <Badge tone="info">改过 {modified} 条</Badge>}</PageHead>
    <p className="mb-3 text-sm text-muted-foreground">石英给模型的所有说明都在这里。保存后马上生效，改坏了可以恢复默认或从修改记录里找回。</p>
    {!entries.length ? <Empty text="加载中…" /> : <div className="grid grid-cols-1 gap-3 md:grid-cols-[280px_minmax(0,1fr)]">
      <Panel>
        <div className="flex flex-col gap-0.5">
          <Input type="search" placeholder="搜标题或内容" className="mb-2" value={query} onChange={e => setQuery(e.target.value)} />
          {groups.map(([group, items]) => <div key={group}>
            <div className="px-2.5 pt-2 text-xs text-muted-foreground">{group}</div>
            {items.map(e => {
              const active = current?.key === e.key;
              return <Button key={e.key} variant={active ? 'secondary' : 'ghost'} size="sm"
                className={cn('list-item h-auto w-full justify-start px-2.5 py-1.5 text-left whitespace-normal font-normal', active && 'font-medium')}
                onClick={() => setSelected(e.key)}>
                <span className="flex flex-wrap items-center gap-1.5">{e.title}{e.modified && <Badge tone="info">已改</Badge>}</span>
              </Button>;
            })}
          </div>)}
        </div>
      </Panel>
      {current && <Editor entry={current} />}
    </div>}
    {archived.length > 0 && <Panel title="已停用提示词的修改记录" className="mt-3">
      <p className="text-sm text-muted-foreground">这些提示词已合并或停用，不再参与生成。此前保存的内容仍可查看和复制。</p>
      <div className="mt-2 flex flex-col gap-2">
        {archived.map(entry => <Collapsible key={entry.key} className="group/collapsible">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
              <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
              {entry.key} · {entry.history.length} 个版本
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-2">
            <div className="mt-1.5 flex flex-col gap-2">
              {entry.history.map(row => <div className="list-item flex flex-col gap-1 rounded-lg border p-2.5" key={row.id}>
                <small className="text-xs text-muted-foreground">{date(row.created)} {row.note}</small>
                <pre className={preClass}>{row.text ?? '当时恢复为默认内容'}</pre>
              </div>)}
            </div>
          </CollapsibleContent>
        </Collapsible>)}
      </div>
    </Panel>}
  </>;
}