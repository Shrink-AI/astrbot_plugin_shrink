import { useState } from 'react';
import { api } from '../api/client';
import type { Settings } from '../api/types';
import { PageHead, Panel } from '../shared/components';
import { useDashboard } from '../shared/dashboard';

type NumberKey = { [K in keyof Settings]: Settings[K] extends number ? K : never }[keyof Settings];
type BoolKey = { [K in keyof Settings]: Settings[K] extends boolean ? K : never }[keyof Settings];

export function SettingsPage() {
  const { settings: saved, providers, busy, action, signOut } = useDashboard();
  const [settings, setSettings] = useState<Settings>(saved);
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const set = <K extends keyof Settings>(name: K, value: Settings[K]) => setSettings(s => ({ ...s, [name]: value }));
  const num = (key: NumberKey, label: string, min: number, max: number, step = 1, hint?: string) =>
    <label className="field" key={key}><span>{label}</span>
      <input type="number" min={min} max={max} step={step} value={settings[key] ?? 0} onChange={e => set(key, Number(e.target.value))} />
      {hint && <small>{hint}</small>}</label>;
  const flag = (key: BoolKey, label: string) =>
    <label className="check" key={key}><input type="checkbox" checked={!!settings[key]} onChange={e => set(key, e.target.checked)} />{label}</label>;
  const save = () => void action(() => api('/settings', 'PUT', { ...settings, collect_groups: settings.collect_groups.map(x => x.trim()).filter(Boolean) }));
  return <>
    <PageHead title="设置"><button className="primary" disabled={busy} onClick={save}>保存设置</button></PageHead>
    <Panel title="聊天">
      <div className="form-grid">
        <label className="field"><span>对话模型</span><select value={settings.model_provider} onChange={e => set('model_provider', e.target.value)}>
          {providers.chat.map(p => <option key={p}>{p}</option>)}</select></label>
        <label className="field"><span>向量模型</span><select value={settings.embedding_provider} onChange={e => set('embedding_provider', e.target.value)}>
          {providers.embedding.map(p => <option key={p}>{p}</option>)}</select></label>
        {num('spontaneous_probability', '主动插话概率', 0, 1, 0.01, '话题和石英有关时概率加倍')}
        {num('spontaneous_limit', '每群每 5 分钟最多主动插话', 0, 10)}
        {num('engaged_seconds', '接着聊的时间窗口（秒）', 0, 1800, 10, '石英回复某人后，这段时间内 TA 不 @ 也算在跟石英说话')}
        {num('stale_seconds', '插话过期时间（秒）', 10, 3600, 5, '超过这个时间且群里已聊了 3 条以上，就不再插话')}
        {num('split_probability', '偶尔分两条发的概率', 0, 1, 0.01)}
        {num('context_budget', '群聊上下文字数预算', 1000, 20000, 100)}
        {flag('debounce_enabled', '合并同一个人的连续短消息')}
        {num('debounce_seconds', '等下一条消息（秒）', 0.1, 5, 0.1)}
        {num('debounce_max_seconds', '连续发言最多等待（秒）', 1, 15, 0.5)}
      </div>
    </Panel>
    <Panel title="记忆与整理">
      <div className="form-grid">
        {flag('digest_enabled', '自动整理群聊（摘要、话题、记忆、称呼、黑话）')}
        {flag('verify_enabled', '整理后二次核对依据')}
        {flag('glossary_search', '联网搜索黑话')}
        {num('glossary_per_run', '每次整理最多查几个黑话', 0, 10)}
        {num('recall_count', '每次最多带几条记忆', 1, 20)}
        {num('memory_budget', '记忆字数预算', 200, 4000, 50)}
      </div>
    </Panel>
    <Panel title="工具与经验">
      <div className="form-grid">
        {flag('skills_enabled', '用工具办事后复盘，积累并修正经验')}
        <label className="field"><span>Python 沙箱地址</span>
          <input value={settings.sandbox_url} onChange={e => set('sandbox_url', e.target.value)} />
          <small>shrink_run_python 工具调用的沙箱服务</small></label>
      </div>
    </Panel>
    <Panel title="学习">
      <div className="form-grid">
        {flag('learning_enabled', '允许自动学习说话习惯')}
        {num('learning_hours', '学习间隔（小时）', 0.25, 168, 0.25)}
        {num('learning_min_messages', '至少攒够多少条新消息', 10, 1000)}
        {num('learning_batch_size', '每次最多学习多少条', 10, 1000)}
      </div>
    </Panel>
    <Panel title="表情">
      <div className="form-grid">
        {flag('collect_enabled', '自动收集二次元表情')}
        {flag('meme_enabled', '允许石英发表情')}
        <label className="field wide"><span>收集表情的群（每行一个群号）</span>
          <textarea rows={3} value={settings.collect_groups.join('\n')} onChange={e => set('collect_groups', e.target.value.split('\n'))} /></label>
        {num('meme_cooldown', '同一个聊天发表情的间隔（秒）', 0, 86400)}
        {num('meme_repeat_cooldown', '同一张表情的重复间隔（秒）', 0, 604800)}
      </div>
    </Panel>
    <Panel title="管理员密码">
      <form className="form-grid" onSubmit={e => {
        e.preventDefault(); void action(async () => {
          await api('/password', 'POST', { current_password: oldPassword, new_password: newPassword });
          signOut(); setOldPassword(''); setNewPassword('');
        }, '密码已修改，请重新登录');
      }}>
        <label className="field"><span>当前密码</span><input type="password" autoComplete="current-password" required value={oldPassword} onChange={e => setOldPassword(e.target.value)} /></label>
        <label className="field"><span>新密码（至少 15 个字符）</span><input type="password" autoComplete="new-password" minLength={15} maxLength={128} required value={newPassword} onChange={e => setNewPassword(e.target.value)} /></label>
        <div className="wide row"><small>修改后所有登录都会失效。</small><span className="spacer" /><button disabled={busy}>修改密码</button></div>
      </form>
    </Panel>
  </>;
}
