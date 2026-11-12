import { RefreshCw } from 'lucide-react';
import { useEffect, useState } from 'react';
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from '@/components/ui/breadcrumb';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import { SidebarTrigger } from '@/components/ui/sidebar';
import { ProfileDropdown } from '@/components/profile-dropdown';
import { ThemeSwitch } from '@/components/theme-switch';
import { cn } from 'cn';

export function Header({ trail, busy, onHome, onRefresh, onSettings, onLogout }: {
  trail: string[];
  busy: boolean;
  onHome: () => void;
  onRefresh: () => void;
  onSettings: () => void;
  onLogout: () => void;
}) {
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    const onScroll = () => setOffset(document.body.scrollTop || document.documentElement.scrollTop);
    document.addEventListener('scroll', onScroll, { passive: true });
    return () => document.removeEventListener('scroll', onScroll);
  }, []);
  const scrolled = offset > 10;
  return <header className={cn('sticky top-0 z-50 h-16 shrink-0', scrolled ? 'shadow-sm' : 'shadow-none')}>
    <div className="relative flex h-full items-center gap-3 p-4 sm:gap-4">
      <div aria-hidden className={cn('absolute inset-0 -z-10 border-b bg-background/20 backdrop-blur-lg', !scrolled && 'border-transparent')} />
      <SidebarTrigger variant="outline" className="max-md:scale-125" />
      <Separator orientation="vertical" data-orientation="vertical" className="h-6!" />
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem className="hidden md:block">
            <BreadcrumbLink asChild>
              <Button type="button" variant="link" className="h-auto p-0 font-normal text-muted-foreground hover:text-foreground" onClick={onHome}>石英后台</Button>
            </BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator className="hidden md:block" />
          <BreadcrumbItem>
            <BreadcrumbPage>{trail[trail.length - 1]}</BreadcrumbPage>
          </BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>
      <div className="ms-auto flex items-center gap-0.5">
        <Button variant="ghost" size="icon-sm" aria-label="刷新数据" disabled={busy} onClick={onRefresh}>
          <RefreshCw />
        </Button>
        <ThemeSwitch />
        <ProfileDropdown busy={busy} onRefresh={onRefresh} onSettings={onSettings} onLogout={onLogout} />
      </div>
    </div>
  </header>;
}