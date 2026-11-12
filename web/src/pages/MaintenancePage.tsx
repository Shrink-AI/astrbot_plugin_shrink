import { useEffect, useState } from 'react';
import { ChevronRightIcon } from 'lucide-react';
import { Alert, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { api } from '@/api/client';
import { useConfirm } from '@/components/confirm-dialog';
import { Empty, PageHead, Panel } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';

export function MaintenancePage({kind = ''}: {kind?: string}) {
  const {action, busy, revision} = useDashboard();
  const ask = useConfirm();
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    let live = true;
    const refresh = () => api('/operations/maintenance').then(r => {if (live) {setData(r); setError('');}})
      .catch(e => {if (live) setError(e.message);});
    void refresh();
    const timer = setInterval(refresh, 15000);
    return () => {live = false; clearInterval(timer);};
  }, [revision]);
  const perform = async (item: any, op: string) => {
    if(op === 'adopt' && !await ask({ title: '纳入夜间维护？', desc: '纳入石英夜间维护后，符合淘汰规则的内容会停用，并在30天后清除快照。继续？' })) return;
    void action(async () => {
      setData(await api('/operations/maintenance/action', 'POST', {kind:item.kind,name:item.name,action:op}));
    });
  };
  const states: Record<string,string> = {active:'在用',retiring:'待核对淘汰',retired:'恢复期',restoring:'恢复中',purged:'已清除'};
  return <div className="flex flex-col gap-4">
    {!kind && <PageHead title="夜间能力维护" />}
    {error && <Alert variant="destructive"><AlertTitle>{error}</AlertTitle></Alert>}
    <Panel title={kind === 'skill' ? '经验质量维护' : kind === 'subagent' ? '子代理质量维护' : '维护概览'}>
      <div className="flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">每天台北时间 04:00 起整理。仅托管内容参与自动淘汰，停用后保留30天恢复期；受保护或被任务引用的内容不会清除。</p>
        <div className="flex flex-wrap items-center gap-2">
          <Button disabled={busy} onClick={() => void action(() => api('/operations/maintenance/run','POST'), '维护已提交，结果将在此处更新')}>立即维护</Button>
          <span className="text-sm text-muted-foreground">{data?.last_run?.at ? `上次整理：${new Date(data.last_run.at * 1000).toLocaleString()}` : '尚无整理记录'}</span>
        </div>
        {data?.last_run?.errors?.length > 0 && <Alert variant="destructive">
          <AlertTitle>有 {data.last_run.errors.length} 项维护未完成，请查看下方详情。</AlertTitle></Alert>}
        {data?.inventory_errors?.length > 0 && <Alert variant="destructive">
          <AlertTitle>部分原生能力读取失败，相关内容不会自动清理。</AlertTitle></Alert>}
        {!data && !error && <Empty text="加载中…" />}
        {data?.items?.filter((i:any) => !kind || i.kind===kind || (kind==='skill' && i.kind==='native_skill')).map((item:any) =>
          <article className="proposal flex flex-col gap-2 border-t pt-3 first:border-t-0 first:pt-0" key={item.kind+item.name}>
            <div className="flex flex-wrap items-center gap-2">
              <b className="break-words">{item.title}</b>
              <span className="text-xs text-muted-foreground">{item.kind === 'subagent' ? '子代理' : 'Skill'} · {states[item.state] || item.state} · {item.managed ? '石英托管' : '未托管'}{item.protected ? ' · 已保护' : ''}</span>
            </div>
            <p className="text-sm text-muted-foreground">来源：{item.origin || '未知'}；引用任务：{item.references.length}{item.reason && `；${item.reason}`}</p>
            {item.evidence?.uses !== undefined && <small className="text-xs text-muted-foreground">使用 {item.evidence.uses} 次 · 成功 {item.evidence.wins} · 失败 {item.evidence.losses}</small>}
            {item.recover_until && item.state !== 'purged' && <small className="text-xs text-muted-foreground">恢复期限：{new Date(item.recover_until*1000).toLocaleString()}</small>}
            <div className="flex flex-wrap items-center gap-2">
              {!item.managed && <Button variant="outline" size="sm" disabled={busy} onClick={() => void perform(item,'adopt')}>纳入托管</Button>}
              {item.state !== 'purged' && <Button variant="outline" size="sm" disabled={busy} onClick={() => void perform(item,item.protected ? 'unprotect':'protect')}>{item.protected ? '解除保护':'保护'}</Button>}
              {['retired','retiring','restoring'].includes(item.state) && <Button variant="outline" size="sm" disabled={busy} onClick={() => void perform(item,'restore')}>恢复并保护</Button>}
            </div>
          </article>)}
        {data && !data.items?.length && <Empty text="没有可维护的能力。" />}
        <Collapsible className="group/collapsible text-sm">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
              <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
              维护记录与错误详情
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-2">
            <pre className="mt-2 max-h-80 overflow-auto rounded-lg border bg-muted/40 p-2.5 font-mono text-xs whitespace-pre-wrap break-words">{JSON.stringify({last_run:data?.last_run,history:data?.history,suggestion:data?.suggestion,inventory_errors:data?.inventory_errors},null,2)}</pre>
          </CollapsibleContent>
        </Collapsible>
      </div>
    </Panel>
  </div>;
}