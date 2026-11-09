import { lazy, Suspense, useEffect, useState } from 'react';
import { Activity, ArrowUpRight, BriefcaseBusiness, ChartNoAxesCombined, Database, FlaskConical, LayoutDashboard, Moon, Newspaper, Settings, ShieldCheck, Sparkles, Sun } from 'lucide-react';
import { AppProvider, useApp } from './api';
import { Empty, Loading, Segmented } from './ui';

const Overview = lazy(() => import('./pages/Overview'));
const Investing = lazy(() => import('./pages/Investing'));
const Charts = lazy(() => import('./pages/Charts'));
const Data = lazy(() => import('./pages/Data'));
const SettingsPage = lazy(() => import('./pages/Settings'));

const nav = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'investing', label: 'Investing', icon: BriefcaseBusiness },
  { id: 'charts', label: 'Charts', icon: ChartNoAxesCombined },
  { id: 'news', label: 'News Engine', icon: Newspaper },
  { id: 'backtest', label: 'Back Test Engine', icon: FlaskConical },
  { id: 'database', label: 'Database', icon: Database },
  { id: 'settings', label: 'Settings', icon: Settings },
];

function Shell() {
  const [page, setPage] = useState('overview');
  const [viewerOffline, setViewerOffline] = useState(false);
  const { state, connected, mode, setMode, theme, setTheme } = useApp();
  const local = state?.access_scope === 'local';
  const viewer = state?.access_scope === 'remote';
  useEffect(() => {
    if (connected || !viewer) {
      setViewerOffline(false);
      return;
    }
    const timer = setTimeout(() => setViewerOffline(true), 8000);
    return () => clearTimeout(timer);
  }, [connected, viewer]);
  const visibleNav = nav.filter(item => local || ['overview','charts','backtest'].includes(item.id));
  const label = nav.find(n => n.id === page)?.label;
  const isPlaceholder = page === 'news' || page === 'backtest';
  return <div className="app-shell">
    <aside className="sidebar">
      <button className="brand" onClick={() => setPage('overview')} aria-label="GARDiFUND home">
        <span className="brand-mark"><span/><span/><span/></span><span>GARD<span className="brand-i">i</span>FUND<small>INVESTMENT WORKSPACE</small></span>
      </button>
      <div className="nav-label">WORKSPACE</div>
      <nav aria-label="Main navigation">{visibleNav.map(item => <div key={item.id}>{item.id === 'database' && <div className="nav-label nav-second">MANAGE</div>}<button onClick={() => setPage(item.id)} className={`nav-item ${page === item.id ? 'active' : ''}`}><item.icon size={17} strokeWidth={1.7}/><span>{item.label}</span>{page === item.id && <span className="active-dot"/>}</button></div>)}</nav>
      <div className="sidebar-bottom"><div className="engine-card"><div className="engine-icon"><Activity size={16}/></div><div><strong>Background engine</strong><span><i className={connected ? 'status-dot' : 'status-dot muted'}/>{connected ? 'Workspace connected' : 'Reconnecting'}</span></div></div><div className="sidebar-foot"><ShieldCheck size={14}/> Private workspace <span>v0.1</span></div></div>
    </aside>
    <div className="main-area">
      <header className="topbar"><div className="breadcrumb"><span>Workspace</span><span className="crumb-divider">/</span><strong>{label}</strong></div><div className="top-actions"><span className="connection"><i className={connected ? 'status-dot' : 'status-dot muted'}/><span>{connected ? 'Synced workspace' : 'Connecting'}</span></span><Segmented values={[{value:'live',label:'Live'},{value:'shadow',label:'Shadow'}]} value={mode} onChange={v => setMode(v as 'live'|'shadow')}/><button className="icon-button" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}>{theme === 'dark' ? <Sun size={16}/> : <Moon size={16}/>}</button><span className="avatar">G</span></div></header>
      <main className="content" id="main-content">{viewerOffline ? <section className="panel" role="status"><Empty title="Dashboard offline" description="Contact the admin to start the GARDiFUND server. Reload if you need to sign in again." action={<button className="button secondary" onClick={() => window.location.reload()}>Try again</button>}/></section> : <Suspense fallback={<Loading/>}>
        {page === 'overview' && <Overview navigate={setPage}/>}{local && page === 'investing' && <Investing/>}{page === 'charts' && <Charts/>}{local && page === 'database' && <Data/>}{local && page === 'settings' && <SettingsPage/>}
        {isPlaceholder && <div className="placeholder-page"><div className="page-kicker"><Sparkles size={14}/> COMING INTO FOCUS</div><h1>{label}</h1><p className="page-subtitle">A dedicated space for {page === 'news' ? 'market news and research' : 'testing investment ideas against history'}.</p><div className="placeholder-card"><div className="placeholder-icon">{page === 'news' ? <Newspaper size={27}/> : <FlaskConical size={27}/>}</div><h2>Built for what comes next.</h2><p>This module is a placeholder. No {page === 'news' ? 'news feeds are connected' : 'backtests have been run'} yet.</p><button className="button secondary" onClick={() => setPage('overview')}>Back to overview <ArrowUpRight size={15}/></button></div></div>}
      </Suspense>}</main>
      <footer className="mobile-nav">{visibleNav.slice(0,4).map(item => <button key={item.id} className={page === item.id ? 'active' : ''} onClick={() => setPage(item.id)}><item.icon size={18}/><span>{item.label}</span></button>)}{local && <button className={['database','settings','backtest'].includes(page) ? 'active' : ''} onClick={() => setPage(page === 'settings' ? 'database' : 'settings')}><Settings size={18}/><span>Manage</span></button>}</footer>
    </div>
  </div>;
}
export default function App() { return <AppProvider><Shell/></AppProvider>; }
