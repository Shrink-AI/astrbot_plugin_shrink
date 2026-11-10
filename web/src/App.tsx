import { Brain, FileText, Gauge, GraduationCap, History, Image, Lightbulb, LogOut, MessagesSquare, Puzzle, RefreshCw, Settings2, UserRound, Users, Wrench, X } from 'lucide-react';
import { useEffect, useState } from 'react';
import { api, setCsrf } from './api/client';
import type { Configuration, Status } from './api/types';
import { LoginPage } from './pages/LoginPage';
import { MemesPage } from './pages/MemesPage';
import { MemoryPage } from './pages/MemoryPage';
import { Overview } from './pages/Overview';
import { PeoplePage } from './pages/PeoplePage';
import { PersonaPage } from './pages/PersonaPage';
import { PromptsPage } from './pages/PromptsPage';
import { RunsPage } from './pages/RunsPage';
import { SettingsPage } from './pages/SettingsPage';
import { SkillsPage } from './pages/SkillsPage';
import { TeachPage } from './pages/TeachPage';
import { OperationsPage, CapabilitiesPage } from './pages/OperationsPage';
import { ChatsPage } from './pages/ChatsPage';
import { DevicesPage } from './pages/DevicesPage';
import { MaintenancePage } from './pages/MaintenancePage';
import { DashboardContext } from './shared/dashboard';

const sections = [
  { name: '', pages: [
    { key: 'overview', name: '概览', icon: Gauge, view: Overview },
  ] },
  { name: '群聊', pages: [
    { key: 'chats', name: '聊天与图片', icon: MessagesSquare, view: ChatsPage },
    { key: 'people', name: '人物', icon: Users, view: PeoplePage },
    { key: 'memory', name: '记忆', icon: Brain, view: MemoryPage },
    { key: 'memes', name: '表情', icon: Image, view: MemesPage },
  ] },
  { name: '说话方式', pages: [
    { key: 'persona', name: '人格', icon: UserRound, view: PersonaPage },
    { key: 'prompts', name: '提示词', icon: FileText, view: PromptsPage },
    { key: 'teach', name: '定向学习', icon: GraduationCap, view: TeachPage },
    { key: 'skills', name: '经验', icon: Lightbulb, view: SkillsPage },
  ] },
  { name: '后台', pages: [
    { key: 'runs', name: '整理记录', icon: History, view: RunsPage },
    { key: 'maintenance', name: '夜间能力维护', icon: History, view: MaintenancePage },
    { key: 'operations', name: '开发任务', icon: Wrench, view: OperationsPage },
    { key: 'devices', name: '电脑与 Agent', icon: Wrench, view: DevicesPage },
    { key: 'capabilities', name: '插件与能力', icon: Puzzle, view: CapabilitiesPage },
    { key: 'settings', name: '设置', icon: Settings2, view: SettingsPage },
  ] },
];
const pages = sections.flatMap(s => s.pages);

export function App() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [page, setPage] = useState('overview');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [status, setStatus] = useState<Status | null>(null);
  const [config, setConfig] = useState<Configuration | null>(null);
  const refresh = () => setRevision(r => r + 1);
  const signOut = () => { setCsrf(''); setAuthenticated(false); setStatus(null); setConfig(null); };
  const action = async (work: () => Promise<unknown>, success = '已保存') => {
    setBusy(true); setError(''); setNotice('');
    try { await work(); if(success) setNotice(success); refresh(); }
    catch(e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  useEffect(() => {
    let cancelled = false;
    api<{ csrf: string }>('/session').then(r => {
      if(!cancelled) { setCsrf(r.csrf); setAuthenticated(true); }
    }).catch(() => { if(!cancelled) setAuthenticated(false); });
    window.addEventListener('signed-out', signOut);
    return () => { cancelled = true; window.removeEventListener('signed-out', signOut); };
  }, []);
  useEffect(() => {
    if(!authenticated) return;
    let cancelled = false;
    Promise.all([api<Status>('/status'), api<Configuration>('/settings')])
      .then(([state, configuration]) => { if(!cancelled) { setStatus(state); setConfig(configuration); } })
      .catch(e => { if(!cancelled) setError(e.message); });
    return () => { cancelled = true; };
  }, [authenticated, revision]);
  const running = !!(status?.learning_running || status?.digest_running || status?.consolidation_running);
  useEffect(() => {
    if(!authenticated || !running) return;
    const timer = setInterval(() => {
      api<Status>('/status').then(state => {
        setStatus(state);
        if(!state.learning_running && !state.digest_running && !state.consolidation_running) refresh();
      }).catch(() => { });
    }, 3000);
    return () => clearInterval(timer);
  }, [authenticated, running]);
  const go = (key: string) => { setPage(key); setError(''); setNotice(''); };
  if(authenticated === false) return <LoginPage onLogin={() => setAuthenticated(true)} />;
  if(!status || !config) return <div className="loading">{error || '加载中…'}{error && <button onClick={refresh}>重试</button>}</div>;
  const current = pages.find(p => p.key === page) ?? pages[0];
  const View = current.view;
  const logout = () => void action(async () => { await api('/logout', 'POST'); signOut(); }, '');
  return <DashboardContext.Provider value={{ ...config, status, busy, revision, refresh, action, setError, signOut, go }}>
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">石英后台</div>
        <nav className="nav">{sections.map(s => <div key={s.name || 'top'} className="nav" role="group" aria-label={s.name || undefined}>
          {s.name && <div className="nav-group">{s.name}</div>}
          {s.pages.map(p => <button key={p.key} className={p.key === current.key ? 'active' : ''} onClick={() => go(p.key)}>
            <p.icon size={16} />{p.name}
            {p.key === 'teach' && status.counts.review_open > 0 && <span className="count">{status.counts.review_open}</span>}
          </button>)}
        </div>)}</nav>
        <div className="sidebar-foot">
          <button className="ghost" disabled={busy} onClick={refresh}><RefreshCw size={15} />刷新</button>
          <button className="ghost" onClick={logout}><LogOut size={15} />退出</button>
        </div>
      </aside>
      <main className="content">
        {error && <div role="alert" className="banner error">{error}<button onClick={() => setError('')} aria-label="关闭"><X size={14} /></button></div>}
        {notice && <div role="status" className="banner success">{notice}<button onClick={() => setNotice('')} aria-label="关闭"><X size={14} /></button></div>}
        <View key={current.key} />
      </main>
    </div>
  </DashboardContext.Provider>;
}
