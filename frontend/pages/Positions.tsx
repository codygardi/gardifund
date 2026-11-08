import { useMemo, useState } from 'react';
import { Search, X } from 'lucide-react';
import { api, money, pct, quotePrice, useApp, useResource, when } from '../api';
import { Empty, ErrorNote, Modal, Segmented, Tip } from '../ui';

export default function Positions() {
  const { state, refresh } = useApp();
  const { data, error } = useResource('/positions');
  const { data: shadow } = useResource('/overview?mode=shadow');
  const [mode, setMode] = useState('all');
  const [category, setCategory] = useState('stock');
  const [query, setQuery] = useState('');
  const [exit, setExit] = useState<any>(null);
  const [quantity, setQuantity] = useState('');
  const [exitType, setExitType] = useState('market');
  const [limitPrice, setLimitPrice] = useState('');
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [failure, setFailure] = useState('');
  const rows = useMemo(() => (data?.rows || []).filter((row:any) => (mode === 'all' || row.mode === mode)
    && (row.category === category || category === 'stock' && row.category === 'unclassified')
    && `${row.symbol || ''} ${row.name || ''}`.toLowerCase().includes(query.trim().toLowerCase())), [data, mode, category, query]);
  async function close(event:React.FormEvent) {
    event.preventDefault(); if (!exit || busy) return; setBusy(true);
    try {
      const result = await api('/shadow/orders',{method:'POST',body:JSON.stringify({symbol:exit.symbol,side:'sell',quantity:Number(quantity),category:exit.category,request_id:exit.requestId,order_type:exitType,...(exitType === 'limit' ? {limit_price:Number(limitPrice)} : {})})});
      setMessage(result.status === 'filled' ? `Shadow exit filled: ${result.quantity} ${result.symbol} at ${money(result.price)}. Proceeds settle ${result.settles_on}.` : result.message || `Exit ${result.status}.`);
      setExit(null); setFailure(''); refresh();
    } catch(e:any) { setFailure(e.message); }
    finally { setBusy(false); }
  }
  async function cancelLimit(id:string) {
    try { await api(`/shadow/pending/${id}/cancel`,{method:'POST'}); setMessage('Shadow limit exit cancelled.'); setFailure(''); refresh(); }
    catch(e:any) {setFailure(e.message);}
  }
  return <div className="positions-workspace">
    <div className="position-tabs"><Segmented values={[{value:'sector',label:'Sectors'},{value:'etf',label:'ETFs'},{value:'stock',label:'Stocks'}]} value={category} onChange={setCategory}/><span className="muted tiny">Marks refresh about every 15 seconds during premarket and the regular session.</span></div>
    <div className="position-filters"><div className="searchbox"><Search size={15}/><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Search positions" aria-label="Search positions"/></div><label>Account<select value={mode} onChange={e => setMode(e.target.value)}><option value="all">Live & Shadow</option><option value="live">Live</option><option value="shadow">Shadow</option></select></label><span className="soft-badge">{state?.session?.regular_open ? 'REGULAR SESSION OPEN' : 'EXITS CLOSED'}</span><Tip text="Shadow exits use a fresh bid and are allowed only during the normal session, including the exchange’s early-close schedule. Live exits must be placed in Tradier while live order submission is disabled here."/></div>
    <ErrorNote text={failure || error}/>{message && <div className="notice" role="status">{message}<button aria-label="Dismiss" onClick={() => setMessage('')}>×</button></div>}
    {!data ? (!error && <p className="muted small">Loading positions…</p>) : rows.length ? <div className="table-wrap"><table><thead><tr><th>MODE</th><th>ASSET</th><th>SHARES</th><th>ENTRY PRICE</th><th>LAST PRICE</th><th>P/L</th><th>P/L %</th><th>ENTRY TIME</th><th>EXIT</th></tr></thead><tbody>{rows.map((row:any, index:number) => <tr key={`${row.mode}-${row.symbol}-${index}`}><td><span className="soft-badge">{row.mode === 'live' ? 'LIVE' : 'SHADOW'}</span></td><td><strong>{row.symbol}</strong><small className="cell-sub">{row.category === 'unclassified' ? 'Type unclassified' : row.sector || row.name || row.category}</small></td><td>{row.quantity ?? '—'}{row.reserved_quantity > 0 && <small className="cell-sub">{row.reserved_quantity} reserved</small>}</td><td>{quotePrice(row.entry_price)}</td><td>{quotePrice(row.price)}</td><td className={row.pl == null ? 'muted' : row.pl >= 0 ? 'positive' : 'negative'}>{money(row.pl)}</td><td className={row.pl_pct == null ? 'muted' : row.pl_pct >= 0 ? 'positive' : 'negative'}>{pct(row.pl_pct)}</td><td>{row.opened ? when(row.opened) : '—'}</td><td><button className="button secondary" disabled={row.mode === 'live' || !state?.session?.regular_open || row.available_quantity <= 0} title={row.mode === 'live' ? 'Place the Live exit in Tradier; live orders are disabled here.' : 'Exit with a simulated market sell at a fresh bid price'} onClick={() => {setExit({...row,requestId:crypto.randomUUID()});setQuantity(String(row.available_quantity));setExitType('market');setLimitPrice(row.price == null ? '' : String(row.price));setFailure('');}}>{row.mode === 'live' ? 'Tradier only' : 'Exit'}</button></td></tr>)}</tbody></table></div> : <Empty title="No matching positions" description="Select another category or account. Live positions sync from Tradier; enter Shadow positions from Setups."/>}
    {shadow?.exit_orders?.length > 0 && <div className="pending-entries"><h3>Working Shadow limit exits</h3>{shadow.exit_orders.map((order:any) => <div className="setting-row" key={order.request_id}><div><strong>Sell {order.quantity} {order.symbol} at {quotePrice(order.limit_price)} or higher</strong><small>Day order · expires {order.expires_on} at the regular-session close</small></div><button className="button secondary" onClick={() => cancelLimit(order.request_id)}>Cancel limit</button></div>)}</div>}
    {shadow?.exit_updates?.map((order:any) => <p className="panel-caption" role="status" key={order.request_id}>{order.symbol}: {order.message}</p>)}
    <p className="panel-caption">{data?.note} Entry prices use average cost; P/L excludes fees and uses the last cached price. Unknown broker entry times remain blank.</p>
    {exit && <Modal busy={busy} onClose={() => setExit(null)}><form className="modal panel" role="dialog" aria-modal="true" aria-labelledby="exit-title" onSubmit={close}><div className="section-title"><div><div className="eyebrow">SHADOW EXIT</div><h2 id="exit-title">Exit {exit.symbol}</h2></div><button className="icon-button" type="button" aria-label="Close exit" disabled={busy} onClick={() => setExit(null)}><X size={18}/></button></div><p className="muted small">Market exits use a fresh bid. A day limit exit waits for a fresh bid at or above your minimum and expires at the regular-session close. Sale proceeds follow the settlement calendar.</p><label>Order type<select disabled={busy} value={exitType} onChange={e => setExitType(e.target.value)}><option value="market">Market</option><option value="limit">Limit · day order</option></select></label>{exitType === 'limit' && <label>Minimum sale price ($)<input disabled={busy} type="number" min="0.01" step="0.01" max="1000000000" required value={limitPrice} onChange={e => setLimitPrice(e.target.value)}/></label>}<label>Shares to sell<input autoFocus disabled={busy} type="number" min="1" max={exit.available_quantity} step="1" value={quantity} onChange={e => setQuantity(e.target.value)} required/></label><ErrorNote text={failure}/><button type="submit" className="button primary full" disabled={busy}>{busy ? 'Submitting…' : exitType === 'limit' ? 'Place Shadow limit exit' : 'Exit Shadow position'}</button></form></Modal>}
  </div>;
}
