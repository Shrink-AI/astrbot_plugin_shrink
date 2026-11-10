import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { Empty, PageHead } from '../shared/components';
import { useDashboard } from '../shared/dashboard';
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
  const remove = (row: Item) => {
    const name = row.name || row.kb_name || row.id;
    if (!window.confirm(`删除“${name}”？`)) return;
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
  const field = (key: string, label: string, multiline = false) => <label>{label}
    {multiline ? <textarea rows={6} value={editor?.[key] || ''} onChange={e => setEditor({ ...editor, [key]: e.target.value })} />
      : <input value={editor?.[key] || ''} disabled={key === 'name' && !!editor?.existing}
        onChange={e => setEditor({ ...editor, [key]: e.target.value })} />}
  </label>;
  const modelSelect = (key: string, label: string, capability?: string) => <label>{label}
    <select value={editor?.[key] || ''} onChange={e => setEditor({ ...editor, [key]: e.target.value || null })}>
      <option value="">未选择</option>{models.filter(m => !capability || m.provider_type === capability).map(m =>
        <option key={m.id} value={m.id}>{m.id}</option>)}
    </select>
  </label>;
  const listDocuments = (kbId: string, page = 1) => void action(async () => {
    const result = await native('knowledge', 'documents', { kb_id: kbId, page_size: 30, page });
    setDocPage(page); setDocTotal(result.total || 0); setDocumentText('');
    setSelectedKb(kbId); setDocuments(result.items || result.documents || []); setOutput(null);
  }, '');
  return <>
    <PageHead title="插件与能力" />
    <PluginCapabilities />
    {resource === 'subagents' && <MaintenancePage kind="subagent" />}
    <div className="toolbar">
      <select value={resource} onChange={e => {
        setResource(e.target.value); setEditor(null); setOutput(null); setDocuments([]); setSelectedKb('');
      }}>{Object.entries(resources).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
      {!['plugins', 'reminders'].includes(resource) && <button onClick={() => edit()}>新增{resources[resource]}</button>}
      {resource === 'plugins' && <button onClick={() => go('operations')}>让石英开发或修改插件</button>}
    </div>
    {resource === 'knowledge' && knowledge && <section className="panel">
      <p>记忆正文：{knowledge.authority === 'astrbot_kb' ? 'AstrBot 知识库' : '迁移中'} · 待同步 {knowledge.pending?.length || 0} 条</p>
      <p>重排：{knowledge.rerank?.status === 'degraded' ? `降级 · ${knowledge.rerank.error}` : knowledge.rerank?.status || '尚未调用'}</p>
      <p>检索：{knowledge.retrieval?.status === 'degraded' ? `使用已校验缓存 · ${knowledge.retrieval.error}` :
        knowledge.retrieval?.status === 'ok' ? '正常' : '尚未调用'} · {knowledge.ready ? '同步服务已就绪' : '正在连接知识库'}</p>
      {!!knowledge.projection_errors?.length && <details open><summary>部分知识库正文暂未读回</summary>
        <pre style={{ whiteSpace: 'pre-wrap' }}>{knowledge.projection_errors.join('\n')}</pre></details>}
      {knowledge.error && <p role="alert">{knowledge.error}</p>}
      <button onClick={() => void action(() => api('/operations/knowledge/sync', 'POST'), '已重试同步')}>重试待处理条目</button>
      <button className="ghost" onClick={() => go('memory')}>整理、确认和修改记忆</button>
    </section>}
    <section className="panel">
      {!rows.length && <Empty text="暂无资源。" />}
      {rows.map((row, i) => <article key={row.name || row.id || row.kb_id || i}
        style={{ padding: '14px 0', borderBottom: '1px solid var(--border)' }}>
        <strong>{row.display_name || row.kb_name || row.name || row.id}</strong>
        <p>{row.desc || row.description || row.public_description || row.model || ''}</p>
        <div className="toolbar">
          {resource === 'plugins' && <>
            <span>{row.activated ? '已启用' : '已停用'} · {row.version}</span>
            <button disabled={busy || row.name === 'astrbot_plugin_shrink'} onClick={() => run(row.activated ? 'disable' : 'enable', { name: row.name })}>
              {row.activated ? '停用' : '启用'}</button>
            <button disabled={busy || row.name === 'astrbot_plugin_shrink'} onClick={() => run('reload', { name: row.name })}>重载</button>
          </>}
          {['skills', 'mcp'].includes(resource) && <>
            <span>{row.active ? '已启用' : '已停用'}{resource === 'mcp' ? (row.connected ? ' · 已连接' : ' · 未连接') : ''}</span>
            <button disabled={busy || row.readonly} onClick={() => run('update', { name: row.name, active: !row.active })}>{row.active ? '停用' : '启用'}</button>
            {resource === 'mcp' && <button onClick={() => {
              const { name, connected, tools, errlogs, ...config } = row; run('test', { name, config });
            }}>测试连接</button>}
          </>}
          {resource === 'subagents' && <>
            <span>{row.provider_id} · {row.enabled ? '已启用' : '已停用'}</span>
            <button onClick={() => run('update', { ...data, agents: rows.map(x => x.name === row.name ? { ...x, enabled: !x.enabled } : x) })}>
              {row.enabled ? '停用' : '启用'}</button>
          </>}
          {resource === 'knowledge' && <>
            <span>{row.doc_count} 篇文档 · {row.embedding_provider_id}</span>
            <button onClick={() => listDocuments(row.kb_id)}>查看文档</button>
          </>}
          {resource === 'models' && <button onClick={() => run('test', { id: row.id })}>测试连接</button>}
          {!['plugins', 'reminders'].includes(resource) && <>
            <button disabled={busy || row.readonly} onClick={() => edit(row)}>编辑</button>
            <button disabled={busy || row.readonly} className="ghost" onClick={() => remove(row)}>删除</button>
          </>}
          {resource === 'reminders' && <span>{row.run_at || row.cron_expression} · {row.last_error || ''}</span>}
        </div>
      </article>)}
    </section>
    {selectedKb && <section className="panel"><h2>知识库文档</h2>
      <div className="toolbar"><span>第 {docPage} 页 · 共 {docTotal} 篇</span>
        <button disabled={docPage <= 1} onClick={() => listDocuments(selectedKb, docPage - 1)}>上一页</button>
        <button disabled={docPage * 30 >= docTotal} onClick={() => listDocuments(selectedKb, docPage + 1)}>下一页</button>
      </div>
      {documents.map(d => <article key={d.doc_id}>
        <strong>{d.doc_name}</strong>
        <button onClick={() => void action(async () => {
          const result = await native('knowledge', 'chunks', { kb_id: selectedKb, doc_id: d.doc_id, page_size: 100 });
          setDocumentText(d.doc_name + '\n\n' + (result.items || result.chunks || []).map((x: Item) => x.content || x.text || '').join('\n\n'));
        }, '')}>阅读正文</button>
        <button className="ghost" onClick={() => {
          if (window.confirm(`删除文档“${d.doc_name}”？关联记忆也会同步删除。`))
            void action(async () => {
              await native('knowledge', 'delete_document', { kb_id: selectedKb, doc_id: d.doc_id }, true);
              setDocuments(documents.filter(x => x.doc_id !== d.doc_id));
            }, '已提交删除');
        }}>删除</button>
      </article>)}
      {documentText && <pre style={{ whiteSpace: 'pre-wrap' }}>{documentText}</pre>}
    </section>}
    {editor && <section className="panel">
      <h2>{editor.existing ? '编辑' : '新增'}{resources[resource]}</h2>
      {['skills', 'mcp', 'subagents'].includes(resource) && field('name', '名称')}
      {resource === 'subagents' && <>
        {modelSelect('provider_id', '使用模型', 'chat_completion')}
        {field('public_description', '交给它处理哪些任务', true)}
        {field('system_prompt', '子代理工作说明', true)}
        <label><input type="checkbox" checked={editor.enabled ?? true}
          onChange={e => setEditor({ ...editor, enabled: e.target.checked })} />启用</label>
        <details><summary>允许使用的工具（{editor.tools?.length || 0}）</summary>
          {tools.map(t => <label key={t.name}><input type="checkbox" checked={(editor.tools || []).includes(t.name)}
            onChange={e => setEditor({ ...editor, tools: e.target.checked ? [...(editor.tools || []), t.name]
              : (editor.tools || []).filter((name: string) => name !== t.name) })} />{t.name}</label>)}
        </details>
      </>}
      {resource === 'knowledge' && <>
        {field('kb_name', '知识库名称')}{field('description', '用途', true)}
        {modelSelect('embedding_provider_id', '向量模型', 'embedding')}
        {modelSelect('rerank_provider_id', '重排模型（可选）', 'rerank')}
      </>}
      {['skills', 'mcp', 'models'].includes(resource) && <label>
        {resource === 'skills' ? 'SKILL.md 内容' : '连接配置（JSON）'}
        <textarea rows={16} value={code} onChange={e => setCode(e.target.value)} spellCheck={false} />
      </label>}
      <div className="toolbar"><button disabled={busy} onClick={save}>保存</button>
        <button className="ghost" onClick={() => setEditor(null)}>取消</button></div>
    </section>}
    {output !== null && <section className="panel"><h2>操作结果</h2>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{typeof output === 'string' ? output : pretty(output)}</pre>
    </section>}
  </>;
}
