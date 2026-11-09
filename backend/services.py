import io
import json
import math
import time
from collections import defaultdict
from datetime import date, datetime

import httpx
import openpyxl

from .db import SECTORS
from .market import SYMBOL, valid_symbol
from .tradier import ProviderError

SSGA_URL = "https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-{}.xlsx"
SECTOR_NAMES = {name for _, name in SECTORS}


def workbook(ticker):
    try:
        with httpx.Client(timeout=30, follow_redirects=False, headers={"User-Agent": "GARDiFUND/1.0"}) as client:
            response = client.get(SSGA_URL.format(ticker.lower()))
            response.raise_for_status()
            if len(response.content) > 5_000_000:
                raise ProviderError("Holdings workbook exceeds size limit")
            sheet = openpyxl.load_workbook(io.BytesIO(response.content), read_only=True, data_only=True).active
            rows = list(sheet.values)
            as_of = datetime.strptime(str(rows[2][1]).replace("As of ", ""), "%d-%b-%Y").date().isoformat()
            if list(rows[4][:6]) != ["Name", "Ticker", "Identifier", "SEDOL", "Weight", "Sector"]:
                raise ValueError("Unrecognized holdings format")
            holdings = []
            for row in rows[5:]:
                name, symbol, _, _, weight, sector, _, currency = row[:8]
                if (isinstance(symbol, str) and SYMBOL.fullmatch(symbol.upper()) and currency == "USD"
                        and isinstance(weight, (int, float)) and weight > 0):
                    holdings.append({"symbol": symbol.upper(), "name": str(name), "weight": float(weight) / 100})
            if not holdings:
                raise ValueError("No equity holdings")
            return as_of, holdings
    except (httpx.HTTPError, ValueError, IndexError, TypeError, KeyError):
        raise ProviderError(f"Could not load or validate {ticker} holdings from State Street") from None


def store_holdings(db, as_of, rows, source, note=""):
    date.fromisoformat(as_of)
    if not rows or len(rows) > 10000:
        raise ValueError("A holdings snapshot must contain 1–10,000 rows")
    seen, cleaned = set(), []
    for row in rows:
        symbol = valid_symbol(row["symbol"])
        sector = row.get("sector") or None
        weight = float(row["weight"])
        if symbol in seen or not math.isfinite(weight) or not 0 < weight <= 1 or (sector and sector not in SECTOR_NAMES):
            raise ValueError("Invalid symbol, weight, sector, or duplicate constituent")
        seen.add(symbol)
        cleaned.append((as_of, symbol, str(row.get("name") or symbol)[:160], sector, weight, source[:120]))
    total = sum(r[4] for r in cleaned)
    if total > 1.01:
        raise ValueError("Holdings weights exceed 100%")
    now = time.time()
    with db.connect() as conn:
        conn.execute("DELETE FROM holdings WHERE as_of=?", (as_of,))
        conn.executemany("INSERT INTO holdings VALUES(?,?,?,?,?,?)", cleaned)
        conn.execute("INSERT OR REPLACE INTO holdings_snapshots VALUES(?,?,?,?,?,?)", (as_of, source[:120], now, len(cleaned), total, note[:500]))
        for _, symbol, name, sector, _, _ in cleaned:
            conn.execute("""INSERT INTO instruments VALUES(?,?,?,?,?,?) ON CONFLICT(symbol) DO UPDATE SET
              name=excluded.name,sector=excluded.sector,source=excluded.source,updated=excluded.updated""",
              (symbol, name, "stock", sector, source[:120], now))
        conn.execute("INSERT INTO imports(dataset,source,created,status,message) VALUES(?,?,?,?,?)",
                     ("SPY constituents", source[:120], now, "complete", f"{len(cleaned)} rows dated {as_of}; weight {total:.2%}"))
        db.event("investing", "holdings_updated", conn)
    return {"as_of": as_of, "count": len(cleaned), "weight": total,
            "classified": sum(1 for r in cleaned if r[3])}


def refresh_holdings(db, checkpoint=None):
    if checkpoint:
        checkpoint()
    as_of, rows = workbook("SPY")
    memberships = defaultdict(set)
    missing = []
    for symbol, sector in SECTORS:
        if checkpoint:
            checkpoint()
        try:
            sector_date, sector_rows = workbook(symbol)
            if sector_date != as_of:
                missing.append(symbol)
                continue
            for item in sector_rows:
                memberships[item["symbol"]].add(sector)
        except ProviderError:
            missing.append(symbol)
    for item in rows:
        matches = memberships[item["symbol"]]
        item["sector"] = next(iter(matches)) if len(matches) == 1 else None
    with db.connect() as conn:
        previous = conn.execute("SELECT * FROM holdings_snapshots WHERE as_of=?", (as_of,)).fetchone()
        previous_classified = conn.execute("SELECT COUNT(*) FROM holdings WHERE as_of=? AND sector IS NOT NULL", (as_of,)).fetchone()[0]
    classified = sum(1 for item in rows if item["sector"])
    if previous and (previous["source"] != "State Street SPY and sector ETF holdings" or previous_classified > classified):
        return {"as_of": as_of, "count": previous["row_count"], "weight": previous["weight_sum"], "classified": previous_classified}
    note = "Sector labels inferred from same-date SPDR sector ETF membership; unmatched or overlapping holdings are unclassified."
    if missing:
        note += " Missing or different-date sector funds: " + ", ".join(missing)
    return store_holdings(db, as_of, rows, "State Street SPY and sector ETF holdings", note)


def sync_accounts(db, adapter, checkpoint=None):
    accounts = adapter.accounts(checkpoint=checkpoint) if checkpoint else adapter.accounts()
    now = time.time()
    with db.connect() as conn:
        conn.execute("DELETE FROM live_accounts")
        for account in accounts:
            conn.execute("INSERT INTO live_accounts VALUES(?,?,?)", (account["account_id"], json.dumps(account), now))
        from .quotes import number
        from .shadow import trading_day
        equities = [number((a.get('balance') or {}).get('total_equity')) for a in accounts]
        cash = [number((a.get('balance') or {}).get('total_cash')) for a in accounts]
        if equities and all(value is not None for value in equities):
            conn.execute('INSERT OR REPLACE INTO live_daily_performance VALUES(?,?,?,?)',
                         (trading_day(now), now, sum(equities), sum(cash) if all(value is not None for value in cash) else None))
        db.event("overview", "account_synced", conn)
    return f"Synchronized {len(accounts)} account(s)"


def enrich_positions(db, positions):
    """Attach cached quotes and known instrument metadata without changing account records."""
    symbols = sorted({p["symbol"] for p in positions if isinstance(p, dict) and isinstance(p.get("symbol"), str)})
    metadata = {}
    with db.connect() as conn:
        for offset in range(0, len(symbols), 100):
            batch = symbols[offset:offset + 100]
            placeholders = ",".join("?" for _ in batch)
            for row in conn.execute(f"""SELECT symbol,name,kind,sector FROM instruments
                                         WHERE symbol IN ({placeholders})""", batch):
                metadata[row["symbol"]] = dict(row)
            for row in conn.execute(f"""SELECT symbol,price,change,change_pct,volume,quote_time,fetched
                                         FROM quotes WHERE symbol IN ({placeholders})""", batch):
                metadata.setdefault(row["symbol"], {}).update(dict(row))
    result = []
    for position in positions:
        if not isinstance(position, dict):
            continue
        symbol = position.get("symbol")
        if not isinstance(symbol, str):
            continue
        result.append({**position, **metadata.get(symbol, {})})
    return result


def discovery_rows(db, kind="all", sector=None, query="", limit=1000):
    with db.connect() as conn:
        snapshot = conn.execute("SELECT * FROM holdings_snapshots ORDER BY as_of DESC LIMIT 1").fetchone()
        stock_rows = [dict(r) for r in conn.execute("""SELECT h.symbol,h.name,'stock' AS kind,h.sector,h.weight,
             q.price,q.change,q.change_pct,q.volume,q.fetched FROM holdings h LEFT JOIN quotes q ON q.symbol=h.symbol
             WHERE h.as_of=?""", (snapshot["as_of"],))] if snapshot else []
        etf_symbols = ["SPY", *(symbol for symbol, _ in SECTORS)]
        placeholders = ",".join("?" for _ in etf_symbols)
        etfs = [dict(r) for r in conn.execute(f"""SELECT i.symbol,i.name,i.kind,i.sector,NULL AS weight,
             q.price,q.change,q.change_pct,q.volume,q.fetched FROM instruments i LEFT JOIN quotes q ON q.symbol=i.symbol
             WHERE i.kind='etf' AND i.symbol IN ({placeholders})""", etf_symbols)]
    rows = stock_rows + etfs
    if kind != "all":
        rows = [r for r in rows if r["kind"] == kind]
    if sector:
        rows = [r for r in rows if r["sector"] == sector]
    if query:
        term = query.lower()
        rows = [r for r in rows if term in r["symbol"].lower() or term in r["name"].lower()]
    rows.sort(key=lambda r: (r["weight"] is None, -(r["weight"] or 0), r["symbol"]))
    return {"snapshot": dict(snapshot) if snapshot else None, "rows": rows[:min(limit, 2000)]}


def attribution(db, market, start, end, sector=None, limit=10):
    date.fromisoformat(start)
    date.fromisoformat(end)
    if start >= end:
        raise ValueError("Start must be before end")
    with db.connect() as conn:
        snap = conn.execute("SELECT * FROM holdings_snapshots WHERE as_of=?", (start,)).fetchone()
        if not snap:
            return {"rows": [], "complete": False, "message": "A holdings and weights snapshot dated exactly at the start is required."}
        holdings = [dict(r) for r in conn.execute("SELECT * FROM holdings WHERE as_of=?", (start,))]
        unclassified = sum(1 for h in holdings if not h["sector"])
        if sector:
            holdings = [r for r in holdings if r["sector"] == sector]
    prices = market.daily_endpoint_closes({h["symbol"] for h in holdings}, start, end)
    output, missing = [], 0
    sector_weight = sum(h["weight"] for h in holdings)
    for h in holdings:
        p = prices.get(h["symbol"], {})
        if start not in p or end not in p or not h["sector"]:
            missing += 1
            continue
        ret = p[end] / p[start] - 1
        output.append({"symbol": h["symbol"], "name": h["name"], "sector": h["sector"], "weight": h["weight"],
                       "return": ret, "contribution": (h["weight"] / sector_weight if sector else h["weight"]) * ret})
    output.sort(key=lambda r: abs(r["contribution"]), reverse=True)
    return {"rows": output[:min(limit, 50)], "complete": bool(holdings) and missing == 0 and unclassified == 0 and snap["weight_sum"] >= .995,
            "eligible": len(holdings), "missing": missing, "unclassified_in_snapshot": unclassified, "as_of": start,
            "message": "Based on start-date weights and provider-declared adjusted closes; incomplete results exclude missing data."}
