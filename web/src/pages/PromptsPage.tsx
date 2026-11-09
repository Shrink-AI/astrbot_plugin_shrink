import { useEffect, useMemo, useState } from 'react';
import { api } from '../api/client';
import type { PromptEntry, PromptTrialRow, PromptVersion } from '../api/types';
import { Badge, Empty, PageHead, Panel } from '../shared/components';
import { useDashboard } from '../shared/dashboard';
import { date, place } from '../shared/format';

const PLACEHOLDER = /\{\{(\w+)\}\}/g;

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
  return <Panel title="试一下" actions={<div className="row">
    <select value={source} onChange={e => setSource(e.target.value)} style={{ width: 'auto' }}>
      {sources.map(s => <option key={s.source} value={s.source}>{place(s.source)}</option>)}
    </select>
    <button className="primary" disabled={running || !source} onClick={() => void run()}>{running ? '生成中…' : '用最近的消息试'}</button>
  </div>}>
    <p className="muted" style={{ marginBottom: 8 }}>
      拿这个群里石英最近回过的几条消息，按当时的聊天记录重新生成回复，把现在的版本和{changed ? '你改的版本' : '现在的版本（还没改）'}放在一起对比。不会发到群里，也不会保存。
    </p>
    {rows && (rows.length ? <div className="table-wrap"><table className="table trial">
      <thead><tr><th>群友说的</th><th>当时石英说的</th><th>现在的版本</th>{changed && <th>改后</th>}</tr></thead>
      <tbody>{rows.map((r, i) => <tr key={i}>
        <td className="text"><small>{r.speaker} · {date(r.time)}</small><div>{r.said}</div></td>
        <td className="text">{r.original.join(' ／ ')}</td>
        <td className="text">{r.saved.error ? <span className="muted">出错：{r.saved.error}</span> : r.saved.text}</td>
        {changed && <td className="text">{r.draft.error ? <span className="muted">出错：{r.draft.error}</span> : r.draft.text}</td>}
      </tr>)}</tbody>
    </table></div> : <Empty text="没有可以试的消息" />)}
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
    <div className="list">{rows.map(r => <div key={r.id} className="list-item">
      <div className="row">
        <span>{date(r.created)}</span>
        {r.reset ? <Badge>恢复默认</Badge> : <Badge tone="info">修改</Badge>}
        {r.note && <small>{r.note}</small>}
        <span className="spacer" />
        <button className="small" onClick={() => pick(r.text)}>放进编辑框</button>
      </div>
      <details><summary className="muted">看内容</summary><pre>{r.text}</pre></details>
    </div>)}</div>
  </Panel>;
}

function Editor({ entry }: { entry: PromptEntry }) {
  const { busy, action } = useDashboard();
  const [draft, setDraft] = useState(entry.text);
  const [note, setNote] = useState('');
  useEffect(() => { setDraft(entry.text); setNote(''); }, [entry.key, entry.text]);
  const problem = problemOf(entry, draft);
  const dirty = draft !== entry.text;
  const showsChat = entry.key.startsWith('chat.');
  return <div className="stack">
    <Panel title={entry.title} actions={<div className="row">
      {entry.modified ? <Badge tone="info">已修改 · {date(entry.updated!)}</Badge> : <Badge>默认</Badge>}
    </div>}>
      <div className="stack">
        {entry.note && <p className="muted">{entry.note}</p>}
        {entry.placeholders.length > 0 && <p className="muted">
          必须保留：{entry.placeholders.map(p => <code key={p} className="placeholder">{`{{${p}}}`}</code>)}，程序会把实际内容填进去。
        </p>}
        <textarea className="prompt-text" rows={Math.min(28, Math.max(4, draft.split('\n').length + 1))}
          value={draft} onChange={e => setDraft(e.target.value)} spellCheck={false} />
        {problem && <div className="banner error" role="alert">{problem}</div>}
        <div className="row">
          <input placeholder="这次改了什么（可不填）" maxLength={200} value={note} onChange={e => setNote(e.target.value)} style={{ flex: 1, minWidth: 180 }} />
          <button className="primary" disabled={busy || !dirty || !!problem}
            onClick={() => void action(() => api(`/prompts/${entry.key}`, 'PUT', { text: draft, note }), '已保存，马上生效')}>保存</button>
          <button disabled={busy || !dirty} onClick={() => setDraft(entry.text)}>撤销修改</button>
          <button disabled={busy || !entry.modified} onClick={() => {
            if(confirm(`把「${entry.title}」恢复成默认内容？`)) void action(() => api(`/prompts/${entry.key}`, 'DELETE'), '已恢复默认');
          }}>恢复默认</button>
        </div>
        {entry.modified && <details><summary className="muted">看默认内容</summary><pre>{entry.default}</pre></details>}
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
  useEffect(() => {
    api<PromptEntry[]>('/prompts').then(setEntries).catch(e => setError(e.message));
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
    <p className="muted" style={{ marginBottom: 12 }}>石英给模型的所有说明都在这里。保存后马上生效，改坏了可以恢复默认或从修改记录里找回。</p>
    {!entries.length ? <Empty text="加载中…" /> : <div className="split">
      <Panel>
        <input type="search" placeholder="搜标题或内容" value={query} onChange={e => setQuery(e.target.value)} style={{ marginBottom: 8 }} />
        <div className="list prompt-list">{groups.map(([group, items]) => <div key={group}>
          <div className="list-group">{group}</div>
          {items.map(e => <button key={e.key} className={'list-item' + (current?.key === e.key ? ' active' : '')} onClick={() => setSelected(e.key)}>
            <span className="title">{e.title}{e.modified && <Badge tone="info">已改</Badge>}</span>
          </button>)}
        </div>)}</div>
      </Panel>
      {current && <Editor entry={current} />}
    </div>}
  </>;
}
