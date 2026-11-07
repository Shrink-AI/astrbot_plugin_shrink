import { api } from '../api/client';
import type { Editing } from '../api/types';
import { Modal } from './components';
import { useDashboard } from './dashboard';

export function EditContent({ editing, setEditing }: { editing: Editing; setEditing: (value: Editing | null) => void }) {
  const { action, busy } = useDashboard();
  if(editing.kind === 'memory') {
    const row = editing.row;
    return <Modal title="纠正记忆" close={() => setEditing(null)}>
      <textarea rows={5} aria-label="记忆内容" value={row.text} onChange={e => setEditing({ ...editing, row: { ...row, text: e.target.value } })} />
      <small>保存后这条记忆标为“已确认”，AI 之后不会改动它。</small>
      <div className="row"><span className="spacer" />
        <button onClick={() => setEditing(null)}>取消</button>
        <button className="primary" disabled={busy || !row.text.trim()} onClick={() => void action(async () => {
          await api(`/memories/${row.id}`, 'PUT', { text: row.text }); setEditing(null);
        })}>保存</button></div>
    </Modal>;
  }
  const row = editing.row;
  return <Modal title="编辑表情" close={() => setEditing(null)}>
    <img className="preview" src={`/api/v1/memes/${row.id}/image`} alt={row.title} />
    <label className="field"><span>描述</span><input value={row.title} onChange={e => setEditing({ ...editing, row: { ...row, title: e.target.value } })} /></label>
    <label className="field"><span>标签（逗号分隔）</span><input value={row.tags} onChange={e => setEditing({ ...editing, row: { ...row, tags: e.target.value } })} /></label>
    <label className="check"><input type="checkbox" checked={!!row.enabled} onChange={e => setEditing({ ...editing, row: { ...row, enabled: e.target.checked } })} />允许石英发送</label>
    <small>来源：{row.source}</small>
    <div className="row">
      <button className="danger" disabled={busy} onClick={() => { if(confirm('永久删除这张表情？')) void action(async () => { await api(`/memes/${row.id}`, 'DELETE'); setEditing(null); }, '已删除'); }}>删除</button>
      <span className="spacer" />
      <button onClick={() => setEditing(null)}>取消</button>
      <button className="primary" disabled={busy} onClick={() => void action(async () => {
        await api(`/memes/${row.id}`, 'PUT', { title: row.title, tags: row.tags, enabled: !!row.enabled }); setEditing(null);
      })}>保存</button></div>
  </Modal>;
}
