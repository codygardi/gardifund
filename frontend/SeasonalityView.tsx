import { useEffect, useMemo, useRef, useState } from 'react';
import { ColorType, createChart, CrosshairMode, TickMarkType, type Time } from 'lightweight-charts';
import { pct, useResource } from './api';
import type { ResearchCatalog, ResearchInstrument } from './ResearchInventory';
import { Empty, ErrorNote, SectionTitle, Tip } from './ui';

type YearCurve = {
  year: number; start: string; end: string; sessions: number; expected_sessions: number; ytd: boolean;
  points: [string, number | null][];
};
type WindowRow = {
  start: string; mean_pct: number; stddev_pct: number; t_stat: number | null;
  positive_pct: number; samples: number; example_start: string; example_end: string;
};
type SeasonalData = {
  symbol: string; trading_days: number; first_year: number; last_completed_year: number; latest_session: string | null;
  coverage: { source: string; adjustment: string; coverage_start: string; coverage_end: string } | null;
  years: YearCurve[]; windows: WindowRow[];
  indicators: { as_of: string | null; rsi: number | null; stochastic_k: number | null; stochastic_d: number | null };
};

const calendarDay = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' });
const calendarMonth = new Intl.DateTimeFormat('en-US', { month: 'short', timeZone: 'UTC' });
const referenceDate = (day: string) => new Date(`2000-${day}T00:00:00Z`);
const dayLabel = (day: string) => calendarDay.format(referenceDate(day));
const decimal = (value: number | null, digits = 2) => value == null ? '—' : value.toFixed(digits);
const LIGHT_YEAR_COLORS = ['#427544','#9a563e','#33708a','#845294','#7e7529','#3a776c','#a04e67','#4c5596',
  '#766237','#2f745b','#a65c21','#6e4e74','#356778','#85622c','#31703b','#96304c'];
const DARK_YEAR_COLORS = ['#a6d68a','#dfb76e','#89b9c7','#d295a1','#bb9cce','#d8cf8a','#87c6a4','#df9579',
  '#9bafe4','#d5a4cb','#8fb9a5','#a5bde1','#dbc2a0','#d8a483','#accf90','#ed8f9f'];

function monthDay(stamp: Time) {
  if (typeof stamp === 'string') return stamp.slice(5);
  if (typeof stamp === 'number') return new Date(stamp * 1000).toISOString().slice(5, 10);
  return `${String(stamp.month).padStart(2, '0')}-${String(stamp.day).padStart(2, '0')}`;
}

function SeasonalChart({ curves, light }: { curves: YearCurve[]; light: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const [hidden, setHidden] = useState<number[]>([]);
  const series = useMemo(() => curves.map((curve, index) => ({
    ...curve, color: (light ? LIGHT_YEAR_COLORS : DARK_YEAR_COLORS)[index],
  })), [curves, light]);
  const visible = useMemo(() => series.filter(curve => !hidden.includes(curve.year)), [series, hidden]);

  useEffect(() => {
    const container = host.current;
    if (!container || !visible.length) return;
    const chart = createChart(container, {
      width: container.clientWidth, height: container.clientHeight,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: light ? '#596d60' : '#87968b',
        fontFamily: 'Inter, system-ui, sans-serif', fontSize: 11 },
      grid: { vertLines: { color: 'rgba(135,150,139,.08)' }, horzLines: { color: 'rgba(135,150,139,.11)' } },
      crosshair: { mode: CrosshairMode.Normal }, rightPriceScale: { borderVisible: false },
      timeScale: {
        borderVisible: false,
        tickMarkFormatter: (stamp: Time, type: TickMarkType) => {
          const day = monthDay(stamp);
          return type === TickMarkType.Month || type === TickMarkType.Year ? calendarMonth.format(referenceDate(day)) : dayLabel(day);
        },
      },
      localization: { timeFormatter: (stamp: Time) => dayLabel(monthDay(stamp)) },
    });
    for (const curve of visible) {
      const line = chart.addLineSeries({
        color: curve.color, lineWidth: curve.ytd ? 3 : 1,
        lastValueVisible: false, priceLineVisible: false,
        priceFormat: { type: 'custom', formatter: (value: number) => `${value.toFixed(1)}%` },
      });
      line.setData(curve.points.map(([day, value]) => {
        const stamp = `2000-${day}`;
        return value == null ? { time: stamp } : { time: stamp, value };
      }));
    }
    chart.timeScale().fitContent();
    const resize = new ResizeObserver(() => chart.applyOptions({ width: container.clientWidth, height: container.clientHeight }));
    resize.observe(container);
    return () => { resize.disconnect(); chart.remove(); };
  }, [visible, light]);

  return <>
    <div className="seasonal-legend" aria-label="Choose years to overlay">
      {series.map(curve => <label key={curve.year}>
        <input type="checkbox" checked={!hidden.includes(curve.year)}
          onChange={() => setHidden(items => items.includes(curve.year) ? items.filter(year => year !== curve.year) : [...items, curve.year])}/>
        <span className="legend-dot" style={{ background: curve.color }} aria-hidden="true"/>
        {curve.year}{curve.ytd ? ' YTD' : ''}
      </label>)}
    </div>
    <div ref={host} className="seasonal-chart" aria-hidden="true" hidden={!visible.length}/>
    {!visible.length && <Empty title="Select at least one year"/>}
    <details className="research-coverage">
      <summary>Coverage by year</summary>
      <div className="table-wrap"><table aria-label="Seasonal year coverage">
        <thead><tr><th scope="col">YEAR</th><th scope="col">STORED DATES</th><th scope="col">SESSIONS</th><th scope="col">RETURN TO LAST CLOSE</th></tr></thead>
        <tbody>{curves.map(curve => <tr key={curve.year}>
          <td>{curve.year}{curve.ytd ? ' YTD' : ''}</td><td>{curve.start} to {curve.end}</td>
          <td>{curve.sessions} / {curve.expected_sessions}</td><td>{pct(curve.points.at(-1)?.[1])}</td>
        </tr>)}</tbody>
      </table></div>
    </details>
  </>;
}

function WindowTable({ rows, title }: { rows: WindowRow[]; title: string }) {
  return <div>
    <h3>{title}</h3>
    {rows.length ? <div className="table-wrap"><table aria-label={title}>
      <thead><tr><th scope="col">START</th><th scope="col">MEAN</th><th scope="col">STD DEV</th>
        <th scope="col">T-STAT</th><th scope="col">POSITIVE</th><th scope="col">YEARS</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.start}>
        <td><strong>{dayLabel(row.start)}</strong><small className="cell-sub" title={`${row.example_start} to ${row.example_end}`}>
          {dayLabel(row.example_start.slice(5))}–{dayLabel(row.example_end.slice(5))}</small></td>
        <td className={row.mean_pct > 0 ? 'positive' : 'negative'}>{pct(row.mean_pct)}</td>
        <td>{row.stddev_pct.toFixed(2)}%</td><td>{decimal(row.t_stat)}</td>
        <td>{row.positive_pct.toFixed(0)}%</td><td>{row.samples}</td>
      </tr>)}</tbody>
    </table></div> : <p className="panel-caption">No qualifying windows in this direction.</p>}
  </div>;
}

export default function SeasonalityView({ active, viewer, light, benchmarks, sectors }: {
  active: boolean; viewer: boolean; light: boolean; benchmarks: ResearchInstrument[]; sectors: ResearchInstrument[];
}) {
  const [symbol, setSymbol] = useState('SPY');
  const [years, setYears] = useState('15');
  const [days, setDays] = useState('21');
  const [selection, setSelection] = useState({ symbol: 'SPY', years: '15', days: '21' });
  const [order, setOrder] = useState<'mean_pct' | 't_stat'>('mean_pct');
  const query = new URLSearchParams({ mode: 'seasonality', symbol: selection.symbol,
    lookback_years: selection.years, trading_days: selection.days });
  const { data, error } = useResource(active ? `/chart-performance?${query}` : null);
  const { data: catalog } = useResource(active && !viewer ? '/database/research' : null);
  const results = data as SeasonalData | null;
  const companies = useMemo(() => {
    const unique = new Map<string, ResearchInstrument>();
    for (const group of (catalog as ResearchCatalog | null)?.groups ?? []) {
      for (const company of group.companies) if (company.coverage) unique.set(company.symbol, company);
    }
    return [...unique.values()].sort((a, b) => a.symbol.localeCompare(b.symbol));
  }, [catalog]);
  if (!active) return null;
  const matching = results?.windows.filter(row => row[order] != null) ?? [];
  const score = (row: WindowRow) => row[order] ?? 0;
  const gainers = matching.filter(row => row.mean_pct > 0).sort((a, b) => score(b) - score(a)).slice(0, 5);
  const losers = matching.filter(row => row.mean_pct < 0).sort((a, b) => score(a) - score(b)).slice(0, 5);
  const options = (items: ResearchInstrument[]) => items.map(item =>
    <option key={item.symbol} value={item.symbol}>{item.symbol} · {item.name}</option>);

  return <>
    <section className="panel chart-panel seasonal-panel" aria-label="Seasonal year overlay">
      <SectionTitle eyebrow="DAILY MARKET RESEARCH" title="Seasonal year overlay"/>
      <form className="chart-mode-controls" onSubmit={event => {
        event.preventDefault();
        setSelection({ symbol, years, days });
      }}>
        <label>Seasonality ticker<select value={symbol} onChange={event => setSymbol(event.target.value)}>
          <optgroup label="Benchmark ETFs">{options(benchmarks)}</optgroup>
          <optgroup label="S&P 500 sector ETFs">{options(sectors)}</optgroup>
          {companies.length > 0 && <optgroup label="Company watchlist">{options(companies)}</optgroup>}
        </select></label>
        <label>Completed years<input type="number" min={3} max={15} required value={years} onChange={event => setYears(event.target.value)}/></label>
        <label>Window trading days<input type="number" min={1} max={63} required value={days} onChange={event => setDays(event.target.value)}/></label>
        <button className="button primary" type="submit">Analyze seasonality</button>
      </form>
      <ErrorNote text={error && (viewer ? 'Seasonal data is unavailable.' : error)}/>
      {results?.years.length ? <>
        <p className="panel-caption">{results.symbol} · {results.years.filter(year => !year.ytd).length} completed years with data
          {results.years.some(year => year.ytd) && ' + current YTD'} · Cumulative return from each year’s first available close</p>
        <SeasonalChart key={query.toString()} curves={results.years} light={light}/>
        <p className="panel-caption">{results.coverage?.source} · {results.coverage?.adjustment === 'split_dividend_adjusted'
          ? 'Provider-declared split and dividend adjustments' : 'Price returns with unverified adjustments'}</p>
      </> : !error && <Empty title={results ? 'No daily history for this ticker' : 'Loading seasonal data'}
        description={results && !viewer ? 'Open Database to load the daily series.' : undefined}/>}
    </section>
    {results?.years.length ? <>
      <section className="panel chart-panel seasonal-panel" aria-label="Seasonal windows">
        <SectionTitle eyebrow={`${results.trading_days} TRADING DAYS`} title="Seasonal windows"
          action={<label>Rank by<select value={order} onChange={event => setOrder(event.target.value as typeof order)}>
            <option value="mean_pct">Average return</option><option value="t_stat">T-statistic</option>
          </select></label>}/>
        <p className="muted small">Each calendar start uses the first session on or after that date in {results.first_year}–{results.last_completed_year}.
          Windows need complete closes and at least three years. Nearby starts can overlap. <Tip
            text="T-statistic is the mean return divided by its standard error. A missing value means the sample had no variation. The table shows the most recent example window below each calendar start."/></p>
        <div className="seasonal-tables">
          <WindowTable rows={gainers} title={order === 'mean_pct' ? 'Highest average returns' : 'Highest positive t-statistics'}/>
          <WindowTable rows={losers} title={order === 'mean_pct' ? 'Lowest average returns' : 'Lowest negative t-statistics'}/>
        </div>
      </section>
      <section className="panel chart-panel seasonal-panel" aria-label="Technical indicators">
        <SectionTitle eyebrow="LATEST DAILY CLOSE" title="Technical indicators"/>
        <p className="muted small">{results.indicators.as_of ?? 'No completed session with data'}
          {results.indicators.as_of && results.latest_session !== results.indicators.as_of &&
            ` · Latest market session ${results.latest_session}`}</p>
        <dl className="seasonal-indicators">
          <div><dt>RSI (14) <Tip text="Wilder’s relative strength index uses the latest run of consecutive daily closes."/></dt>
            <dd>{decimal(results.indicators.rsi, 1)}</dd></div>
          <div><dt>Stochastic %K (14) <Tip text="The latest close’s position in the 14-session high–low range."/></dt>
            <dd>{decimal(results.indicators.stochastic_k, 1)}</dd></div>
          <div><dt>Stochastic %D (3) <Tip text="The three-session average of fast stochastic %K."/></dt>
            <dd>{decimal(results.indicators.stochastic_d, 1)}</dd></div>
        </dl>
      </section>
    </> : null}
  </>;
}
