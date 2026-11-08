import { useState } from 'react';
import { RotateCcw, X } from 'lucide-react';
import { api, useApp } from './api';
import { ErrorNote, Modal } from './ui';

export default function ShadowReset() {
  const { state, refresh } = useApp();
  const [requestId, setRequestId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  if (state?.access_scope !== 'local') return null;
  async function reset(event:React.FormEvent) {
    event.preventDefault(); setBusy(true);
    try { await api('/shadow/reset',{method:'POST',body:JSON.stringify({confirmation:'RESET SHADOW',request_id:requestId})}); setRequestId(''); setError(''); setMessage('Shadow account reset to $10,000. Previous activity was archived.'); refresh(); }
    catch(e:any) { setError(e.message); }
    finally { setBusy(false); }
  }
  return <><button className="button secondary" onClick={() => {setRequestId(crypto.randomUUID());setError('');setMessage('');}}><RotateCcw size={15}/> Reset Shadow</button>{message && <span className="muted small" role="status">{message}</span>}{requestId && <Modal busy={busy} onClose={() => setRequestId('')}><form className="modal panel" role="dialog" aria-modal="true" aria-labelledby="reset-title" onSubmit={reset}><div className="section-title"><h2 id="reset-title">Reset Shadow to $10,000?</h2><button type="button" className="icon-button" disabled={busy} onClick={() => setRequestId('')} aria-label="Close reset"><X size={18}/></button></div><p className="muted small">This archives the current simulated cash, positions, orders, and performance, cancels pending Shadow entries, and starts a fresh $10,000 cash account.</p><ErrorNote text={error}/><div className="setup-sync-actions"><button type="button" className="button secondary" disabled={busy} onClick={() => setRequestId('')}>Keep account</button><button type="submit" className="button primary" disabled={busy}>{busy ? 'Resetting…' : 'Reset Shadow account'}</button></div></form></Modal>}</>;
}
