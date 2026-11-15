import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { Button } from '@/components/ui/button';
import { Field, FieldContent, FieldDescription, FieldGroup, FieldLabel } from '@/components/ui/field';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Separator } from '@/components/ui/separator';
import { Switch } from '@/components/ui/switch';
import { Textarea } from '@/components/ui/textarea';
import { api } from '@/api/client';
import type { Configuration, Settings } from '@/api/types';
import { PageHead } from '@/shared/components';
import { useDashboard } from '@/shared/dashboard';

type NumberKey = { [K in keyof Settings]: Settings[K] extends number ? K : never }[keyof Settings];
type BoolKey = { [K in keyof Settings]: Settings[K] extends boolean ? K : never }[keyof Settings];
const fractionalSettings = new Set<NumberKey>([
  'spontaneous_probability', 'split_probability', 'history_retain_ratio',
  'debounce_seconds', 'debounce_max_seconds', 'decision_timeout',
  'foreground_retrieval_timeout', 'foreground_rerank_timeout', 'foreground_grilling_timeout',
  'notification_max_delay', 'learning_hours',
]);

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
  const { settings: saved, providers, model_profiles = [], model_status = {}, busy, action, signOut, setError } = useDashboard();
  const [settings, setSettings] = useState<Settings>(saved);
  const [profile, setProfile] = useState('default');
  const [fallbackToAdd, setFallbackToAdd] = useState('');
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  useEffect(() => {
    let cancelled = false;
    api<Configuration>('/settings').then(current => {
      if (!cancelled) setSettings(current.settings);
    }).catch(error => { if (!cancelled) setError(error.message); });
    return () => { cancelled = true; };
  }, []);
  const set = <K extends keyof Settings>(name: K, value: Settings[K]) => setSettings(s => ({ ...s, [name]: value }));
  const num = (key: NumberKey, label: string, min: number, max: number, step = 1, hint?: string, exclusiveMin = false) =>
    <Field key={key} orientation="horizontal">
      {hint
        ? <FieldContent>
          <FieldLabel htmlFor={`setting-${key}`}>{label}</FieldLabel>
          <FieldDescription>{hint}</FieldDescription>
        </FieldContent>
        : <FieldLabel htmlFor={`setting-${key}`}>{label}</FieldLabel>}
      <Input id={`setting-${key}`} type="number" min={min} max={max} step={step} required
        data-exclusive-min={exclusiveMin} data-integer={!fractionalSettings.has(key)}
        className="w-24 shrink-0" value={settings[key] ?? 0} onChange={e => set(key, Number(e.target.value))} />
    </Field>;
  const flag = (key: BoolKey, label: string) =>
    <Field key={key} orientation="horizontal">
      <FieldLabel htmlFor={`setting-${key}`}>{label}</FieldLabel>
      <Switch id={`setting-${key}`} checked={!!settings[key]}
        onCheckedChange={value => set(key, value === true)} />
    </Field>;
  const hostEditable = model_profiles.find(item => item.id === profile)?.editable ?? true;
  const hostFields = ['model_provider', 'compression_provider', 'image_caption_provider'];
  const select = (key: 'model_provider' | 'embedding_provider' | 'decision_provider' | 'learning_provider'
    | 'summary_provider' | 'compression_provider' | 'image_caption_provider',
    label: string, options: string[], hint?: string, follow?: string) =>
    <Field key={key} orientation="horizontal">
      <FieldContent>
        <FieldLabel htmlFor={`setting-${key}`}>{label}</FieldLabel>
        {hint && <FieldDescription>{hint}</FieldDescription>}
      </FieldContent>
      <Select value={settings[key] || 'follow-model'} disabled={hostFields.includes(key) && !hostEditable}
        onValueChange={next => set(key, next === 'follow-model' ? '' : next)}>
        <SelectTrigger id={`setting-${key}`} className="w-60 shrink-0">
          <SelectValue placeholder="选择模型" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {follow && <SelectItem value="follow-model">{follow}</SelectItem>}
            {[...new Set([settings[key], ...options])].filter(Boolean).map(p =>
              <SelectItem key={p} value={p}>{p}{!options.includes(p) && '（不可用）'}</SelectItem>)}
          </SelectGroup>
        </SelectContent>
      </Select>
    </Field>;
  const groups = (values: string[]) => [...new Set(values.map(x => x.trim()).filter(Boolean))];
  const save = (form: HTMLFormElement) => void action(async () => {
    for (const input of form.querySelectorAll<HTMLInputElement>('input[type="number"]')) {
      const value = input.valueAsNumber;
      const min = Number(input.min);
      const max = Number(input.max);
      const exclusive = input.dataset.exclusiveMin === 'true';
      const integer = input.dataset.integer === 'true';
      if (!Number.isFinite(value) || (exclusive ? value <= min : value < min) || value > max
        || (integer && !Number.isInteger(value))) {
        const label = form.querySelector(`label[for="${input.id}"]`)?.textContent;
        const range = exclusive ? `大于 ${min} 且不超过 ${max} ` : ` ${min}～${max} 之间`;
        throw new Error(`${label}：请输入${range}的${integer ? '整数' : '数值'}`);
      }
    }
    if (settings.learning_batch_size < settings.learning_min_messages) throw new Error('每次学习的条数不能少于攒够的条数');
    await api(`/settings?profile=${encodeURIComponent(profile)}`, 'PUT', {
      ...settings, collect_groups: groups(settings.collect_groups), group_blacklist: groups(settings.group_blacklist),
    });
  });
  return <>
    <PageHead title="设置" description="调整石英在群里的说话方式、记忆整理、工具、学习和表情行为。" />
    <div className="flex w-full max-w-2xl flex-col gap-8">
      <form noValidate className="flex flex-col gap-8" onSubmit={e => { e.preventDefault(); save(e.currentTarget); }}>
        <Section title="聊天" desc="决定石英什么时候接话、一次说几句，以及群聊上下文带多少。">
          <FieldGroup className="flex flex-col gap-5">
            {!!model_profiles.length && <Field orientation="horizontal">
              <FieldLabel htmlFor="model-profile">模型使用的配置</FieldLabel>
              <Select value={profile} onValueChange={async next => {
                try {
                  const current = await api<Configuration>(`/settings?profile=${encodeURIComponent(next)}`);
                  setSettings(previous => ({ ...previous, model_provider: current.settings.model_provider,
                    fallback_providers: current.settings.fallback_providers,
                    compression_provider: current.settings.compression_provider,
                    image_caption_provider: current.settings.image_caption_provider }));
                  setProfile(next);
                } catch (error) { setError((error as Error).message); }
              }}>
                <SelectTrigger id="model-profile" className="w-60"><SelectValue /></SelectTrigger>
                <SelectContent><SelectGroup>{model_profiles.map(item =>
                  <SelectItem key={item.id} value={item.id}>{item.name}{!item.editable && '（外部对话运行器）'}</SelectItem>)}
                </SelectGroup></SelectContent>
              </Select>
            </Field>}
            {select('model_provider', '对话模型', providers.chat, '与 AstrBot 的主模型设置是同一项。', '由 AstrBot 自动选择')}
            <Field>
              <FieldContent>
                <FieldLabel htmlFor="setting-fallback_providers">备用模型</FieldLabel>
                <FieldDescription>与 AstrBot 的备用模型设置是同一项，按从上到下的顺序尝试。</FieldDescription>
              </FieldContent>
              {settings.fallback_providers.map((identity, index) => <div key={identity} className="flex items-center gap-2">
                <span className="min-w-0 flex-1 break-words text-sm">{index + 1}. {identity}</span>
                <Button type="button" variant="ghost" size="sm" disabled={busy || !hostEditable || index === 0} onClick={() => {
                  const values = [...settings.fallback_providers];
                  [values[index - 1], values[index]] = [values[index], values[index - 1]];
                  set('fallback_providers', values);
                }}>上移</Button>
                <Button type="button" variant="ghost" size="sm" disabled={busy || !hostEditable || index === settings.fallback_providers.length - 1} onClick={() => {
                  const values = [...settings.fallback_providers];
                  [values[index], values[index + 1]] = [values[index + 1], values[index]];
                  set('fallback_providers', values);
                }}>下移</Button>
                <Button type="button" variant="ghost" size="sm" disabled={busy || !hostEditable}
                  onClick={() => set('fallback_providers', settings.fallback_providers.filter(value => value !== identity))}>移除</Button>
              </div>)}
              <div className="flex items-center gap-2">
                <Select value={fallbackToAdd} disabled={!hostEditable} onValueChange={setFallbackToAdd}>
                  <SelectTrigger id="setting-fallback_providers" className="min-w-0 flex-1"><SelectValue placeholder="选择备用模型" /></SelectTrigger>
                  <SelectContent><SelectGroup>{providers.chat.filter(identity => !settings.fallback_providers.includes(identity)).map(identity =>
                    <SelectItem key={identity} value={identity}>{identity}</SelectItem>)}</SelectGroup></SelectContent>
                </Select>
                <Button type="button" variant="outline" disabled={!hostEditable || !fallbackToAdd || settings.fallback_providers.includes(fallbackToAdd)}
                  onClick={() => { set('fallback_providers', [...settings.fallback_providers, fallbackToAdd]); setFallbackToAdd(''); }}>添加</Button>
              </div>
            </Field>
            {select('embedding_provider', '向量模型', providers.embedding)}
            {select('compression_provider', '压缩模型', providers.chat, '与 AstrBot 的上下文压缩模型设置是同一项。', '跟随对话模型')}
            {select('image_caption_provider', '图片描述模型', providers.chat, '与 AstrBot 的图片描述模型设置是同一项。', '不另选模型')}
            {num('spontaneous_probability', '主动插话概率', 0, 1, 0.01, '话题和石英有关时概率加倍')}
            {num('spontaneous_limit', '每群每 5 分钟最多主动插话', 0, 10)}
            <Field>
              <FieldContent>
                <FieldLabel htmlFor="setting-group_blacklist">静默群名单（每行一个群号）</FieldLabel>
                <FieldDescription>这些群只记录消息、不接话也不插话，被 @ 或唤醒时仍会回应。</FieldDescription>
              </FieldContent>
              <Textarea id="setting-group_blacklist" rows={3} value={settings.group_blacklist.join('\n')}
                onChange={e => set('group_blacklist', e.target.value.split('\n'))} />
            </Field>
            {num('engaged_seconds', '接着聊的时间窗口（秒）', 0, 1800, 10, '石英回复某人后，这段时间内 TA 不 @ 也算在跟石英说话')}
            {num('stale_seconds', '插话过期时间（秒）', 10, 3600, 5, '超过这个时间且群里已聊了 3 条以上，就不再插话')}
            {num('split_probability', '偶尔分两条发的概率', 0, 1, 0.01)}
            {num('history_compress_tokens', '会话历史压缩阈值（估算 token）', 1000, 2000000, 1000)}
            {num('history_retain_ratio', '压缩后最近原文保留比例', 0.1, 0.9, 0.05)}
            {num('context_budget', '辅助上下文字数预算', 1000, 20000, 100)}
            {flag('debounce_enabled', '合并同一个人的连续短消息')}
            {num('debounce_seconds', '等下一条消息（秒）', 0.1, 5, 0.1)}
            {num('debounce_max_seconds', '连续发言最多等待（秒）', 1, 15, 0.5)}
            {flag('cooperative_stop', '新消息或“停”让还没动手的旧回复立刻收手（试验）')}
          </FieldGroup>
        </Section>
        <Section title="决策" desc="每次仅调用所选后端；超时仍允许私聊和明确呼叫进入主人格。Laya 接口尚未核实。">
          <FieldGroup className="flex flex-col gap-5">
            <Field orientation="horizontal">
              <FieldLabel htmlFor="decision-backend">决策后端</FieldLabel>
              <Select value={settings.decision_backend} onValueChange={value => set('decision_backend', value as 'haiku' | 'laya')}>
                <SelectTrigger id="decision-backend" className="w-60"><SelectValue /></SelectTrigger>
                <SelectContent><SelectItem value="haiku">Haiku</SelectItem><SelectItem value="laya">Laya（自配端点）</SelectItem></SelectContent>
              </Select>
            </Field>
            {select('decision_provider', 'Haiku 模型', [...new Set([settings.decision_provider, ...providers.chat])])}
            {num('decision_timeout', '决策时限（秒）', 0.01, 10, 0.1)}
            {num('decision_capability_limit', '工具候选上限', 1, 20)}
            <Field><FieldLabel htmlFor="laya-endpoint">Laya HTTP 端点</FieldLabel>
              <Input id="laya-endpoint" value={settings.decision_laya_endpoint} onChange={e => set('decision_laya_endpoint', e.target.value)} /></Field>
            <Field><FieldLabel htmlFor="laya-version">Laya 版本标识</FieldLabel>
              <Input id="laya-version" value={settings.decision_laya_version} onChange={e => set('decision_laya_version', e.target.value)} /></Field>
          </FieldGroup>
        </Section>
        <Section title="记忆与整理" desc="决定群聊什么时候被整理，以及记忆怎么存下来、带几条回去。">
          <FieldGroup className="flex flex-col gap-5">
            {select('summary_provider', '总结模型', providers.chat, '用于群聊整理和二次核对；不可用时尝试对话模型及备用模型。', '跟随对话模型')}
            {model_status.summary?.status && model_status.summary.status !== 'ok' && <p className="text-xs text-muted-foreground">
              {model_status.summary.status === 'fallback' ? `总结模型上次改用 ${model_status.summary.using}。` : '总结模型和备用模型均不可用，请检查模型。'}
            </p>}
            {flag('digest_enabled', '自动整理群聊（摘要、话题、记忆、称呼、黑话）')}
            {flag('verify_enabled', '整理后二次核对依据')}
            {flag('glossary_search', '联网搜索黑话')}
            {num('glossary_per_run', '每次整理最多查几个黑话', 0, 10)}
            {num('recall_count', '每次最多带几条记忆', 1, 20)}
            {num('memory_budget', '记忆字数预算', 200, 4000, 50)}
            {num('foreground_retrieval_timeout', '前台检索预算（秒）', 0, 300, 0.1, '默认 10 秒，线上测量后再调', true)}
            {num('foreground_rerank_timeout', '前台重排预算（秒）', 0, 300, 0.1, '默认 20 秒；资源忙时跳过，线上测量后再调', true)}
          </FieldGroup>
        </Section>
        <Section title="工具与经验" desc="决定石英能不能用工具办事，以及经验记在哪里。">
          <FieldGroup className="flex flex-col gap-5">
            {flag('skills_enabled', '用工具办事后复盘，积累并修正经验')}
            {num('task_retry_limit', '任务单步自动重试次数', 0, 3)}
            {num('foreground_grilling_timeout', 'grilling 准备预算（秒）', 0, 300, 0.1, '默认 0，不额外限时；线上测量后再调')}
            {num('notification_max_delay', '必要通知最长推迟（秒）', 0, 86400, 1, '默认 60；超过后不再因前台活跃而延后')}
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
            {select('learning_provider', '学习模型', providers.chat, '不可用时尝试对话模型及备用模型。', '跟随对话模型')}
            {model_status.learning?.status && model_status.learning.status !== 'ok' && <p className="text-xs text-muted-foreground">
              {model_status.learning.status === 'fallback' ? `学习模型上次改用 ${model_status.learning.using}。` : '学习模型和备用模型均不可用，请检查模型。'}
            </p>}
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
