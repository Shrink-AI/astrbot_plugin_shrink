import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { useDashboard } from '../shared/dashboard';

type Capability = { name: string; version: number; provider: string; description?: string;
  actions: Record<string, { description?: string; readonly?: boolean; parameters?: unknown }> };

export function PluginCapabilities() {
  const { revision, setError, action, busy } = useDashboard();
  const [catalog, setCatalog] = useState<Capability[]>([]);
  const [selected, setSelected] = useState('');
  const [operation, setOperation] = useState('');
  const [session, setSession] = useState('');
  const [argumentsText, setArgumentsText] = useState('{}');
  const [result, setResult] = useState<unknown>();
  useEffect(() => {
    api<Capability[]>('/operations/plugin-capabilities').then(setCatalog).catch(e => setError(e.message));
  }, [revision]);
  const key = (c: Capability) => `${c.provider}:${c.name}:${c.version}`;
  const capability = catalog.find(c => key(c) === selected);
  const schema = capability?.actions[operation];
  return <section className="panel">
    <h2>插件公开能力</h2>
    <p>从已启用插件实时发现。操作范围使用所填原会话和管理员身份，具体校验由能力提供者执行。</p>
    {!catalog.length && <p>没有已注册的公开能力。</p>}
    <label>能力提供者<select value={selected} onChange={e => {
      setSelected(e.target.value); setOperation(''); setResult(undefined);
    }}><option value="">选择能力</option>{catalog.map(c =>
      <option key={key(c)} value={key(c)}>{c.description || c.name} · {c.provider} · v{c.version}</option>)}</select></label>
    {capability && <>
      <label>操作<select value={operation} onChange={e => setOperation(e.target.value)}>
        <option value="">选择操作</option>{Object.entries(capability.actions).map(([name, spec]) =>
          <option key={name} value={name}>{spec.description || name} · {spec.readonly ? '读取' : '可能写入'}</option>)}
      </select></label>
      <label>原会话 ID<input value={session} onChange={e => setSession(e.target.value)} /></label>
      {schema && <details><summary>参数定义</summary><pre>{JSON.stringify(schema.parameters, null, 2)}</pre></details>}
      <label>参数（JSON 对象）<textarea rows={6} value={argumentsText} onChange={e => setArgumentsText(e.target.value)} /></label>
      <button disabled={busy || !operation || !session.trim()} onClick={() => {
        if (!schema?.readonly && !window.confirm('执行所选插件操作？请确认参数和会话范围。')) return;
        void action(async () => {
          const args = JSON.parse(argumentsText);
          if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('参数必须是 JSON 对象');
          setResult(await api('/operations/plugin-capabilities', 'POST', {
            capability: capability.name, version: capability.version, provider: capability.provider,
            action: operation, session, arguments: args,
          }));
        }, '插件已返回结果');
      }}>调用能力</button>
    </>}
    {result !== undefined && <pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(result, null, 2)}</pre>}
  </section>;
}
