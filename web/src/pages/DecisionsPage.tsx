import { useEffect, useState } from 'react';
import { api } from '@/api/client';
import type { DecisionRecords } from '@/api/types';
import { PageHead } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';

export function DecisionsPage() {
  const { revision, setError } = useDashboard();
  const [data, setData] = useState<DecisionRecords | null>(null);
  useEffect(() => {
    let cancelled = false;
    api<DecisionRecords>('/decisions').then(value => { if (!cancelled) setData(value); })
      .catch(error => { if (!cancelled) setError(error.message); });
    return () => { cancelled = true; };
  }, [revision, setError]);
  return <>
    <PageHead title="决策记录" description="查看最近 14 天的路由、候选、依据与降级；分数按后端解释。" />
    {data && <p className="text-sm text-muted-foreground">
      共 {data.stats.total} 次；规则直达 {data.stats.direct} 次（{(data.stats.rule_direct_ratio * 100).toFixed(1)}%）。
      下方显示最近 100 次。
    </p>}
    <div className="flex flex-col gap-3">
      {data?.records.map(row => <details key={row.id} className="rounded-lg border p-3">
        <summary className="cursor-pointer text-sm">
          {new Date(row.created * 1000).toLocaleString()} · {row.backend} · {row.decision.route}
          {' · '}{(row.elapsed * 1000).toFixed(0)} ms{row.fallback ? ' · 已降级' : ''}
        </summary>
        <p className="mt-3 text-sm">版本：{row.version}；依据：{row.reason}；
          分数类型：{row.confidence_kind}；分数：{row.decision.confidence}</p>
        <pre className="mt-2 whitespace-pre-wrap break-all text-xs">{JSON.stringify({
          input_id: row.input_id, decision: row.decision, candidates: row.candidates,
        }, null, 2)}</pre>
      </details>)}
      {data && !data.records.length && <p className="text-sm text-muted-foreground">暂无决策记录。</p>}
    </div>
  </>;
}
