import { MaintenancePage } from './MaintenancePage';
import { useEffect, useState } from 'react';
import { ChevronRightIcon } from 'lucide-react';
import { cn } from 'cn';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Field, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { useConfirm } from '@/components/confirm-dialog';
import { api } from '@/api/client';
import type { Episode, Skill, SkillDetail } from '@/api/types';
import { Badge, Empty, PageHead, Pagination, Panel } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';
import { date, place } from '@/shared/format';

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
  return <div className="proposal flex flex-col gap-2 rounded-lg border p-3 text-sm">
    <div className="flex flex-wrap items-center gap-2">
      <b className="font-medium">{e.user_name}</b>
      <span className="text-xs text-muted-foreground">QQ {e.account} · {place(e.source)} · {date(e.created)}</span>
      <span className="flex-1" />
      <Badge tone={e.outcome === 'ok' ? undefined : 'bad'}>{outcomes[e.outcome] ?? e.outcome}，{Math.round(e.duration)} 秒</Badge>
      {e.reviewed ? <Badge tone={verdictTone}>{verdicts[e.verdict] ?? e.verdict}</Badge> : <Badge>待复盘</Badge>}
    </div>
    <div className="chatlog flex flex-col gap-1 rounded-lg border bg-muted/30 p-2.5 text-sm">
      <div className="chatline cited rounded bg-accent px-1 font-medium break-words"><span className="speaker text-foreground">{e.user_name}：</span>{e.question}</div>
      {e.tools.map((t, i) => <div key={i} className="chatline flex flex-col gap-0.5 break-words">
        <div><span className="tabular-nums text-xs text-muted-foreground">{i + 1}. {toolNames[t.name] ?? t.name}</span> {t.args}</div>
        <div className="text-xs text-muted-foreground">→ {t.result.slice(0, 200)}{t.result.length > 200 ? '…' : ''}</div>
      </div>)}
      <div className="chatline break-words"><span className="speaker text-foreground">石英：</span>{e.answer || '（没有回复）'}</div>
    </div>
    {e.follow_up && <Collapsible className="group/collapsible text-sm">
      <CollapsibleTrigger asChild>
        <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
          <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
          之后的聊天
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="pt-2">
        <div className="chatlog mt-2 rounded-lg border bg-muted/30 p-2.5 whitespace-pre-wrap break-words">{e.follow_up}</div>
      </CollapsibleContent>
    </Collapsible>}
    {e.lesson && <div className="text-xs text-muted-foreground">学到：{e.lesson}</div>}
  </div>;
}

function SkillEditor({ skill }: { skill: SkillDetail }) {
  const { busy, action } = useDashboard();
  const ask = useConfirm();
  const [title, setTitle] = useState(skill.title);
  const [trigger, setTrigger] = useState(skill.trigger);
  const [steps, setSteps] = useState(skill.steps);
  const dirty = title !== skill.title || trigger !== skill.trigger || steps !== skill.steps;
  const retired = skill.status === 'retired';
  return <Panel title={`#${skill.id} ${skill.title}`} actions={<div className="flex flex-wrap items-center gap-2">
    <Badge tone={retired ? undefined : 'ok'}>{retired ? '已停用' : '在用'}</Badge>
    <span className="text-xs text-muted-foreground">第 {skill.version} 版 · 用过 {skill.uses} 次 · 好 {skill.wins} · 坏 {skill.losses}</span>
  </div>}>
    <div className="flex flex-col gap-2.5">
      <Field>
        <FieldLabel htmlFor="skill-title">名称</FieldLabel>
        <Input id="skill-title" maxLength={30} value={title} onChange={e => setTitle(e.target.value)} />
      </Field>
      <Field>
        <FieldLabel htmlFor="skill-trigger">什么时候用</FieldLabel>
        <Input id="skill-trigger" maxLength={120} value={trigger} onChange={e => setTrigger(e.target.value)} />
      </Field>
      <Field>
        <FieldLabel htmlFor="skill-steps">做法</FieldLabel>
        <Textarea id="skill-steps" rows={5} maxLength={600} value={steps} onChange={e => setSteps(e.target.value)} />
      </Field>
      <div className="flex flex-wrap items-center gap-2">
        <Button disabled={busy || !dirty || !title.trim() || !trigger.trim() || !steps.trim()}
          onClick={() => void action(() => api(`/skills/${skill.id}`, 'PATCH', { title, trigger, steps }))}>保存</Button>
        <Button variant="outline" disabled={busy} onClick={() => void action(() => api(`/skills/${skill.id}`, 'PATCH', { status: retired ? 'active' : 'retired' }), retired ? '已启用' : '已停用')}>
          {retired ? '重新启用' : '停用'}</Button>
        <span className="flex-1" />
        <Button variant="destructive" disabled={busy} onClick={async () => {
          if(!await ask({ title: '删除这条经验？', desc: `永久删除经验「${skill.title}」和它的修改历史？`, destructive: true })) return;
          void action(() => api(`/skills/${skill.id}`, 'DELETE'), '已删除');
        }}>删除</Button>
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
    <div className="flex flex-col gap-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">{version.name}</code>
        <Badge tone={version.conflict ? 'bad' : undefined}>{version.conflict ? '两边均有修改，等待选择' :
          version.native.exists ? version.native.active ? '原生端已启用' : '原生端已停用' : '原生端不存在'}</Badge>
      </div>
      {version.conflict && <p className="text-sm text-muted-foreground">两份内容都会保存在冲突处理记录中。选择原生版本时，也会保留经验修改历史。</p>}
      <Collapsible defaultOpen={version.conflict} className="group/collapsible text-sm">
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
            <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
            查看原生内容
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="pt-2">
          <pre className="mt-2 max-h-80 overflow-auto rounded-lg border bg-muted/40 p-2.5 font-mono text-xs whitespace-pre-wrap break-words">{version.native.content || '（已删除）'}</pre>
        </CollapsibleContent>
      </Collapsible>
      {version.conflict && <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" disabled={busy} onClick={() => void action(() => api(`/skills/${id}/native`, 'POST',
          { choice: 'local', snapshot: version.snapshot }), '已将经验版本同步到原生 Skill')}>采用经验版本</Button>
        <Button variant="outline" disabled={busy} onClick={() => void action(() => api(`/skills/${id}/native`, 'POST',
          { choice: 'native', snapshot: version.snapshot }), '已采用原生 Skill 版本')}>采用原生 Skill 版本</Button>
      </div>}
    </div>
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
    <div className="mb-3 flex flex-wrap items-center gap-2">
      <ToggleGroup type="single" variant="outline" size="sm" value={status || 'all'}
        onValueChange={value => { if(!value) return; setStatus(value === 'all' ? '' : value); setOffset(0); }}>
        {filters.map(([value, label]) => <ToggleGroupItem key={value || 'all'} value={value || 'all'}>{label}</ToggleGroupItem>)}
      </ToggleGroup>
    </div>
    {!skills.length ? <Panel><Empty text="还没有经验。石英用工具办过事、复盘之后，这里会出现它总结的做法。" /></Panel> :
      <div className="grid items-start gap-3 md:grid-cols-[280px_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-2">
          <Card className="gap-0 py-0">
            <div className="flex max-h-[70vh] flex-col gap-0.5 overflow-y-auto p-1.5">{skills.map(s =>
              <Button key={s.id} variant="ghost" onClick={() => setOpenId(s.id)}
                className={cn('list-item h-auto w-full flex-col items-start justify-start gap-0.5 px-2.5 py-2 text-left whitespace-normal',
                  s.id === openId && 'active bg-secondary')}>
                <span className="title flex flex-wrap items-center gap-2 text-sm font-medium">{s.title}{s.status === 'retired' && <Badge>停用</Badge>}</span>
                <span className="text-xs text-muted-foreground">{s.trigger}</span>
                <span className="text-xs text-muted-foreground">用过 {s.uses} 次 · 更新于 {date(s.updated)}</span>
              </Button>)}</div>
          </Card>
          <Pagination offset={offset} count={skills.length} size={50} change={setOffset} />
        </div>
        {detail && <div className="flex min-w-0 flex-col gap-3">
          <SkillEditor key={`${detail.id}:${detail.version}:${detail.status}`} skill={detail} />
          <NativeSkillStatus id={detail.id} />
          <Panel title="修改历史">
            <div className="flex flex-col gap-2.5">{detail.versions.map(v => <div key={v.id} className="proposal flex flex-col gap-2 rounded-lg border p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2"><b className="font-medium">第 {v.version} 版</b><Badge>{versionOrigins[v.origin] ?? v.origin}</Badge>
                {v.status === 'retired' && <Badge>停用</Badge>}<span className="text-xs text-muted-foreground">{date(v.created)}</span></div>
              <div className="text-xs text-muted-foreground">原因：{v.reason}{v.episodes.length ? `（依据第 ${v.episodes.join('、')} 次求助）` : ''}</div>
              <div className="text-xs text-muted-foreground">{v.title}｜{v.trigger}</div>
              <div className="chatline whitespace-pre-wrap break-words">{v.steps}</div>
              <div><Button variant="outline" size="sm" disabled={busy || v.version === detail.version} onClick={() => void action(
                () => api(`/skills/${detail.id}`, 'PATCH', {
                  title: v.title, trigger: v.trigger, steps: v.steps, status: v.status,
                }), '已恢复此版本，原有修改历史保留')}>恢复此版本</Button></div>
            </div>)}</div>
          </Panel>
          <Panel title="依据的求助经过">
            {!detail.episodes.length ? <Empty text="这条经验是手动写的，没有对应的求助记录。" /> :
              <div className="flex flex-col gap-2.5">{detail.episodes.map(e => <div key={e.id} className="flex flex-col gap-1">
                <span className="text-xs text-muted-foreground">第 {e.id} 次求助</span><EpisodeCard e={e} /></div>)}</div>}
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
    {!episodes.length ? <Panel><Empty text="还没有用工具办过事。" /></Panel> :
      <div className="flex flex-col gap-2.5">{episodes.map(e => <div key={e.id} className="flex flex-col gap-1">
        <span className="text-xs text-muted-foreground">第 {e.id} 次求助</span><EpisodeCard e={e} /></div>)}</div>}
    <Pagination offset={offset} count={episodes.length} size={30} change={setOffset} />
  </>;
}

export function SkillsPage() {
  const { settings, action } = useDashboard();
  const [tab, setTab] = useState<'skills' | 'episodes' | 'maintenance'>('skills');
  return <>
    <PageHead title="经验">
      <Field orientation="horizontal" className="w-auto">
        <Checkbox id="skills-enabled" checked={!!settings.skills_enabled}
          onCheckedChange={value => void action(() => api('/settings', 'PUT', { ...settings, skills_enabled: value === true }), value === true ? '已开启经验' : '已暂停经验')} />
        <FieldLabel htmlFor="skills-enabled">使用并积累经验</FieldLabel>
      </Field>
      <ToggleGroup type="single" variant="outline" size="sm" value={tab}
        onValueChange={value => { if(value === 'skills' || value === 'episodes' || value === 'maintenance') setTab(value); }}>
        <ToggleGroupItem value="skills">经验</ToggleGroupItem>
        <ToggleGroupItem value="maintenance">质量维护</ToggleGroupItem>
        <ToggleGroupItem value="episodes">求助记录</ToggleGroupItem>
      </ToggleGroup>
    </PageHead>
    {tab === 'maintenance' ? <MaintenancePage kind="skill" /> : tab === 'skills' ? <SkillsTab /> : <EpisodesTab />}
  </>;
}