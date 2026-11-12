import type { ReactNode } from 'react';
import { XIcon } from 'lucide-react';
import { cn } from 'cn';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';

/**
 * 选中行后从底部居中浮出的批量操作胶囊工具栏。
 * 最外层保留 `selection-bar` 类名，回归脚本依赖它。
 */
export function BulkActions({ count, label, onClear, children }: {
  count: number;
  label: string;
  onClear: () => void;
  children: ReactNode;
}) {
  if(count === 0) return null;
  return <div role="toolbar" aria-label={`已选 ${count} ${label}的批量操作`}
    className={cn('selection-bar fixed bottom-6 left-1/2 z-50 -translate-x-1/2',
      'flex items-center gap-2 rounded-xl border bg-background/95 p-2 shadow-xl backdrop-blur-lg',
      'supports-backdrop-filter:bg-background/60')}>
    <Button variant="outline" size="icon-xs" className="rounded-full" onClick={onClear}
      aria-label="清除选择" title="清除选择">
      <XIcon />
    </Button>
    <Separator orientation="vertical" className="h-5" aria-hidden="true" />
    <div className="flex items-center gap-1 text-sm">
      <Badge className="min-w-8 rounded-lg">{count}</Badge>
      <span className="whitespace-nowrap">{label}</span>
    </div>
    <Separator orientation="vertical" className="h-5" aria-hidden="true" />
    {children}
  </div>;
}