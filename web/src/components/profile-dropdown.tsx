import { LogOut, RefreshCw, Settings2, ShieldCheck } from 'lucide-react';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

export function ProfileDropdown({ onRefresh, onSettings, onLogout, busy }: {
  onRefresh: () => void;
  onSettings: () => void;
  onLogout: () => void;
  busy: boolean;
}) {
  return <DropdownMenu>
    <DropdownMenuTrigger asChild>
      <Button variant="ghost" size="icon-sm" className="rounded-full" aria-label="管理员菜单">
        <Avatar className="size-8">
          <AvatarFallback className="text-xs">石英</AvatarFallback>
        </Avatar>
      </Button>
    </DropdownMenuTrigger>
    <DropdownMenuContent align="end" className="w-44">
      <DropdownMenuLabel className="flex items-center gap-2 font-normal">
        <ShieldCheck className="size-4 text-muted-foreground" />
        <span className="text-sm font-medium">管理员</span>
      </DropdownMenuLabel>
      <DropdownMenuSeparator />
      <DropdownMenuGroup>
        <DropdownMenuItem disabled={busy} onSelect={onRefresh}>
          <RefreshCw />
          刷新数据
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={onSettings}>
          <Settings2 />
          设置
        </DropdownMenuItem>
      </DropdownMenuGroup>
      <DropdownMenuSeparator />
      <DropdownMenuItem variant="destructive" onSelect={onLogout}>
        <LogOut />
        退出
      </DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>;
}