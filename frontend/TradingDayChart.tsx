import { useEffect, useRef, useState } from 'react';
import { ColorType, createChart, CrosshairMode, LineStyle, type Time, type UTCTimestamp } from 'lightweight-charts';
import { pct } from './api';
import { Tip } from './ui';

export type TradingDaySeries = { symbol: string; return_pct_by_day: (number | null)[] };

// Lightweight Charts needs a time coordinate; both formatters show the elapsed day.
const SECONDS_PER_DAY = 86_400;
const dayTime = (day: number) => (day * SECONDS_PER_DAY) as UTCTimestamp;
const tradingDay = (time?: Time) => typeof time === 'number' ? time / SECONDS_PER_DAY : null;

export default function TradingDayChart({ series, days, light, palette }: {
  series: TradingDaySeries[]; days: number; light: boolean; palette: string[];
}) {
  const host = useRef<HTMLDivElement>(null);
  const positionCrosshair = useRef<(day: number) => void>();
  const [selectedDay, setSelectedDay] = useState(days);
  const [hoverDay, setHoverDay] = useState<number | null>(null);
  const displayedDay = hoverDay ?? selectedDay;
  const readout = (day: number) => series.map(item => `${item.symbol} ${pct(item.return_pct_by_day[day])}`).join(', ');

  useEffect(() => {
    const container = host.current;
    if (!container) return;
    const chart = createChart(container, {
      width: container.clientWidth, height: container.clientHeight,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, textColor: light ? '#596d60' : '#87968b',
        fontFamily: 'Inter, system-ui, sans-serif', fontSize: 11 },
      grid: { vertLines: { color: 'rgba(135,150,139,.08)' }, horzLines: { color: 'rgba(135,150,139,.11)' } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, minBarSpacing: 0.03, tickMarkFormatter: (time: Time) => tradingDay(time)?.toString() ?? '' },
      localization: { timeFormatter: (time: Time) => `Day ${tradingDay(time)}` },
    });
    const lines = series.map((item, index) => {
      const line = chart.addLineSeries({
        color: palette[index], lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
        priceFormat: { type: 'custom', formatter: (value: number) => `${value.toFixed(2)}%` },
      });
      line.setData(item.return_pct_by_day.map((value, day) => value === null ? { time: dayTime(day) } : { time: dayTime(day), value }));
      return line;
    });
    lines[0]?.createPriceLine({ price: 0, color: light ? '#829386' : '#758b7a', lineStyle: LineStyle.Dashed,
      lineWidth: 1, axisLabelVisible: false });
    chart.timeScale().fitContent();
    chart.subscribeCrosshairMove(event => setHoverDay(tradingDay(event.time)));
    positionCrosshair.current = day => {
      const index = series.findIndex(item => item.return_pct_by_day[day] != null);
      if (index >= 0) chart.setCrosshairPosition(series[index].return_pct_by_day[day]!, dayTime(day), lines[index]);
      else chart.clearCrosshairPosition();
    };
    const observer = new ResizeObserver(() => chart.applyOptions({ width: container.clientWidth, height: container.clientHeight }));
    observer.observe(container);
    return () => {
      positionCrosshair.current = undefined;
      observer.disconnect();
      chart.remove();
    };
  }, [series, light, palette]);

  return <section className="panel chart-panel trading-day-panel" aria-labelledby="trading-day-title">
    <div className="eyebrow">AVERAGE CUMULATIVE RETURN</div>
    <div className="chart-symbol"><h2 id="trading-day-title">Trading day breakdown</h2>
      <Tip text="Day 0 is the starting close. Each line averages the return since that close at every trading day, using the same complete windows as the rankings."/>
    </div>
    <div className="trading-day-readout">
      <span className="soft-badge">Day {displayedDay}</span>
      <ul aria-label={`Average returns on day ${displayedDay}`}>
        {series.map((item, index) => <li key={item.symbol}>
          <span className="legend-dot" style={{ background: palette[index] }} aria-hidden="true"/>
          <strong>{item.symbol}</strong><span>{pct(item.return_pct_by_day[displayedDay])}</span>
        </li>)}
      </ul>
    </div>
    <div className="trading-day-chart" ref={host} aria-hidden="true"/>
    <p className="trading-day-axis muted tiny">Trading day</p>
    <label className="trading-day-control">Inspect trading day
      <input type="range" min={0} max={days} step={1} value={selectedDay}
        aria-valuetext={`Day ${selectedDay}: ${readout(selectedDay)}`}
        onChange={event => {
          const day = Number(event.target.value);
          setHoverDay(null);
          setSelectedDay(day);
          positionCrosshair.current?.(day);
        }}/>
    </label>
  </section>;
}
