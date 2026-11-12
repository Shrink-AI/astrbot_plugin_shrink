import { Brain, FileText, Gauge, GraduationCap, History, Image, Lightbulb, MessagesSquare, Puzzle, Settings2, UserRound, Users, Wrench, XIcon } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Alert, AlertAction, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar';
import { Spinner } from '@/components/ui/spinner';
import { TooltipProvider } from '@/components/ui/tooltip';
import { AppSidebar } from '@/components/layout/app-sidebar';
import { Header } from '@/components/layout/header';
import { Main } from '@/components/layout/main';
import { SkipToMain } from '@/components/layout/skip-to-main';
import { ConfirmProvider } from '@/components/confirm-dialog';
import { api, setCsrf } from '@/api/client';
import type { Configuration, Status } from '@/api/types';
import { LoginPage } from '@/pages/LoginPage';
import { MemesPage } from '@/pages/MemesPage';
import { MemoryPage } from '@/pages/MemoryPage';
import { Overview } from '@/pages/Overview';
import { PeoplePage } from '@/pages/PeoplePage';
import { PersonaPage } from '@/pages/PersonaPage';
import { PromptsPage } from '@/pages/PromptsPage';
import { RunsPage } from '@/pages/RunsPage';
import { SettingsPage } from '@/pages/SettingsPage';
import { SkillsPage } from '@/pages/SkillsPage';
import { TeachPage } from '@/pages/TeachPage';
import { OperationsPage, CapabilitiesPage } from '@/pages/OperationsPage';
import { ChatsPage } from '@/pages/ChatsPage';
import { DevicesPage } from '@/pages/DevicesPage';
import { MaintenancePage } from '@/pages/MaintenancePage';
import { DashboardContext } from '@/shared/dashboard';

const sections = [
  { title: undefined as string | undefined, pages: [
    { key: 'overview', name: '概览', icon: Gauge, view: Overview },
  ] },
  { title: '群聊', pages: [
    { key: 'chats', name: '聊天与图片', icon: MessagesSquare, view: ChatsPage },
    { key: 'people', name: '人物', icon: Users, view: PeoplePage },
    { key: 'memory', name: '记忆', icon: Brain, view: MemoryPage },
    { key: 'memes', name: '表情', icon: Image, view: MemesPage },
  ] },
  { title: '说话方式', pages: [
    { key: 'persona', name: '人格', icon: UserRound, view: PersonaPage },
    { key: 'prompts', name: '提示词', icon: FileText, view: PromptsPage },
    { key: 'teach', name: '定向学习', icon: GraduationCap, view: TeachPage },
    { key: 'skills', name: '经验', icon: Lightbulb, view: SkillsPage },
  ] },
  { title: '后台', pages: [
    { key: 'runs', name: '整理记录', icon: History, view: RunsPage },
    { key: 'maintenance', name: '夜间能力维护', icon: History, view: MaintenancePage },
    { key: 'operations', name: '开发任务', icon: Wrench, view: OperationsPage },
    { key: 'devices', name: '电脑与 Agent', icon: Wrench, view: DevicesPage },
    { key: 'capabilities', name: '插件与能力', icon: Puzzle, view: CapabilitiesPage },
    { key: 'settings', name: '设置', icon: Settings2, view: SettingsPage },
  ] },
];
const pages = sections.flatMap(section => section.pages);

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
  if(authenticated === false) return <ConfirmProvider><LoginPage onLogin={() => setAuthenticated(true)} /></ConfirmProvider>;
  if(!status || !config) return <div className="grid min-h-svh place-items-center px-4 text-sm text-muted-foreground">
    <div className="flex items-center gap-3">
      {!error && <Spinner />}
      {error || '加载中…'}
      {error && <Button variant="outline" size="sm" onClick={refresh}>重试</Button>}
    </div>
  </div>;
  const current = pages.find(item => item.key === page) ?? pages[0];
  const View = current.view;
  const groups = sections.map(section => ({
    title: section.title,
    items: section.pages.map(item => ({
      key: item.key,
      title: item.name,
      icon: item.icon,
      badge: item.key === 'teach' && status.counts.review_open > 0 ? status.counts.review_open : undefined,
    })),
  }));
  const logout = () => void action(async () => { await api('/logout', 'POST'); signOut(); }, '');
  return <ConfirmProvider><DashboardContext.Provider value={{ ...config, status, busy, revision, refresh, action, setError, signOut, go }}>
    <TooltipProvider>
      <SkipToMain />
      <SidebarProvider>
        <AppSidebar groups={groups} active={current.key} onSelect={go} onRefresh={refresh} busy={busy} />
        <SidebarInset>
          <Header trail={[current.name]} busy={busy} onHome={() => go('overview')} onRefresh={refresh}
            onSettings={() => go('settings')} onLogout={logout} />
          <Main>
            {error && <Alert variant="destructive">
              <AlertTitle>{error}</AlertTitle>
              <AlertAction><Button variant="ghost" size="icon-xs" aria-label="关闭" onClick={() => setError('')}><XIcon /></Button></AlertAction>
            </Alert>}
            {notice && <Alert role="status" className="border-success/30 bg-success/5 text-success">
              <AlertTitle>{notice}</AlertTitle>
              <AlertAction><Button variant="ghost" size="icon-xs" aria-label="关闭" onClick={() => setNotice('')}><XIcon /></Button></AlertAction>
            </Alert>}
            <View key={current.key} />
          </Main>
        </SidebarInset>
      </SidebarProvider>
    </TooltipProvider>
  </DashboardContext.Provider></ConfirmProvider>;
}
