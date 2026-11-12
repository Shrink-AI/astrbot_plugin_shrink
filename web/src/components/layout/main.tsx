import type { ReactNode } from 'react';
import { cn } from 'cn';

export function Main({ children, className }: { children: ReactNode; className?: string }) {
  return <main id="content" className={cn('mx-auto flex w-full max-w-7xl flex-1 flex-col gap-4 px-4 py-6', className)}>{children}</main>;
}