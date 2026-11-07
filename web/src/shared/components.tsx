import { X } from 'lucide-react';
import React, { useEffect } from 'react';

export function Empty({ text }: { text: string }) {
  return <div className="empty">{text}</div>;
}

export function Pagination({ offset, count, size, change }: { offset: number; count: number; size: number; change: (n: number) => void }) {
  if(offset === 0 && count < size) return null;
  return <div className="pagination">
    <button className="small" disabled={offset === 0} onClick={() => change(Math.max(0, offset - size))}>上一页</button>
    <span>第 {Math.floor(offset / size) + 1} 页</span>
    <button className="small" disabled={count < size} onClick={() => change(offset + size)}>下一页</button>
  </div>;
}

export function Modal({ title, close, children }: { title: string; close: () => void; children: React.ReactNode }) {
  const ref = React.useRef<HTMLDialogElement>(null);
  useEffect(() => { ref.current?.showModal(); }, []);
  return <dialog ref={ref} className="modal" onCancel={close}>
    <div className="modal-head"><h2>{title}</h2><button className="ghost small" aria-label="关闭" onClick={close}><X size={16} /></button></div>
    <div className="modal-body">{children}</div>
  </dialog>;
}

export function Badge({ tone, children }: { tone?: 'ok' | 'warn' | 'bad' | 'info'; children: React.ReactNode }) {
  return <span className={'badge' + (tone ? ' ' + tone : '')}>{children}</span>;
}

export function Panel({ title, actions, children }: { title?: string; actions?: React.ReactNode; children: React.ReactNode }) {
  return <section className="panel">
    {(title || actions) && <div className="panel-head">{title ? <h2>{title}</h2> : <span />}{actions}</div>}
    {children}
  </section>;
}

export function PageHead({ title, children }: { title: string; children?: React.ReactNode }) {
  return <div className="page-head"><h1>{title}</h1>{children && <div className="actions">{children}</div>}</div>;
}
