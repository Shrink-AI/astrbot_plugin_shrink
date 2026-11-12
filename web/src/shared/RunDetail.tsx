import { ChevronRightIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Table, TableBody, TableCell, TableRow } from '@/components/ui/table';
import { api } from '@/api/client';
import { useConfirm } from '@/components/confirm-dialog';
import type { Run } from '@/api/types';
import { Badge, Facts, Modal } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';
import { date, place, reasonLabel, runKinds, runLabels } from '@/shared/format';

const outcomeLabels: Record<string, string> = {
  created: '新增', merged: '合并到已有', superseded: '取代旧记忆', conflict: '与已确认记忆矛盾', unchanged: '已确认，未改动',
  nickname_created: '新称呼', nickname_merged: '称呼补充依据', nickname_conflict: '称呼冲突', nickname_exists: '称呼已存在',
  nickname_rejected: '已被否定的称呼', glossary_created: '新黑话', glossary_exists: '黑话已存在',
  glossary_rejected: '黑话释义不合格', glossary_failed: '黑话查询失败',
};

function Counts({ title, data, label }: { title: string; data?: Record<string, number>; label: (k: string) => string }) {
  const entries = Object.entries(data ?? {});
  if(!entries.length) return null;
  return <div className="flex flex-col gap-1">
    <h3 className="text-sm font-medium">{title}</h3>
    <Table><TableBody>
      {entries.sort((a, b) => b[1] - a[1]).map(([k, v]) => <TableRow key={k}>
        <TableCell>{label(k)}</TableCell><TableCell className="text-right tabular-nums">{v}</TableCell>
      </TableRow>)}
    </TableBody></Table>
  </div>;
}

export function RunDetail({ run, close }: { run: Run; close: () => void }) {
  const { action, busy } = useDashboard();
  const ask = useConfirm();
  const stats = run.stats ?? {};
  const changes = (run.changes ?? []).reduce((n, c) => n + c.n, 0);
  return <Modal title={`${runKinds[run.kind] ?? run.kind} #${run.id}`} close={close}>
    <Facts>
      <dt>来源</dt><dd>{place(run.source)}</dd>
      <dt>状态</dt><dd><Badge>{run.undone ? '已撤销' : runLabels[run.status] ?? run.status}</Badge></dd>
      <dt>时间</dt><dd>{date(run.started)}{run.finished ? ` → ${date(run.finished)}` : ''}</dd>
      {stats.messages !== undefined && <><dt>消息数</dt><dd>{stats.messages}</dd></>}
      <dt>说明</dt><dd>{run.detail || '—'}</dd>
      <dt>写入的改动</dt><dd>{changes} 处</dd>
    </Facts>
    <Counts title="写入结果" data={stats.outcomes} label={k => outcomeLabels[k] ?? k} />
    <Counts title="被拦下的条目" data={stats.rejected} label={reasonLabel} />
    <Collapsible className="group/collapsible text-sm">
      <CollapsibleTrigger asChild>
        <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
          <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
          原始统计
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-2">
        <pre className="mt-2 max-h-80 overflow-auto rounded-lg border bg-muted/40 p-2.5 font-mono text-xs whitespace-pre-wrap break-words">{JSON.stringify(stats, null, 2)}</pre>
      </CollapsibleContent>
    </Collapsible>
    {!run.undone && changes > 0 && run.status !== 'running' && <div className="flex justify-end">
      <Button variant="destructive" disabled={busy} onClick={async () => {
        if(!await ask({ title: '撤销这次改动？', desc: '撤销这次记录写入的全部改动？被它取代的旧记忆会恢复。', destructive: true })) return;
        void action(async () => { await api(`/runs/${run.id}/undo`, 'POST'); close(); }, '已撤销');
      }}>撤销这次改动</Button></div>}
  </Modal>;
}