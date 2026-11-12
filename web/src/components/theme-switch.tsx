import { Check, Moon, Sun } from 'lucide-react';
import { cn } from 'cn';
import { useTheme } from '@/components/theme-provider';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';

const options = [
  { value: 'light', label: '浅色' },
  { value: 'dark', label: '深色' },
  { value: 'system', label: '跟随系统' },
] as const;

export function ThemeSwitch() {
  const { theme, setTheme } = useTheme();
  return <DropdownMenu>
    <DropdownMenuTrigger asChild>
      <Button variant="ghost" size="icon-sm" className="relative" aria-label="切换主题">
        <Sun className="size-4 scale-100 rotate-0 transition-transform dark:scale-0 dark:-rotate-90" />
        <Moon className="absolute size-4 scale-0 rotate-90 transition-transform dark:scale-100 dark:rotate-0" />
      </Button>
    </DropdownMenuTrigger>
    <DropdownMenuContent align="end" className="w-32">
      {options.map(option => <DropdownMenuItem key={option.value} onClick={() => setTheme(option.value)}>
        {option.label}
        <Check size={14} className={cn('ms-auto', theme !== option.value && 'hidden')} />
      </DropdownMenuItem>)}
    </DropdownMenuContent>
  </DropdownMenu>;
}