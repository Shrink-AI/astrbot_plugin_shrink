import { useState } from 'react';
import { Alert, AlertAction, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Table, TableBody, TableCell, TableRow } from '@/components/ui/table';
import { api } from '@/api/client';
import type { LearningRun, Run } from '@/api/types';
import { Badge, Empty, Facts, PageHead, Panel } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';
import { date, place, runKinds, runLabels, syncLabels } from '@/shared/format';
import { LearningDetail } from '@/shared/LearningDetail';
import { RunDetail } from '@/shared/RunDetail';

const statCells: [string, string][] = [
  ['people', '群友'], ['memories', '正式记忆'], ['candidates', '候选记忆'], ['topics', '话题'],
  ['glossary', '黑话'], ['nicknames', '称呼'], ['expressions', '表达'], ['review_open', '待确认'],
  ['memes_enabled', '可用表情'], ['messages', '消息'],
];

export function Overview() {
  const { status, settings, busy, action, go } = useDashboard();
  const [run, setRun] = useState<Run | null>(null);
  const [learningRun, setLearningRun] = useState<LearningRun | null>(null);
  const tone = (s: string) => s === 'success' ? 'ok' as const : s === 'failed' ? 'bad' as const : s === 'running' ? 'info' as const : undefined;
  return <>
    <PageHead title="概览">
      <Button disabled={busy || status.digest_running} onClick={() => void action(() => api('/digest', 'POST'), '已开始整理群聊')}>
        {status.digest_running ? '整理中…' : '立即整理'}</Button>
      <Button variant="outline" disabled={busy || status.consolidation_running} onClick={() => void action(() => api('/consolidate', 'POST'), '已开始夜间整理')}>
        {status.consolidation_running ? '整理中…' : '运行夜间整理'}</Button>
      <Button variant="outline" disabled={busy || status.learning_running} onClick={() => void action(() => api('/learning/preview', 'POST'), '已开始预演学习')}>
        {status.learning_running ? '学习中…' : '预演学习'}</Button>
    </PageHead>
    <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
      {statCells.map(([key, label]) => <Card key={key} size="sm" className="gap-0.5 py-3">
        <CardContent className="px-4">
          <div className="text-xl font-semibold tracking-tight tabular-nums">{status.counts[key] ?? 0}</div>
          <div className="text-xs text-muted-foreground">{label}</div>
        </CardContent>
      </Card>)}
    </div>
    {status.counts.review_open > 0 && <Alert className="mb-4 border-success/30 bg-success/5 text-success">
      <AlertTitle>有 {status.counts.review_open} 条数据等你确认。</AlertTitle>
      <AlertAction><Button size="sm" variant="ghost" className="text-success hover:bg-success/10 hover:text-success" onClick={() => go('teach')}>去确认</Button></AlertAction>
    </Alert>}
    <div className="mb-4 grid gap-3 lg:grid-cols-2">
      <Panel title="运行状态">
        <Facts>
          <dt>对话模型</dt><dd>{settings.model_provider}</dd>
          <dt>人格版本</dt><dd>{status.active_persona ?? '—'} · {syncLabels[status.persona_sync?.status ?? ''] ?? '未知'}</dd>
          <dt>最近收到消息</dt><dd>{date(status.last_message)}</dd>
          <dt>最近整理群聊</dt><dd>{date(status.last_digest)}（待整理 {status.undigested} 条）</dd>
          <dt>最近夜间整理</dt><dd>{date(status.last_consolidation)}</dd>
          <dt>最近学习</dt><dd>{date(status.last_learning)}（待学习 {status.pending_samples} 条）</dd>
          <dt>下次学习检查</dt><dd>{!settings.learning_enabled ? '已暂停' : (status.next_learning_check ?? 0) > 1e9 ? date(status.next_learning_check) : '等待首次检查'}</dd>
          <dt>最近错误</dt><dd>{status.last_error ? `${status.last_error.stage} / ${status.last_error.type}，${date(status.last_error.at)}` : '无'}</dd>
        </Facts>
      </Panel>
      <Panel title="最近的整理" actions={<Button variant="outline" size="sm" onClick={() => go('runs')}>全部记录</Button>}>
        {!status.runs.length && <Empty text="还没有整理记录。" />}
        {!!status.runs.length && <Table>
          <TableBody>
            {status.runs.map(r => <TableRow key={r.id} className="cursor-pointer"
              onClick={() => void action(async () => setRun(await api<Run>(`/runs/${r.id}`)), '')}>
              <TableCell>{runKinds[r.kind] ?? r.kind}</TableCell>
              <TableCell>{place(r.source)}</TableCell>
              <TableCell><Badge tone={tone(r.status)}>{r.undone ? '已撤销' : runLabels[r.status] ?? r.status}</Badge></TableCell>
              <TableCell className="text-right tabular-nums">{date(r.started)}</TableCell>
            </TableRow>)}
          </TableBody>
        </Table>}
      </Panel>
    </div>
    <Panel title="最近的学习">
      {!status.learning_runs.length && <Empty text="还没有学习记录。" />}
      {!!status.learning_runs.length && <Table>
        <TableBody>
          {status.learning_runs.map(r => <TableRow key={r.id} className="cursor-pointer" onClick={() => setLearningRun(r)}>
            <TableCell>第 {r.id} 轮</TableCell>
            <TableCell>{r.diagnostics.mode === 'preview' ? '预演' : '正式'}</TableCell>
            <TableCell><Badge tone={tone(r.status)}>{runLabels[r.status]}</Badge></TableCell>
            <TableCell className="min-w-[260px] whitespace-normal">{r.detail}</TableCell>
            <TableCell className="text-right tabular-nums">{date(r.started)}</TableCell>
          </TableRow>)}
        </TableBody>
      </Table>}
    </Panel>
    {run && <RunDetail run={run} close={() => setRun(null)} />}
    {learningRun && <LearningDetail selectedRun={learningRun} close={() => setLearningRun(null)} />}
  </>;
}