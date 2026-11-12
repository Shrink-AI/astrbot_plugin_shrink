import { createContext, useCallback, useContext, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog';

export type ConfirmOptions = {
  title: string;
  desc?: string;
  cancelBtn?: string;
  confirmBtn?: string;
  destructive?: boolean;
};

type Ask = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<Ask | null>(null);

export function ConfirmDialog({ open, title, desc, cancelBtn, confirmBtn, destructive, onConfirm, onCancel }: ConfirmOptions & {
  open: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return <AlertDialog open={open} onOpenChange={next => { if(!next) onCancel(); }}>
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogTitle>{title}</AlertDialogTitle>
        {desc && <AlertDialogDescription>{desc}</AlertDialogDescription>}
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel>{cancelBtn ?? '取消'}</AlertDialogCancel>
        <AlertDialogAction variant={destructive ? 'destructive' : 'default'} onClick={onConfirm}>
          {confirmBtn ?? '确认'}
        </AlertDialogAction>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>;
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [options, setOptions] = useState<ConfirmOptions | null>(null);
  const [open, setOpen] = useState(false);
  const resolver = useRef<((confirmed: boolean) => void) | null>(null);
  const ask = useCallback<Ask>(next => {
    resolver.current?.(false);
    return new Promise<boolean>(resolve => {
      resolver.current = resolve;
      setOptions(next);
      setOpen(true);
    });
  }, []);
  const settle = useCallback((confirmed: boolean) => {
    const resolve = resolver.current;
    resolver.current = null;
    setOpen(false);
    resolve?.(confirmed);
  }, []);
  return <ConfirmContext.Provider value={ask}>
    {children}
    <ConfirmDialog open={open} {...options ?? { title: '' }} onConfirm={() => settle(true)} onCancel={() => settle(false)} />
  </ConfirmContext.Provider>;
}

export function useConfirm() {
  const ask = useContext(ConfirmContext);
  if(!ask) throw new Error('ConfirmProvider is missing');
  return ask;
}