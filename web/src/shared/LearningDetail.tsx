import { ChevronRightIcon } from 'lucide-react';
import type { LearningRun } from '@/api/types';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Facts, Modal } from '@/shared/components';
import { date, syncLabels } from '@/shared/format';

export function LearningDetail({ selectedRun, close }: { selectedRun: LearningRun; close: () => void }) {
  const d = selectedRun.diagnostics;
  return <Modal title={`第 ${selectedRun.id} 轮学习`} close={close}>
    <Facts>
      <dt>类型</dt><dd>{d.mode === 'preview' ? '预演（不修改人格）' : '正式学习'}</dd>
      <dt>时间</dt><dd>{date(selectedRun.started)}{selectedRun.finished ? `，用时 ${Math.round(selectedRun.finished - selectedRun.started)} 秒` : ''}</dd>
      <dt>样本</dt><dd>{selectedRun.sample_count} 条</dd>
      <dt>结论</dt><dd>{d.reason}</dd>
      <dt>表达库</dt><dd>{d.expressions_eligible !== undefined
        ? `合格 ${d.expressions_eligible} 条，新增 ${d.expressions_added ?? 0} 条，跳过 ${d.expressions_skipped ?? 0} 条`
        : '—'}</dd>
      <dt>人格同步</dt><dd>{syncLabels[d.sync ?? ''] ?? '—'}{d.new_version ? `，新版本 ${d.new_version}` : ''}</dd>
    </Facts>
    <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
      <section className="flex flex-col gap-1 rounded-lg border p-3 text-sm">
        <h3 className="text-sm font-medium">原来的说话习惯（版本 {d.base_version ?? '未知'}）</h3>
        <p className="whitespace-pre-wrap break-words">{d.original_style || '—'}</p>
      </section>
      <section className="flex flex-col gap-1 rounded-lg border p-3 text-sm">
        <h3 className="text-sm font-medium">候选</h3>
        <p className="whitespace-pre-wrap break-words">{d.candidate || '未生成'}</p>
      </section>
    </div>
    <Collapsible className="group/collapsible text-sm">
      <CollapsibleTrigger asChild>
        <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
          <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
          样本依据与完整诊断
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-2">
        <pre className="mt-2 max-h-80 overflow-auto rounded-lg border bg-muted/40 p-2.5 font-mono text-xs whitespace-pre-wrap break-words">{JSON.stringify(d, null, 2)}</pre>
      </CollapsibleContent>
    </Collapsible>
  </Modal>;
}