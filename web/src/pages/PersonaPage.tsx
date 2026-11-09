import { useEffect, useRef, useState } from 'react';
import { api } from '../api/client';
import type { Expression, LearningRun, Memory, Persona, PersonaVersion } from '../api/types';
import { Badge, Empty, Modal, PageHead, Pagination, Panel } from '../shared/components';
import { useDashboard } from '../shared/dashboard';
import { date, place, runLabels, shortDate } from '../shared/format';
import { LearningDetail } from '../shared/LearningDetail';

export function PersonaPage() {
  const { status, busy, action, revision, setError, go } = useDashboard();
  const [persona, setPersona] = useState<Persona | null>(null);
  const [draft, setDraft] = useState('');
  const dirty = useRef(false);
  const [runs, setRuns] = useState<LearningRun[]>([]);
  const [runOffset, setRunOffset] = useState(0);
  const [expressions, setExpressions] = useState<Expression[]>([]);
  const [lessons, setLessons] = useState<Memory[]>([]);
  const [selectedRun, setSelectedRun] = useState<LearningRun | null>(null);
  const [version, setVersion] = useState<PersonaVersion | null>(null);
  useEffect(() => {
    let cancelled = false;
    Promise.all([
      api<Persona>('/persona'), api<LearningRun[]>(`/learning/runs?offset=${runOffset}`),
      api<Expression[]>('/expressions'), api<Memory[]>('/memories?kind=lesson'),
    ]).then(([p, history, bank, notes]) => {
      if(cancelled) return;
      setPersona(p); if(!dirty.current) setDraft(p.learned);
      setRuns(history); setExpressions(bank); setLessons(notes);
    }).catch(e => { if(!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [revision, runOffset]);
  if(!persona) return <Empty text="加载中…" />;
  return <>
    <PageHead title="人格">
      <button disabled={busy || status.learning_running} onClick={() => void action(() => api('/learning/preview', 'POST'), '已开始预演，完成后在下方查看')}>
        {status.learning_running ? '学习中…' : '预演学习'}</button>
    </PageHead>
    <div className="grid-2">
      <Panel title="说话习惯" actions={<Badge>当前版本 {persona.active}</Badge>}>
        <p className="muted">从群聊中学到的说话方式，会随学习更新，也可以手动改。保存后生成新版本。</p>
        <textarea style={{ margin: '10px 0' }} aria-label="说话习惯" rows={8} disabled={busy} value={draft}
          onChange={e => { dirty.current = true; setDraft(e.target.value); }} />
        <div className="row"><span className="spacer" />
          <button className="primary" disabled={busy || !draft.trim() || draft === persona.learned} onClick={() => void action(async () => {
            await api('/persona', 'PUT', { text: draft }); dirty.current = false;
          })}>保存为新版本</button></div>
        <details style={{ marginTop: 10 }}><summary>固定规则（不参与学习）</summary><pre>{persona.fixed}</pre><button className="small" style={{ marginTop: 6 }} onClick={() => go('prompts')}>在提示词页修改</button></details>
      </Panel>
      <Panel title="版本">
        <div className="table-wrap"><table className="table"><tbody>
          {persona.versions.map(v => <tr key={v.id} className="clickable" onClick={() => setVersion(v)}>
            <td>版本 {v.id}{persona.active === v.id && <> <Badge tone="ok">当前</Badge></>}</td>
            <td>{v.reason}</td><td className="num">{shortDate(v.created)}</td></tr>)}
        </tbody></table></div>
      </Panel>
    </div>
    <Panel title="群友提醒过的事" >
      <p className="muted">群友对石英提的意见，会在回复时提醒它，不改变固定规则。可在“记忆”页删除。</p>
      {!lessons.length ? <Empty text="还没有。" /> : <ul>{lessons.map(l => <li key={l.id}>{l.text} <small>（{shortDate(l.updated)}）</small></li>)}</ul>}
    </Panel>
    <Panel title={`表达库（${expressions.length}）`}>
      <p className="muted">从群聊里学到的真人说法，回复时挑几条给石英参考语气。</p>
      {!expressions.length ? <Empty text="学习后会出现在这里。" /> : <div className="table-wrap"><table className="table">
        <thead><tr><th>场合</th><th>说法</th><th>来自</th><th>用过</th><th /></tr></thead>
        <tbody>{expressions.map(x => <tr key={x.id}>
          <td>{x.situation}</td><td>{x.phrase}</td><td>{place(x.scope)}</td><td className="num">{x.uses}</td>
          <td className="num"><div className="row" style={{ justifyContent: 'flex-end' }}>
            <button className="small" disabled={busy} onClick={() => void action(() => api(`/expressions/${x.id}`, 'PUT', { enabled: !x.enabled }), x.enabled ? '已停用' : '已启用')}>{x.enabled ? '停用' : '启用'}</button>
            <button className="small danger" disabled={busy} onClick={() => { if(confirm('删除这条说法？')) void action(() => api(`/expressions/${x.id}`, 'DELETE'), '已删除'); }}>删除</button>
          </div></td></tr>)}</tbody>
      </table></div>}
    </Panel>
    <Panel title="学习记录">
      {!runs.length ? <Empty text="还没有学习记录。" /> : <div className="table-wrap"><table className="table"><tbody>
        {runs.map(r => <tr key={r.id} className="clickable" onClick={() => setSelectedRun(r)}>
          <td>第 {r.id} 轮</td><td>{r.diagnostics.mode === 'preview' ? '预演' : '正式'}</td>
          <td><Badge tone={r.status === 'success' ? 'ok' : r.status === 'failed' ? 'bad' : undefined}>{runLabels[r.status]}</Badge></td>
          <td className="text">{r.detail} · {r.sample_count} 条样本</td><td className="num">{date(r.started)}</td></tr>)}
      </tbody></table></div>}
      <Pagination offset={runOffset} count={runs.length} size={20} change={setRunOffset} />
    </Panel>
    {selectedRun && <LearningDetail selectedRun={selectedRun} close={() => setSelectedRun(null)} />}
    {version && <Modal title={`版本 ${version.id}`} close={() => setVersion(null)}>
      <div className="compare"><section><h3>当前</h3><p>{persona.learned}</p></section><section><h3>版本 {version.id}</h3><p>{version.text}</p></section></div>
      <small>{version.reason} · {date(version.created)}</small>
      <details><summary>学习依据</summary><pre>{JSON.stringify(JSON.parse(version.evidence), null, 2)}</pre></details>
      <div className="row"><span className="spacer" />
        <button className="primary" disabled={busy || version.id === persona.active} onClick={() => void action(async () => {
          await api(`/persona/${version.id}/rollback`, 'POST'); dirty.current = false; setVersion(null);
        }, '已回退，相同的修改之后不会被再次自动应用')}>回退到这个版本</button></div>
    </Modal>}
  </>;
}
