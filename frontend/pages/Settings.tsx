import { useEffect, useState } from 'react';
import { Check, Cloud, Clock3, Database, HardDriveDownload, KeyRound, Laptop, Moon, RefreshCw, ShieldCheck, Sun, Wallet } from 'lucide-react';
import { api, money, useApp, useResource, when } from '../api';
import { Tip } from '../ui';
import ShadowReset from '../ShadowReset';

export default function SettingsPage() {
  const { state, refresh, theme, setTheme } = useApp();
  const { data: shadow } = useResource(state?.access_scope === 'local' ? '/overview?mode=shadow' : null);
  const { data: backups } = useResource(state?.access_scope === 'local' ? '/backups' : null);
  const [discovery, setDiscovery] = useState('5');
  const [account, setAccount] = useState('15');
  const [message, setMessage] = useState('');
  useEffect(() => { if (state) { setDiscovery(String(state.settings.discovery_minutes)); setAccount(String(state.settings.account_minutes)); } }, [state?.settings?.discovery_minutes, state?.settings?.account_minutes]);
  async function saveSchedule(e: React.FormEvent) { e.preventDefault(); try { await api('/settings', {method:'PATCH', body:JSON.stringify({discovery_minutes:Number(discovery),account_minutes:Number(account)})}); setMessage('Schedules saved.'); refresh(); } catch(e:any) { setMessage(e.message); } }
  async function run(name:string) { try { await api(`/jobs/${name}/run`, {method:'POST'}); setMessage(`${name[0].toUpperCase()+name.slice(1)} job queued.`); refresh(); } catch(e:any) { setMessage(e.message); } }
  async function backup() { try { const result = await api('/backups',{method:'POST'}); setMessage(`Backup created: ${result.name}`); refresh(); } catch(e:any) { setMessage(e.message); } }
  async function appearance(value:string) { try { await api('/settings',{method:'PATCH',body:JSON.stringify({appearance:value})}); setTheme(value as 'dark'|'light'); refresh(); } catch(e:any) { setMessage(e.message); } }
  if (state?.access_scope !== 'local') return null;
  const shareMode = state.connection?.auth_mode;
  const sharingEnabled = shareMode === 'public' || shareMode === 'cloudflare';
  return <>
    <div className="page-heading"><div><div className="page-kicker"><span className="kicker-line"/> MAKE IT YOURS</div><h1>Settings<span className="heading-period">.</span></h1><p className="page-subtitle">The connections and controls behind your workspace.</p></div></div>
    {message && <div className="notice" role="status">{message}<button aria-label="Dismiss" onClick={() => setMessage('')}>×</button></div>}
    <div className="settings-grid"><section className="panel settings-panel"><div className="settings-heading"><div className="settings-icon"><KeyRound size={18}/></div><div><h2>Connections</h2><p>Data access stays on this machine.</p></div></div><div className="setting-row"><div><strong>Tradier production</strong><small>Account, quotes, and daily prices · backend only</small></div><span className={`connection-pill ${state?.connection?.tradier_configured ? '' : 'off'}`}><span className="status-dot"/> {state?.connection?.tradier_configured ? 'Key configured' : 'Key missing'}</span></div><div className="setting-row"><div><strong>Yahoo Finance via yfinance</strong><small>SPY and sector ETF intraday history · limited coverage</small></div><span className="soft-badge">PROVIDER</span></div><p className="panel-caption">The configured key is never exposed to the browser. Provider availability is reflected in job status below.</p></section>
      <section className="panel settings-panel"><div className="settings-heading"><div className="settings-icon"><Clock3 size={18}/></div><div><h2>Schedules</h2><p>One worker keeps your workspace current.</p></div></div><form onSubmit={saveSchedule}><div className="settings-form"><label>Discovery interval <span>minutes</span><input type="number" min="5" max="1440" value={discovery} onChange={e => setDiscovery(e.target.value)} required/></label><label>Account sync interval <span>minutes</span><input type="number" min="5" max="1440" value={account} onChange={e => setAccount(e.target.value)} required/></label><button className="button primary" type="submit">Save schedule</button></div></form><div className="job-list">{state?.jobs?.map((job:any) => <div key={job.name}><div><strong>{job.name === 'holdings' ? 'SPY holdings' : job.name === 'market' ? 'Market history' : job.name === 'account' ? 'Account sync' : job.name === 'adjustments' ? 'Adjustment comparison' : job.name === 'portfolio' ? 'Portfolio marks & queued Shadow entries' : 'SPY discovery'}</strong><small>{job.message || 'Waiting for first run'} · Next {when(job.next_run)}</small></div><span className={`job-status ${job.status}`}>{job.status}</span><button className="row-action" onClick={() => run(job.name)} aria-label={`Run ${job.name} now`} title="Run now"><RefreshCw size={15}/></button></div>)}<div><div><strong>Sector hourly history</strong><small>{state?.sector_history?.complete ?? 0} of {state?.sector_history?.total ?? 11} series refreshed · one series per background job</small>{state?.sector_history?.failures?.map((failure:any) => <small key={failure.series}>{failure.series}: {failure.message} · retry {when(failure.next_run)}</small>)}</div><span className={state?.sector_history?.errors ? 'job-status error' : 'job-status'}>{state?.sector_history?.errors ? `${state.sector_history.errors} retrying` : state?.sector_history?.running ? 'running' : 'scheduled'}</span></div></div></section>
      <section className="panel settings-panel"><div className="settings-heading"><div className="settings-icon"><Wallet size={18}/></div><div><h2>Shadow cash account</h2><p>A separate $10,000 starting balance.</p></div></div><div className="setting-row"><div><strong>Settled cash</strong><small>Available for new simulated purchases</small></div><strong>{money(shadow?.state?.settled_cash_cents / 100)}</strong></div><div className="setting-row"><div><strong>Unsettled sale proceeds</strong><small>Conservative T+1 settlement calendar</small></div><strong>{money(shadow?.state?.unsettled_cash_cents / 100)}</strong></div><ShadowReset/><p className="panel-caption">Manage entries and exits in Investing. Market simulations use fresh ask/bid quotes. Fees, order-book priority and market impact are not modeled.</p></section>
      <section className="panel settings-panel"><div className="settings-heading"><div className="settings-icon"><Cloud size={18}/></div><div><h2>Sessions & providers</h2><p>Explicit sources for every series.</p></div></div><div className="setting-row"><div><strong>Discovery session</strong><small>Tradier market clock: premarket through regular-session close</small></div><span className="soft-badge">PREMARKET + OPEN</span></div><div className="setting-row"><div><strong>Intraday history session</strong><small>Regular US market session for Yahoo intraday data</small></div><span className="soft-badge">REGULAR</span></div><div className="setting-row"><div><strong>Historical adjustments</strong><small>Tradier and Yahoo data remain unverified until independently checked</small></div><Tip text="Provider-declared adjustments on imported data are recorded but are not independently certified."/></div></section>
      <section className="panel settings-panel"><div className="settings-heading"><div className="settings-icon"><Database size={18}/></div><div><h2>Storage & backups</h2><p>SQLite WAL and local Parquet files.</p></div></div><div className="setting-row"><div><strong>Local data directory</strong><small>Workspace data/ folder; backups exclude credentials</small></div><button className="button secondary" onClick={backup}><HardDriveDownload size={15}/> Create backup</button></div><div className="setting-row"><div><strong>Most recent backup</strong><small>{backups?.backups?.[0]?.name || 'No backups created yet'}</small></div><span className="muted small">{when(backups?.backups?.[0]?.created)}</span></div><p className="panel-caption">Backups contain private account data. Store them securely; restore while the app is stopped.</p></section>
      <section className="panel settings-panel">
        <div className="settings-heading">
          <div className="settings-icon"><ShieldCheck size={18}/></div>
          <div><h2>Access</h2><p>Control how this workspace is reached.</p></div>
        </div>
        <div className="setting-row">
          <div>
            <strong>Read-only dashboard</strong>
            <small>{sharingEnabled ? `Loopback viewer on port ${state.connection.remote_port}` : 'Enable the Worker connection in the local .env'}</small>
          </div>
          <span className="soft-badge">
              {shareMode === 'public' ? 'PUBLIC READ ONLY' : !sharingEnabled ? 'LOCAL ONLY' : state.connection.access_configured ? 'JWT CONFIGURED' : 'ACCESS SETUP PENDING'}
          </span>
        </div>
        <div className="setting-row">
          <div><strong>Live order submission</strong><small>Open or close real positions in Tradier</small></div>
          <span className="connection-pill">DISABLED</span>
        </div>
        <p className="panel-caption">The dashboard on GitHub Pages shows aggregate account metrics, market charts, and the Back Test placeholder.</p>
      </section>
      <section className="panel settings-panel appearance-panel"><div className="settings-heading"><div className="settings-icon"><Laptop size={18}/></div><div><h2>Appearance</h2><p>Choose your workspace theme.</p></div></div><div className="theme-options"><button onClick={() => appearance('dark')} className={theme === 'dark' ? 'theme-choice selected' : 'theme-choice'}><Moon size={17}/> Dark {theme === 'dark' && <Check size={15}/>}</button><button onClick={() => appearance('light')} className={theme === 'light' ? 'theme-choice selected' : 'theme-choice'}><Sun size={17}/> Light {theme === 'light' && <Check size={15}/>}</button></div></section>
    </div>
  </>;
}
