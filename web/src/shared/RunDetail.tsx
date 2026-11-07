import { api } from '../api/client';
import type { Run } from '../api/types';
import { Badge, Modal } from './components';
import { useDashboard } from './dashboard';
import { date, place, reasonLabel, runKinds, runLabels } from './format';

const outcomeLabels: Record<string, string> = {
  created: '新增', merged: '合并到已有', superseded: '取代旧记忆', conflict: '与已确认记忆矛盾', unchanged: '已确认，未改动',
  nickname_created: '新称呼', nickname_merged: '称呼补充依据', nickname_conflict: '称呼冲突', nickname_exists: '称呼已存在',
  nickname_rejected: '已被否定的称呼', glossary_created: '新黑话', glossary_exists: '黑话已存在',
  glossary_rejected: '黑话释义不合格', glossary_failed: '黑话查询失败',
};

function Counts({ title, data, label }: { title: string; data?: Record<string, number>; label: (k: string) => string }) {
  const entries = Object.entries(data ?? {});
  if(!entries.length) return null;
  return <div><h3>{title}</h3><table className="table"><tbody>
    {entries.sort((a, b) => b[1] - a[1]).map(([k, v]) => <tr key={k}><td>{label(k)}</td><td className="num">{v}</td></tr>)}
  </tbody></table></div>;
}

export function RunDetail({ run, close }: { run: Run; close: () => void }) {
  const { action, busy } = useDashboard();
  const stats = run.stats ?? {};
  const changes = (run.changes ?? []).reduce((n, c) => n + c.n, 0);
  return <Modal title={`${runKinds[run.kind] ?? run.kind} #${run.id}`} close={close}>
    <dl className="facts">
      <dt>来源</dt><dd>{place(run.source)}</dd>
      <dt>状态</dt><dd><Badge>{run.undone ? '已撤销' : runLabels[run.status] ?? run.status}</Badge></dd>
      <dt>时间</dt><dd>{date(run.started)}{run.finished ? ` → ${date(run.finished)}` : ''}</dd>
      {stats.messages !== undefined && <><dt>消息数</dt><dd>{stats.messages}</dd></>}
      <dt>说明</dt><dd>{run.detail || '—'}</dd>
      <dt>写入的改动</dt><dd>{changes} 处</dd>
    </dl>
    <Counts title="写入结果" data={stats.outcomes} label={k => outcomeLabels[k] ?? k} />
    <Counts title="被拦下的条目" data={stats.rejected} label={reasonLabel} />
    <details><summary>原始统计</summary><pre>{JSON.stringify(stats, null, 2)}</pre></details>
    {!run.undone && changes > 0 && run.status !== 'running' && <div className="row">
      <span className="spacer" />
      <button className="danger" disabled={busy} onClick={() => {
        if(confirm('撤销这次记录写入的全部改动？被它取代的旧记忆会恢复。')) void action(async () => { await api(`/runs/${run.id}/undo`, 'POST'); close(); }, '已撤销');
      }}>撤销这次改动</button></div>}
  </Modal>;
}
