import { useEffect, useMemo, useRef, useState } from 'react';
import { createChart, ColorType, CrosshairMode, Time } from 'lightweight-charts';
import { ArrowDownUp, BarChart3, Check, ChevronDown, LineChart, Search } from 'lucide-react';
import { api, money, pct, useApp, useResource } from '../api';
import { Empty, ErrorNote, Segmented, Tip } from '../ui';

const COLORS = ['#a6d68a','#dfb76e','#89b9c7','#d295a1','#bb9cce','#d8cf8a','#87c6a4','#df9579','#9bafe4','#d5a4cb','#8fb9a5'];
const LIGHT_COLORS = ['#387349','#9a6720','#327387','#a64d61','#75548a','#827520','#35785d','#a35439','#485e9a','#935482','#4d7564'];
const EMPTY_BARS: any[] = [];
const chartTime = (s: string): Time => (s.length === 10 ? s : Math.floor(new Date(s).getTime() / 1000)) as Time;
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

type ChartMode = 'market' | 'sectors' | 'etfs' | 'stocks';
type PeriodView = 'history' | 'year' | 'month';
type ChartSeries = { symbol: string; bars: any[]; color: string };
type PerformanceRow = { symbol: string; name: string; sector: string | null; return_pct: number };

function storedRankCount(key: string, legacyKey?: string) {
  try {
    const previous = legacyKey ? localStorage.getItem(legacyKey) : null;
    const value = Number(localStorage.getItem(key) ?? previous ?? '5');
    return Number.isInteger(value) ? Math.max(1, Math.min(5, value)) : 5;
  } catch {
    return 5;
  }
}

function PriceChart({ bars, comparisons, mode, chartType, volume, interval, light, rankingStart }: {
  bars: any[]; comparisons: ChartSeries[]; mode: ChartMode; chartType: string; volume: boolean;
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
    if (!rankingStart && (mode === 'market' || mode === 'etfs')) {
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
      const available = rankingStart ? selectedSeries : [{ symbol: 'SPY', bars, color: light ? '#263c2c' : '#ecf3e9' }, ...selectedSeries];
      let baseline = rankingStart;
      if (!baseline) {
        const common = available.reduce((set, item) => new Set(item.bars.map(b => b.time).filter(t => set.has(t))), new Set<string>(bars.map(b => b.time)));
        baseline = [...common].sort()[0];
      }
      const start = baseline;
      if (start) for (const item of available) {
        const base = item.bars.find(b => b.time === start)?.close;
        if (!base) continue;
        const points = item.bars.filter(b => b.time >= start).map(b => ({ time: chartTime(b.time), value: (b.close / base - 1) * 100 }));
        const series = chart.addLineSeries({ color: item.color, lineWidth: item.symbol === 'SPY' ? 2 : 1, title: item.symbol, priceFormat: { type: 'custom', formatter: (n: number) => `${n.toFixed(1)}%` } });
        series.setData(points.filter(point => Number.isFinite(point.value)));
      }
    }
    chart.timeScale().fitContent();
    const observer = new ResizeObserver(() => { if (host.current) chart.applyOptions({ width: host.current.clientWidth }); });
    observer.observe(host.current);
    return () => { observer.disconnect(); chart.remove(); };
  }, [bars, comparisons, mode, chartType, volume, interval, light, rankingStart]);
  return <div className="chart-host" ref={host}/>;
}

export default function Charts() {
  const { state, refresh, marketVersions, theme } = useApp();
  const [interval, setInterval] = useState('1d');
  const [chartType, setChartType] = useState('candles');
  const [mode, setMode] = useState<ChartMode>('market');
  const [fund, setFund] = useState('SPY');
  const [periodView, setPeriodView] = useState<PeriodView>('history');
  const [year, setYear] = useState(String(new Date().getFullYear()));
  const [month, setMonth] = useState(new Date().getMonth() + 1);
  const [rankCounts, setRankCounts] = useState<Record<string, number>>({});
  const [volume, setVolume] = useState(true);
  const [selected, setSelected] = useState<string[]>([]);
  const [compare, setCompare] = useState<{ symbol: string; bars: any[]; color: string }[]>([]);
  const [older, setOlder] = useState<{key: string; bars: any[]; hasMore: boolean} | null>(null);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const historyKeyRef = useRef('');
  const [comparisonMessage, setComparisonMessage] = useState('');
  const comparisonEpoch = useRef(0);
  const requestSequence = useRef<Record<string, number>>({});
  const seenVersions = useRef<Record<string, number>>({});
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState('');
  const [topN, setTopN] = useState('10');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [attribution, setAttribution] = useState<any>(null);
  const [message, setMessage] = useState('');
  const today = new Date();
  const period = mode === 'stocks' && periodView === 'history' ? 'year' : periodView;
  const ranked = period !== 'history';
  const monthLimit = Number(year) === today.getFullYear() ? today.getMonth() + 1 : 12;
  const selectedMonth = Math.min(month, monthLimit);
  const periodLabel = period === 'month' ? `${MONTHS[selectedMonth - 1]} ${year}` : year;
  const rankKey = `gardifund-rank-${mode}-${year}${period === 'month' ? '-' + selectedMonth : ''}`;
  const legacyKey = mode === 'stocks' && period === 'year' ? `gardifund-top-${year}` : undefined;
  const rankCount = mode === 'market' ? 1 : rankCounts[rankKey] ?? storedRankCount(rankKey, legacyKey);
  const params = new URLSearchParams({mode, year, top_n: String(rankCount)});
  if (period === 'month') params.set('month', String(selectedMonth));
  const chartSymbol = mode === 'etfs' ? fund : 'SPY';
  const { data, error } = useResource(ranked ? null : `/bars?symbol=${chartSymbol}&interval=${interval}&limit=12000`);
  const { data: performance, error: performanceError } = useResource(ranked ? `/chart-performance?${params}` : null);
  const { data: trends } = useResource(`/sectors?q=${encodeURIComponent(search)}&top_n=${Math.max(1, Math.min(10, Number(topN) || 10))}`);
  const sectorsKey = (state?.sectors || []).map((s: any) => s.symbol).join(',');
  const light = theme === 'light';
  const palette = light ? LIGHT_COLORS : COLORS;
  const chartComparisons = useMemo(() => ranked
    ? (performance?.series || []).map((item: Omit<ChartSeries, 'color'>, index: number) => ({ ...item, color: palette[index % palette.length] }))
    : compare, [ranked, performance, palette, compare]);
  useEffect(() => { if (state?.settings?.top_n) setTopN(String(state.settings.top_n)); }, [state?.settings?.top_n]);

  function saveRankCount(value: number) {
    setRankCounts(current => ({ ...current, [rankKey]: value }));
    try { localStorage.setItem(rankKey, String(value)); } catch { /* Keep the current session's choice. */ }
  }

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
    if (ranked || mode !== 'sectors' || !selected.length) { setCompare([]); return; }
    const current = selected.map(symbol => {
      const key = `${symbol}:${interval}`;
      seenVersions.current[key] = marketVersions[key] || 0;
      const index = (state?.sectors || []).findIndex((s: any) => s.symbol === symbol);
      return { symbol, bars: [], color: palette[index] || palette[0] };
    });
    setCompare(current);
    Promise.all(selected.map(symbol => loadComparison(symbol, interval, epoch))).then(results => {
      if (comparisonEpoch.current !== epoch || interval === '1d') return;
      const missing = results.filter(result => result && result.bars.length === 0 && requestSequence.current[result.key] === result.sequence).map(result => result!.symbol);
      if (!missing.length) return;
      if (state?.access_scope !== 'local') { setComparisonMessage('Refresh missing sector history from the local workspace.'); return; }
      api('/market/sector-history', { method: 'POST', body: JSON.stringify({ interval, symbols: missing }) })
        .then(result => { if (comparisonEpoch.current === epoch && result.retrying?.length) setComparisonMessage(`Retrying unavailable series: ${result.retrying.join(', ')}.`); })
        .catch(error => { if (comparisonEpoch.current === epoch) setComparisonMessage(error.message); });
    });
    return () => { if (comparisonEpoch.current === epoch) comparisonEpoch.current++; };
  }, [selected.join(','), interval, mode, ranked, sectorsKey, light, state?.access_scope]);

  useEffect(() => {
    if (ranked || mode !== 'sectors') return;
    const epoch = comparisonEpoch.current;
    for (const symbol of selected) {
      const key = `${symbol}:${interval}`;
      const version = marketVersions[key] || 0;
      if (version > (seenVersions.current[key] || 0)) {
        seenVersions.current[key] = version;
        void loadComparison(symbol, interval, epoch);
      }
    }
  }, [marketVersions, selected.join(','), interval, mode, ranked, light]);
  const historyKey = `${chartSymbol}:${interval}:${data?.coverage?.updated ?? ''}`;
  historyKeyRef.current = historyKey;
  const baseBars = data?.bars || EMPTY_BARS;
  const earlier = older?.key === historyKey ? older.bars : EMPTY_BARS;
  const bars = useMemo(() => ranked ? performance?.series?.[0]?.bars || EMPTY_BARS
    : (mode === 'market' || mode === 'etfs') && earlier.length ? [...earlier, ...baseBars] : baseBars,
  [ranked, mode, earlier, baseBars, performance]);
  const hasOlder = older?.key === historyKey ? older.hasMore : Boolean(data?.has_more);
  async function loadOlder() {
    if (ranked || loadingOlder || !bars.length) return;
    const expected = historyKey;
    setLoadingOlder(true);
    try {
      const params = new URLSearchParams({symbol:chartSymbol, interval, limit:'12000', before:bars[0].time});
      const result = await api(`/bars?${params}`);
      if (historyKeyRef.current !== expected) return;
      setOlder(current => ({key: expected, bars: [...result.bars, ...(current?.key === expected ? current.bars : [])], hasMore: result.has_more}));
    } catch(e: any) { if (historyKeyRef.current === expected) setMessage(e.message); }
    finally { setLoadingOlder(false); }
  }
  const last = bars.at(-1), previous = bars.at(-2);
  const change = last && previous ? (last.close / previous.close - 1) * 100 : null;
  const sectors = state?.sectors || [];
  function toggle(symbol: string) { setSelected(current => current.includes(symbol) ? current.filter(x => x !== symbol) : [...current, symbol]); }
  async function saveTop() { const n = Math.max(1, Math.min(10, Number(topN) || 10)); setTopN(String(n)); if (state?.access_scope !== 'local' || n === state?.settings?.top_n) return; try { await api('/settings', { method: 'PATCH', body: JSON.stringify({ top_n: n }) }); refresh(); } catch(e: any) { setMessage(e.message); } }
  async function calculate(e: React.FormEvent) { e.preventDefault(); try { setAttribution(await api(`/attribution?${new URLSearchParams({start,end,sector: expanded,limit:topN})}`)); setMessage(''); } catch(e: any) { setMessage(e.message); } }
  return <>
    <div className="page-heading"><div><div className="page-kicker"><span className="kicker-line"/> MARKET INTELLIGENCE</div><h1>Charts<span className="heading-period">.</span></h1><p className="page-subtitle">See the market from a wider perspective.</p></div><div className="market-tag"><span className="status-dot"/> SPY · S&P 500 ETF</div></div>
    <ErrorNote text={error || performanceError || message}/>
    <section className="panel chart-panel">
      <div className="chart-heading">
        <div>
          <div className="eyebrow">
            {ranked ? 'PERIOD PERFORMANCE' : 'MARKET OVERVIEW'}
            <Tip text="Daily market data comes from Tradier. Intraday comes from Yahoo Finance via yfinance. Coverage and adjustment status appear below."/>
          </div>
          <div className="chart-symbol">
            <h2>{ranked
              ? (mode === 'market' ? `SPY · ${periodLabel}` : `Top ${rankCount} ${mode === 'etfs' ? 'ETFs' : mode} · ${periodLabel}`)
              : chartSymbol}</h2>
            <span>{mode === 'stocks' ? 'Start-date SPY constituents'
              : ranked ? 'SPY and related funds' : mode === 'etfs' ? 'Tradable SPY-related ETF' : 'SPDR S&P 500 ETF Trust'}</span>
          </div>
          <div className="chart-price">
            {ranked ? (mode === 'market' ? pct(performance?.rows?.[0]?.return_pct)
              : period === 'month' ? 'Monthly performance' : 'Annual performance') : money(last?.close)}
            {!ranked && <span className={change == null ? 'muted' : change >= 0 ? 'positive' : 'negative'}>
              {pct(change)} <small>vs. prior bar</small>
            </span>}
          </div>
        </div>
        <Segmented values={[{value:'market',label:'Market'},{value:'sectors',label:'Sectors'},
          {value:'etfs',label:'ETFs'},{value:'stocks',label:'Stocks'}]} value={mode} onChange={value => setMode(value as ChartMode)}/>
      </div>
      <div className="chart-mode-controls">
        <label>Period
          <select value={period} onChange={e => setPeriodView(e.target.value as PeriodView)}>
            {mode !== 'stocks' && <option value="history">Full history</option>}
            <option value="year">Year</option>
            <option value="month">Month</option>
          </select>
        </label>
        {ranked && <>
          <label>Year
            <select value={year} onChange={e => setYear(e.target.value)}>
              {Array.from({length: today.getFullYear() - 1999}, (_, index) => today.getFullYear() - index)
                .map(value => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>
          {period === 'month' && <label>Month
            <select value={selectedMonth} onChange={e => setMonth(Number(e.target.value))}>
              {MONTHS.slice(0, monthLimit).map((name, index) => <option key={name} value={index + 1}>{name}</option>)}
            </select>
          </label>}
          {mode !== 'market' && <label>Top N
            <select value={rankCount} onChange={e => saveRankCount(Number(e.target.value))}>
              {[1, 2, 3, 4, 5].map(value => <option key={value} value={value}>{value}</option>)}
            </select>
          </label>}
          <Tip text="Period returns compare the first session close with the last completed session close. Stock rankings use dated holdings and declared-adjusted prices. Historical winners use hindsight."/>
        </>}
        {!ranked && mode === 'etfs' && <label>SPY-related fund
          <select value={fund} onChange={e => setFund(e.target.value)}>
            <option value="SPY">SPY · S&P 500</option>
            {sectors.map((sector: any) => <option key={sector.symbol} value={sector.symbol}>{sector.symbol} · {sector.name}</option>)}
          </select>
        </label>}
      </div>
      <div className="chart-toolbar">
        {ranked ? <>
          <span className="soft-badge">DAILY · {period === 'month' ? 'MONTH' : 'YEAR'}</span>
          <span className="muted tiny">Indexed to the first session close</span>
        </> : <>
          <div className="toolbar-group">
            <Segmented values={[{value:'1d',label:'Daily'},{value:'1h',label:'1 hour'},
              {value:'30m',label:'30 min'}]} value={interval} onChange={setInterval}/>
            <span className="toolbar-separator"/>
            <button className={`chart-tool ${chartType === 'candles' ? 'on' : ''}`} onClick={() => setChartType('candles')}
              disabled={mode === 'sectors'} title="Candlesticks" aria-label="Candlesticks" aria-pressed={chartType === 'candles'}><BarChart3 size={16}/></button>
            <button className={`chart-tool ${chartType === 'line' ? 'on' : ''}`} onClick={() => setChartType('line')}
              disabled={mode === 'sectors'} title="Line chart" aria-label="Line chart" aria-pressed={chartType === 'line'}><LineChart size={16}/></button>
            <button className={`chart-tool volume-toggle ${volume ? 'on' : ''}`} disabled={mode === 'sectors'}
              onClick={() => setVolume(value => !value)} aria-pressed={volume}>Volume</button>
          </div>
          <span className="muted tiny">{mode === 'sectors' ? 'Indexed to first common available close' : 'Drag to pan · scroll to zoom'}</span>
        </>}
      </div>
      {bars.length ? <PriceChart bars={bars} comparisons={chartComparisons} mode={mode} chartType={chartType}
        volume={volume} interval={ranked ? '1d' : interval} light={light} rankingStart={ranked ? performance?.start : undefined}/>
        : <div className="chart-empty"><Empty
          title={ranked ? (performance ? 'No comparable period data' : performanceError ? 'Performance unavailable' : 'Loading performance') : 'Waiting for market data'}
          description={ranked ? performance?.message || performanceError || 'Reading stored daily closes for the selected period.'
            : 'Refresh stored market data from the local workspace. Missing history remains blank.'}/></div>}
      {!ranked && (mode === 'market' || mode === 'etfs') && bars.length > 0 && <div className="history-controls">
        <span className="muted tiny">{bars.length.toLocaleString()} of {data?.coverage?.row_count?.toLocaleString() || '—'} stored {chartSymbol} bars loaded</span>
        {hasOlder && <button className="button secondary" onClick={loadOlder} disabled={loadingOlder}>{loadingOlder ? 'Loading earlier bars…' : 'Load earlier bars'}</button>}
      </div>}
      {!ranked && <div className="coverage-note">
        <span><span className="status-dot"/> {data?.coverage ? `${data.coverage.source} · ${data.coverage.coverage_start} to ${data.coverage.coverage_end}` : 'No data imported'}</span>
        <span>{data?.coverage?.adjustment === 'split_dividend_adjusted' ? 'Provider-declared adjusted' : 'Adjustments unverified'}
          <Tip text="The stored date range can contain gaps. Tradier daily prices may lack dividend adjustments, and Yahoo limits intraday history."/>
        </span>
      </div>}
      {ranked && performance?.rows?.length > 0 && <div className="period-results">
        <p className="panel-caption">
          {performance.start} to {performance.end} · {performance.complete ? 'Comparable endpoints available' : 'Partial coverage'}. {performance.message}
        </p>
        <div className="table-wrap">
          <table>
            <thead><tr><th>RANK</th><th>{mode === 'sectors' ? 'SECTOR' : 'SYMBOL'}</th>
              {mode === 'stocks' && <th>SECTOR</th>}<th>PERIOD RETURN</th></tr></thead>
            <tbody>{performance.rows.map((row: PerformanceRow, index: number) => <tr key={row.symbol}>
              <td>{index + 1}</td>
              <td><span className="legend-dot" style={{display:'inline-block',background:palette[index % palette.length]}}/> <strong>{mode === 'sectors' ? row.name : row.symbol}</strong>
                <small className="cell-sub">{mode === 'sectors' ? row.symbol : row.name}</small></td>
              {mode === 'stocks' && <td>{row.sector || 'Unclassified'}</td>}
              <td className={row.return_pct >= 0 ? 'positive' : 'negative'}>{pct(row.return_pct)}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </div>}
      {!ranked && mode === 'sectors' && <div className="sector-picker"><div className="sector-picker-top"><div><strong>Compare sector ETFs</strong><small>Normalized price change · ETF data must match the selected interval</small></div><button className="text-link" onClick={() => setSelected(selected.length === sectors.length ? [] : sectors.map((s: any) => s.symbol))}>{selected.length === sectors.length ? 'Clear all' : 'Select all'}</button></div><div className="sector-checks">{sectors.map((s: any, i: number) => <label key={s.symbol}><input type="checkbox" checked={selected.includes(s.symbol)} onChange={() => toggle(s.symbol)}/><span className="legend-dot" style={{background: palette[i]}}/><span>{s.name}</span><small>{s.symbol}</small></label>)}</div>{compare.some(c => !c.bars.length) && <div className="muted tiny">No {interval} data yet for: {compare.filter(c => !c.bars.length).map(c => c.symbol).join(', ')}. {interval !== '1d' ? 'The background worker will request available Yahoo history.' : ''}</div>}{comparisonMessage && <div className="muted tiny" role="status">{comparisonMessage}</div>}</div>}
    </section>
    <section className="panel sector-panel"><div className="section-title"><div><div className="eyebrow">BENEATH THE SURFACE</div><h2>Sector trends</h2><p className="muted small">Explore sector ETFs and their strongest cached daily movers.</p></div><div className="sector-controls"><div className="searchbox small-search"><Search size={15}/><input placeholder="Search sectors" aria-label="Search sectors" value={search} onChange={e => setSearch(e.target.value)}/></div><label className="top-label">Top <input type="number" min="1" max="10" aria-label="Top N stocks" value={topN} onChange={e => setTopN(e.target.value)} onBlur={saveTop}/> stocks</label></div></div>
      <div className="sector-list">{trends?.rows?.map((row: any, i: number) => <div key={row.symbol}><button className="sector-row" onClick={() => { setExpanded(expanded === row.name ? '' : row.name); setAttribution(null); }} aria-expanded={expanded === row.name}><span className="sector-index">{String(i+1).padStart(2,'0')}</span><span className="sector-name"><strong>{row.name}</strong><small>{row.symbol} · tradable ETF</small></span><span className="sector-classified">{row.classified_count} quoted stocks</span><span>{money(row.quote?.price)}</span><span className={row.quote?.change_pct == null ? 'muted' : row.quote.change_pct >= 0 ? 'positive' : 'negative'}>{pct(row.quote?.change_pct)}</span><ChevronDown className={expanded === row.name ? 'rotate' : ''} size={16}/></button>{expanded === row.name && <div className="sector-detail"><div className="detail-title">TOP STOCKS BY DAY CHANGE <Tip text="Sector membership is inferred from same-date sector ETF holdings. Missing quotes and unclassified stocks are excluded."/></div>{row.top.length ? <div className="top-stock-grid">{row.top.map((s: any) => <div key={s.symbol}><strong>{s.symbol}</strong><span>{s.name}</span><b className={s.change_pct >= 0 ? 'positive' : 'negative'}>{pct(s.change_pct)}</b></div>)}</div> : <p className="muted small">No classified stock quotes available.</p>}</div>}</div>)}</div><p className="panel-caption">{trends?.note}</p>
    </section>
    <section className="panel attribution-panel"><div className="section-title"><div><div className="eyebrow">HISTORICAL ATTRIBUTION</div><h2>Return contributors <Tip text="Requires an exact start-date holdings snapshot with weights and provider-declared split- and dividend-adjusted closes at both endpoints. Missing constituents are reported, not estimated."/></h2><p className="muted small">Select a sector above, then choose exact start and end dates.</p></div></div><form className="attribution-form" onSubmit={calculate}><label>From<input type="date" required value={start} onChange={e => setStart(e.target.value)}/></label><label>To<input type="date" required value={end} onChange={e => setEnd(e.target.value)}/></label><div className="attribution-sector"><span>SECTOR</span><strong>{expanded || 'All sectors'}</strong></div><button className="button secondary" type="submit"><ArrowDownUp size={15}/> Calculate</button></form>{attribution && <><div className="notice">{attribution.message} {attribution.eligible != null && `${attribution.missing} of ${attribution.eligible} constituents missing.`}</div>{attribution.rows?.length > 0 && <div className="table-wrap"><table><thead><tr><th>STOCK</th><th>SECTOR</th><th>START WEIGHT</th><th>RETURN</th><th>CONTRIBUTION</th></tr></thead><tbody>{attribution.rows.map((r: any) => <tr key={r.symbol}><td><strong>{r.symbol}</strong></td><td>{r.sector}</td><td>{(r.weight*100).toFixed(2)}%</td><td>{pct(r.return*100)}</td><td>{pct(r.contribution*100)}</td></tr>)}</tbody></table></div>}</>}</section>
  </>;
}
