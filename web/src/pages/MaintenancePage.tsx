import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { PageHead } from '../shared/components';
import { useDashboard } from '../shared/dashboard';

export function MaintenancePage({kind = ''}: {kind?: string}) {
  const {action, busy, revision} = useDashboard();
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
  const perform = (item: any, op: string) => void action(async () => {
    if (op === 'adopt' && !confirm('纳入石英夜间维护后，符合淘汰规则的内容会停用，并在30天后清除快照。继续？')) return;
    setData(await api('/operations/maintenance/action', 'POST', {kind:item.kind,name:item.name,action:op}));
  });
  const states: Record<string,string> = {active:'在用',retiring:'待核对淘汰',retired:'恢复期',restoring:'恢复中',purged:'已清除'};
  return <div className="stack">
    {!kind && <PageHead title="夜间能力维护" />}
    {error && <div className="banner error">{error}</div>}
    <section className="panel stack"><h3>{kind === 'skill' ? '经验质量维护' : kind === 'subagent' ? '子代理质量维护' : '维护概览'}</h3>
      <p>每天台北时间 04:00 起整理。仅托管内容参与自动淘汰，停用后保留30天恢复期；受保护或被任务引用的内容不会清除。</p>
      <div className="row"><button disabled={busy} onClick={() => void action(() => api('/operations/maintenance/run','POST'), '维护已提交，结果将在此处更新')}>立即维护</button>
        <span>{data?.last_run?.at ? `上次整理：${new Date(data.last_run.at * 1000).toLocaleString()}` : '尚无整理记录'}</span></div>
      {data?.last_run?.errors?.length > 0 && <div className="banner error">有 {data.last_run.errors.length} 项维护未完成，请查看下方详情。</div>}
      {data?.inventory_errors?.length > 0 && <div className="banner error">部分原生能力读取失败，相关内容不会自动清理。</div>}
      {data?.items?.filter((i:any) => !kind || i.kind===kind || (kind==='skill' && i.kind==='native_skill')).map((item:any) => <article className="proposal stack" key={item.kind+item.name}>
        <div className="row"><b>{item.title}</b><span>{item.kind === 'subagent' ? '子代理' : 'Skill'} · {states[item.state] || item.state} · {item.managed ? '石英托管' : '未托管'}{item.protected ? ' · 已保护' : ''}</span></div>
        <p>来源：{item.origin || '未知'}；引用任务：{item.references.length}{item.reason && `；${item.reason}`}</p>
        {item.evidence?.uses !== undefined && <small>使用 {item.evidence.uses} 次 · 成功 {item.evidence.wins} · 失败 {item.evidence.losses}</small>}
        {item.recover_until && item.state !== 'purged' && <small>恢复期限：{new Date(item.recover_until*1000).toLocaleString()}</small>}
        <div className="row">{!item.managed && <button disabled={busy} onClick={() => perform(item,'adopt')}>纳入托管</button>}
          {item.state !== 'purged' && <button disabled={busy} onClick={() => perform(item,item.protected ? 'unprotect':'protect')}>{item.protected ? '解除保护':'保护'}</button>}
          {['retired','retiring','restoring'].includes(item.state) && <button disabled={busy} onClick={() => perform(item,'restore')}>恢复并保护</button>}</div>
      </article>)}
      {data && !data.items?.length && <p>没有可维护的能力。</p>}
      <details><summary>维护记录与错误详情</summary><pre className="device-output">{JSON.stringify({last_run:data?.last_run,history:data?.history,suggestion:data?.suggestion,inventory_errors:data?.inventory_errors},null,2)}</pre></details>
    </section>
  </div>;
}
