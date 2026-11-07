import { Upload } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Editing, Meme } from '../api/types';
import { Empty, PageHead, Pagination } from '../shared/components';
import { useDashboard } from '../shared/dashboard';
import { EditContent } from '../shared/EditContent';

const filters = [['', '全部'], ['1', '可用'], ['0', '已禁用']] as const;

export function MemesPage() {
  const { settings, busy, action, revision, setError } = useDashboard();
  const [memes, setMemes] = useState<Meme[]>([]);
  const [q, setQ] = useState('');
  const [enabled, setEnabled] = useState('');
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<number[]>([]);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [title, setTitle] = useState('');
  const [tags, setTags] = useState('');
  useEffect(() => {
    let cancelled = false;
    const timer = setTimeout(() => {
      api<Meme[]>(`/memes?q=${encodeURIComponent(q)}&offset=${offset}${enabled ? `&enabled=${enabled}` : ''}`)
        .then(rows => { if(!cancelled) { setMemes(rows); setSelected([]); } })
        .catch(e => { if(!cancelled) setError(e.message); });
    }, q ? 250 : 0);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [revision, q, enabled, offset]);
  const toggle = (id: number) => setSelected(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id]);
  const batch = (verb: 'enable' | 'disable' | 'delete', label: string) => {
    if(verb === 'delete' && !confirm(`永久删除选中的 ${selected.length} 张表情？`)) return;
    void action(() => api('/memes/batch', 'POST', { ids: selected, action: verb }), label);
  };
  return <>
    <PageHead title="表情">
      <label className="check"><input type="checkbox" checked={!!settings.collect_enabled}
        onChange={e => void action(() => api('/settings', 'PUT', { ...settings, collect_enabled: e.target.checked }), e.target.checked ? '已开启自动收集' : '已暂停自动收集')} />自动收集</label>
      <label className="check"><input type="checkbox" checked={!!settings.meme_enabled}
        onChange={e => void action(() => api('/settings', 'PUT', { ...settings, meme_enabled: e.target.checked }), e.target.checked ? '已允许发表情' : '已停止发表情')} />允许发送</label>
    </PageHead>
    <form className="panel upload" style={{ marginBottom: 12 }} onSubmit={e => {
      e.preventDefault(); void action(async () => {
        for(const file of files) { const form = new FormData(); form.set('file', file); form.set('title', title || file.name); form.set('tags', tags); await api('/memes', 'POST', form); }
        setFiles([]); setTitle(''); setTags('');
      }, '已上传，重复的图片会自动合并');
    }}>
      <label className="field"><span>上传表情（PNG / JPEG / WebP / GIF，每张最多 10 MB）</span>
        <input type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple onChange={e => setFiles(Array.from(e.target.files || []))} /></label>
      <label className="field"><span>描述</span><input value={title} onChange={e => setTitle(e.target.value)} /></label>
      <label className="field"><span>标签</span><input placeholder="开心, 疑惑" value={tags} onChange={e => setTags(e.target.value)} /></label>
      <button className="primary" disabled={busy || !files.length}><Upload size={15} />上传{files.length ? ` ${files.length} 张` : ''}</button>
    </form>
    <div className="toolbar">
      <input type="search" placeholder="搜索描述或标签" value={q} onChange={e => { setQ(e.target.value); setOffset(0); }} />
      <div className="segmented">{filters.map(([value, label]) =>
        <button key={value} className={enabled === value ? 'on' : ''} onClick={() => { setEnabled(value); setOffset(0); }}>{label}</button>)}</div>
      <span className="spacer" />
      <button className="small" onClick={() => setSelected(selected.length === memes.length ? [] : memes.map(m => m.id))}>
        {selected.length === memes.length && memes.length ? '取消全选' : '全选本页'}</button>
    </div>
    {!!selected.length && <div className="selection-bar">已选 {selected.length} 张
      <button className="small" disabled={busy} onClick={() => batch('enable', '已启用')}>启用</button>
      <button className="small" disabled={busy} onClick={() => batch('disable', '已禁用')}>禁用</button>
      <button className="small danger" disabled={busy} onClick={() => batch('delete', '已删除')}>删除</button>
      <span className="spacer" /><button className="small ghost" onClick={() => setSelected([])}>取消选择</button></div>}
    {!memes.length && <section className="panel"><Empty text="没有表情。" /></section>}
    <div className="meme-grid">{memes.map(m => <article key={m.id} className={'meme' + (m.enabled ? '' : ' off') + (selected.includes(m.id) ? ' selected' : '')}>
      <button className="thumb" title="编辑" onClick={() => setEditing({ kind: 'meme', row: { ...m } })}>
        <img src={`/api/v1/memes/${m.id}/image`} alt={m.title} loading="lazy" />
        <input className="pick" type="checkbox" aria-label="选择" checked={selected.includes(m.id)}
          onClick={e => e.stopPropagation()} onChange={() => toggle(m.id)} />
      </button>
      <div className="info"><b>{m.title}</b><small>{m.tags || '无标签'}</small>
        <div className="row"><small>{m.enabled ? '可用' : '已禁用'}</small>
          <button className="small" disabled={busy} onClick={() => void action(() => api(`/memes/${m.id}/enabled`, 'PUT', { enabled: !m.enabled }), m.enabled ? '已禁用' : '已启用')}>
            {m.enabled ? '禁用' : '启用'}</button></div>
      </div>
    </article>)}</div>
    <Pagination offset={offset} count={memes.length} size={60} change={setOffset} />
    {editing && <EditContent editing={editing} setEditing={setEditing} />}
  </>;
}
