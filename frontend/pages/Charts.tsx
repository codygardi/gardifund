import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react';
import { createChart, ColorType, CrosshairMode, Time } from 'lightweight-charts';
import { BarChart3, LineChart } from 'lucide-react';
import { api, money, pagesBuild, pct, useApp, useResource } from '../api';
import { Empty, ErrorNote, Segmented, Tip } from '../ui';

const COLORS = ['#a6d68a','#dfb76e','#89b9c7','#d295a1','#bb9cce','#d8cf8a','#87c6a4','#df9579','#9bafe4','#d5a4cb','#8fb9a5'];
const LIGHT_COLORS = ['#387349','#9a6720','#327387','#a64d61','#75548a','#827520','#35785d','#a35439','#485e9a','#935482','#4d7564'];
const EMPTY_BARS: any[] = [];
const chartTime = (s: string): Time => (s.length === 10 ? s : Math.floor(new Date(s).getTime() / 1000)) as Time;
const FIRST_HISTORY_YEAR = 2010;
const MAX_WINDOW_DAYS = 5000;

const SNAPSHOT_TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'analysis', label: 'Analysis' },
] as const;

type SnapshotTab = typeof SNAPSHOT_TABS[number]['id'];
type Interval = '1d' | '1h';
type ChartSeries = { symbol: string; bars: any[]; color: string };
type PerformanceRow = { symbol: string; name: string; return_pct: number };
type Sector = { symbol: string; name: string };
type Coverage = { source: string; coverage_start: string; coverage_end: string; row_count: number; adjustment: string };
type SectorWindows = {
  start: string | null; end: string | null; basis: string | null; message: string | null;
  ranked: number; missing: number; incompatible: number; possible_windows: number;
  rows: (PerformanceRow & { profitable_window_pct: number; window_count: number; window_start: string; window_end: string })[];
};
const NO_COMPARISONS: ChartSeries[] = [];

function PriceChart({ bars, comparisons, symbol, compareFunds, chartType, volume, interval, light }: {
  bars: any[]; comparisons: ChartSeries[]; symbol: string; compareFunds: boolean; chartType: string; volume: boolean;
  interval: string; light: boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!host.current || !bars.length) return;
    const chart = createChart(host.current, { width: host.current.clientWidth, height: 480,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: light ? '#596d60' : '#87968b', fontFamily: 'Inter, system-ui, sans-serif', fontSize: 11 },
      grid: { vertLines: { color: 'rgba(135,150,139,.08)' }, horzLines: { color: 'rgba(135,150,139,.11)' } },
      rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false, timeVisible: interval !== '1d', secondsVisible: false, minBarSpacing: 0.03 },
      crosshair: { mode: CrosshairMode.Normal }, handleScroll: true, handleScale: true });
    if (!compareFunds) {
      if (chartType === 'candles') {
        const up = light ? '#428451' : '#a9d38f', down = light ? '#bd6557' : '#d48a7c';
        const series = chart.addCandlestickSeries({ upColor: up, downColor: down, borderVisible: false, wickUpColor: up, wickDownColor: down });
        series.setData(bars.map(b => ({ time: chartTime(b.time), open: b.open, high: b.high, low: b.low, close: b.close })));
      } else {
        const series = chart.addAreaSeries({ lineColor: light ? '#477e4a' : '#b5dd8e', topColor: light ? 'rgba(71,126,74,.16)' : 'rgba(181,221,142,.20)', bottomColor: 'rgba(181,221,142,.01)', lineWidth: 2 });
        series.setData(bars.map(b => ({ time: chartTime(b.time), value: b.close })));
      }
      if (volume) {
        const series = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: '' });
        series.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
        series.setData(bars.map(b => ({ time: chartTime(b.time), value: b.volume, color: b.close >= b.open ? 'rgba(169,211,143,.25)' : 'rgba(212,138,124,.25)' })));
      }
    } else {
      const selectedSeries = comparisons.filter(item => item.bars.length);
      const available = [{ symbol, bars, color: light ? '#263c2c' : '#ecf3e9' }, ...selectedSeries];
      const common = available.reduce((set, item) => new Set(item.bars.map(b => b.time).filter(t => set.has(t))), new Set<string>(bars.map(b => b.time)));
      const start = [...common].sort()[0];
      if (start) for (const item of available) {
        const base = item.bars.find(b => b.time === start)?.close;
        if (!base) continue;
        const points = item.bars.filter(b => b.time >= start).map(b => {
          const time = chartTime(b.time);
          const value = b.close == null ? NaN : (b.close / base - 1) * 100;
          return Number.isFinite(value) ? { time, value } : { time };
        });
        const hasGaps = points.some(point => point.value === undefined);
        const series = chart.addLineSeries({ color: item.color, lineWidth: item.symbol === 'SPY' ? 2 : 1,
          title: item.symbol, lineVisible: !hasGaps, pointMarkersVisible: hasGaps, pointMarkersRadius: 2,
          priceFormat: { type: 'custom', formatter: (n: number) => `${n.toFixed(1)}%` } });
        series.setData(points);
      }
    }
    chart.timeScale().fitContent();
    const observer = new ResizeObserver(() => { if (host.current) chart.applyOptions({ width: host.current.clientWidth }); });
    observer.observe(host.current);
    return () => { observer.disconnect(); chart.remove(); };
  }, [bars, comparisons, symbol, compareFunds, chartType, volume, interval, light]);
  return <div className="chart-host" ref={host}/>;
}

type ChartAppearance = { viewer: boolean; light: boolean; palette: string[] };
type FundSelection = {
  sectors: Sector[]; fund: string; setFund: (fund: string) => void;
  interval: Interval; setInterval: (interval: Interval) => void;
};

function FundSelect({ fund, setFund, sectors }: Pick<FundSelection, 'fund' | 'setFund' | 'sectors'>) {
  return <label>SPY-related fund
    <select value={fund} onChange={event => setFund(event.target.value)}>
      <option value="SPY">SPY · S&P 500</option>
      {sectors.map(sector => <option key={sector.symbol} value={sector.symbol}>{sector.symbol} · {sector.name}</option>)}
    </select>
  </label>;
}

function CoverageNote({ coverage, viewer }: { coverage?: Coverage | null; viewer: boolean }) {
  if (!coverage) return viewer ? null : <div className="coverage-note">No coverage available</div>;
  return <div className="coverage-note">
    <span>{`${viewer ? '' : coverage.source + ' · '}${coverage.coverage_start} to ${coverage.coverage_end}`}</span>
    <span>{coverage.adjustment === 'split_dividend_adjusted' ? 'Provider-declared adjusted' : 'Adjustments unverified'}
      <Tip text={viewer ? 'The displayed date range may contain gaps.' : 'The stored date range can contain gaps. Tradier daily prices may lack dividend adjustments, and Yahoo limits hourly history.'}/>
    </span>
  </div>;
}

function MarketChart({ active, viewer, light, palette, sectors, fund, setFund, interval, setInterval }: ChartAppearance & FundSelection & { active: boolean }) {
  const { state, marketVersions } = useApp();
  const [chartType, setChartType] = useState('candles');
  const [volume, setVolume] = useState(true);
  const [selected, setSelected] = useState<string[]>([]);
  const [compare, setCompare] = useState<ChartSeries[]>([]);
  const [older, setOlder] = useState<{key: string; bars: any[]; hasMore: boolean} | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const historyKeyRef = useRef('');
  const [comparisonMessage, setComparisonMessage] = useState('');
  const comparisonEpoch = useRef(0);
  const requestSequence = useRef<Record<string, number>>({});
  const seenVersions = useRef<Record<string, number>>({});
  const [message, setMessage] = useState('');
  const availableFunds = sectors.filter(sector => sector.symbol !== fund);
  const comparisonSymbols = active ? selected.filter(symbol => symbol !== fund) : [];
  const compareFunds = comparisonSymbols.length > 0;
  const { data, error } = useResource(active ? `/bars?symbol=${fund}&interval=${interval}&limit=12000` : null);
  const sectorsKey = sectors.map(sector => sector.symbol).join(',');

  async function loadComparison(symbol: string, seriesInterval: string, epoch: number) {
    const key = `${symbol}:${seriesInterval}`;
    const sequence = (requestSequence.current[key] || 0) + 1;
    requestSequence.current[key] = sequence;
    try {
      const result = await api(`/bars?symbol=${symbol}&interval=${seriesInterval}&limit=12000`);
      if (comparisonEpoch.current !== epoch || requestSequence.current[key] !== sequence) return null;
      setCompare(current => current.map(item => item.symbol === symbol ? { ...item, bars: result.bars } : item));
      return { symbol, bars: result.bars as any[], sequence, key };
    } catch {
      return null;
    }
  }

  useEffect(() => {
    const epoch = ++comparisonEpoch.current;
    setComparisonMessage('');
    if (!compareFunds) { setCompare([]); return; }
    const current = comparisonSymbols.map(symbol => {
      const key = `${symbol}:${interval}`;
      seenVersions.current[key] = marketVersions[key] || 0;
      const index = sectors.findIndex(sector => sector.symbol === symbol);
      return { symbol, bars: [], color: palette[index] || palette[0] };
    });
    setCompare(current);
    Promise.all(comparisonSymbols.map(symbol => loadComparison(symbol, interval, epoch))).then(results => {
      if (comparisonEpoch.current !== epoch || interval === '1d') return;
      const missing = results.filter(result => result && result.bars.length === 0 && requestSequence.current[result.key] === result.sequence).map(result => result!.symbol);
      if (!missing.length) return;
      if (state?.access_scope !== 'local') return;
      api('/market/sector-history', { method: 'POST', body: JSON.stringify({ interval, symbols: missing }) })
        .then(result => { if (comparisonEpoch.current === epoch && result.retrying?.length) setComparisonMessage(`Retrying unavailable series: ${result.retrying.join(', ')}.`); })
        .catch(error => { if (comparisonEpoch.current === epoch) setComparisonMessage(error.message); });
    });
    return () => { if (comparisonEpoch.current === epoch) comparisonEpoch.current++; };
  }, [comparisonSymbols.join(','), interval, sectorsKey, light, state?.access_scope]);

  useEffect(() => {
    const epoch = comparisonEpoch.current;
    for (const symbol of comparisonSymbols) {
      const key = `${symbol}:${interval}`;
      const version = marketVersions[key] || 0;
      if (version > (seenVersions.current[key] || 0)) {
        seenVersions.current[key] = version;
        void loadComparison(symbol, interval, epoch);
      }
    }
  }, [marketVersions, comparisonSymbols.join(','), interval, light]);
  const historyKey = `${fund}:${interval}:${data?.coverage?.updated ?? ''}`;
  historyKeyRef.current = historyKey;
  const baseBars = data?.bars || EMPTY_BARS;
  const earlier = older?.key === historyKey ? older.bars : EMPTY_BARS;
  const bars = useMemo(() => earlier.length ? [...earlier, ...baseBars] : baseBars, [earlier, baseBars]);
  const hasOlder = older?.key === historyKey ? older.hasMore : Boolean(data?.has_more);
  async function loadOlder() {
    if (!active || loadingOlder || !bars.length) return;
    const expected = historyKey;
    setLoadingOlder(true);
    try {
      const params = new URLSearchParams({symbol:fund, interval, limit:'12000', before:bars[0].time});
      const result = await api(`/bars?${params}`);
      if (historyKeyRef.current !== expected) return;
      setOlder(current => ({key: expected, bars: [...result.bars, ...(current?.key === expected ? current.bars : [])], hasMore: result.has_more}));
    } catch(e: any) { if (historyKeyRef.current === expected) setMessage(e.message); }
    finally { setLoadingOlder(false); }
  }
  const last = bars.at(-1), previous = bars.at(-2);
  const change = last && previous ? (last.close / previous.close - 1) * 100 : null;
  function toggle(symbol: string) { setSelected(current => current.includes(symbol) ? current.filter(x => x !== symbol) : [...current, symbol]); }
  const feedback = error || message;
  const missingComparisons = compareFunds ? compare.filter(series => !series.bars.length).map(series => series.symbol) : [];
  if (!active) return null;
  return <>
    <ErrorNote text={viewer && feedback ? 'Data temporarily unavailable.' : feedback}/>
    <section className="panel chart-panel" aria-label="Market overview chart">
      <div className="chart-heading">
        <div>
          <div className="eyebrow">
            MARKET OVERVIEW
            {!viewer && <Tip text="Daily market data comes from Tradier. Hourly data comes from Yahoo Finance via yfinance."/>}
          </div>
          <div className="chart-symbol">
            <h2>{fund}</h2>
            <span>{fund === 'SPY' ? 'SPDR S&P 500 ETF Trust' : 'Tradable SPY-related ETF'}</span>
          </div>
          <div className="chart-price">
            {money(last?.close)}
            <span className={change == null ? 'muted' : change >= 0 ? 'positive' : 'negative'}>
              {pct(change)} <small>vs. prior bar</small>
            </span>
          </div>
          <div className="chart-mode-controls">
            <FundSelect fund={fund} setFund={setFund} sectors={sectors}/>
          </div>
        </div>
        <div className="chart-tools">
          {bars.length > 0 && (!viewer || hasOlder) && <div className="history-controls">
            {!viewer && <span className="muted tiny">{bars.length.toLocaleString()} of {data?.coverage?.row_count?.toLocaleString() || '—'} stored {fund} bars loaded</span>}
            {hasOlder && <button type="button" className="button secondary" onClick={loadOlder} disabled={loadingOlder}>{loadingOlder ? 'Loading earlier bars…' : 'Load earlier bars'}</button>}
          </div>}
          <CoverageNote coverage={data?.coverage} viewer={viewer}/>
          {availableFunds.length > 0 && <div className="sector-picker">
            <div className="sector-picker-top">
              <div><strong>Compare sector ETFs</strong>{!viewer && <small>Normalized price change · ETF data must match the selected interval</small>}</div>
              <button type="button" className="text-link" onClick={() => setSelected(comparisonSymbols.length === availableFunds.length ? [] : availableFunds.map(s => s.symbol))}>
                {comparisonSymbols.length === availableFunds.length ? 'Clear all' : 'Select all'}
              </button>
            </div>
            <div className="sector-checks">{availableFunds.map(s => <label key={s.symbol}>
              <input type="checkbox" checked={selected.includes(s.symbol)} onChange={() => toggle(s.symbol)}/>
              <span className="legend-dot" style={{background: palette[sectors.indexOf(s)]}}/>
              <span>{s.name} <small>{s.symbol}</small></span>
            </label>)}</div>
            {missingComparisons.length > 0 && <div className="muted tiny">{interval === '1h' ? 'Hourly' : 'Daily'} data unavailable for {missingComparisons.join(', ')}.</div>}
            {comparisonMessage && <div className="muted tiny" role="status">{comparisonMessage}</div>}
          </div>}
        </div>
      </div>
      <div className="chart-toolbar">
        <div className="toolbar-group">
          <Segmented values={[{value:'1d',label:'Daily'},{value:'1h',label:'Hourly'}]} value={interval} onChange={value => setInterval(value as Interval)}/>
          <span className="toolbar-separator"/>
          <button className={`chart-tool ${chartType === 'candles' ? 'on' : ''}`} onClick={() => setChartType('candles')}
            disabled={compareFunds} title="Candlesticks" aria-label="Candlesticks" aria-pressed={!compareFunds && chartType === 'candles'}><BarChart3 size={16}/></button>
          <button className={`chart-tool ${chartType === 'line' ? 'on' : ''}`} onClick={() => setChartType('line')}
            disabled={compareFunds} title="Line chart" aria-label="Line chart" aria-pressed={compareFunds || chartType === 'line'}><LineChart size={16}/></button>
          <button className={`chart-tool volume-toggle ${volume ? 'on' : ''}`} disabled={compareFunds}
            onClick={() => setVolume(value => !value)} aria-pressed={!compareFunds && volume}>Volume</button>
        </div>
        {compareFunds ? <span className="muted tiny">Indexed to first common close</span> : !viewer && <span className="muted tiny">Drag to pan · scroll to zoom</span>}
      </div>
      {bars.length ? <PriceChart bars={bars} comparisons={compareFunds ? compare : NO_COMPARISONS} symbol={fund} compareFunds={compareFunds}
          chartType={chartType} volume={volume} interval={interval} light={light}/>
        : <div className="chart-empty"><Empty title="Waiting for market data"
          description={viewer ? undefined : 'Refresh stored market data from the local workspace. Missing history remains blank.'}/></div>}
    </section>
  </>;
}

function SnapshotAnalysis({ active, viewer }: { active: boolean; viewer: boolean }) {
  const maxYears = new Date().getFullYear() - FIRST_HISTORY_YEAR + 1;
  const [days, setDays] = useState('20');
  const [years, setYears] = useState(() => String(Math.min(maxYears, 16)));
  const [top, setTop] = useState('3');
  const [selection, setSelection] = useState(() => ({ days, years, top }));
  const query = new URLSearchParams({ mode: 'sector_windows', trading_days: selection.days,
    lookback_years: selection.years, top_n: selection.top });
  const { data, error } = useResource(active ? `/chart-performance?${query}` : null);
  const results = data as SectorWindows | null;
  if (!active) return null;
  return (
    <section className="panel chart-panel" aria-label="Rolling sector returns">
      <div className="eyebrow">ROLLING RETURNS</div>
      <div className="chart-symbol"><h2>Top sectors</h2>
        <Tip text="Sector ETFs represent the sectors. The ranking averages close-to-close returns across valid windows of the selected length. Profitable windows show the share of valid windows with a return above 0%."/>
      </div>
      <form className="chart-mode-controls sector-window-controls" onSubmit={event => {
        event.preventDefault();
        setSelection({ days, years, top });
      }}>
        <label>Top sectors
          <select value={top} onChange={event => setTop(event.target.value)}>
            {[1, 2, 3].map(n => <option key={n} value={n}>{n}</option>)}
          </select>
        </label>
        <label>Trading days
          <input type="number" min={1} max={MAX_WINDOW_DAYS} step={1} required value={days} onChange={event => setDays(event.target.value)}/>
        </label>
        <label>Lookback years
          <input type="number" min={1} max={maxYears} step={1} required value={years} onChange={event => setYears(event.target.value)}/>
        </label>
        <button className="button primary" type="submit">Calculate</button>
      </form>
      <div className="sector-window-results" aria-live="polite" aria-busy={!error && !results}>
        {error ? <ErrorNote text={viewer ? 'Sector analysis is unavailable.' : error}/> : results?.rows.length ? <>
          <p className="muted small">{selection.days} trading days · {results.start} to {results.end} · {results.ranked} sector ETFs compared</p>
          <div className="table-wrap"><table aria-label="Sector ETF rankings">
            <thead><tr><th scope="col">RANK</th><th scope="col">SECTOR ETF</th><th scope="col">AVERAGE RETURN</th>
              <th scope="col">PROFITABLE WINDOWS</th><th scope="col">WINDOWS</th><th scope="col">SAMPLE DATES</th></tr></thead>
            <tbody>{results.rows.map((row, index) => <tr key={row.symbol}>
              <td>{index + 1}</td>
              <td><strong>{row.name}</strong> <span className="ticker-label">{row.symbol}</span></td>
              <td className={row.return_pct >= 0 ? 'positive' : 'negative'}>{pct(row.return_pct)}</td>
              <td>{Number.isFinite(row.profitable_window_pct) ? `${row.profitable_window_pct.toFixed(1)}%` : '—'}</td>
              <td>{row.window_count.toLocaleString()} / {results.possible_windows.toLocaleString()}</td>
              <td><time dateTime={row.window_start}>{row.window_start}</time> to <time dateTime={row.window_end}>{row.window_end}</time></td>
            </tr>)}</tbody>
          </table></div>
          <p className="panel-caption">
            {results.basis === 'unverified' ? 'Price returns; adjustments unverified.' : 'Provider-declared split and dividend adjustments.'}
            {' '}Windows with missing closes are skipped, so sample dates can differ.
            {results.missing > 0 && ` ${results.missing} ETFs lacked a complete window.`}
            {results.incompatible > 0 && ` ${results.incompatible} ETFs used a different adjustment basis.`}
          </p>
        </> : <Empty title={results ? 'No comparable windows' : 'Calculating sector returns'} description={results?.message ?? undefined}/>}
      </div>
    </section>
  );
}

export default function Charts() {
  const { state, theme } = useApp();
  const [tab, setTab] = useState<SnapshotTab>('overview');
  const [fund, setFund] = useState('SPY');
  const [interval, setInterval] = useState<Interval>('1d');
  const viewer = pagesBuild || state?.access_scope === 'remote';
  const sectors: Sector[] = state?.sectors || [];
  const light = theme === 'light';
  const palette = light ? LIGHT_COLORS : COLORS;
  const fundSelection = { fund, setFund, interval, setInterval, sectors };

  function navigateTabs(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const count = SNAPSHOT_TABS.length;
    let next: number;
    switch (event.key) {
      case 'ArrowLeft': next = (index + count - 1) % count; break;
      case 'ArrowRight': next = (index + 1) % count; break;
      case 'Home': next = 0; break;
      case 'End': next = count - 1; break;
      default: return;
    }
    event.preventDefault();
    const id = SNAPSHOT_TABS[next].id;
    setTab(id);
    event.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`#snapshot-tab-${id}`)?.focus();
  }

  return <>
    <div className="page-heading snapshot-heading">
      <div>{!viewer && <div className="page-kicker"><span className="kicker-line"/> MARKET INTELLIGENCE</div>}
        <h1>Snapshot<span className="heading-period">.</span></h1>
        {!viewer && <p className="page-subtitle">See the market from a wider perspective.</p>}
      </div>
      <div className="segmented snapshot-tabs" role="tablist" aria-label="Snapshot views">
        {SNAPSHOT_TABS.map(({ id, label }, index) => <button key={id} type="button" role="tab" id={`snapshot-tab-${id}`}
          aria-controls={`snapshot-${id}`} aria-selected={tab === id} tabIndex={tab === id ? 0 : -1}
          className={tab === id ? 'selected' : ''} onClick={() => setTab(id)} onKeyDown={event => navigateTabs(event, index)}>{label}</button>)}
      </div>
    </div>
    <div role="tabpanel" id="snapshot-overview" aria-labelledby="snapshot-tab-overview" tabIndex={0} hidden={tab !== 'overview'}>
      <MarketChart active={tab === 'overview'} viewer={viewer} light={light} palette={palette} {...fundSelection}/>
    </div>
    <div role="tabpanel" id="snapshot-analysis" aria-labelledby="snapshot-tab-analysis" tabIndex={0} hidden={tab !== 'analysis'}>
      <SnapshotAnalysis active={tab === 'analysis'} viewer={viewer}/>
    </div>
  </>;
}
