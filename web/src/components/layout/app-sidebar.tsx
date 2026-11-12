import { Gem, RefreshCw, Settings2 } from 'lucide-react';
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
  useSidebar,
} from '@/components/ui/sidebar';
import { NavGroup } from './nav-group';
import type { NavItem } from './types';

export function AppSidebar({ groups, active, onSelect, onRefresh, busy }: {
  groups: { title?: string; items: NavItem[] }[];
  active: string;
  onSelect: (key: string) => void;
  onRefresh: () => void;
  busy: boolean;
}) {
  const { setOpenMobile } = useSidebar();
  return <Sidebar collapsible="icon">
    <SidebarHeader>
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton size="lg" className="gap-2" tooltip="石英后台"
            onClick={() => { onSelect('overview'); setOpenMobile(false); }}>
            <div className="flex aspect-square size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <Gem className="size-4" />
            </div>
            <div className="grid flex-1 text-start text-sm leading-tight">
              <span className="truncate font-semibold">石英后台</span>
              <span className="truncate text-xs text-muted-foreground">群聊人格后台</span>
            </div>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarHeader>
    <SidebarContent className="nav">
      {groups.map(group => <NavGroup key={group.title ?? 'top'} title={group.title} items={group.items}
        active={active} onSelect={onSelect} />)}
    </SidebarContent>
    <SidebarFooter>
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton tooltip="刷新数据" disabled={busy} onClick={onRefresh}>
            <RefreshCw />
            <span>刷新数据</span>
          </SidebarMenuButton>
        </SidebarMenuItem>
        <SidebarMenuItem>
          <SidebarMenuButton tooltip="设置" onClick={() => { onSelect('settings'); setOpenMobile(false); }}>
            <Settings2 />
            <span>设置</span>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarFooter>
    <SidebarRail />
  </Sidebar>;
}