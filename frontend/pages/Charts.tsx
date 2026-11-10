import { type KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react';
import { createChart, ColorType, CrosshairMode, Time } from 'lightweight-charts';
import { BarChart3, LineChart } from 'lucide-react';
import { api, money, pagesBuild, pct, useApp, useResource } from '../api';
import { Empty, ErrorNote, SectionTitle, Segmented, Tip } from '../ui';

const COLORS = ['#a6d68a','#dfb76e','#89b9c7','#d295a1','#bb9cce','#d8cf8a','#87c6a4','#df9579','#9bafe4','#d5a4cb','#8fb9a5'];
const LIGHT_COLORS = ['#387349','#9a6720','#327387','#a64d61','#75548a','#827520','#35785d','#a35439','#485e9a','#935482','#4d7564'];
const EMPTY_BARS: any[] = [];
const chartTime = (s: string): Time => (s.length === 10 ? s : Math.floor(new Date(s).getTime() / 1000)) as Time;
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

const SNAPSHOT_TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'data', label: 'Data' },
  { id: 'analysis', label: 'Analysis' },
] as const;

type SnapshotTab = typeof SNAPSHOT_TABS[number]['id'];
type RankingMode = 'market' | 'etfs';
type PeriodView = 'history' | 'year' | 'month';
type Interval = '1d' | '1h';
type ChartSeries = { symbol: string; bars: any[]; color: string };
type PerformanceRow = { symbol: string; name: string; return_pct: number };
type Performance = { start: string | null; end: string | null; rows: PerformanceRow[];
  series: Omit<ChartSeries, 'color'>[]; complete: boolean; basis: string | null; message: string };
type Sector = { symbol: string; name: string };
type Bar = { time: string; open: number; high: number; low: number; close: number; volume: number };
type Coverage = { source: string; coverage_start: string; coverage_end: string; row_count: number; adjustment: string };
const NO_COMPARISONS: ChartSeries[] = [];

function storedRankCount(key: string) {
  try {
    const value = Number(localStorage.getItem(key) ?? '5');
    return Number.isInteger(value) ? Math.max(1, Math.min(5, value)) : 5;
  } catch {
    return 5;
  }
}

function useRanking(mode: RankingMode, period: PeriodView) {
  const today = new Date();
  const currentYear = today.getFullYear();
  const [year, setYear] = useState(String(currentYear));
  const [month, setMonth] = useState(today.getMonth() + 1);
  const [rankCounts, setRankCounts] = useState<Record<string, number>>({});
  const monthLimit = Number(year) === currentYear ? today.getMonth() + 1 : 12;
  const selectedMonth = Math.min(month, monthLimit);
  const periodLabel = period === 'month' ? `${MONTHS[selectedMonth - 1]} ${year}` : year;
  const rankKey = `gardifund-rank-${mode}-${year}${period === 'month' ? '-' + selectedMonth : ''}`;
  const rankCount = mode === 'market' ? 1 : rankCounts[rankKey] ?? storedRankCount(rankKey);
  const params = new URLSearchParams({mode, year, top_n: String(rankCount)});
  if (period === 'month') params.set('month', String(selectedMonth));
  const { data, error } = useResource(period === 'history' ? null : `/chart-performance?${params}`);
  function saveRankCount(value: number) {
    setRankCounts(current => ({ ...current, [rankKey]: value }));
    try { localStorage.setItem(rankKey, String(value)); } catch { /* Keep the current session's choice. */ }
  }
  return { performance: data as Performance | null, error, currentYear, year, setYear, monthLimit, selectedMonth,
    setMonth, periodLabel, rankCount, saveRankCount };
}

function RankingControls({ period, mode, ranking }: {
  period: PeriodView; mode: RankingMode; ranking: ReturnType<typeof useRanking>;
}) {
  return <>
    <label>Year
      <select value={ranking.year} onChange={e => ranking.setYear(e.target.value)}>
        {Array.from({length: ranking.currentYear - 1999}, (_, index) => ranking.currentYear - index)
          .map(value => <option key={value} value={value}>{value}</option>)}
      </select>
    </label>
    {period === 'month' && <label>Month
      <select value={ranking.selectedMonth} onChange={e => ranking.setMonth(Number(e.target.value))}>
        {MONTHS.slice(0, ranking.monthLimit).map((name, index) => <option key={name} value={index + 1}>{name}</option>)}
      </select>
    </label>}
    {mode !== 'market' && <label>Top N
      <select value={ranking.rankCount} onChange={e => ranking.saveRankCount(Number(e.target.value))}>
        {[1, 2, 3, 4, 5].map(value => <option key={value} value={value}>{value}</option>)}
      </select>
    </label>}
    <Tip text="Period returns compare the first session close with the last completed session close."/>
  </>;
}

function PriceChart({ bars, comparisons, symbol, compareFunds, chartType, volume, interval, light, rankingStart }: {
  bars: any[]; comparisons: ChartSeries[]; symbol: string; compareFunds: boolean; chartType: string; volume: boolean;
  interval: string; light: boolean; rankingStart?: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!host.current || !bars.length) return;
    const chart = createChart(host.current, { width: host.current.clientWidth, height: 480,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: light ? '#596d60' : '#87968b', fontFamily: 'Inter, system-ui, sans-serif', fontSize: 11 },
      grid: { vertLines: { color: 'rgba(135,150,139,.08)' }, horzLines: { color: 'rgba(135,150,139,.11)' } },
      rightPriceScale: { borderVisible: false }, timeScale: { borderVisible: false, timeVisible: interval !== '1d', secondsVisible: false, minBarSpacing: 0.03 },
      crosshair: { mode: CrosshairMode.Normal }, handleScroll: true, handleScale: true });
    if (!rankingStart && !compareFunds) {
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
      const available = rankingStart ? selectedSeries : [{ symbol, bars, color: light ? '#263c2c' : '#ecf3e9' }, ...selectedSeries];
      let baseline = rankingStart;
      if (!baseline) {
        const common = available.reduce((set, item) => new Set(item.bars.map(b => b.time).filter(t => set.has(t))), new Set<string>(bars.map(b => b.time)));
        baseline = [...common].sort()[0];
      }
      const start = baseline;
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
  }, [bars, comparisons, symbol, compareFunds, chartType, volume, interval, light, rankingStart]);
  return <div className="chart-host" ref={host}/>;
}

function RankedPlot({ performance, error, showDetails, light, palette }: {
  performance: Performance | null; error: string; showDetails: boolean; light: boolean; palette: string[];
}) {
  const comparisons = useMemo(() => (performance?.series || []).map((series, index) => ({
    ...series, color: palette[index % palette.length],
  })), [performance, palette]);
  const first = comparisons[0];
  return performance?.start && first?.bars.length
    ? <PriceChart bars={first.bars} comparisons={comparisons} symbol={first.symbol} compareFunds={false}
      chartType="line" volume={false} interval="1d" light={light} rankingStart={performance.start}/>
    : <div className="chart-empty"><Empty
      title={performance ? 'No comparable period data' : error ? 'Performance unavailable' : 'Loading performance'}
      description={showDetails ? performance?.message || error || 'Reading stored daily closes for the selected period.' : undefined}/></div>;
}

function RankingTable({ performance, palette, viewer }: {
  performance: Performance | null; palette: string[]; viewer: boolean;
}) {
  if (!performance?.rows.length) return null;
  return <div className="period-results">
    <p className="panel-caption">
      {performance.start} to {performance.end}{!performance.complete && ' · Partial coverage'}
      {viewer ? (performance.basis === 'unverified' ? ' · Adjustments unverified' : ' · Provider-declared adjusted') : `. ${performance.message}`}
    </p>
    <div className="table-wrap"><table>
      <thead><tr><th>RANK</th><th>SYMBOL</th><th>PERIOD RETURN</th></tr></thead>
      <tbody>{performance.rows.map((row, index) => <tr key={row.symbol}>
        <td>{index + 1}</td>
        <td><span className="legend-dot" style={{display:'inline-block',background:palette[index % palette.length]}}/> <strong>{row.symbol}</strong>
          <small className="cell-sub">{row.name}</small></td>
        <td className={row.return_pct >= 0 ? 'positive' : 'negative'}>{pct(row.return_pct)}</td>
      </tr>)}</tbody>
    </table></div>
  </div>;
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

function SnapshotData({ viewer, fund, setFund, sectors, interval, setInterval }: FundSelection & { viewer: boolean }) {
  const { data, error } = useResource(`/bars?symbol=${fund}&interval=${interval}&limit=50`);
  const bars: Bar[] = data?.bars || [];
  return <section className="panel data-panel">
    <SectionTitle title="Market data"/>
    <div className="chart-mode-controls">
      <FundSelect fund={fund} setFund={setFund} sectors={sectors}/>
      <label>Interval
        <select value={interval} onChange={event => setInterval(event.target.value as Interval)}>
          <option value="1d">Daily</option>
          <option value="1h">Hourly</option>
        </select>
      </label>
    </div>
    {data && <div className="snapshot-data-coverage"><CoverageNote coverage={data.coverage} viewer={viewer}/></div>}
    {bars.length > 0 ? <>
      <p className="panel-caption">Latest {bars.length.toLocaleString()} of {data.coverage?.row_count?.toLocaleString() ?? '—'} stored {fund} bars</p>
      <div className="table-wrap"><table aria-label={`${fund} stored prices`}>
        <thead><tr><th>{interval === '1h' ? 'TIME (UTC)' : 'DATE'}</th><th>OPEN</th><th>HIGH</th><th>LOW</th><th>CLOSE</th><th>VOLUME</th></tr></thead>
        <tbody>{[...bars].reverse().map(bar => <tr key={bar.time}>
          <td><time dateTime={bar.time}>{bar.time.replace('T', ' ').replace(/Z$/, '')}</time></td>
          <td>{money(bar.open)}</td><td>{money(bar.high)}</td><td>{money(bar.low)}</td><td>{money(bar.close)}</td>
          <td>{bar.volume.toLocaleString()}</td>
        </tr>)}</tbody>
      </table></div>
    </> : <Empty title={error ? 'Market data unavailable' : data ? 'No stored bars for this selection' : 'Loading market data'}
      description={viewer ? undefined : error || undefined}/>}
  </section>;
}

function SnapshotAnalysis({ active, viewer, light, palette }: ChartAppearance & { active: boolean }) {
  const [mode, setMode] = useState<RankingMode>('etfs');
  const [period, setPeriod] = useState<'year' | 'month'>('year');
  const ranking = useRanking(mode, active ? period : 'history');
  const { performance, error, rankCount, periodLabel } = ranking;
  if (!active) return null;
  return <section className="panel chart-panel" aria-label="Return analysis">
    <div className="chart-heading">
      <div>
        <div className="eyebrow">PERIOD PERFORMANCE</div>
        <div className="chart-symbol"><h2>{mode === 'market' ? `SPY · ${periodLabel}` : `Top ${rankCount} ETFs · ${periodLabel}`}</h2></div>
        {mode === 'market' && <div className="chart-price">{pct(performance?.rows[0]?.return_pct)}</div>}
        <div className="chart-mode-controls">
          <label>Funds
            <select value={mode} onChange={event => setMode(event.target.value as RankingMode)}>
              <option value="etfs">SPY and sector ETFs</option>
              <option value="market">SPY only</option>
            </select>
          </label>
          <label>Period
            <select value={period} onChange={event => setPeriod(event.target.value as 'year' | 'month')}>
              <option value="year">Year</option>
              <option value="month">Month</option>
            </select>
          </label>
          <RankingControls period={period} mode={mode} ranking={ranking}/>
        </div>
      </div>
      <div className="chart-tools"><RankingTable performance={performance} palette={palette} viewer={viewer}/></div>
    </div>
    <div className="chart-toolbar">
      <span className="soft-badge">DAILY · {period === 'month' ? 'MONTH' : 'YEAR'}</span>
      <span className="muted tiny">Indexed to the first session close</span>
    </div>
    <RankedPlot performance={performance} error={error} showDetails={!viewer} light={light} palette={palette}/>
  </section>;
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
    <div role="tabpanel" id="snapshot-data" aria-labelledby="snapshot-tab-data" tabIndex={0} hidden={tab !== 'data'}>
      {tab === 'data' && <SnapshotData viewer={viewer} {...fundSelection}/>}
    </div>
    <div role="tabpanel" id="snapshot-analysis" aria-labelledby="snapshot-tab-analysis" tabIndex={0} hidden={tab !== 'analysis'}>
      <SnapshotAnalysis active={tab === 'analysis'} viewer={viewer} light={light} palette={palette}/>
    </div>
  </>;
}
