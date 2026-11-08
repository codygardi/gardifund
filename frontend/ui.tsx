import React, { useEffect, useRef } from 'react';
import { ArrowUpRight, HelpCircle, LoaderCircle } from 'lucide-react';

export function Tip({ text }: { text: string }) {
  return <span className="tip-wrap"><button type="button" className="tip" aria-label={text}><HelpCircle size={13} /></button><span role="tooltip" className="tooltip">{text}</span></span>;
}
export function SectionTitle({ title, eyebrow, action }: { title: string; eyebrow?: string; action?: React.ReactNode }) {
  return <div className="section-title"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h2>{title}</h2></div>{action}</div>;
}
export function Empty({ title, description, action }: { title: string; description: string; action?: React.ReactNode }) {
  return <div className="empty"><div className="empty-icon"><ArrowUpRight size={19}/></div><strong>{title}</strong><p>{description}</p>{action}</div>;
}
export function Loading() { return <div className="loading"><LoaderCircle className="spin" size={19}/> Loading workspace</div>; }
export function ErrorNote({ text }: { text?: string }) { return text ? <div className="error" role="alert">{text}</div> : null; }
export function Segmented({ values, value, onChange }: { values: { value: string; label: string }[]; value: string; onChange: (s: string) => void }) {
  return <div className="segmented">{values.map(item => <button type="button" key={item.value} onClick={() => onChange(item.value)} className={value === item.value ? 'selected' : ''} aria-pressed={value === item.value}>{item.label}</button>)}</div>;
}

export function Modal({ children, onClose, busy=false }: {children:React.ReactNode;onClose:() => void;busy?:boolean}) {
  const host = useRef<HTMLDivElement>(null);
  const current = useRef({onClose,busy});
  current.current = {onClose,busy};
  useEffect(() => {
    const previous = document.activeElement as HTMLElement|null;
    const focusable = () => [...(host.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href],[tabindex="0"]') || [])].filter(el => el.offsetParent !== null);
    (host.current?.querySelector<HTMLElement>('input') || focusable()[0])?.focus();
    const key = (event:KeyboardEvent) => {
      if (event.key === 'Escape' && !current.current.busy) {event.preventDefault();current.current.onClose();}
      if (event.key !== 'Tab') return;
      const elements=focusable(), first=elements[0], last=elements.at(-1);
      if (event.shiftKey && document.activeElement === first) {event.preventDefault();last?.focus();}
      else if (!event.shiftKey && document.activeElement === last) {event.preventDefault();first?.focus();}
    };
    document.addEventListener('keydown',key);
    return () => {document.removeEventListener('keydown',key);previous?.focus();};
  },[]);
  return <div className="modal-backdrop" ref={host}>{children}</div>;
}
