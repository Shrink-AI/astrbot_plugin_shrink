import { useState } from 'react';
import { api } from '../api/client';
import type { LearningRun, Run } from '../api/types';
import { Badge, Empty, PageHead, Panel } from '../shared/components';
import { useDashboard } from '../shared/dashboard';
import { date, place, runKinds, runLabels, syncLabels } from '../shared/format';
import { LearningDetail } from '../shared/LearningDetail';
import { RunDetail } from '../shared/RunDetail';

const statCells: [string, string][] = [
  ['people', '群友'], ['memories', '正式记忆'], ['candidates', '候选记忆'], ['topics', '话题'],
  ['glossary', '黑话'], ['nicknames', '称呼'], ['expressions', '表达'], ['review_open', '待确认'],
  ['memes_enabled', '可用表情'], ['messages', '消息'],
];

export function Overview() {
  const { status, settings, busy, action, go } = useDashboard();
  const [run, setRun] = useState<Run | null>(null);
  const [learningRun, setLearningRun] = useState<LearningRun | null>(null);
  const tone = (s: string) => s === 'success' ? 'ok' : s === 'failed' ? 'bad' : s === 'running' ? 'info' : undefined;
  return <>
    <PageHead title="概览">
      <button disabled={busy || status.digest_running} onClick={() => void action(() => api('/digest', 'POST'), '已开始整理群聊')}>
        {status.digest_running ? '整理中…' : '立即整理'}</button>
      <button disabled={busy || status.consolidation_running} onClick={() => void action(() => api('/consolidate', 'POST'), '已开始夜间整理')}>
        {status.consolidation_running ? '整理中…' : '运行夜间整理'}</button>
      <button disabled={busy || status.learning_running} onClick={() => void action(() => api('/learning/preview', 'POST'), '已开始预演学习')}>
        {status.learning_running ? '学习中…' : '预演学习'}</button>
    </PageHead>
    <div className="stats">{statCells.map(([key, label]) =>
      <div className="stat" key={key}><strong>{status.counts[key] ?? 0}</strong><small>{label}</small></div>)}</div>
    {status.counts.review_open > 0 && <div className="banner success">有 {status.counts.review_open} 条数据等你确认。
      <button onClick={() => go('teach')}>去确认</button></div>}
    <div className="grid-2">
      <Panel title="运行状态">
        <dl className="facts">
          <dt>对话模型</dt><dd>{settings.model_provider}</dd>
          <dt>人格版本</dt><dd>{status.active_persona ?? '—'} · {syncLabels[status.persona_sync?.status ?? ''] ?? '未知'}</dd>
          <dt>最近收到消息</dt><dd>{date(status.last_message)}</dd>
          <dt>最近整理群聊</dt><dd>{date(status.last_digest)}（待整理 {status.undigested} 条）</dd>
          <dt>最近夜间整理</dt><dd>{date(status.last_consolidation)}</dd>
          <dt>最近学习</dt><dd>{date(status.last_learning)}（待学习 {status.pending_samples} 条）</dd>
          <dt>下次学习检查</dt><dd>{!settings.learning_enabled ? '已暂停' : (status.next_learning_check ?? 0) > 1e9 ? date(status.next_learning_check) : '等待首次检查'}</dd>
          <dt>最近错误</dt><dd>{status.last_error ? `${status.last_error.stage} / ${status.last_error.type}，${date(status.last_error.at)}` : '无'}</dd>
        </dl>
      </Panel>
      <Panel title="最近的整理" actions={<button className="small" onClick={() => go('runs')}>全部记录</button>}>
        {!status.runs.length && <Empty text="还没有整理记录。" />}
        {!!status.runs.length && <div className="table-wrap"><table className="table"><tbody>
          {status.runs.map(r => <tr key={r.id} className="clickable" onClick={() => void action(async () => setRun(await api<Run>(`/runs/${r.id}`)), '')}>
            <td>{runKinds[r.kind] ?? r.kind}</td><td>{place(r.source)}</td>
            <td><Badge tone={tone(r.status)}>{r.undone ? '已撤销' : runLabels[r.status] ?? r.status}</Badge></td>
            <td className="num">{date(r.started)}</td></tr>)}
        </tbody></table></div>}
      </Panel>
    </div>
    <Panel title="最近的学习">
      {!status.learning_runs.length && <Empty text="还没有学习记录。" />}
      {!!status.learning_runs.length && <div className="table-wrap"><table className="table"><tbody>
        {status.learning_runs.map(r => <tr key={r.id} className="clickable" onClick={() => setLearningRun(r)}>
          <td>第 {r.id} 轮</td><td>{r.diagnostics.mode === 'preview' ? '预演' : '正式'}</td>
          <td><Badge tone={tone(r.status)}>{runLabels[r.status]}</Badge></td>
          <td className="text">{r.detail}</td><td className="num">{date(r.started)}</td></tr>)}
      </tbody></table></div>}
    </Panel>
    {run && <RunDetail run={run} close={() => setRun(null)} />}
    {learningRun && <LearningDetail selectedRun={learningRun} close={() => setLearningRun(null)} />}
  </>;
}
