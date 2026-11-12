import { useEffect, useRef, useState } from 'react';
import { ChevronRightIcon } from 'lucide-react';
import { cn } from 'cn';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Field, FieldLabel } from '@/components/ui/field';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Textarea } from '@/components/ui/textarea';
import { api } from '@/api/client';
import { useConfirm } from '@/components/confirm-dialog';
import type { Expression, LearningRun, Memory, Persona, PersonaVersion } from '@/api/types';
import { Badge, Empty, Modal, PageHead, Pagination, Panel } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';
import { date, place, runLabels, shortDate } from '@/shared/format';
import { LearningDetail } from '@/shared/LearningDetail';

const preClass = 'max-h-80 overflow-auto rounded-lg border bg-muted/40 p-2.5 font-mono text-xs whitespace-pre-wrap break-words';

export function PersonaPage() {
  const { status, busy, action, revision, setError, go } = useDashboard();
  const ask = useConfirm();
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
      <Button disabled={busy || status.learning_running} onClick={() => void action(() => api('/learning/preview', 'POST'), '已开始预演，完成后在下方查看')}>
        {status.learning_running ? '学习中…' : '预演学习'}</Button>
    </PageHead>
    <div className="mb-3 grid gap-3 lg:grid-cols-2">
      <Panel title="说话习惯" actions={<Badge>当前版本 {persona.active}</Badge>}>
        <p className="text-sm text-muted-foreground">从群聊中学到的说话方式，会随学习更新，也可以手动改。保存后生成新版本。</p>
        <Field className="my-2.5">
          <FieldLabel htmlFor="persona-learned">说话习惯</FieldLabel>
          <Textarea id="persona-learned" aria-label="说话习惯" rows={8} disabled={busy} value={draft}
            className="min-h-[180px]"
            onChange={e => { dirty.current = true; setDraft(e.target.value); }} />
        </Field>
        <div className="flex flex-wrap items-center gap-2"><span className="flex-1" />
          <Button disabled={busy || !draft.trim() || draft === persona.learned} onClick={() => void action(async () => {
            await api('/persona', 'PUT', { text: draft }); dirty.current = false;
          })}>保存为新版本</Button></div>
        <Collapsible className="group/collapsible mt-2.5">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
              <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
              固定规则（不参与学习）
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-2">
            <pre className={cn(preClass, 'mt-1.5')}>{persona.fixed}</pre>
            <Button variant="outline" size="sm" className="mt-1.5" onClick={() => go('prompts')}>在提示词页修改</Button>
          </CollapsibleContent>
        </Collapsible>
      </Panel>
      <Panel title="版本">
        <Table>
          <TableBody>
            {persona.versions.map(v => <TableRow key={v.id} className="clickable cursor-pointer" onClick={() => setVersion(v)}>
              <TableCell>版本 {v.id}{persona.active === v.id && <> <Badge tone="ok">当前</Badge></>}</TableCell>
              <TableCell>{v.reason}</TableCell>
              <TableCell className="text-right tabular-nums">{shortDate(v.created)}</TableCell>
            </TableRow>)}
          </TableBody>
        </Table>
      </Panel>
    </div>
    <Panel title="群友提醒过的事" className="mb-3">
      <p className="text-sm text-muted-foreground">群友对石英提的意见，会在回复时提醒它，不改变固定规则。可在“记忆”页删除。</p>
      {!lessons.length ? <Empty text="还没有。" /> : <ul className="mt-2 flex list-disc flex-col gap-1 pl-5">
        {lessons.map(l => <li key={l.id}>{l.text} <small className="text-xs text-muted-foreground">（{shortDate(l.updated)}）</small></li>)}
      </ul>}
    </Panel>
    <Panel title={`表达库（${expressions.length}）`} className="mb-3">
      <p className="text-sm text-muted-foreground">从群聊里学到的真人说法，回复时挑几条给石英参考语气。</p>
      {!expressions.length ? <Empty text="学习后会出现在这里。" /> : <Table>
        <TableHeader>
          <TableRow>
            <TableHead>场合</TableHead><TableHead>说法</TableHead><TableHead>来自</TableHead>
            <TableHead className="text-right">用过</TableHead><TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>{expressions.map(x => <TableRow key={x.id}>
          <TableCell className="whitespace-normal">{x.situation}</TableCell>
          <TableCell className="whitespace-normal">{x.phrase}</TableCell>
          <TableCell>{place(x.scope)}</TableCell>
          <TableCell className="text-right tabular-nums">{x.uses}</TableCell>
          <TableCell className="text-right">
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button variant="outline" size="sm" disabled={busy} onClick={() => void action(() => api(`/expressions/${x.id}`, 'PUT', { enabled: !x.enabled }), x.enabled ? '已停用' : '已启用')}>{x.enabled ? '停用' : '启用'}</Button>
              <Button variant="destructive" size="sm" disabled={busy} onClick={async () => {
                if(!await ask({ title: '删除说法？', desc: '删除这条说法？', destructive: true })) return;
                void action(() => api(`/expressions/${x.id}`, 'DELETE'), '已删除');
              }}>删除</Button>
            </div>
          </TableCell>
        </TableRow>)}</TableBody>
      </Table>}
    </Panel>
    <Panel title="学习记录">
      {!runs.length ? <Empty text="还没有学习记录。" /> : <Table>
        <TableBody>
          {runs.map(r => <TableRow key={r.id} className="clickable cursor-pointer" onClick={() => setSelectedRun(r)}>
            <TableCell>第 {r.id} 轮</TableCell>
            <TableCell>{r.diagnostics.mode === 'preview' ? '预演' : '正式'}</TableCell>
            <TableCell><Badge tone={r.status === 'success' ? 'ok' : r.status === 'failed' ? 'bad' : undefined}>{runLabels[r.status]}</Badge></TableCell>
            <TableCell className="min-w-[260px] whitespace-normal">{r.detail} · {r.sample_count} 条样本</TableCell>
            <TableCell className="text-right tabular-nums">{date(r.started)}</TableCell>
          </TableRow>)}
        </TableBody>
      </Table>}
      <Pagination offset={runOffset} count={runs.length} size={20} change={setRunOffset} />
    </Panel>
    {selectedRun && <LearningDetail selectedRun={selectedRun} close={() => setSelectedRun(null)} />}
    {version && <Modal title={`版本 ${version.id}`} close={() => setVersion(null)}>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <section className="flex flex-col gap-1.5 rounded-lg border p-2.5">
          <h3 className="text-sm font-semibold">当前</h3>
          <p className="text-sm whitespace-pre-wrap">{persona.learned}</p>
        </section>
        <section className="flex flex-col gap-1.5 rounded-lg border p-2.5">
          <h3 className="text-sm font-semibold">版本 {version.id}</h3>
          <p className="text-sm whitespace-pre-wrap">{version.text}</p>
        </section>
      </div>
      <small className="text-xs text-muted-foreground">{version.reason} · {date(version.created)}</small>
      <Collapsible className="group/collapsible">
        <CollapsibleTrigger asChild>
          <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
            <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
            学习依据
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="pt-2">
          <pre className={cn(preClass, 'mt-1.5')}>{JSON.stringify(JSON.parse(version.evidence), null, 2)}</pre>
        </CollapsibleContent>
      </Collapsible>
      <div className="flex flex-wrap items-center gap-2"><span className="flex-1" />
        <Button disabled={busy || version.id === persona.active} onClick={() => void action(async () => {
          await api(`/persona/${version.id}/rollback`, 'POST'); dirty.current = false; setVersion(null);
        }, '已回退，相同的修改之后不会被再次自动应用')}>回退到这个版本</Button></div>
    </Modal>}
  </>;
}