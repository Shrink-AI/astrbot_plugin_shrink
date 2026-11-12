import {
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from '@/components/ui/sidebar';
import type { NavGroupProps } from './types';

export function NavGroup({ title, items, active, onSelect }: NavGroupProps) {
  const { setOpenMobile } = useSidebar();
  return <SidebarGroup>
    {title ? <SidebarGroupLabel>{title}</SidebarGroupLabel> : null}
    <SidebarMenu>
      {items.map(item => <SidebarMenuItem key={item.key}>
        <SidebarMenuButton
          isActive={active === item.key}
          tooltip={item.title}
          aria-current={active === item.key ? 'page' : undefined}
          onClick={() => { onSelect(item.key); setOpenMobile(false); }}
        >
          <item.icon />
          <span>{item.title}</span>
          {item.badge ? <SidebarMenuBadge>{item.badge}</SidebarMenuBadge> : null}
        </SidebarMenuButton>
      </SidebarMenuItem>)}
    </SidebarMenu>
  </SidebarGroup>;
}