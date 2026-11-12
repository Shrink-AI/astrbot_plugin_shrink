import { useState } from 'react';
import type { ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { api } from '@/api/client';
import type { Settings } from '@/api/types';
import { PageHead } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';

type NumberKey = { [K in keyof Settings]: Settings[K] extends number ? K : never }[keyof Settings];
type BoolKey = { [K in keyof Settings]: Settings[K] extends boolean ? K : never }[keyof Settings];

function Section({ title, desc, children }: { title: string; desc: string; children: ReactNode }) {
  return <section className="flex w-full flex-col gap-4">
    <div className="flex flex-col gap-1">
      <h2 className="text-lg font-medium">{title}</h2>
      <p className="text-sm text-muted-foreground">{desc}</p>
    </div>
    <Separator />
    <div className="flex flex-col gap-6">{children}</div>
  </section>;
}

export function SettingsPage() {
  const { settings: saved, providers, busy, action, signOut } = useDashboard();
  const [settings, setSettings] = useState<Settings>(saved);
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const set = <K extends keyof Settings>(name: K, value: Settings[K]) => setSettings(s => ({ ...s, [name]: value }));
  const num = (key: NumberKey, label: string, min: number, max: number, step = 1, hint?: string) =>
    <Field key={key} orientation="horizontal">
      {hint
        ? <FieldContent>
          <FieldLabel htmlFor={`setting-${key}`}>{label}</FieldLabel>
          <FieldDescription>{hint}</FieldDescription>
        </FieldContent>
        : <FieldLabel htmlFor={`setting-${key}`}>{label}</FieldLabel>}
      <Input id={`setting-${key}`} type="number" min={min} max={max} step={step}
        className="w-24 shrink-0" value={settings[key] ?? 0} onChange={e => set(key, Number(e.target.value))} />
    </Field>;
  const flag = (key: BoolKey, label: string) =>
    <Field key={key} orientation="horizontal">
      <FieldLabel htmlFor={`setting-${key}`}>{label}</FieldLabel>
      <Switch id={`setting-${key}`} checked={!!settings[key]}
        onCheckedChange={value => set(key, value === true)} />
    </Field>;
  const select = (key: 'model_provider' | 'embedding_provider', label: string, options: string[]) =>
    <Field key={key} orientation="horizontal">
      <FieldLabel htmlFor={`setting-${key}`}>{label}</FieldLabel>
      <Select value={settings[key]} onValueChange={next => set(key, next)}>
        <SelectTrigger id={`setting-${key}`} className="w-60 shrink-0">
          <SelectValue placeholder="选择模型" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {options.map(p => <SelectItem key={p} value={p}>{p}</SelectItem>)}
          </SelectGroup>
        </SelectContent>
      </Select>
    </Field>;
  const save = () => void action(() => api('/settings', 'PUT', { ...settings, collect_groups: settings.collect_groups.map(x => x.trim()).filter(Boolean) }));
  return <>
    <PageHead title="设置" description="调整石英在群里的说话方式、记忆整理、工具、学习和表情行为。" />
    <div className="flex w-full max-w-2xl flex-col gap-8">
      <form className="flex flex-col gap-8" noValidate onSubmit={e => { e.preventDefault(); save(); }}>
        <Section title="聊天" desc="决定石英什么时候接话、一次说几句，以及群聊上下文带多少。">
          <FieldGroup className="flex flex-col gap-5">
            {select('model_provider', '对话模型', providers.chat)}
            {select('embedding_provider', '向量模型', providers.embedding)}
            {num('spontaneous_probability', '主动插话概率', 0, 1, 0.01, '话题和石英有关时概率加倍')}
            {num('spontaneous_limit', '每群每 5 分钟最多主动插话', 0, 10)}
            {num('engaged_seconds', '接着聊的时间窗口（秒）', 0, 1800, 10, '石英回复某人后，这段时间内 TA 不 @ 也算在跟石英说话')}
            {num('stale_seconds', '插话过期时间（秒）', 10, 3600, 5, '超过这个时间且群里已聊了 3 条以上，就不再插话')}
            {num('split_probability', '偶尔分两条发的概率', 0, 1, 0.01)}
            {num('context_budget', '群聊上下文字数预算', 1000, 20000, 100)}
            {flag('debounce_enabled', '合并同一个人的连续短消息')}
            {num('debounce_seconds', '等下一条消息（秒）', 0.1, 5, 0.1)}
            {num('debounce_max_seconds', '连续发言最多等待（秒）', 1, 15, 0.5)}
          </FieldGroup>
        </Section>
        <Section title="记忆与整理" desc="决定群聊什么时候被整理，以及记忆怎么存下来、带几条回去。">
          <FieldGroup className="flex flex-col gap-5">
            {flag('digest_enabled', '自动整理群聊（摘要、话题、记忆、称呼、黑话）')}
            {flag('verify_enabled', '整理后二次核对依据')}
            {flag('glossary_search', '联网搜索黑话')}
            {num('glossary_per_run', '每次整理最多查几个黑话', 0, 10)}
            {num('recall_count', '每次最多带几条记忆', 1, 20)}
            {num('memory_budget', '记忆字数预算', 200, 4000, 50)}
          </FieldGroup>
        </Section>
        <Section title="工具与经验" desc="决定石英能不能用工具办事，以及经验记在哪里。">
          <FieldGroup className="flex flex-col gap-5">
            {flag('skills_enabled', '用工具办事后复盘，积累并修正经验')}
            <Field>
              <FieldContent>
                <FieldLabel htmlFor="setting-sandbox_url">Python 沙箱地址</FieldLabel>
                <FieldDescription>shrink_run_python 工具调用的沙箱服务</FieldDescription>
              </FieldContent>
              <Input id="setting-sandbox_url" value={settings.sandbox_url}
                onChange={e => set('sandbox_url', e.target.value)} />
            </Field>
          </FieldGroup>
        </Section>
        <Section title="学习" desc="决定石英多久学一次说话习惯，每次拿多少条新消息。">
          <FieldGroup className="flex flex-col gap-5">
            {flag('learning_enabled', '允许自动学习说话习惯')}
            {num('learning_hours', '学习间隔（小时）', 0.25, 168, 0.25)}
            {num('learning_min_messages', '至少攒够多少条新消息', 10, 1000)}
            {num('learning_batch_size', '每次最多学习多少条', 10, 1000)}
          </FieldGroup>
        </Section>
        <Section title="表情" desc="决定自动收集哪些群的表情，以及石英发表情的间隔。">
          <FieldGroup className="flex flex-col gap-5">
            {flag('collect_enabled', '自动收集二次元表情')}
            {flag('meme_enabled', '允许石英发表情')}
            <Field>
              <FieldLabel htmlFor="setting-collect_groups">收集表情的群（每行一个群号）</FieldLabel>
              <Textarea id="setting-collect_groups" rows={3} value={settings.collect_groups.join('\n')}
                onChange={e => set('collect_groups', e.target.value.split('\n'))} />
            </Field>
            {num('meme_cooldown', '同一个聊天发表情的间隔（秒）', 0, 86400)}
            {num('meme_repeat_cooldown', '同一张表情的重复间隔（秒）', 0, 604800)}
          </FieldGroup>
        </Section>
        <div className="flex items-center gap-2">
          <Button type="submit" disabled={busy}>保存设置</Button>
        </div>
      </form>
      <Section title="管理员密码" desc="修改后台登录密码，改完需要重新登录。">
        <form className="flex flex-col gap-6" onSubmit={e => {
          e.preventDefault(); void action(async () => {
            await api('/password', 'POST', { current_password: oldPassword, new_password: newPassword });
            signOut(); setOldPassword(''); setNewPassword('');
          }, '密码已修改，请重新登录');
        }}>
          <FieldGroup className="flex flex-col gap-5">
            <Field>
              <FieldLabel htmlFor="current-password">当前密码</FieldLabel>
              <Input id="current-password" type="password" autoComplete="current-password" required
                value={oldPassword} onChange={e => setOldPassword(e.target.value)} />
            </Field>
            <Field>
              <FieldLabel htmlFor="new-password">新密码（至少 15 个字符）</FieldLabel>
              <Input id="new-password" type="password" autoComplete="new-password" minLength={15} maxLength={128} required
                value={newPassword} onChange={e => setNewPassword(e.target.value)} />
            </Field>
          </FieldGroup>
          <div className="flex flex-wrap items-center gap-2">
            <p className="text-xs text-muted-foreground">修改后所有登录都会失效。</p>
            <span className="flex-1" />
            <Button type="submit" disabled={busy}>修改密码</Button>
          </div>
        </form>
      </Section>
    </div>
  </>;
}