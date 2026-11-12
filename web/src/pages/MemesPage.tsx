import { Upload } from 'lucide-react';
import { useEffect, useState } from 'react';
import { cn } from 'cn';
import { BulkActions } from '@/components/data-table/bulk-actions';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Field, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { api } from '@/api/client';
import { useConfirm } from '@/components/confirm-dialog';
import type { Editing, Meme } from '@/api/types';
import { Empty, PageHead, Pagination, Panel } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';
import { EditContent } from '@/shared/EditContent';

const filters = [['', '全部'], ['1', '可用'], ['0', '已禁用']] as const;

export function MemesPage() {
  const { settings, busy, action, revision, setError } = useDashboard();
  const ask = useConfirm();
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
  const batch = async (verb: 'enable' | 'disable' | 'delete', label: string) => {
    if(verb === 'delete' && !await ask({ title: '批量删除表情？', desc: `永久删除选中的 ${selected.length} 张表情？`, destructive: true })) return;
    void action(() => api('/memes/batch', 'POST', { ids: selected, action: verb }), label);
  };
  return <>
    <PageHead title="表情">
      <Field orientation="horizontal" className="w-auto">
        <Checkbox id="collect-enabled" checked={!!settings.collect_enabled}
          onCheckedChange={value => void action(() => api('/settings', 'PUT', { ...settings, collect_enabled: value === true }), value === true ? '已开启自动收集' : '已暂停自动收集')} />
        <FieldLabel htmlFor="collect-enabled">自动收集</FieldLabel>
      </Field>
      <Field orientation="horizontal" className="w-auto">
        <Checkbox id="meme-enabled" checked={!!settings.meme_enabled}
          onCheckedChange={value => void action(() => api('/settings', 'PUT', { ...settings, meme_enabled: value === true }), value === true ? '已允许发表情' : '已停止发表情')} />
        <FieldLabel htmlFor="meme-enabled">允许发送</FieldLabel>
      </Field>
    </PageHead>
    <Panel className="mb-3">
      <form className="flex flex-col gap-4" onSubmit={e => {
        e.preventDefault(); void action(async () => {
          for(const file of files) { const form = new FormData(); form.set('file', file); form.set('title', title || file.name); form.set('tags', tags); await api('/memes', 'POST', form); }
          setFiles([]); setTitle(''); setTags('');
        }, '已上传，重复的图片会自动合并');
      }}>
        <Field>
          <FieldLabel htmlFor="meme-files">上传表情（PNG / JPEG / WebP / GIF，每张最多 10 MB）</FieldLabel>
          <Input id="meme-files" type="file" accept="image/png,image/jpeg,image/webp,image/gif" multiple
            onChange={e => setFiles(Array.from(e.target.files || []))} />
        </Field>
        <FieldGroup className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <Field>
            <FieldLabel htmlFor="meme-upload-title">描述</FieldLabel>
            <Input id="meme-upload-title" value={title} onChange={e => setTitle(e.target.value)} />
          </Field>
          <Field>
            <FieldLabel htmlFor="meme-upload-tags">标签</FieldLabel>
            <Input id="meme-upload-tags" placeholder="开心, 疑惑" value={tags} onChange={e => setTags(e.target.value)} />
          </Field>
        </FieldGroup>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" disabled={busy || !files.length}><Upload data-icon="inline-start" />上传{files.length ? ` ${files.length} 张` : ''}</Button>
        </div>
      </form>
    </Panel>
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <Input type="search" placeholder="搜索描述或标签" className="w-56" value={q} onChange={e => { setQ(e.target.value); setOffset(0); }} />
      <ToggleGroup type="single" variant="outline" size="sm" value={enabled || 'all'}
        onValueChange={value => { if(!value) return; setEnabled(value === 'all' ? '' : value); setOffset(0); }}>
        {filters.map(([value, label]) => <ToggleGroupItem key={value || 'all'} value={value || 'all'}>{label}</ToggleGroupItem>)}
      </ToggleGroup>
      <span className="flex-1" />
      <Button variant="outline" size="sm" onClick={() => setSelected(selected.length === memes.length ? [] : memes.map(m => m.id))}>
        {selected.length === memes.length && memes.length ? '取消全选' : '全选本页'}</Button>
    </div>
    <BulkActions count={selected.length} label="张表情" onClear={() => setSelected([])}>
      <Button variant="outline" size="sm" disabled={busy} onClick={() => void batch('enable', '已启用')}>启用</Button>
      <Button variant="outline" size="sm" disabled={busy} onClick={() => void batch('disable', '已禁用')}>禁用</Button>
      <Button variant="destructive" size="sm" disabled={busy} onClick={() => void batch('delete', '已删除')}>删除</Button>
    </BulkActions>
    {!memes.length && <Panel><Empty text="没有表情。" /></Panel>}
    <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-[repeat(auto-fill,minmax(150px,1fr))]">{memes.map(m => {
      const isSelected = selected.includes(m.id);
      return <Card key={m.id} className={cn('meme gap-0 overflow-hidden rounded-lg py-0 shadow-none',
        !m.enabled && 'off opacity-60', isSelected && 'selected ring-2 ring-ring/40')}>
        <Button type="button" variant="ghost" className="relative h-[140px] w-full rounded-none bg-muted p-0 hover:bg-muted" title="编辑" onClick={() => setEditing({ kind: 'meme', row: { ...m } })}>
          <img className="size-full object-contain" src={`/api/v1/memes/${m.id}/image`} alt={m.title} loading="lazy" />
          <Checkbox className="absolute top-1.5 left-1.5" aria-label="选择" checked={isSelected}
            onClick={e => e.stopPropagation()} onCheckedChange={() => toggle(m.id)} />
        </Button>
        <div className="flex flex-col gap-1 p-2">
          <b className="truncate">{m.title}</b>
          <small className="text-xs text-muted-foreground">{m.tags || '无标签'}</small>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <small className="text-xs text-muted-foreground">{m.enabled ? '可用' : '已禁用'}</small>
            <Button variant="outline" size="xs" disabled={busy}
              onClick={() => void action(() => api(`/memes/${m.id}/enabled`, 'PUT', { enabled: !m.enabled }), m.enabled ? '已禁用' : '已启用')}>
              {m.enabled ? '禁用' : '启用'}</Button>
          </div>
        </div>
    </Card>;
    })}</div>
    <Pagination offset={offset} count={memes.length} size={60} change={setOffset} />
    {editing && <EditContent editing={editing} setEditing={setEditing} />}
  </>;
}