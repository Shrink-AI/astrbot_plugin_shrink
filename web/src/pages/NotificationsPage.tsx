import { useEffect, useState } from 'react';
import { api } from '@/api/client';
import { PageHead } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';

type Notice = {
  id: string; event_id: string; session: string; sender: string; kind: string; task_id: string;
  state: string; reason: string; facts: unknown; fact_refs: string[]; merged_into: string;
  created: number; decision: unknown; receipt: { state: string; text: string; error: string } | null;
};
const labels: Record<string, string> = {
  pending: '待处理', merging: '待合并', delivering: '表达或发送中', suppressed: '已抑制',
  sent: '平台已接受', unknown: '发送未知，待核对', failed: '表达失败，可从原任务补发',
};

export function NotificationsPage() {
  const { revision, setError } = useDashboard();
  const [rows, setRows] = useState<Notice[]>([]);
  useEffect(() => {
    let cancelled = false;
    api<{ records: Notice[] }>('/notifications').then(value => { if (!cancelled) setRows(value.records); })
      .catch(error => { if (!cancelled) setError(error.message); });
    return () => { cancelled = true; };
  }, [revision, setError]);
  return <>
    <PageHead title="通知记录" description="原会话、通知事实、合并与抑制依据，以及平台发送回执。" />
    <div className="flex flex-col gap-3">
      {rows.map(row => <details key={row.id} className="rounded-lg border p-3">
        <summary className="cursor-pointer text-sm">
          {new Date(row.created * 1000).toLocaleString()} · {labels[row.state] || row.state} · {row.kind}
          {row.task_id && ` · 任务 ${row.task_id}`}
        </summary>
        <p className="mt-3 text-sm">原会话：{row.session}；创建者：{row.sender}；依据：{row.reason || '排队中'}</p>
        {row.receipt?.text && <p className="mt-2 whitespace-pre-wrap text-sm">{row.receipt.text}</p>}
        <pre className="mt-2 whitespace-pre-wrap break-all text-xs">{JSON.stringify({
          event_id: row.event_id, facts: row.facts, references: row.fact_refs,
          merged_into: row.merged_into, decision: row.decision, receipt: row.receipt,
        }, null, 2)}</pre>
      </details>)}
      {!rows.length && <p className="text-sm text-muted-foreground">暂无通知记录。</p>}
    </div>
  </>;
}
