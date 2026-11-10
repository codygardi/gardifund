import { lazy, Suspense, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { api, money, pagesBuild, pct, useApp, useResource, when } from '../api';
import { Empty, ErrorNote, SectionTitle, Tip } from '../ui';
import type { ActivityKind } from './OverviewDetails';

const ShadowReset = import.meta.env.MODE === 'pages' ? null : lazy(() => import('../ShadowReset'));
const OverviewDetails = import.meta.env.MODE === 'pages' ? null : lazy(() => import('./OverviewDetails'));

type PerformancePoint = { created: number; equity_cents: number | null };

function PerformanceLine({ records }: { records: PerformancePoint[] }) {
  const values = records.flatMap(record => record.equity_cents == null ? [] : [record.equity_cents]);
  if (!values.length) return <div className="mini-empty">No performance history yet.</div>;
  const min = Math.min(...values), max = Math.max(...values), span = max - min || 1;
  const groups: string[][] = [[]];
  records.forEach((record, index) => {
    if (record.equity_cents == null) {
      if (groups.at(-1)!.length) groups.push([]);
      return;
    }
    const x = records.length === 1 ? 300 : (index / (records.length - 1)) * 600;
    const y = max === min ? 70 : 122 - (record.equity_cents - min) / span * 105;
    groups.at(-1)!.push(`${x.toFixed(1)},${y.toFixed(1)}`);
  });
  return <svg viewBox="0 0 600 140" preserveAspectRatio="none" className="sparkline" role="img"
    aria-label="Recorded portfolio values; missing values are gaps">
    {groups.map((group, index) => {
      if (group.length > 1) return <polyline key={index} points={group.join(' ')} fill="none" stroke="var(--chart-line)" strokeWidth="2.3" vectorEffect="non-scaling-stroke"/>;
      if (!group.length) return null;
      const [x, y] = group[0].split(',');
      return <circle key={index} cx={x} cy={y} r="3" fill="var(--chart-line)"/>;
    })}
  </svg>;
}

function ActivityPanel({ kind, count, mode, local }: {
  kind: ActivityKind; count: number | null; mode: 'live' | 'shadow'; local: boolean;
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const positions = kind === 'positions';
  const title = positions ? 'Positions' : 'Orders';
  const hasActivity = count != null && count > 0;
  return <section className={`panel ${positions ? 'positions-panel' : 'activity-panel'}`}>
    <SectionTitle eyebrow={positions ? 'YOUR PORTFOLIO' : 'RECENT DECISIONS'} title={title}
      action={<div className="overview-activity-actions">
        {local && OverviewDetails && hasActivity && <button type="button" className="text-link" aria-expanded={detailsOpen}
          aria-label={`${detailsOpen ? 'Hide' : 'View'} ${kind} details`}
          onClick={() => setDetailsOpen(open => !open)}>{detailsOpen ? 'Hide' : 'Details'}</button>}
        <span className="soft-badge">{positions ? mode.toUpperCase() : count ?? '—'}</span>
      </div>}/>
    {hasActivity ? <div className="empty overview-count">
      <strong>{count.toLocaleString()}</strong><p>{positions ? 'Open positions' : mode === 'live' ? 'Current-session orders' : 'Orders in this Shadow account'}</p>
    </div> : <Empty title={count == null ? `${title} unavailable` : positions ? 'No open positions' : 'No recent orders'}/>}
    {local && OverviewDetails && hasActivity && detailsOpen && <Suspense fallback={<p className="muted small">Loading details…</p>}>
      <OverviewDetails kind={kind} mode={mode}/>
    </Suspense>}
  </section>;
}

export default function Overview() {
  const { mode, state, refresh } = useApp();
  const local = !pagesBuild && state?.access_scope === 'local';
  const live = mode === 'live';
  const path = state ? `${local ? '/overview/summary' : '/overview'}?mode=${mode}` : null;
  const { data, error } = useResource(path);
  const [message, setMessage] = useState('');
  const summary = data?.summary;
  const unavailable = Boolean(error || (data && !summary));

  async function sync() {
    try {
      await api('/jobs/account/run', { method: 'POST' });
      setMessage('Account synchronization queued.');
      refresh();
    } catch (e: any) {
      setMessage(e.message);
    }
  }

  const daily = data?.daily_performance || [];
  const performance: PerformancePoint[] = live
    ? daily.map((record: { created: number; equity: number | null }) => ({
      created: record.created, equity_cents: record.equity == null ? null : record.equity * 100,
    }))
    : daily.length > 1 ? daily : data?.performance || [];
  const baseline = live ? daily[0]?.equity : summary?.initial;
  const growth = baseline > 0 && summary?.equity != null ? (summary.equity / baseline - 1) * 100 : null;
  const hasAccount = live ? summary?.account_count > 0 : summary?.initialized;
  const positions = hasAccount ? summary.open_positions ?? null : null;
  const orders = hasAccount ? summary.order_count ?? null : null;

  return <>
    <div className="page-heading">
      <div><h1>Account<span className="heading-period">.</span></h1>
        <p className="page-subtitle">Live and simulated portfolio performance.</p></div>
      {local && <div className="heading-actions">
        {live ? <button className="button secondary" onClick={sync}><RefreshCw size={15}/> Sync account</button> : ShadowReset && <ShadowReset/>}
      </div>}
    </div>
    {local && <ErrorNote text={error}/>}
    {local && message && <div className="notice" role="status">{message}<button aria-label="Dismiss" onClick={() => setMessage('')}>×</button></div>}
    {!summary ? <section className="panel" role="status">
      <Empty title={unavailable ? 'Account unavailable' : 'Loading account'}
        action={unavailable ? <button className="button secondary" onClick={refresh}><RefreshCw size={15}/> Try again</button> : undefined}/>
    </section> : <div className="overview-grid">
      <section className="panel portfolio-panel">
        <div className="panel-top">
          <div>
            <div className="eyebrow" title={live && summary.synced ? `Synced ${when(summary.synced)}` : undefined}>
              {live ? 'LIVE ACCOUNT' : 'SHADOW ACCOUNT'}
              <Tip text={live ? 'The latest account snapshot from Tradier.' : 'A simulated cash account.'}/>
            </div>
            <div className="portfolio-value">{money(summary.equity)}</div>
            <p className="muted small">{live
              ? `${summary.account_count ?? 0} connected account${summary.account_count === 1 ? '' : 's'}`
              : 'Estimated value using cached quotes'}</p>
          </div>
          <span className="account-badge"><span className="status-dot"/> {live ? 'LIVE' : 'SIMULATED'}</span>
        </div>
        <div className="portfolio-metrics">
          <div><span>{live ? 'Cash' : 'Settled cash'}</span><strong>{money(live ? summary.cash : summary.settled_cash)}</strong></div>
          <div><span>Open positions</span><strong>{positions ?? '—'}</strong></div>
          <div>
            <span>{live ? 'Equity change' : 'Growth since reset'} <Tip text={live
              ? 'Change from the first retained daily equity snapshot, including deposits and withdrawals.'
              : 'Change from the starting Shadow cash, including realized and unrealized simulated gains.'}/></span>
            <strong className={growth == null ? 'muted' : growth >= 0 ? 'positive' : 'negative'}>{pct(growth)}</strong>
          </div>
        </div>
        {!live && <p className="panel-caption">Unsettled proceeds {money(summary.unsettled_cash)} · Realized P/L {money(summary.realized)}</p>}
      </section>
      <section className="panel performance-panel">
        <div className="panel-top">
          <div><div className="eyebrow">PORTFOLIO PERFORMANCE</div><h2>Account value</h2></div>
          <span className="soft-badge">{live ? 'DAILY SNAPSHOTS' : 'SIMULATED'}</span>
        </div>
        <PerformanceLine records={performance}/>
        {performance.length > 0 && <div className="chart-foot"><span>{when(performance[0].created)}</span><span>{when(performance.at(-1)?.created)}</span></div>}
      </section>
      <ActivityPanel key={`positions-${mode}`} kind="positions" count={positions} mode={mode} local={local}/>
      <ActivityPanel key={`orders-${mode}`} kind="orders" count={orders} mode={mode} local={local}/>
    </div>}
  </>;
}
