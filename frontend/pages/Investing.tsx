import { useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { api, useApp } from '../api';
import { Empty, ErrorNote, Tip } from '../ui';
import Screening from './Screening';
import Setups from './Setups';
import Positions from './Positions';

export type InvestingView = 'discovery' | 'setups' | 'positions';

export default function Investing({ view, onPositions }: { view: InvestingView; onPositions: () => void }) {
  const { state, refresh } = useApp();
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
    <section className="panel investing-panel"><div className="investing-header">
      <h2>{view === 'discovery' ? 'Discover SPY opportunities' : view === 'setups' ? 'Screen your setups' : 'Your positions'}</h2>
      {view === 'discovery' && <span className="discovery-cadence"><span className="status-dot"/> Every {state.settings.discovery_minutes} min <Tip text="Discovery runs from premarket through the regular-session close, even with the browser closed."/></span>}
    </div>
      {view === 'discovery' ? <Screening/> : view === 'setups' ? <Setups onPositions={onPositions}/> : <Positions/>}
    </section>
  </>;
}
