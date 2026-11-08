import { useState } from 'react';
import { ArrowRight, RefreshCw } from 'lucide-react';
import { api, useApp } from '../api';
import { Empty, ErrorNote, Segmented, Tip } from '../ui';
import Screening from './Screening';
import Setups from './Setups';
import Positions from './Positions';

export default function Investing() {
  const { state, refresh } = useApp();
  const [view, setView] = useState('discovery');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  async function run() {
    try { await api('/jobs/discovery/run',{method:'POST'}); setMessage('SPY discovery queued.'); setError(''); refresh(); }
    catch(e:any) { setError(e.message); }
  }
  if (state?.access_scope !== 'local') return <Empty title="Investing is local only" description="Open the local workspace to screen opportunities and manage positions."/>;
  return <>
    <div className="page-heading"><div><div className="page-kicker"><span className="kicker-line"/> SPY RESEARCH WORKSPACE</div><h1>Investing<span className="heading-period">.</span></h1><p className="page-subtitle">Discover, screen, and manage SPY-related positions.</p></div>{view === 'discovery' && <button className="button secondary" onClick={run}><RefreshCw size={15}/> Run discovery</button>}</div>
    <ErrorNote text={error}/>{message && <div className="notice" role="status">{message}<button aria-label="Dismiss" onClick={() => setMessage('')}>×</button></div>}
    <div className="workflow"><div><span className="workflow-number">01</span><div><strong>Discovery</strong><small>Current SPY universe</small></div></div><ArrowRight size={16}/><div><span className="workflow-number">02</span><div><strong>Setups</strong><small>Screen &amp; enter</small></div></div><ArrowRight size={16}/><div><span className="workflow-number">03</span><div><strong>Positions</strong><small>Live &amp; Shadow</small></div></div><div className="workflow-status"><span className="status-dot"/> Every {state.settings.discovery_minutes} min <Tip text="Discovery scans SPY and its current constituents from premarket to the regular-session close. The background worker runs with the browser closed."/></div></div>
    <section className="panel investing-panel"><div className="investing-header"><div><div className="eyebrow">YOUR INVESTING PIPELINE</div><h2>{view === 'discovery' ? 'Discover SPY opportunities' : view === 'setups' ? 'Screen your setups' : 'Your positions'}</h2></div><Segmented values={[{value:'discovery',label:'Discovery'},{value:'setups',label:'Setups'},{value:'positions',label:'Positions'}]} value={view} onChange={setView}/></div>
      {view === 'discovery' ? <Screening/> : view === 'setups' ? <Setups onPositions={() => setView('positions')}/> : <Positions/>}
    </section>
  </>;
}
