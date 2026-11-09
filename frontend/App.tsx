import { lazy, Suspense, useEffect, useState } from 'react';
import { ArrowUpRight, BriefcaseBusiness, ChartNoAxesCombined, Database, FlaskConical, LayoutDashboard, Moon, Newspaper, Settings, Sparkles, Sun } from 'lucide-react';
import { AppProvider, pagesBuild, useApp } from './api';
import type { InvestingView } from './pages/Investing';
import { Loading, Segmented } from './ui';

const Overview = lazy(() => import('./pages/Overview'));
const Investing = import.meta.env.MODE === 'pages' ? null : lazy(() => import('./pages/Investing'));
const Charts = lazy(() => import('./pages/Charts'));
const Data = import.meta.env.MODE === 'pages' ? null : lazy(() => import('./pages/Data'));
const SettingsPage = import.meta.env.MODE === 'pages' ? null : lazy(() => import('./pages/Settings'));

const nav = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'investing', label: 'Investing', icon: BriefcaseBusiness },
  { id: 'charts', label: 'Charts', icon: ChartNoAxesCombined },
  { id: 'news', label: 'News Engine', shortLabel: 'News', icon: Newspaper },
  { id: 'backtest', label: 'Back Test Engine', shortLabel: 'Back Test', icon: FlaskConical },
  { id: 'database', label: 'Database', icon: Database },
  { id: 'settings', label: 'Settings', icon: Settings },
];

const investingViews: {value: InvestingView; label: string}[] = [
  { value: 'discovery', label: 'Discovery' },
  { value: 'setups', label: 'Setups' },
  { value: 'positions', label: 'Positions' },
];

function Brand() {
  return <><span className="brand-mark" aria-hidden="true"><span/><span/><span/></span><span>GARD<span className="brand-i">i</span>FUND</span></>;
}

function Offline() {
  return <main className="offline-page" id="main-content">
    <div className="brand"><Brand/></div>
    <h1>Portfolio &amp; markets<span className="heading-period">.</span></h1>
    <p className="page-subtitle">A read-only view of portfolio performance and SPY.</p>
    <div className="offline-status" role="status">
      <strong><span className="status-dot muted" aria-hidden="true"/> Dashboard offline</strong>
      <p>Contact the admin to start the GARDiFUND server.</p>
    </div>
    <button className="button secondary" onClick={() => window.location.reload()}>Try again</button>
  </main>;
}

function Shell() {
  const [page, setPage] = useState('overview');
  const [investingView, setInvestingView] = useState<InvestingView>('discovery');
  const [viewerOffline, setViewerOffline] = useState(false);
  const { state, stateUnavailable, connected, mode, setMode, theme, setTheme } = useApp();
  const local = !pagesBuild && state?.access_scope === 'local';
  const viewer = pagesBuild || state?.access_scope === 'remote';
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
  function navigate(next: string) {
    if (next === 'investing' && page !== next) setInvestingView('discovery');
    setPage(next);
  }

  if (viewer && (stateUnavailable || viewerOffline)) return <Offline/>;
  return <div className="app-shell">
    <header className="app-header">
      <div className="topbar">
        <button type="button" className="brand" onClick={() => navigate('overview')} aria-label="GARDiFUND home"><Brand/></button>
        <nav className="primary-nav" aria-label="Main navigation">
          {visibleNav.map(item => <button type="button" key={item.id} onClick={() => navigate(item.id)}
            className={`nav-item ${page === item.id ? 'active' : ''}`} aria-current={page === item.id ? 'page' : undefined} aria-label={item.label}>
            <item.icon size={17} strokeWidth={1.7} aria-hidden="true"/>
            <span className={item.shortLabel ? 'nav-wide-label' : undefined}>{item.label}</span>
            {item.shortLabel && <span className="nav-short-label" aria-hidden="true">{item.shortLabel}</span>}
          </button>)}
        </nav>
        <div className="top-actions">
          <span className="connection" role="status" title={connected ? 'Connected' : 'Connecting'}>
            <span className={connected ? 'status-dot' : 'status-dot muted'} aria-hidden="true"/>
            <span className="connection-text">{connected ? 'Connected' : 'Connecting'}</span>
          </span>
          <Segmented values={[{value:'live',label:'Live'},{value:'shadow',label:'Shadow'}]} value={mode} onChange={v => setMode(v as 'live'|'shadow')}/>
          <button type="button" className="icon-button" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')} aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}>{theme === 'dark' ? <Sun size={16}/> : <Moon size={16}/>}</button>
        </div>
      </div>
      {local && page === 'investing' && <div className="topbar-context">
        <nav className="investing-nav" aria-label="Investing views">
          <Segmented values={investingViews} value={investingView} onChange={view => setInvestingView(view as InvestingView)}/>
        </nav>
      </div>}
    </header>
    <main className="content" id="main-content">
      <Suspense fallback={<Loading/>}>
        {page === 'overview' && <Overview/>}
        {local && Investing && page === 'investing' && <Investing view={investingView} onPositions={() => setInvestingView('positions')}/>}
        {page === 'charts' && <Charts/>}
        {local && Data && page === 'database' && <Data/>}
        {local && SettingsPage && page === 'settings' && <SettingsPage/>}
        {isPlaceholder && (viewer
          ? <div className="placeholder-page"><h1>{label}</h1><p className="page-subtitle">Backtests will appear here.</p></div>
          : <div className="placeholder-page"><div className="page-kicker"><Sparkles size={14}/> COMING INTO FOCUS</div><h1>{label}</h1><p className="page-subtitle">A dedicated space for {page === 'news' ? 'market news and research' : 'testing investment ideas against history'}.</p><div className="placeholder-card"><div className="placeholder-icon">{page === 'news' ? <Newspaper size={27}/> : <FlaskConical size={27}/>}</div><h2>Built for what comes next.</h2><p>This module is a placeholder. No {page === 'news' ? 'news feeds are connected' : 'backtests have been run'} yet.</p><button className="button secondary" onClick={() => navigate('overview')}>Back to overview <ArrowUpRight size={15}/></button></div></div>)}
      </Suspense>
    </main>
  </div>;
}
export default function App() { return <AppProvider><Shell/></AppProvider>; }
