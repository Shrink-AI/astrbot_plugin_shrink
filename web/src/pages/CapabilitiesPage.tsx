import { useEffect, useState } from 'react';
import { ChevronRightIcon } from 'lucide-react';
import { Alert, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Field, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { api } from '@/api/client';
import { useConfirm } from '@/components/confirm-dialog';
import { Empty, PageHead, Panel } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';
import { PluginCapabilities } from './PluginCapabilities';
import { MaintenancePage } from './MaintenancePage';

type Item = Record<string, any>;
const resources: Record<string, string> = {
  plugins: '插件', mcp: 'MCP', skills: 'Skills', subagents: '子代理',
  knowledge: '知识库', models: '模型', reminders: '未来任务',
};
const native = (resource: string, action: string, args: Item = {}, confirmed = false) =>
  api<any>('/operations/native', 'POST', { resource, action, arguments: args, confirmed });
const pretty = (value: unknown) => JSON.stringify(value, null, 2);

export function CapabilitiesPage() {
  const { action, revision, setError, busy, go } = useDashboard();
  const ask = useConfirm();
  const [resource, setResource] = useState('plugins');
  const [data, setData] = useState<any>([]);
  const [editor, setEditor] = useState<Item | null>(null);
  const [code, setCode] = useState('');
  const [output, setOutput] = useState<any>(null);
  const [models, setModels] = useState<Item[]>([]);
  const [tools, setTools] = useState<Item[]>([]);
  const [documents, setDocuments] = useState<Item[]>([]);
  const [selectedKb, setSelectedKb] = useState('');
  const [docPage, setDocPage] = useState(1);
  const [docTotal, setDocTotal] = useState(0);
  const [documentText, setDocumentText] = useState('');
  const [knowledge, setKnowledge] = useState<any>(null);
  useEffect(() => {
    let stopped = false;
    native(resource, 'list').then(v => { if (!stopped) setData(v); })
      .catch(e => { if (!stopped) setError(e.message); });
    return () => { stopped = true; };
  }, [resource, revision]);
  useEffect(() => {
    Promise.all([native('models', 'list'), native('tools', 'list'),
      api('/operations/knowledge')]).then(([m, t, k]) => {
      setModels(Array.isArray(m) ? m : []); setTools(Array.isArray(t) ? t : []); setKnowledge(k);
    }).catch(e => setError(e.message));
  }, [revision]);
  const rows: Item[] = Array.isArray(data) ? data : data?.skills || data?.agents || data?.items || data?.jobs || [];
  const run = (op: string, args: Item, confirmed = false) => void action(async () => {
    const result = await native(resource, op, args, confirmed); setOutput(result);
  }, '操作已完成');
  const remove = async (row: Item) => {
    const name = row.name || row.kb_name || row.id;
    if(!await ask({ title: `删除${resources[resource]}？`, desc: `删除“${name}”？`, destructive: true })) return;
    if (resource === 'subagents') {
      run('update', { ...data, agents: rows.filter(x => x.name !== row.name) });
    } else run('delete', resource === 'knowledge' ? { kb_id: row.kb_id }
      : resource === 'models' ? { id: row.id } : { name: row.name }, true);
  };
  const edit = (row: Item = {}) => void action(async () => {
    const form = { ...row, existing: !!(row.name || row.kb_id || row.id) };
    setEditor(form); setOutput(null);
    if (resource === 'skills') {
      const file = row.name ? await native('skills', 'read', { name: row.name, path: 'SKILL.md' }) : null;
      setCode(file?.content || '---\nname: new-skill\ndescription: 说明何时使用\n---\n# 技能名称\n\n具体步骤。\n');
    } else if (resource === 'mcp') {
      const { name, active, connected, tools, errlogs, ...config } = row;
      setCode(pretty(row.name ? config : { url: '', transport: 'streamable_http' }));
    } else if (resource === 'models') {
      setCode(pretty(row.id ? row : { id: '', model: '', provider_source_id: '', enable: true }));
    }
  }, '');
  const save = () => void action(async () => {
    if (!editor) return;
    const { existing, ...form } = editor;
    if (resource === 'skills') {
      await native('skills', existing ? 'write' : 'create', {
        name: form.name, path: 'SKILL.md', content: code });
    } else if (resource === 'mcp') {
      const config = JSON.parse(code);
      await native('mcp', existing ? 'update' : 'create', existing
        ? { name: form.name, config } : { ...config, name: form.name });
    } else if (resource === 'subagents') {
      if (!form.name?.trim() || !form.provider_id) throw new Error('请填写名称并选择模型');
      const agents = existing ? rows.map(x => x.name === form.name ? form : x) : [...rows, form];
      await native('subagents', 'update', { ...data, main_enable: true, agents });
    } else if (resource === 'knowledge') {
      const fields = ['kb_id', 'kb_name', 'description', 'embedding_provider_id', 'rerank_provider_id',
        'top_k_dense', 'top_k_sparse', 'top_m_final'];
      const args = Object.fromEntries(fields.filter(k => form[k] !== undefined).map(k => [k, form[k]]));
      await native('knowledge', existing ? 'update' : 'create', args);
    } else if (resource === 'models') {
      const config = JSON.parse(code);
      await native('models', existing ? 'update' : 'create', existing ? { id: form.id, config } : config);
    }
    setEditor(null);
  }, '已保存并交给 AstrBot 应用');
  const field = (key: string, label: string, multiline = false) => <Field key={key}>
    <FieldLabel htmlFor={`capability-${key}`}>{label}</FieldLabel>
    {multiline
      ? <Textarea id={`capability-${key}`} rows={6} value={editor?.[key] || ''}
        onChange={e => setEditor({ ...editor, [key]: e.target.value })} />
      : <Input id={`capability-${key}`} value={editor?.[key] || ''} disabled={key === 'name' && !!editor?.existing}
        onChange={e => setEditor({ ...editor, [key]: e.target.value })} />}
  </Field>;
  const modelSelect = (key: string, label: string, capability?: string) => <Field key={key}>
    <FieldLabel htmlFor={`capability-${key}`}>{label}</FieldLabel>
    <Select value={editor?.[key] || 'none-member'} onValueChange={next => setEditor({ ...editor, [key]: next === 'none-member' ? null : next })}>
      <SelectTrigger id={`capability-${key}`} className="w-full">
        <SelectValue placeholder="未选择" />
      </SelectTrigger>
      <SelectContent>
        <SelectGroup>
          <SelectItem value="none-member">未选择</SelectItem>
          {models.filter(m => !capability || m.provider_type === capability).map(m =>
            <SelectItem key={m.id} value={m.id}>{m.id}</SelectItem>)}
        </SelectGroup>
      </SelectContent>
    </Select>
  </Field>;
  const listDocuments = (kbId: string, page = 1) => void action(async () => {
    const result = await native('knowledge', 'documents', { kb_id: kbId, page_size: 30, page });
    setDocPage(page); setDocTotal(result.total || 0); setDocumentText('');
    setSelectedKb(kbId); setDocuments(result.items || result.documents || []); setOutput(null);
  }, '');
  return <>
    <PageHead title="插件与能力" />
    <div className="flex flex-col gap-4">
      <PluginCapabilities />
      {resource === 'subagents' && <MaintenancePage kind="subagent" />}
      <div className="flex flex-wrap items-center gap-2">
        <Select value={resource} onValueChange={next => {
          setResource(next); setEditor(null); setOutput(null); setDocuments([]); setSelectedKb('');
        }}>
          <SelectTrigger className="w-full">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              {Object.entries(resources).map(([k, v]) => <SelectItem key={k} value={k}>{v}</SelectItem>)}
            </SelectGroup>
          </SelectContent>
        </Select>
        {!['plugins', 'reminders'].includes(resource) && <Button onClick={() => edit()}>新增{resources[resource]}</Button>}
        {resource === 'plugins' && <Button onClick={() => go('operations')}>让石英开发或修改插件</Button>}
      </div>
      {resource === 'knowledge' && knowledge && <Panel>
        <div className="flex flex-col gap-3">
          <p className="text-sm">记忆正文：{knowledge.authority === 'astrbot_kb' ? 'AstrBot 知识库' : '迁移中'} · 待同步 {knowledge.pending?.length || 0} 条</p>
          <p className="text-sm">重排：{knowledge.rerank?.status === 'degraded' ? `降级 · ${knowledge.rerank.error}` : knowledge.rerank?.status || '尚未调用'}</p>
          <p className="text-sm">检索：{knowledge.retrieval?.status === 'degraded' ? `使用已校验缓存 · ${knowledge.retrieval.error}` :
            knowledge.retrieval?.status === 'ok' ? '正常' : '尚未调用'} · {knowledge.ready ? '同步服务已就绪' : '正在连接知识库'}</p>
          {!!knowledge.projection_errors?.length && <Collapsible className="group/collapsible text-sm" defaultOpen>
            <CollapsibleTrigger asChild>
              <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
                <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
                部分知识库正文暂未读回
              </Button>
            </CollapsibleTrigger>
            <CollapsibleContent className="pt-2">
              <pre className="mt-2 max-h-80 overflow-auto rounded-lg border bg-muted/40 p-2.5 font-mono text-xs whitespace-pre-wrap break-words">{knowledge.projection_errors.join('\n')}</pre>
            </CollapsibleContent>
          </Collapsible>}
          {knowledge.error && <Alert variant="destructive"><AlertTitle>{knowledge.error}</AlertTitle></Alert>}
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => void action(() => api('/operations/knowledge/sync', 'POST'), '已重试同步')}>重试待处理条目</Button>
            <Button variant="ghost" onClick={() => go('memory')}>整理、确认和修改记忆</Button>
          </div>
        </div>
      </Panel>}
      <Panel>
        {!rows.length && <Empty text="暂无资源。" />}
        {!!rows.length && <div className="flex flex-col">
          {rows.map((row, i) => <article key={row.name || row.id || row.kb_id || i}
            className="flex flex-col gap-2 border-b py-3 first:pt-0 last:border-b-0 last:pb-0">
            <strong className="break-words">{row.display_name || row.kb_name || row.name || row.id}</strong>
            <p className="text-sm text-muted-foreground break-words">{row.desc || row.description || row.public_description || row.model || ''}</p>
            <div className="flex flex-wrap items-center gap-2">
              {resource === 'plugins' && <>
                <span className="text-xs text-muted-foreground">{row.activated ? '已启用' : '已停用'} · {row.version}</span>
                <Button variant="outline" size="sm" disabled={busy || row.name === 'astrbot_plugin_shrink'} onClick={() => run(row.activated ? 'disable' : 'enable', { name: row.name })}>
                  {row.activated ? '停用' : '启用'}</Button>
                <Button variant="outline" size="sm" disabled={busy || row.name === 'astrbot_plugin_shrink'} onClick={() => run('reload', { name: row.name })}>重载</Button>
              </>}
              {['skills', 'mcp'].includes(resource) && <>
                <span className="text-xs text-muted-foreground">{row.active ? '已启用' : '已停用'}{resource === 'mcp' ? (row.connected ? ' · 已连接' : ' · 未连接') : ''}</span>
                <Button variant="outline" size="sm" disabled={busy || row.readonly} onClick={() => run('update', { name: row.name, active: !row.active })}>{row.active ? '停用' : '启用'}</Button>
                {resource === 'mcp' && <Button variant="outline" size="sm" onClick={() => {
                  const { name, connected, tools, errlogs, ...config } = row; run('test', { name, config });
                }}>测试连接</Button>}
              </>}
              {resource === 'subagents' && <>
                <span className="text-xs text-muted-foreground">{row.provider_id} · {row.enabled ? '已启用' : '已停用'}</span>
                <Button variant="outline" size="sm" onClick={() => run('update', { ...data, agents: rows.map(x => x.name === row.name ? { ...x, enabled: !x.enabled } : x) })}>
                  {row.enabled ? '停用' : '启用'}</Button>
              </>}
              {resource === 'knowledge' && <>
                <span className="text-xs text-muted-foreground">{row.doc_count} 篇文档 · {row.embedding_provider_id}</span>
                <Button variant="outline" size="sm" onClick={() => listDocuments(row.kb_id)}>查看文档</Button>
              </>}
              {resource === 'models' && <Button variant="outline" size="sm" onClick={() => run('test', { id: row.id })}>测试连接</Button>}
              {!['plugins', 'reminders'].includes(resource) && <>
                <Button variant="outline" size="sm" disabled={busy || row.readonly} onClick={() => edit(row)}>编辑</Button>
                <Button variant="ghost" size="sm" disabled={busy || row.readonly} onClick={() => void remove(row)}>删除</Button>
              </>}
              {resource === 'reminders' && <span className="text-xs text-muted-foreground">{row.run_at || row.cron_expression} · {row.last_error || ''}</span>}
            </div>
          </article>)}
        </div>}
      </Panel>
      {selectedKb && <Panel title="知识库文档">
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-muted-foreground">第 {docPage} 页 · 共 {docTotal} 篇</span>
            <Button variant="outline" size="sm" disabled={docPage <= 1} onClick={() => listDocuments(selectedKb, docPage - 1)}>上一页</Button>
            <Button variant="outline" size="sm" disabled={docPage * 30 >= docTotal} onClick={() => listDocuments(selectedKb, docPage + 1)}>下一页</Button>
          </div>
          <div className="flex flex-col">
            {documents.map(d => <article key={d.doc_id} className="flex flex-wrap items-center gap-2 border-b py-2 last:border-b-0">
              <strong className="min-w-0 break-words">{d.doc_name}</strong>
              <span className="flex-1" />
              <Button variant="outline" size="sm" onClick={() => void action(async () => {
                const result = await native('knowledge', 'chunks', { kb_id: selectedKb, doc_id: d.doc_id, page_size: 100 });
                setDocumentText(d.doc_name + '\n\n' + (result.items || result.chunks || []).map((x: Item) => x.content || x.text || '').join('\n\n'));
              }, '')}>阅读正文</Button>
              <Button variant="ghost" size="sm" onClick={async () => {
                if(!await ask({ title: '删除这份文档？', desc: `删除文档“${d.doc_name}”？关联记忆也会同步删除。`, destructive: true })) return;
                void action(async () => {
                  await native('knowledge', 'delete_document', { kb_id: selectedKb, doc_id: d.doc_id }, true);
                  setDocuments(documents.filter(x => x.doc_id !== d.doc_id));
                }, '已提交删除');
              }}>删除</Button>
            </article>)}
          </div>
          {documentText && <pre className="max-h-80 overflow-auto rounded-lg border bg-muted/40 p-2.5 font-mono text-xs whitespace-pre-wrap break-words">{documentText}</pre>}
        </div>
      </Panel>}
      {editor && <Panel title={`${editor.existing ? '编辑' : '新增'}${resources[resource]}`}>
        <div className="flex flex-col gap-3">
          {['skills', 'mcp', 'subagents'].includes(resource) && field('name', '名称')}
          {resource === 'subagents' && <>
            {modelSelect('provider_id', '使用模型', 'chat_completion')}
            {field('public_description', '交给它处理哪些任务', true)}
            {field('system_prompt', '子代理工作说明', true)}
            <Field orientation="horizontal">
              <Checkbox id="subagent-enabled" checked={editor.enabled ?? true}
                onCheckedChange={value => setEditor({ ...editor, enabled: value === true })} />
              <FieldLabel htmlFor="subagent-enabled">启用</FieldLabel>
            </Field>
            <Collapsible className="group/collapsible text-sm">
              <CollapsibleTrigger asChild>
                <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
                  <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
                  允许使用的工具（{editor.tools?.length || 0}）
                </Button>
              </CollapsibleTrigger>
              <CollapsibleContent className="pt-2">
                <div className="mt-2 flex flex-col gap-2">
                  {tools.map(t => <Field key={t.name} orientation="horizontal">
                    <Checkbox id={`subagent-tool-${t.name}`} checked={(editor.tools || []).includes(t.name)}
                      onCheckedChange={value => setEditor({ ...editor, tools: value === true ? [...(editor.tools || []), t.name]
                        : (editor.tools || []).filter((name: string) => name !== t.name) })} />
                    <FieldLabel htmlFor={`subagent-tool-${t.name}`}>{t.name}</FieldLabel>
                  </Field>)}
                </div>
              </CollapsibleContent>
            </Collapsible>
          </>}
          {resource === 'knowledge' && <>
            {field('kb_name', '知识库名称')}{field('description', '用途', true)}
            {modelSelect('embedding_provider_id', '向量模型', 'embedding')}
            {modelSelect('rerank_provider_id', '重排模型（可选）', 'rerank')}
          </>}
          {['skills', 'mcp', 'models'].includes(resource) && <Field>
            <FieldLabel htmlFor="capability-code">{resource === 'skills' ? 'SKILL.md 内容' : '连接配置（JSON）'}</FieldLabel>
            <Textarea id="capability-code" rows={16} className="min-h-72 font-mono text-xs" value={code}
              onChange={e => setCode(e.target.value)} spellCheck={false} />
          </Field>}
          <div className="flex flex-wrap items-center gap-2">
            <Button disabled={busy} onClick={save}>保存</Button>
            <Button variant="ghost" onClick={() => setEditor(null)}>取消</Button>
          </div>
        </div>
      </Panel>}
      {output !== null && <Panel title="操作结果">
        <pre className="max-h-80 overflow-auto rounded-lg border bg-muted/40 p-2.5 font-mono text-xs whitespace-pre-wrap break-words">{typeof output === 'string' ? output : pretty(output)}</pre>
      </Panel>}
    </div>
  </>;
}