import { useEffect, useState } from 'react';
import { ChevronRightIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible';
import { Field, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { api } from '@/api/client';
import { useConfirm } from '@/components/confirm-dialog';
import { Empty, Panel } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';

type Capability = { name: string; version: number; provider: string; description?: string;
  actions: Record<string, { description?: string; readonly?: boolean; parameters?: unknown }> };

export function PluginCapabilities() {
  const { revision, setError, action, busy } = useDashboard();
  const ask = useConfirm();
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
  return <Panel title="插件公开能力">
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground">从已启用插件实时发现。操作范围使用所填原会话和管理员身份，具体校验由能力提供者执行。</p>
      {!catalog.length && <Empty text="没有已注册的公开能力。" />}
      <Field>
        <FieldLabel htmlFor="plugin-capability">能力提供者</FieldLabel>
        <Select value={selected || 'none-capability'} onValueChange={next => {
          setSelected(next === 'none-capability' ? '' : next); setOperation(''); setResult(undefined);
        }}>
          <SelectTrigger id="plugin-capability" className="w-full">
            <SelectValue placeholder="选择能力" />
          </SelectTrigger>
          <SelectContent>
            <SelectGroup>
              <SelectItem value="none-capability">选择能力</SelectItem>
              {catalog.map(c => <SelectItem key={key(c)} value={key(c)}>
                {c.description || c.name} · {c.provider} · v{c.version}
              </SelectItem>)}
            </SelectGroup>
          </SelectContent>
        </Select>
      </Field>
      {capability && <div className="flex flex-col gap-3">
        <Field>
          <FieldLabel htmlFor="plugin-operation">操作</FieldLabel>
          <Select value={operation || 'none-operation'} onValueChange={next => setOperation(next === 'none-operation' ? '' : next)}>
            <SelectTrigger id="plugin-operation" className="w-full">
              <SelectValue placeholder="选择操作" />
            </SelectTrigger>
            <SelectContent>
              <SelectGroup>
                <SelectItem value="none-operation">选择操作</SelectItem>
                {Object.entries(capability.actions).map(([name, spec]) => <SelectItem key={name} value={name}>
                  {spec.description || name} · {spec.readonly ? '读取' : '可能写入'}
                </SelectItem>)}
              </SelectGroup>
            </SelectContent>
          </Select>
        </Field>
        <Field>
          <FieldLabel htmlFor="plugin-session">原会话 ID</FieldLabel>
          <Input id="plugin-session" value={session} onChange={e => setSession(e.target.value)} />
        </Field>
        {schema && <Collapsible className="group/collapsible text-sm">
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm" className="-ms-2 h-8 gap-1.5 px-2 font-normal text-muted-foreground">
              <ChevronRightIcon className="transition-transform group-data-[state=open]/collapsible:rotate-90" />
              参数定义
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-2">
            <pre className="mt-2 max-h-80 overflow-auto rounded-lg border bg-muted/40 p-2.5 font-mono text-xs whitespace-pre-wrap break-words">{JSON.stringify(schema.parameters, null, 2)}</pre>
          </CollapsibleContent>
        </Collapsible>}
        <Field>
          <FieldLabel htmlFor="plugin-arguments">参数（JSON 对象）</FieldLabel>
          <Textarea id="plugin-arguments" rows={6} className="font-mono text-xs" value={argumentsText} onChange={e => setArgumentsText(e.target.value)} />
        </Field>
        <div className="flex flex-wrap items-center gap-2">
          <Button disabled={busy || !operation || !session.trim()} onClick={async () => {
            if(!schema?.readonly && !await ask({ title: '执行插件操作？', desc: '执行所选插件操作？请确认参数和会话范围。' })) return;
            void action(async () => {
              const args = JSON.parse(argumentsText);
              if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('参数必须是 JSON 对象');
              setResult(await api('/operations/plugin-capabilities', 'POST', {
                capability: capability.name, version: capability.version, provider: capability.provider,
                action: operation, session, arguments: args,
              }));
            }, '插件已返回结果');
          }}>调用能力</Button>
        </div>
      </div>}
      {result !== undefined && <pre className="max-h-80 overflow-auto rounded-lg border bg-muted/40 p-2.5 font-mono text-xs whitespace-pre-wrap break-words">{JSON.stringify(result, null, 2)}</pre>}
    </div>
  </Panel>;
}