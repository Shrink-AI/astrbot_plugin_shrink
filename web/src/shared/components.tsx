import { ChevronFirstIcon, ChevronLastIcon, ChevronLeftIcon, ChevronRightIcon, XIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { Badge as UiBadge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardAction, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Dialog, DialogClose, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Empty as UiEmpty, EmptyDescription, EmptyHeader } from '@/components/ui/empty';
import { PaginationContent, PaginationItem } from '@/components/ui/pagination';
import { cn } from 'cn';

const toneVariants = { ok: 'success', warn: 'warning', bad: 'destructive', info: 'info' } as const;

export function Badge({ tone, children }: { tone?: keyof typeof toneVariants; children: ReactNode }) {
  return <UiBadge variant={tone ? toneVariants[tone] : 'secondary'}>{children}</UiBadge>;
}

export function Panel({ title, actions, children, className, contentClassName, flush }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
  contentClassName?: string; flush?: boolean;
}) {
  const head = title !== undefined || actions !== undefined;
  return <Card className={cn('gap-0 py-0', className)}>
    {head && <CardHeader className="flex flex-row items-center justify-between gap-3 border-b px-4 py-2.5">
      {title !== undefined
        ? <CardTitle className="min-w-0 text-sm font-semibold">
          {typeof title === 'string' ? <h2 className="text-sm leading-snug font-semibold break-words">{title}</h2> : title}
        </CardTitle>
        : <span />}
      {actions !== undefined && <CardAction className="static self-center">{actions}</CardAction>}
    </CardHeader>}
    <CardContent className={cn('py-4', flush && 'px-0 py-0', contentClassName)}>{children}</CardContent>
  </Card>;
}

export function PageHead({ title, description, children }: { title: string; description?: ReactNode; children?: ReactNode }) {
  return <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
    <div className="flex min-w-0 flex-col gap-1">
      <h1 className="text-2xl font-bold tracking-tight">{title}</h1>
      {description && <p className="text-muted-foreground">{description}</p>}
    </div>
    {children && <div className="flex flex-wrap items-center gap-2">{children}</div>}
  </div>;
}

export function Empty({ text, action }: { text: string; action?: ReactNode }) {
  return <UiEmpty className="gap-3 border-0 py-7">
    <EmptyHeader>
      <EmptyDescription>{text}</EmptyDescription>
      {action}
    </EmptyHeader>
  </UiEmpty>;
}

export function TableToolbar({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('flex flex-wrap items-center justify-between gap-2', className)}>{children}</div>;
}

export function TableShell({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('overflow-hidden rounded-md border', className)}>{children}</div>;
}

export function Pagination({ offset, count, size, change }: {
  offset: number; count: number; size: number; change: (n: number) => void;
}) {
  const page = Math.floor(offset / size) + 1;
  const more = count >= size;
  if(offset === 0 && !more) return null;
  return <div className="flex flex-wrap items-center justify-between gap-2">
    <p className="text-sm text-muted-foreground">第 {page} 页 · 本页 {count} 条</p>
    <PaginationContent className="mx-0 flex-wrap justify-end gap-1">
      <PaginationItem>
        <Button variant="outline" size="icon-sm" aria-label="第一页" disabled={offset === 0}
          onClick={() => change(0)}><ChevronFirstIcon /></Button>
      </PaginationItem>
      <PaginationItem>
        <Button variant="outline" size="icon-sm" aria-label="上一页" disabled={offset === 0}
          onClick={() => change(Math.max(0, offset - size))}><ChevronLeftIcon /></Button>
      </PaginationItem>
      <PaginationItem>
        <span className="flex size-8 items-center justify-center text-sm font-medium tabular-nums">{page}</span>
      </PaginationItem>
      <PaginationItem>
        <Button variant="outline" size="icon-sm" aria-label="下一页" disabled={!more}
          onClick={() => change(offset + size)}><ChevronRightIcon /></Button>
      </PaginationItem>
      <PaginationItem>
        <Button variant="outline" size="icon-sm" aria-label="最后一页" disabled={!more}
          onClick={() => change(offset + size)}><ChevronLastIcon /></Button>
      </PaginationItem>
    </PaginationContent>
  </div>;
}

export function Modal({ title, close, children }: { title: string; close: () => void; children: ReactNode }) {
  return <Dialog open onOpenChange={open => { if(!open) close(); }}>
    <DialogContent showCloseButton={false} className="max-h-[88vh] grid-rows-[auto_minmax(0,1fr)] gap-3 overflow-hidden sm:max-w-[720px]">
      <DialogHeader className="flex flex-row items-center justify-between gap-3">
        <DialogTitle>{title}</DialogTitle>
        <DialogClose asChild>
          <Button variant="ghost" size="icon-sm" aria-label="关闭"><XIcon /></Button>
        </DialogClose>
      </DialogHeader>
      <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">{children}</div>
    </DialogContent>
  </Dialog>;
}

export function Facts({ children }: { children: ReactNode }) {
  return <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm [&>dd]:m-0 [&>dd]:break-words [&>dt]:text-muted-foreground">
    {children}
  </dl>;
}