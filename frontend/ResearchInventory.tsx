import { useState } from 'react';
import { api, useApp, useResource, when } from './api';
import { Empty, ErrorNote, SectionTitle } from './ui';

export type ResearchInstrument = { symbol: string; name: string };
type ResearchCoverage = {
  source: string; adjustment: string; coverage_start: string; coverage_end: string;
  row_count: number; updated: number; status: string; missing_sessions: number | null;
};
type ResearchCompany = ResearchInstrument & {
  weight: number; tickers: string[]; coverage: ResearchCoverage | null;
  history_status: 'missing' | 'stored' | 'queued' | 'running' | 'error'; history_message: string | null;
};
export type ResearchCatalog = {
  funds: (ResearchInstrument & { coverage: ResearchCoverage | null })[];
  groups: (ResearchInstrument & { as_of: string | null; source: string; updated: number | null;
    holdings: number; unclassified: number | null; weight_sum: number | null; companies: ResearchCompany[] })[];
  unique_tickers: number; tickers_with_history: number;
};

export default function ResearchInventory({ onNotice }: { onNotice: (message: string) => void }) {
  const { state, refresh } = useApp();
  const { data, error } = useResource(state?.access_scope === 'local' ? '/database/research' : null);
  const research = data as ResearchCatalog | null;
  const [group, setGroup] = useState('DIA');
  const [busy, setBusy] = useState(false);
  const watchlist = research?.groups.find(item => item.symbol === group);
  const benchmark = group === 'DIA' || group === 'QQQ';
  const holdingsJob = benchmark ? 'benchmark_holdings' : 'holdings';
  const holdingsRunning = state?.jobs?.some((job: { name: string; status: string }) => job.name === holdingsJob && job.status === 'running');
  const hasRequest = watchlist?.companies.some(company => company.history_status === 'queued' || company.history_status === 'running');
  const storedFunds = research?.funds.filter(item => item.coverage).length ?? 0;

  async function run(path: string, message: (result: any) => string) {
    setBusy(true);
    try {
      const result = await api(path, { method: 'POST' });
      onNotice(message(result));
      refresh();
    } catch (err) {
      onNotice(err instanceof Error ? err.message : 'Could not queue the research update.');
    } finally {
      setBusy(false);
    }
  }

  return <>
    <ErrorNote text={error}/>
    <section className="panel data-panel" aria-label="Fund price coverage">
      <SectionTitle title="Fund price coverage" action={
        <button className="button secondary" disabled={busy || state?.jobs?.some((job: { name: string; status: string }) => job.name === 'market' && job.status === 'running')}
          onClick={() => void run('/jobs/market/run', () => 'Market history refresh queued.')}>Refresh fund prices</button>}/>
      <p className="muted small">Daily ETF histories for the S&amp;P 500, Dow, Nasdaq-100, and 11 S&amp;P sectors.</p>
      {research && <details className="research-coverage">
        <summary>{storedFunds} of {research.funds.length} daily series stored</summary>
        <div className="table-wrap"><table aria-label="Research fund coverage">
          <thead><tr><th scope="col">FUND</th><th scope="col">SOURCE</th><th scope="col">DAILY COVERAGE</th><th scope="col">LAST UPDATED</th><th scope="col">STATUS</th></tr></thead>
          <tbody>{research.funds.map(fund => <tr key={fund.symbol}>
            <td><strong>{fund.symbol}</strong><small className="cell-sub">{fund.name}</small></td>
            <td>{fund.coverage?.source ?? 'Tradier'}</td>
            <td>{fund.coverage ? `${fund.coverage.coverage_start} to ${fund.coverage.coverage_end}` : '—'}</td>
            <td>{when(fund.coverage?.updated)}</td>
            <td><span className="data-status">{fund.coverage?.status ?? 'Awaiting prices'}</span>
              {fund.coverage?.missing_sessions != null && <small className="cell-sub">{fund.coverage.missing_sessions} missing sessions</small>}</td>
          </tr>)}</tbody>
        </table></div>
      </details>}
    </section>
    <section className="panel data-panel" aria-label="Company watchlists">
      <SectionTitle eyebrow="DATED HOLDINGS" title="Company watchlists"/>
      <p className="muted small">Up to 10 companies per group, ranked by fund weight. The largest share class supplies the price series.</p>
      {research && <div className="research-controls">
        <label>Watchlist group<select value={group} onChange={event => setGroup(event.target.value)}>
          {research.groups.map(item => <option key={item.symbol} value={item.symbol}>{item.name} · {item.symbol}</option>)}
        </select></label>
        <button className="button secondary" disabled={busy || holdingsRunning}
          onClick={() => void run(`/jobs/${holdingsJob}/run`, () => 'Holdings refresh queued.')}>
          {benchmark ? 'Refresh DIA and QQQ holdings' : 'Refresh SPY holdings'}
        </button>
        <button className="button primary" disabled={busy || hasRequest || !watchlist?.companies.length}
          onClick={() => void run(`/database/research/${group}/history`, result =>
            `Queued ${result.queued} daily series${result.pending ? ` · ${result.pending} already pending` : ''}.`)}>
          Load daily history
        </button>
      </div>}
      {watchlist?.as_of && <p className="panel-caption">
        {watchlist.source} · {watchlist.as_of} · {watchlist.holdings} holdings
        {!!watchlist.unclassified && ` · ${watchlist.unclassified} SPY holdings unclassified`}
        {watchlist.weight_sum != null && watchlist.weight_sum < .95 && ` · ${(watchlist.weight_sum * 100).toFixed(1)}% of fund weight available`}
      </p>}
      {watchlist?.companies.length ? <div className="table-wrap"><table aria-label="Company watchlist">
        <thead><tr><th scope="col">COMPANY</th><th scope="col">FUND WEIGHT</th><th scope="col">DAILY COVERAGE</th><th scope="col">IMPORT STATUS</th></tr></thead>
        <tbody>{watchlist.companies.map(company => <tr key={company.symbol}>
          <td><strong>{company.name}</strong><small className="cell-sub">{company.tickers.join(' · ')}</small></td>
          <td>{(company.weight * 100).toFixed(2)}%</td>
          <td>{company.coverage ? <>{company.coverage.coverage_start} to {company.coverage.coverage_end}<small className="cell-sub">{company.coverage.source}</small></> : '—'}</td>
          <td><span className="data-status">{{ missing: 'Not loaded', stored: 'Stored', queued: 'Queued', running: 'Loading', error: 'Needs review' }[company.history_status]}</span>
            {company.history_message && <small className="cell-sub">{company.history_message}</small>}</td>
        </tr>)}</tbody>
      </table></div> : <Empty title={research ? 'No dated companies for this group' : 'Loading company watchlists'}/>}
      {research && <p className="panel-caption">{research.tickers_with_history} of {research.unique_tickers} distinct watchlist tickers have daily prices.</p>}
    </section>
  </>;
}
