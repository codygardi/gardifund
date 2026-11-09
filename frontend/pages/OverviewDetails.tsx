import { useState } from 'react';
import { ArrowDownLeft, ArrowUpRight } from 'lucide-react';
import { pct, quotePrice, useResource } from '../api';
import { Empty, ErrorNote } from '../ui';

export type ActivityKind = 'positions' | 'orders';

export default function OverviewDetails({ kind, mode }: { kind: ActivityKind; mode: 'live' | 'shadow' }) {
  const positions = kind === 'positions';
  const live = mode === 'live';
  const { data, error } = useResource(positions ? '/positions' : `/overview?mode=${mode}`);
  const [showAll, setShowAll] = useState(false);
  if (error) return <ErrorNote text={error}/>;
  if (!data) return <p className="muted small">Loading details…</p>;

  if (positions) {
    const rows = (data.rows || []).filter((row: any) => row.mode === mode);
    if (!rows.length) return <Empty title="No position details available"/>;
    return <>
      <div className="table-wrap"><table>
        <thead><tr><th>ASSET</th><th>SHARES</th><th>ENTRY</th><th>LAST</th><th>P/L %</th></tr></thead>
        <tbody>{(showAll ? rows : rows.slice(0, 8)).map((position: any, index: number) => <tr key={`${position.symbol}-${index}`}>
          <td><strong>{position.symbol}</strong><small className="cell-sub">{position.category}</small></td>
          <td>{position.quantity}</td><td>{quotePrice(position.entry_price)}</td><td>{quotePrice(position.price)}</td>
          <td className={position.pl_pct == null ? 'muted' : position.pl_pct >= 0 ? 'positive' : 'negative'}>{pct(position.pl_pct)}</td>
        </tr>)}</tbody>
      </table></div>
      {rows.length > 8 && <button type="button" className="text-link" onClick={() => setShowAll(open => !open)}>{showAll ? 'Show fewer' : 'Show all'}</button>}
    </>;
  }

  const orders = live ? (data.accounts || []).flatMap((account: any) => account.orders || []) : data.activity || [];
  if (!orders.length) return <Empty title="No order details available"/>;
  return <div className="activity-list">{orders.slice(0, 10).map((order: any, index: number) => {
    const price = order.fill_price ?? order.avg_fill_price ?? (order.price_cents == null ? order.price : order.price_cents / 100);
    return <div className="activity-row" key={index}>
      <span className="activity-icon">{order.side?.startsWith('sell') ? <ArrowUpRight size={16}/> : <ArrowDownLeft size={16}/>}</span>
      <div><strong>{order.side?.toUpperCase()} {order.symbol}</strong>
        <small>{order.quantity} shares · {order.order_type ? `${order.order_type} · ` : ''}{order.status || (live ? 'Status unavailable' : 'simulated fill')}</small></div>
      <span>{order.status === 'working' && order.limit_price != null ? `≥ ${quotePrice(order.limit_price)}` : quotePrice(price)}</span>
    </div>;
  })}</div>;
}
