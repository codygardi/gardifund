import { useEffect, useMemo, useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { Search, SlidersHorizontal } from 'lucide-react';
import { api, pct, quotePrice, useApp, when } from '../api';
import { Empty, ErrorNote, Tip } from '../ui';

type Category = 'stocks' | 'etfs' | 'sectors';
type Filters = { query:string; sector:string; group:string; fund:string; direction:string; minWeight:string; maxWeight:string;
  minPrice:string; maxPrice:string; minVolume:string; maxVolume:string; minChange:string; maxChange:string; minTurnover:string; maxSpread:string };
export type ScreenRow = { symbol:string; name:string; sector:string | null; group?:string; weight?:number | null; weight_complete?:boolean;
  constituent_count?:number; price:number | null; trade_time:number | null; volume:number | null; change_pct:number | null; spread_pct?:number | null };
const emptyFilters = ():Filters => ({query:'',sector:'',group:'all',fund:'',direction:'all',minWeight:'',maxWeight:'',
  minPrice:'',maxPrice:'',minVolume:'',maxVolume:'',minChange:'',maxChange:'',minTurnover:'',maxSpread:''});
const EMPTY_ROWS:ScreenRow[] = [];
const bound = (value:string) => value === '' || !Number.isFinite(Number(value)) ? null : Number(value);
const inside = (value:number | null | undefined, minimum:string, maximum:string) => {
  const min=bound(minimum), max=bound(maximum);
  return (min == null && max == null) || (value != null && (min == null || value >= min) && (max == null || value <= max));
};
const volumeText = (value:number | null) => value == null ? '—' : value.toLocaleString('en-US');
const weightText = (value:number | null | undefined) => value == null ? '—' : `${(value * 100).toFixed(2)}%`;

export default function Screening({ setups = false, live = false, onEnter }: { setups?:boolean; live?:boolean; onEnter?:(row:ScreenRow, category:'sector'|'etf'|'stock') => void }) {
  const { state } = useApp();
  const [category,setCategory] = useState<Category>('stocks');
  const [filters,setFilters] = useState<Record<Category,Filters>>({stocks:emptyFilters(),etfs:emptyFilters(),sectors:emptyFilters()});
  const [snapshot,setSnapshot] = useState<any>(null);
  const [error,setError] = useState('');
  const container=useRef<HTMLDivElement>(null);
  const active=filters[category];
  const setFilter=(key:keyof Filters,value:string) => setFilters(current => ({...current,[category]:{...current[category],[key]:value}}));
  useEffect(() => {
    let mounted=true;
    api(live ? '/setups/live' : '/discovery').then(data => { if (mounted) { setSnapshot(data); setError(''); } })
      .catch(e => { if (mounted) setError(e.message); });
    return () => { mounted=false; };
  },[state?.discovery?.completed,live]);
  const sectors:{symbol:string;name:string}[]=state?.sectors || [];
  const categoryRows:ScreenRow[]=snapshot?.lists?.[category] || EMPTY_ROWS;
  const rows=useMemo(() => {
    const query=active.query.trim().toLowerCase();
    return categoryRows.filter(row => {
      if (query && !`${row.symbol} ${row.name}`.toLowerCase().includes(query)) return false;
      if (active.sector === '__unclassified' ? row.sector != null : active.sector && row.sector !== active.sector) return false;
      if (category === 'etfs' && ((active.group !== 'all' && row.group !== active.group) || (active.fund && row.symbol !== active.fund))) return false;
      if (category === 'sectors' && active.direction !== 'all') {
        if (row.change_pct == null) return false;
        if (active.direction === 'up' && row.change_pct <= 0) return false;
        if (active.direction === 'down' && row.change_pct >= 0) return false;
        if (active.direction === 'flat' && row.change_pct !== 0) return false;
      }
      const usableWeight=category === 'sectors' && !row.weight_complete ? null : row.weight == null ? null : row.weight * 100;
      if (setups && (!inside(row.price != null && row.volume != null ? row.price * row.volume : null, active.minTurnover, '') || !inside(row.spread_pct, '', active.maxSpread))) return false;
      return inside(usableWeight,active.minWeight,active.maxWeight) && inside(row.price,active.minPrice,active.maxPrice)
        && inside(row.volume,active.minVolume,active.maxVolume) && inside(row.change_pct,active.minChange,active.maxChange);
    }).sort((a,b) => (b.volume ?? -1)-(a.volume ?? -1) || a.symbol.localeCompare(b.symbol));
  },[active,categoryRows,category,setups]);
  const virtual=useVirtualizer({count:rows.length,getScrollElement:() => container.current,estimateSize:() => 62,overscan:8});
  const job=state?.jobs?.find((item:any) => item.name === 'discovery');
  const progress=state?.discovery?.progress;
  const ranges=[
    {label:'Price ($)',min:'minPrice',max:'maxPrice',step:'0.0001',floor:'0'},
    {label:'Volume',min:'minVolume',max:'maxVolume',step:'1',floor:'0'},
    {label:'Day change (%)',min:'minChange',max:'maxChange',step:'0.01',floor:undefined},
  ] as const;
  const weightRange={label:category === 'stocks' ? 'Constituent SPY weight (%)' : 'Sector SPY weight (%)',min:'minWeight',max:'maxWeight',step:'0.01',floor:'0'} as const;
  return <><ErrorNote text={error}/><div className={`discovery-layout ${setups ? 'setup-results' : ''}`}>
        <aside className="discovery-filters" aria-label="Discovery filters">
          <div className="discovery-filter-heading"><div><div className="eyebrow">REFINE THE SPY LIST</div><h3><SlidersHorizontal size={15}/> Filters</h3></div><button className="text-link" onClick={() => setFilters(current => ({...current,[category]:emptyFilters()}))}>Reset</button></div>
          <label>Explore<select aria-label="Discovery category" value={category} onChange={e => setCategory(e.target.value as Category)}><option value="stocks">Stocks</option><option value="etfs">ETFs</option><option value="sectors">Sectors</option></select></label>
          <label>Search {category}<div className="searchbox discovery-search"><Search size={14}/><input value={active.query} onChange={e => setFilter('query',e.target.value)} placeholder="Symbol or name" aria-label={`Search ${category}`}/></div></label>
          {category !== 'etfs' && <label>{category === 'sectors' ? 'Choose a sector' : 'Constituent sector'}<select value={active.sector} onChange={e => setFilter('sector',e.target.value)}><option value="">All sectors</option>{category === 'stocks' && <option value="__unclassified">Unclassified</option>}{sectors.map(s => <option key={s.symbol} value={s.name}>{s.name}</option>)}</select></label>}
          {category === 'etfs' && <><label>ETF group<select value={active.group} onChange={e => setFilter('group',e.target.value)}><option value="all">All SPY-related ETFs</option><option value="market">SPY</option><option value="sector">Sector ETFs</option></select></label><label>Fund<select value={active.fund} onChange={e => setFilter('fund',e.target.value)}><option value="">All funds</option><option value="SPY">SPY</option>{sectors.map(s => <option key={s.symbol} value={s.symbol}>{s.symbol} · {s.name}</option>)}</select></label></>}
          {category === 'sectors' && <label>Sector direction<select value={active.direction} onChange={e => setFilter('direction',e.target.value)}><option value="all">Any direction</option><option value="up">Gainers</option><option value="down">Decliners</option><option value="flat">Unchanged</option></select></label>}
          <div className="discovery-range-title">{category === 'etfs' ? 'QUOTE RANGES' : 'SPY WEIGHT & QUOTE RANGES'} <Tip text="Ranges use the dated SPY holdings weights and Tradier's reported last price, volume, and percentage change. Missing values do not pass an active range. Partial sector weights are excluded from weight filters."/></div>
          {[...(category === 'etfs' ? [] : [weightRange]),...ranges].map(range => <fieldset className="discovery-range" key={range.label}><legend>{range.label}</legend><div><label><span className="sr-only">Minimum {range.label}</span><input type="number" step={range.step} min={range.floor} placeholder="Min" value={active[range.min]} onChange={e => setFilter(range.min,e.target.value)}/></label><span>to</span><label><span className="sr-only">Maximum {range.label}</span><input type="number" step={range.step} min={range.floor} placeholder="Max" value={active[range.max]} onChange={e => setFilter(range.max,e.target.value)}/></label></div></fieldset>)}
          {setups && <><div className="discovery-range-title">ADDITIONAL SCREENING <Tip text="These filters screen the same completed Discovery snapshot. Turnover is last price × reported daily volume; the spread comes from that snapshot’s bid and ask. Missing values do not pass an active filter."/></div><label>Minimum daily turnover ($)<input type="number" min="0" step="1000" value={active.minTurnover} onChange={e => setFilter('minTurnover',e.target.value)} placeholder="Any"/></label><label>Maximum bid/ask spread (%)<input type="number" min="0" step="0.01" value={active.maxSpread} onChange={e => setFilter('maxSpread',e.target.value)} placeholder="Any"/></label></>}
          <p className="panel-caption">Sector membership is sourced from the dated SPY snapshot; unclassified constituents are not assigned a sector.</p>
        </aside>
        <div className="discovery-results">
          <div className="discovery-results-heading"><div><div className="eyebrow">TEMPORARY SPY LIST <Tip text="Last trade times are shown in your local timezone when provided. A scan may contain an earlier-session trade, especially in premarket."/></div><h3>{category === 'stocks' ? 'SPY constituents' : category === 'etfs' ? 'SPY-related ETFs' : 'SPY sectors'}</h3></div><span className="soft-badge">{rows.length.toLocaleString()} MATCHES</span></div>
          <div className="results-meta"><span>{categoryRows.length.toLocaleString()} in this list · sorted by volume</span><span>Last scan {when(snapshot?.completed)}</span></div>
          {progress && <div className="discovery-progress" role="status">{progress.phase === 'preparing' ? 'Preparing the SPY universe…' : `Scanning ${progress.processed.toLocaleString()} of ${progress.total.toLocaleString()} symbols · ${Math.floor(progress.processed / progress.total * 100)}%`} If available, the previous completed lists stay visible until the scan finishes.</div>}
          {snapshot?.completed ? <><div role="table" aria-label={`${category} discovery results`} className="virtual-table discovery-table"><div role="row" className="virtual-head"><span role="columnheader">ASSET</span><span role="columnheader">SECTOR</span><span role="columnheader">LAST</span><span role="columnheader">DAY CHANGE</span><span role="columnheader">VOLUME</span><span role="columnheader">{category === 'etfs' ? 'GROUP' : 'SPY WEIGHT'}</span>{onEnter && <span role="columnheader">ENTRY</span>}</div><div className="virtual-scroll" ref={container}><div style={{height:virtual.getTotalSize(),position:'relative'}}>{virtual.getVirtualItems().map(item => { const row=rows[item.index]; return <div role="row" className="virtual-row" key={item.key} style={{transform:`translateY(${item.start}px)`,height:item.size}}><div role="cell" className="asset-cell"><span className="asset-icon">{row.symbol.slice(0,1)}</span><div><strong>{row.symbol}</strong><small>{row.name}</small></div></div><span role="cell" className="muted">{row.sector || (category === 'etfs' && row.symbol === 'SPY' ? 'All sectors' : 'Unclassified')}</span><span role="cell">{quotePrice(row.price)}{row.price != null && <small className="cell-sub">{row.trade_time ? `Trade ${when(row.trade_time)}` : 'Trade time unavailable'}</small>}</span><span role="cell" className={row.change_pct == null ? 'muted' : row.change_pct >= 0 ? 'positive' : 'negative'}>{pct(row.change_pct)}</span><span role="cell">{volumeText(row.volume)}</span><span role="cell">{category === 'etfs' ? (row.group === 'market' ? 'SPY' : 'Sector') : weightText(row.weight)}{category === 'sectors' && !row.weight_complete && row.weight != null && <small className="cell-sub">Partial</small>}</span>{onEnter && <span role="cell"><button className="button secondary" disabled={live} title={live ? 'Live order submission is disabled; place orders in Tradier.' : 'Enter a Shadow position using fresh market quotes'} onClick={() => onEnter(row, category === 'sectors' ? 'sector' : category === 'etfs' ? 'etf' : 'stock')}>{live ? 'Disabled' : 'Enter'}</button></span>}</div>; })}</div></div></div>{!rows.length && <Empty title="No matches" description="Adjust the filters to see more of this temporary SPY list."/>}</> : <Empty title="Waiting for a SPY market-session scan" description="The worker scans from premarket through the regular market close. The lists are cleared when the application restarts."/>}
          <div className="table-foot"><span>{snapshot?.completed ? `${snapshot.quoted.toLocaleString()} of ${snapshot.requested.toLocaleString()} SPY-related symbols have a usable last price.` : (job?.message || 'The scan has not run in this session.')}</span></div>
          <p className="panel-caption">{snapshot?.snapshot ? `SPY holdings dated ${snapshot.snapshot.as_of}: ${snapshot.snapshot.row_count.toLocaleString()} constituents, ${(snapshot.snapshot.weight_sum * 100).toFixed(2)}% reported weight; ${snapshot.unclassified} unclassified. ` : state?.holdings ? `SPY holdings are ready, dated ${state.holdings.as_of}; lists appear after the next market-session scan. ` : 'A dated SPY holdings snapshot is required. '}{snapshot?.sector_weights_complete === false ? 'Sector weights are partial and excluded from weight filters. ' : ''}Lists are temporary and are not saved as curated results.</p>
        </div>
      </div>
  </>;
}
