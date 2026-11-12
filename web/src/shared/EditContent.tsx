import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Field, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { api } from '@/api/client';
import { useConfirm } from '@/components/confirm-dialog';
import type { Editing } from '@/api/types';
import { Modal } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';

export function EditContent({ editing, setEditing }: { editing: Editing; setEditing: (value: Editing | null) => void }) {
  const { action, busy } = useDashboard();
  const ask = useConfirm();
  if(editing.kind === 'memory') {
    const row = editing.row;
    return <Modal title="纠正记忆" close={() => setEditing(null)}>
      <Field>
        <FieldLabel htmlFor="memory-text">记忆内容</FieldLabel>
        <Textarea id="memory-text" rows={5} aria-label="记忆内容" value={row.text}
          onChange={e => setEditing({ ...editing, row: { ...row, text: e.target.value } })} />
      </Field>
      <p className="text-sm text-muted-foreground">保存后这条记忆标为“已确认”，AI 之后不会改动它。</p>
      <div className="flex items-center justify-end gap-2">
        <Button variant="outline" onClick={() => setEditing(null)}>取消</Button>
        <Button disabled={busy || !row.text.trim()} onClick={() => void action(async () => {
          await api(`/memories/${row.id}`, 'PUT', { text: row.text }); setEditing(null);
        })}>保存</Button>
      </div>
    </Modal>;
  }
  const row = editing.row;
  return <Modal title="编辑表情" close={() => setEditing(null)}>
    <img className="max-h-60 max-w-full self-center rounded-lg object-contain" src={`/api/v1/memes/${row.id}/image`} alt={row.title} />
    <Field>
      <FieldLabel htmlFor="meme-title">描述</FieldLabel>
      <Input id="meme-title" value={row.title} onChange={e => setEditing({ ...editing, row: { ...row, title: e.target.value } })} />
    </Field>
    <Field>
      <FieldLabel htmlFor="meme-tags">标签（逗号分隔）</FieldLabel>
      <Input id="meme-tags" value={row.tags} onChange={e => setEditing({ ...editing, row: { ...row, tags: e.target.value } })} />
    </Field>
    <Field orientation="horizontal">
      <Checkbox id="meme-enabled" checked={!!row.enabled}
        onCheckedChange={value => setEditing({ ...editing, row: { ...row, enabled: value === true } })} />
      <FieldLabel htmlFor="meme-enabled">允许石英发送</FieldLabel>
    </Field>
    <p className="text-sm text-muted-foreground">来源：{row.source}</p>
    <div className="flex items-center gap-2">
      <Button variant="destructive" disabled={busy} onClick={async () => {
        if(!await ask({ title: '删除这张表情？', desc: '永久删除这张表情？', destructive: true })) return;
        void action(async () => { await api(`/memes/${row.id}`, 'DELETE'); setEditing(null); }, '已删除');
      }}>删除</Button>
      <span className="flex-1" />
      <Button variant="outline" onClick={() => setEditing(null)}>取消</Button>
      <Button disabled={busy} onClick={() => void action(async () => {
        await api(`/memes/${row.id}`, 'PUT', { title: row.title, tags: row.tags, enabled: !!row.enabled }); setEditing(null);
      })}>保存</Button>
    </div>
  </Modal>;
}