import { useEffect, useState } from 'react';
import { LockKeyhole, RefreshCw, ShieldCheck, X } from 'lucide-react';
import { api, money, useApp, useResource, when } from '../api';
import { ErrorNote, Modal, Segmented, Tip } from '../ui';
import Screening, { ScreenRow } from './Screening';

type Entry = {row:ScreenRow; category:'sector'|'etf'|'stock'; requestId:string; manual?:boolean};
export default function Setups({ onPositions }: { onPositions: () => void }) {
  const { state, refresh, mode, setMode } = useApp();
  const [password, setPassword] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [entry, setEntry] = useState<Entry|null>(null);
  const [quantity, setQuantity] = useState('1');
  const { data: access, error: accessError } = useResource(mode === 'live' ? '/setup-access' : null);
  const { data: account, error: accountError } = useResource(`/overview?mode=${mode}`);
  const live = mode === 'live';
  useEffect(() => {
    if (!access?.unlocked || !Number.isFinite(access.expires_in)) return;
    const timer = setTimeout(refresh, (access.expires_in + 1) * 1000);
    return () => clearTimeout(timer);
  }, [access?.unlocked, access?.expires_in, refresh]);
  async function unlock(event: React.FormEvent) {
    event.preventDefault(); setBusy(true);
    try { await api('/setup-access/unlock',{method:'POST',body:JSON.stringify({password})}); setError(''); refresh(); }
    catch(e:any) { setError(e.message); }
    finally { setPassword(''); setBusy(false); }
  }
  async function lock() {
    try { await api('/setup-access/lock',{method:'POST'}); setError(''); refresh(); }
    catch(e:any) { setError(e.message); }
  }
  async function sync() {
    try { await api('/jobs/account/run',{method:'POST'}); setMessage('Account sync queued. Filled positions reported by Tradier will synchronize here.'); setError(''); refresh(); }
    catch(e:any) { setError(e.message); }
  }
  async function enter(event:React.FormEvent) {
    event.preventDefault(); if (!entry || busy || live) return; setBusy(true);
    try {
      const result = await api('/shadow/orders',{method:'POST',body:JSON.stringify({symbol:entry.row.symbol,side:'buy',quantity:Number(quantity),category:entry.category,request_id:entry.requestId})});
      setMessage(result.status === 'filled' ? `Shadow purchase filled: ${result.quantity} ${result.symbol} at ${money(result.price)}.` : result.message || `Entry ${result.status}.`);
      setEntry(null); setError(''); refresh();
    } catch(e:any) { setError(e.message); }
    finally { setBusy(false); }
  }
  async function cancel(id:string) {
    try { await api(`/shadow/pending/${id}/cancel`,{method:'POST'}); setError(''); setMessage('Pending Shadow entry cancelled.'); refresh(); }
    catch(e:any) { setError(e.message); }
  }
  return <div className="setup-workspace">
    <div className="setup-toolbar"><div><div className="eyebrow">SCREEN & ENTER</div><p className="muted small">Additional filters screen the current Discovery lists.</p></div><Segmented values={[{value:'shadow',label:'Shadow'},{value:'live',label:'Live'}]} value={mode} onChange={value => {setMode(value as 'shadow'|'live');setEntry(null);setError('');setMessage('');}}/></div>
    <ErrorNote text={error || accessError || accountError}/>{message && <div className="notice" role="status">{message}<button aria-label="Dismiss" onClick={() => setMessage('')}>×</button></div>}
    {live && !access?.unlocked ? <div className="setup-lock"><div className="settings-icon"><LockKeyhole size={19}/></div><div><h3>Live setups are locked</h3><p className="muted small">Unlock locally to screen Live setups and synchronize your Tradier account. Live order submission remains disabled.</p>{access?.configured ? <form onSubmit={unlock} className="inline-form"><label>Password<input type="password" autoComplete="off" value={password} onChange={e => setPassword(e.target.value)} maxLength={128} required/></label><button className="button primary" disabled={busy} type="submit">Unlock Live setups</button></form> : <p className="panel-caption">{access ? 'Configure the local Live setup password, then restart GARDiFUND.' : 'Checking the local lock…'}</p>}</div></div> : <>
      {live ? <><div className="setup-unlocked"><span><ShieldCheck size={16}/> Unlocked locally · live order submission disabled</span><button className="text-link" onClick={lock}>Lock now</button></div><div className="setup-sync"><p className="muted small">Open and close Live positions in Tradier. Filled positions synchronize into GARDiFUND; this app sends no Live orders.</p><div className="setup-sync-actions"><button className="button secondary" onClick={sync}><RefreshCw size={15}/> Sync Live account</button><button className="button secondary" onClick={onPositions}>View Positions</button></div></div></> : <div className="shadow-cash-strip"><span>Settled cash <strong>{money(account?.state?.settled_cash_cents / 100)}</strong></span><span>Unsettled proceeds <strong>{money(account?.state?.unsettled_cash_cents / 100)}</strong></span><span className="soft-badge">SIMULATED CASH ACCOUNT</span><button className="button secondary" onClick={() => {setEntry({row:{symbol:'SPY',name:'SPY-related instrument',sector:null,price:null,trade_time:null,volume:null,change_pct:null},category:'etf',requestId:crypto.randomUUID(),manual:true});setQuantity('1');setError('');}}>Enter by symbol</button><Tip text="Whole shares, no borrowing or short selling. Market fills use a fresh ask price. Entries submitted outside the regular session queue for its next opening. Sale proceeds follow a conservative T+1 calendar. Fees, queue priority and market impact are not modeled."/></div>}
      <Screening setups live={live} onEnter={(row, category) => {setEntry({row,category,requestId:crypto.randomUUID()});setQuantity('1');setError('');}}/>
      {!live && account?.entry_updates?.map((update:any) => <div className="error" role="status" key={update.request_id}>{update.symbol}: {update.message}</div>)}
      {!live && account?.pending?.length > 0 && <div className="pending-entries"><h3>Pending Shadow entries</h3><p className="panel-caption">Waiting for a regular session, fresh quotes, and sufficient settled cash.</p>{account.pending.map((order:any) => <div className="setting-row" key={order.request_id}><div><strong>{order.quantity} {order.symbol}</strong><small>{order.category} · queued {when(order.created)}</small></div><button className="button secondary" onClick={() => cancel(order.request_id)}>Cancel entry</button></div>)}</div>}
    </>}
    {entry && <Modal busy={busy} onClose={() => setEntry(null)}><form className="modal panel" onSubmit={enter} role="dialog" aria-modal="true" aria-labelledby="entry-title"><div className="section-title"><div><div className="eyebrow">SHADOW ONLY</div><h2 id="entry-title">Enter {entry.row.symbol}</h2></div><button className="icon-button" type="button" aria-label="Close entry" disabled={busy} onClick={() => setEntry(null)}><X size={18}/></button></div><p className="muted small">{entry.row.name} · {entry.category}. Latest Discovery price: {money(entry.row.price)}. The fill uses a fresh ask during the regular session.</p>{entry.manual && <div className="form-grid"><label>Symbol<input value={entry.row.symbol} maxLength={15} required disabled={busy} onChange={e => setEntry({...entry,row:{...entry.row,symbol:e.target.value.toUpperCase()}})}/></label><label>Position category<select value={entry.category} disabled={busy} onChange={e => setEntry({...entry,category:e.target.value as Entry['category']})}><option value="etf">ETF</option><option value="sector">Sector ETF</option><option value="stock">SPY constituent stock</option></select></label></div>}<label>Whole shares<input autoFocus disabled={busy} type="number" min="1" max="1000000" step="1" required value={quantity} onChange={e => setQuantity(e.target.value)}/></label><p className="panel-caption">{state?.session?.regular_open ? 'A current quote and settled cash are checked before the simulated fill.' : 'This entry will queue for the next regular session. Its final price and cash requirement are determined then.'}</p><ErrorNote text={error}/><button className="button primary full" type="submit" disabled={busy}>{busy ? 'Submitting…' : state?.session?.regular_open ? 'Enter Shadow position' : 'Queue Shadow entry'}</button></form></Modal>}
  </div>;
}
