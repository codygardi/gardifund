import { useMemo, useState } from 'react';
import { ArrowDownLeft, ArrowUpRight, Clock3, RefreshCw, ShieldCheck } from 'lucide-react';
import { api, money, pct, quotePrice, useApp, useResource, when } from '../api';
import { Empty, ErrorNote, SectionTitle, Tip } from '../ui';
import ShadowReset from '../ShadowReset';

function Sparkline({ bars }: { bars: any[] }) {
  const points = useMemo(() => {
    const values = bars.slice(-250).map(b => b.close as number);
    if (values.length < 2) return '';
    const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
    return values.map((v, i) => `${(i / (values.length - 1) * 600).toFixed(1)},${(122 - (v - min) / span * 105).toFixed(1)}`).join(' ');
  }, [bars]);
  if (!points) return <div className="mini-empty">Market history will appear after the first data refresh.</div>;
  return <svg viewBox="0 0 600 140" preserveAspectRatio="none" className="sparkline" role="img" aria-label="SPY closing prices over the available recent history"><defs><linearGradient id="spark-fill" x1="0" x2="0" y1="0" y2="1"><stop offset="0%" stopColor="var(--chart-line)" stopOpacity=".20"/><stop offset="100%" stopColor="var(--chart-line)" stopOpacity="0"/></linearGradient></defs><polygon points={`0,140 ${points} 600,140`} fill="url(#spark-fill)"/><polyline points={points} fill="none" stroke="var(--chart-line)" strokeWidth="2.3" vectorEffect="non-scaling-stroke"/></svg>;
}

function PerformanceLine({ records }: { records: any[] }) {
  const values = records.filter(r => r.equity_cents != null).map(r => r.equity_cents as number);
  if (!values.length) return <div className="mini-empty">Performance appears after portfolio snapshots have been recorded.</div>;
  const min = Math.min(...values), span = Math.max(...values) - min || 1;
  const groups: string[][] = [[]];
  records.forEach((r, i) => {
    if (r.equity_cents == null) { if (groups.at(-1)!.length) groups.push([]); return; }
    const x = records.length === 1 ? 300 : i / (records.length - 1) * 600;
    groups.at(-1)!.push(`${x.toFixed(1)},${(122 - (r.equity_cents - min) / span * 105).toFixed(1)}`);
  });
  return <svg viewBox="0 0 600 140" preserveAspectRatio="none" className="sparkline" role="img" aria-label="Recorded portfolio values; missing values are gaps">{groups.filter(g => g.length > 1).map((g,i) => <polyline key={i} points={g.join(' ')} fill="none" stroke="var(--chart-line)" strokeWidth="2.3" vectorEffect="non-scaling-stroke"/>)}{groups.filter(g => g.length === 1).map((g,i) => { const [cx,cy] = g[0].split(','); return <circle key={i} cx={cx} cy={cy} r="3" fill="var(--chart-line)"/>; })}</svg>;
}

export default function Overview({ navigate }: { navigate: (page: string) => void }) {
  const { mode, state, refresh } = useApp();
  const local = state?.access_scope === 'local';
  const live = mode === 'live';
  const { data, error } = useResource(`/overview?mode=${mode}`);
  const { data: portfolio } = useResource(local ? '/positions' : null);
  const { data: market } = useResource('/bars?symbol=SPY&interval=1d&limit=260');
  const [message, setMessage] = useState('');
  const [allPositions, setAllPositions] = useState(false);
  const accounts = data?.accounts || [];
  const totalField = (field:string) => accounts.length && accounts.every((a:any) => typeof a.balance?.[field] === 'number') ? accounts.reduce((sum:number,a:any) => sum + a.balance[field],0) : null;
  const summary = data?.summary || (live ? {equity:totalField('total_equity'),cash:totalField('total_cash'),account_count:accounts.length,synced:accounts[0]?.synced} : {equity:data?.equity_cents == null ? null : data.equity_cents / 100,cash:data?.state?.cash_cents / 100,settled_cash:data?.state?.settled_cash_cents / 100,unsettled_cash:data?.state?.unsettled_cash_cents / 100,initial:data?.state?.initial_cents / 100,realized:data?.realized_cents / 100,order_count:data?.activity_count});
  const positions = local ? (portfolio?.rows || []).filter((p:any) => p.mode === mode) : data?.positions || [];
  const orders = local && live ? accounts.flatMap((a:any) => a.orders || []) : (local ? data?.activity : data?.orders) || [];
  const daily = data?.daily_performance || [];
  const performance = live ? daily.map((p:any) => ({created:p.created,equity_cents:p.equity * 100})) : daily.length > 1 ? daily : data?.performance || [];
  const baseline = live ? daily[0]?.equity : summary.initial;
  const growth = baseline > 0 && summary.equity != null ? (summary.equity / baseline - 1) * 100 : null;
  const bars = market?.bars || [];
  const last = bars.at(-1), previous = bars.at(-2);
  const change = last && previous ? (last.close / previous.close - 1) * 100 : null;
  async function sync() { try { await api('/jobs/account/run',{method:'POST'}); setMessage('Account synchronization queued.'); refresh(); } catch(e:any) { setMessage(e.message); } }
  return <>
    <div className="page-heading"><div><div className="page-kicker"><span className="kicker-line"/> YOUR INVESTMENT WORKSPACE</div><h1>Portfolio overview<span className="heading-period">.</span></h1><p className="page-subtitle">Your {live ? 'Tradier account' : 'Shadow cash account'}, positions, and progress.</p></div><div className="heading-actions">{local ? live ? <button className="button secondary" onClick={sync}><RefreshCw size={15}/> Sync account</button> : <ShadowReset/> : <span className="soft-badge">READ ONLY</span>}</div></div>
    <ErrorNote text={error}/>{message && <div className="notice" role="status">{message}<button aria-label="Dismiss" onClick={() => setMessage('')}>×</button></div>}
    <div className="overview-grid">
      <section className="panel portfolio-panel"><div className="panel-top"><div><div className="eyebrow">{live ? 'LIVE ACCOUNT' : 'SHADOW ACCOUNT'} <Tip text={live ? 'Balances and positions read from Tradier. All trading decisions are made locally or in Tradier.' : 'A separate $10,000 simulated cash account. Entries and exits are managed in Investing.'}/></div><div className="portfolio-value">{money(summary.equity)}</div><p className="muted small">{live ? `${summary.account_count ?? 0} connected account${summary.account_count === 1 ? '' : 's'}` : 'Estimated value using cached market quotes'}</p></div><span className="account-badge"><span className="status-dot"/> {live ? 'LIVE' : 'SIMULATED'}</span></div><div className="portfolio-metrics"><div><span>{live ? 'Cash' : 'Settled cash'}</span><strong>{money(live ? summary.cash : summary.settled_cash)}</strong></div><div><span>Open positions</span><strong>{summary.open_positions ?? positions.length}</strong></div><div><span>{live ? 'Equity change' : 'Growth since reset'} <Tip text={live ? 'Change from the first retained daily equity snapshot. Deposits and withdrawals affect this number; it is not a cash-flow-adjusted investment return.' : 'Change from starting Shadow cash, including realized and unrealized simulated gains. Missing marks make equity unavailable.'}/></span><strong className={growth == null ? 'muted' : growth >= 0 ? 'positive' : 'negative'}>{pct(growth)}</strong></div></div>{!live && <p className="panel-caption">Unsettled sale proceeds {money(summary.unsettled_cash)} · Realized P/L {money(summary.realized)}</p>}</section>
      <section className="panel market-panel"><div className="panel-top"><div><div className="eyebrow">PORTFOLIO PERFORMANCE</div><h2>Account value</h2></div><span className="soft-badge">{live ? 'DAILY SNAPSHOTS' : 'SIMULATED'}</span></div><PerformanceLine records={performance}/><div className="chart-foot"><span>{performance.length ? when(performance[0].created) : 'AWAITING DATA'}</span><span>{performance.length ? when(performance.at(-1).created) : '—'}</span></div></section>
      <section className="panel positions-panel"><SectionTitle eyebrow="YOUR PORTFOLIO" title="Positions" action={positions.length > 8 ? <button className="text-link" onClick={() => setAllPositions(v => !v)}>{allPositions ? 'Show fewer' : 'Show all'}</button> : <span className="soft-badge">{mode.toUpperCase()}</span>}/>{positions.length ? <div className="table-wrap"><table><thead><tr><th>ASSET</th><th>SHARES</th><th>ENTRY</th><th>LAST</th><th>P/L %</th></tr></thead><tbody>{(allPositions ? positions : positions.slice(0,8)).map((p:any,i:number) => <tr key={`${p.symbol}-${i}`}><td><strong>{p.symbol}</strong><small className="cell-sub">{p.category}</small></td><td>{p.quantity}</td><td>{quotePrice(p.entry_price)}</td><td>{quotePrice(p.price)}</td><td className={p.pl_pct == null ? 'muted' : p.pl_pct >= 0 ? 'positive' : 'negative'}>{pct(p.pl_pct)}</td></tr>)}</tbody></table></div> : <Empty title="No open positions" description={live ? 'Positions reported by Tradier appear after account sync.' : 'Enter your first simulated position from Investing → Setups.'}/>}</section>
      <section className="panel activity-panel"><SectionTitle eyebrow="RECENT DECISIONS" title="Orders" action={<span className="soft-badge">{summary.order_count ?? orders.length}</span>}/>{orders.length ? <div className="activity-list">{orders.slice(0,10).map((o:any,i:number) => <div className="activity-row" key={i}><span className="activity-icon">{o.side?.startsWith('sell') ? <ArrowUpRight size={16}/> : <ArrowDownLeft size={16}/>}</span><div><strong>{o.side?.toUpperCase()} {o.symbol}</strong><small>{o.quantity} shares · {o.order_type ? `${o.order_type} · ` : ''}{o.status || (live ? 'Status unavailable' : 'simulated fill')}</small></div><span>{o.status === 'working' && o.limit_price != null ? `≥ ${quotePrice(o.limit_price)}` : quotePrice(o.fill_price ?? o.avg_fill_price ?? (o.price_cents != null ? o.price_cents / 100 : o.price))}</span></div>)}</div> : <Empty title="No recent orders" description={live ? 'Current-session Tradier orders synchronize here.' : 'Completed Shadow fills appear here after entry or exit.'}/>}</section>
      <section className="panel market-panel"><div className="panel-top"><div><div className="eyebrow">THE MARKET</div><h2>S&amp;P 500 <span className="ticker-label">SPY</span></h2></div><button className="icon-button" aria-label="Open charts" onClick={() => navigate('charts')}><ArrowUpRight size={17}/></button></div><div className="market-quote"><strong>{money(last?.close)}</strong><span className={change == null ? 'muted' : change >= 0 ? 'positive' : 'negative'}>{pct(change)}</span><span className="muted tiny">vs. prior close</span></div><Sparkline bars={bars}/></section>
    </div><div className="bottom-note"><ShieldCheck size={15}/><span>Live order submission is disabled. {local ? 'Manage Shadow entries and exits in Investing.' : 'This shared view is read-only.'}</span><span className="bottom-note-right"><Clock3 size={14}/> {live ? `Synced ${when(summary.synced)}` : 'Shadow cash account'}</span></div>
  </>;
}
