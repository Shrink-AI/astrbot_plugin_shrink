import type { LearningRun } from '../api/types';
import { Modal } from './components';
import { date, syncLabels } from './format';

export function LearningDetail({ selectedRun, close }: { selectedRun: LearningRun; close: () => void }) {
  const d = selectedRun.diagnostics;
  return <Modal title={`第 ${selectedRun.id} 轮学习`} close={close}>
    <dl className="facts">
      <dt>类型</dt><dd>{d.mode === 'preview' ? '预演（不修改人格）' : '正式学习'}</dd>
      <dt>时间</dt><dd>{date(selectedRun.started)}{selectedRun.finished ? `，用时 ${Math.round(selectedRun.finished - selectedRun.started)} 秒` : ''}</dd>
      <dt>样本</dt><dd>{selectedRun.sample_count} 条</dd>
      <dt>结论</dt><dd>{d.reason}</dd>
      <dt>表达库</dt><dd>{d.expressions_eligible !== undefined
        ? `合格 ${d.expressions_eligible} 条，新增 ${d.expressions_added ?? 0} 条，跳过 ${d.expressions_skipped ?? 0} 条`
        : '—'}</dd>
      <dt>人格同步</dt><dd>{syncLabels[d.sync ?? ''] ?? '—'}{d.new_version ? `，新版本 ${d.new_version}` : ''}</dd>
    </dl>
    <div className="compare">
      <section><h3>原来的说话习惯（版本 {d.base_version ?? '未知'}）</h3><p>{d.original_style || '—'}</p></section>
      <section><h3>候选</h3><p>{d.candidate || '未生成'}</p></section>
    </div>
    <details><summary>样本依据与完整诊断</summary><pre>{JSON.stringify(d, null, 2)}</pre></details>
  </Modal>;
}
