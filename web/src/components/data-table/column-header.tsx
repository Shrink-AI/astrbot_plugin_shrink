import { ArrowDownIcon, ArrowUpDownIcon, ArrowUpIcon } from 'lucide-react';
import { cn } from 'cn';
import { Button } from '@/components/ui/button';

export type SortDirection = 'asc' | 'desc';

/** 可排序表头：ghost 按钮 + 方向图标，未排序时图标淡化。 */
export function DataTableColumnHeader({ title, direction, onSort, className }: {
  title: string;
  direction?: SortDirection;
  onSort: () => void;
  className?: string;
}) {
  return <Button variant="ghost" size="sm" onClick={onSort} title={`按${title}排序`}
    className={cn('-ms-2 h-8', className)}>
    <span>{title}</span>
    {direction === 'desc'
      ? <ArrowDownIcon data-icon="inline-end" className="size-3.5" />
      : direction === 'asc'
        ? <ArrowUpIcon data-icon="inline-end" className="size-3.5" />
        : <ArrowUpDownIcon data-icon="inline-end" className="size-3.5 opacity-50" />}
  </Button>;
}