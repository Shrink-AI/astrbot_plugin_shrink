import { MaintenancePage } from './MaintenancePage';
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import type { Episode, Skill, SkillDetail } from '../api/types';
import { Badge, Empty, PageHead, Pagination, Panel } from '../shared/components';
import { useDashboard } from '../shared/dashboard';
import { date, place } from '../shared/format';

const outcomes: Record<string, string> = { ok: '回复了', timeout: '超时没回上', error: '出错' };
const verdicts: Record<string, string> = { good: '办得好', bad: '没办好', unclear: '看不出', failed: '复盘失败' };
const versionOrigins: Record<string, string> = { reflection: '复盘', admin: '管理员修改', rule: '自动停用', native_skill: '原生 Skill 修改' };
const toolNames: Record<string, string> = {
  web_search_deepseek: '联网搜索', deepseek_fetch_web_page: '打开网页', shrink_chat_history: '翻聊天记录',
  shrink_lookup: '查人和记忆', shrink_find_meme: '找表情', shrink_run_python: '跑代码', shrink_remember: '记住',
};
const filters = [['active', '在用'], ['retired', '已停用'], ['', '全部']] as const;

function EpisodeCard({ e }: { e: Episode }) {
  const verdictTone = e.verdict === 'good' ? 'ok' : e.verdict === 'bad' ? 'bad' : undefined;
  return <div className="proposal">
    <div className="row">
      <b>{e.user_name}</b><small>QQ {e.account} · {place(e.source)} · {date(e.created)}</small>
      <span className="spacer" />
      <Badge tone={e.outcome === 'ok' ? undefined : 'bad'}>{outcomes[e.outcome] ?? e.outcome}，{Math.round(e.duration)} 秒</Badge>
      {e.reviewed ? <Badge tone={verdictTone}>{verdicts[e.verdict] ?? e.verdict}</Badge> : <Badge>待复盘</Badge>}
    </div>
    <div className="review-block" style={{ marginTop: 6 }}>
      <div className="chatlog">
        <div className="chatline cited"><span className="speaker">{e.user_name}：</span>{e.question}</div>
        {e.tools.map((t, i) => <div key={i} className="chatline">
          <small>{i + 1}. {toolNames[t.name] ?? t.name}</small> {t.args}
          <div className="review-note">→ {t.result.slice(0, 200)}{t.result.length > 200 ? '…' : ''}</div>
        </div>)}
        <div className="chatline"><span className="speaker">石英：</span>{e.answer || '（没有回复）'}</div>
      </div>
      {e.follow_up && <details><summary>之后的聊天</summary><div className="chatlog" style={{ whiteSpace: 'pre-wrap' }}>{e.follow_up}</div></details>}
      {e.lesson && <div className="review-note">学到：{e.lesson}</div>}
    </div>
  </div>;
}

function SkillEditor({ skill }: { skill: SkillDetail }) {
  const { busy, action } = useDashboard();
  const [title, setTitle] = useState(skill.title);
  const [trigger, setTrigger] = useState(skill.trigger);
  const [steps, setSteps] = useState(skill.steps);
  const dirty = title !== skill.title || trigger !== skill.trigger || steps !== skill.steps;
  const retired = skill.status === 'retired';
  return <Panel title={`#${skill.id} ${skill.title}`} actions={<div className="row">
    <Badge tone={retired ? undefined : 'ok'}>{retired ? '已停用' : '在用'}</Badge>
    <small>第 {skill.version} 版 · 用过 {skill.uses} 次 · 好 {skill.wins} · 坏 {skill.losses}</small>
  </div>}>
    <div className="stack">
      <label className="field"><span>名称</span><input maxLength={30} value={title} onChange={e => setTitle(e.target.value)} /></label>
      <label className="field"><span>什么时候用</span><input maxLength={120} value={trigger} onChange={e => setTrigger(e.target.value)} /></label>
      <label className="field"><span>做法</span><textarea rows={5} maxLength={600} value={steps} onChange={e => setSteps(e.target.value)} /></label>
      <div className="row">
        <button className="primary" disabled={busy || !dirty || !title.trim() || !trigger.trim() || !steps.trim()}
          onClick={() => void action(() => api(`/skills/${skill.id}`, 'PATCH', { title, trigger, steps }))}>保存</button>
        <button disabled={busy} onClick={() => void action(() => api(`/skills/${skill.id}`, 'PATCH', { status: retired ? 'active' : 'retired' }), retired ? '已启用' : '已停用')}>
          {retired ? '重新启用' : '停用'}</button>
        <span className="spacer" />
        <button className="danger" disabled={busy} onClick={() => {
          if(confirm(`永久删除经验「${skill.title}」和它的修改历史？`)) void action(() => api(`/skills/${skill.id}`, 'DELETE'), '已删除');
        }}>删除</button>
      </div>
    </div>
  </Panel>;
}

type NativeVersion = {
  name: string; conflict: boolean; snapshot: string;
  local: { title: string; trigger: string; steps: string; status: string };
  native: { content: string; active: boolean; exists: boolean };
};

function NativeSkillStatus({ id }: { id: number }) {
  const { revision, busy, action, setError } = useDashboard();
  const [version, setVersion] = useState<NativeVersion | null>(null);
  useEffect(() => {
    let cancelled = false;
    setVersion(null);
    api<NativeVersion>(`/skills/${id}/native`).then(v => { if(!cancelled) setVersion(v); })
      .catch(e => { if(!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [id, revision]);
  if(!version) return null;
  return <Panel title="AstrBot Skill">
    <div className="row"><code>{version.name}</code>
      <Badge tone={version.conflict ? 'bad' : undefined}>{version.conflict ? '两边均有修改，等待选择' :
        version.native.exists ? version.native.active ? '原生端已启用' : '原生端已停用' : '原生端不存在'}</Badge></div>
    {version.conflict && <p>两份内容都会保存在冲突处理记录中。选择原生版本时，也会保留经验修改历史。</p>}
    <details open={version.conflict}><summary>查看原生内容</summary>
      <pre style={{ whiteSpace: 'pre-wrap' }}>{version.native.content || '（已删除）'}</pre></details>
    {version.conflict && <div className="row">
      <button disabled={busy} onClick={() => void action(() => api(`/skills/${id}/native`, 'POST',
        { choice: 'local', snapshot: version.snapshot }), '已将经验版本同步到原生 Skill')}>采用经验版本</button>
      <button disabled={busy} onClick={() => void action(() => api(`/skills/${id}/native`, 'POST',
        { choice: 'native', snapshot: version.snapshot }), '已采用原生 Skill 版本')}>采用原生 Skill 版本</button>
    </div>}
  </Panel>;
}

function SkillsTab() {
  const { revision, setError, busy, action } = useDashboard();
  const [status, setStatus] = useState('active');
  const [offset, setOffset] = useState(0);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [openId, setOpenId] = useState<number | null>(null);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  useEffect(() => {
    let cancelled = false;
    api<Skill[]>(`/skills?status=${status}&offset=${offset}`).then(rows => {
      if(cancelled) return;
      setSkills(rows);
      if(!rows.some(r => r.id === openId)) setOpenId(rows[0]?.id ?? null);
    }).catch(e => { if(!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [status, offset, revision]);
  useEffect(() => {
    if(openId === null) { setDetail(null); return; }
    let cancelled = false;
    api<SkillDetail>(`/skills/${openId}`).then(d => { if(!cancelled) setDetail(d); })
      .catch(e => { if(!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [openId, revision]);
  return <>
    <div className="toolbar"><div className="segmented">{filters.map(([value, label]) =>
      <button key={value} className={status === value ? 'on' : ''} onClick={() => { setStatus(value); setOffset(0); }}>{label}</button>)}</div></div>
    {!skills.length ? <section className="panel"><Empty text="还没有经验。石英用工具办过事、复盘之后，这里会出现它总结的做法。" /></section> :
      <div className="split">
        <section className="panel" style={{ padding: 0 }}><div className="list">{skills.map(s =>
          <button key={s.id} className={'list-item' + (s.id === openId ? ' active' : '')} onClick={() => setOpenId(s.id)}>
            <span className="title">{s.title}{s.status === 'retired' && <Badge>停用</Badge>}</span>
            <small>{s.trigger}</small>
            <small>用过 {s.uses} 次 · 更新于 {date(s.updated)}</small>
          </button>)}</div>
          <Pagination offset={offset} count={skills.length} size={50} change={setOffset} />
        </section>
        {detail && <div>
          <SkillEditor key={`${detail.id}:${detail.version}:${detail.status}`} skill={detail} />
          <NativeSkillStatus id={detail.id} />
          <Panel title="修改历史">
            {detail.versions.map(v => <div key={v.id} className="proposal">
              <div className="row"><b>第 {v.version} 版</b><Badge>{versionOrigins[v.origin] ?? v.origin}</Badge>
                {v.status === 'retired' && <Badge>停用</Badge>}<small>{date(v.created)}</small></div>
              <div className="review-note">原因：{v.reason}{v.episodes.length ? `（依据第 ${v.episodes.join('、')} 次求助）` : ''}</div>
              <div className="review-note">{v.title}｜{v.trigger}</div>
              <div className="chatline">{v.steps}</div>
              <button disabled={busy || v.version === detail.version} onClick={() => void action(
                () => api(`/skills/${detail.id}`, 'PATCH', {
                  title: v.title, trigger: v.trigger, steps: v.steps, status: v.status,
                }), '已恢复此版本，原有修改历史保留')}>恢复此版本</button>
            </div>)}
          </Panel>
          <Panel title="依据的求助经过">
            {!detail.episodes.length ? <Empty text="这条经验是手动写的，没有对应的求助记录。" /> :
              <div className="stack">{detail.episodes.map(e => <div key={e.id}><small>第 {e.id} 次求助</small><EpisodeCard e={e} /></div>)}</div>}
          </Panel>
        </div>}
      </div>}
  </>;
}

function EpisodesTab() {
  const { revision, setError } = useDashboard();
  const [offset, setOffset] = useState(0);
  const [episodes, setEpisodes] = useState<Episode[]>([]);
  useEffect(() => {
    let cancelled = false;
    api<Episode[]>(`/episodes?offset=${offset}`).then(rows => { if(!cancelled) setEpisodes(rows); })
      .catch(e => { if(!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [offset, revision]);
  return <>
    {!episodes.length ? <section className="panel"><Empty text="还没有用工具办过事。" /></section> :
      <div className="stack">{episodes.map(e => <div key={e.id}><small>第 {e.id} 次求助</small><EpisodeCard e={e} /></div>)}</div>}
    <Pagination offset={offset} count={episodes.length} size={30} change={setOffset} />
  </>;
}

export function SkillsPage() {
  const { settings, action } = useDashboard();
  const [tab, setTab] = useState<'skills' | 'episodes' | 'maintenance'>('skills');
  return <>
    <PageHead title="经验">
      <label className="check"><input type="checkbox" checked={!!settings.skills_enabled}
        onChange={e => void action(() => api('/settings', 'PUT', { ...settings, skills_enabled: e.target.checked }), e.target.checked ? '已开启经验' : '已暂停经验')} />使用并积累经验</label>
      <div className="segmented">
        <button className={tab === 'skills' ? 'on' : ''} onClick={() => setTab('skills')}>经验</button>
<button className={tab === 'maintenance' ? 'on' : ''} onClick={() => setTab('maintenance')}>质量维护</button>
        <button className={tab === 'episodes' ? 'on' : ''} onClick={() => setTab('episodes')}>求助记录</button>
      </div>
    </PageHead>
    {tab === 'maintenance' ? <MaintenancePage kind="skill" /> : tab === 'skills' ? <SkillsTab /> : <EpisodesTab />}
  </>;
}
