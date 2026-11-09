"""Bounded rankings for SPY, its sector funds and dated stock constituents."""
from collections import OrderedDict
from datetime import datetime, timezone
from math import isfinite
from threading import RLock
from typing import Literal
import time

from .db import SECTORS
from .market import LOCK, nyse_session_hours


PerformanceMode = Literal["market", "sectors", "etfs", "stocks"]
ADJUSTED = "split_dividend_adjusted"
UNVERIFIED = "unverified"
SPY = {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "sector": None}
SECTOR_FUNDS = [{"symbol": symbol, "name": sector, "sector": sector} for symbol, sector in SECTORS]


class PeriodPerformance:
    def __init__(self, db, market, clock=time.time):
        self.db, self.market, self.clock = db, market, clock
        self.cache = OrderedDict()
        self.lock = RLock()

    def get(self, mode: PerformanceMode, year: int, month: int | None = None, top_n: int = 5):
        now = self.clock()
        today = datetime.fromtimestamp(now, timezone.utc)
        if not 2000 <= year <= today.year or not 1 <= top_n <= 5:
            raise ValueError("Choose a year from 2000 onward and a Top N from 1–5.")
        if month is not None and (not 1 <= month <= 12 or (year, month) > (today.year, today.month)):
            raise ValueError("Choose a month that has started.")
        prefix = f"{year:04d}-" if month is None else f"{year:04d}-{month:02d}-"
        sessions = [day for day, _, closing in nyse_session_hours(year, year)
                    if day.startswith(prefix) and closing.timestamp() <= now]
        period = {"mode": mode, "year": year, "month": month,
                  "start": sessions[0] if sessions else None, "end": sessions[-1] if sessions else None}
        empty = {**period, "rows": [], "series": [], "complete": False}
        if len(sessions) < 2:
            return {**empty, "message": "This period needs at least two completed NYSE sessions."}
        start, end = sessions[0], sessions[-1]
        with self.lock, LOCK, self.db.connect() as conn:
            snapshot = None
            if mode == "stocks":
                snapshot = conn.execute("SELECT imported, weight_sum FROM holdings_snapshots WHERE as_of=?", (start,)).fetchone()
                if not snapshot:
                    return {**empty, "message": f"Import a dated SPY holdings snapshot for {start} to rank stocks in this period."}
                members = [dict(row) for row in conn.execute(
                    "SELECT symbol, name, sector FROM holdings WHERE as_of=? LIMIT 1001", (start,))]
                if len(members) > 1000:
                    raise ValueError("The constituent universe exceeds the 1,000-symbol calculation limit.")
            elif mode == "market":
                members = [SPY]
            elif mode == "sectors":
                members = SECTOR_FUNDS
            else:
                members = [SPY, *SECTOR_FUNDS]
            symbols = {row["symbol"] for row in members}
            generations = tuple(tuple(row) for row in conn.execute(
                "SELECT symbol, updated, adjustment FROM market_files WHERE interval='1d' ORDER BY symbol"
            ) if row["symbol"] in symbols)
            key = (mode, year, month, start, end, snapshot["imported"] if snapshot else None, generations)
            if key not in self.cache:
                self.cache[key] = self._rank(period, members, symbols, generations, set(sessions), snapshot)
                while len(self.cache) > 8:
                    self.cache.popitem(last=False)
            self.cache.move_to_end(key)
            result = self.cache[key]
            return {**result, "rows": result["rows"][:top_n], "series": result["series"][:top_n]}

    def _rank(self, period, members, symbols, generations, sessions, snapshot):
        start, end = period["start"], period["end"]
        stocks = period["mode"] == "stocks"
        prices = self.market.daily_endpoint_closes(symbols, start, end, ADJUSTED if stocks else None)
        adjustments = {symbol: adjustment for symbol, _, adjustment in generations}
        groups = {ADJUSTED: [], UNVERIFIED: []}
        for member in members:
            symbol = member["symbol"]
            values = prices.get(symbol, {})
            if start not in values or end not in values:
                continue
            change = (values[end] / values[start] - 1) * 100
            basis = adjustments.get(symbol)
            if basis in groups and isfinite(change):
                groups[basis].append({**member, "return_pct": change})

        # Pick the largest comparable group of funds. Prefer the adjusted basis on ties.
        basis = ADJUSTED if stocks or len(groups[ADJUSTED]) >= len(groups[UNVERIFIED]) else UNVERIFIED
        ranked = sorted(groups[basis], key=lambda row: (-row["return_pct"], row["symbol"]))
        other_basis = UNVERIFIED if basis == ADJUSTED else ADJUSTED
        incompatible = len(groups[other_basis])
        missing = len(members) - len(ranked) - incompatible
        weight_covered = not stocks or snapshot["weight_sum"] >= .995
        complete = bool(members) and not missing and not incompatible and weight_covered
        rows, series = ranked[:5], []
        for row in rows:
            data = self.market.read(row["symbol"], "1d", start=start, end=end, limit=400)
            bars = [{"time": bar["time"], "close": bar["close"]} for bar in data["bars"] if bar["time"] in sessions]
            series.append({"symbol": row["symbol"], "bars": bars})

        if stocks:
            basis_text = "Provider-declared split- and dividend-adjusted closes for start-date SPY constituents."
        elif ranked and basis == ADJUSTED:
            basis_text = "Provider-declared split- and dividend-adjusted fund closes."
        else:
            basis_text = "Stored fund closes with unverified adjustments."
        notes = [basis_text, "Measured from the first session close to the last completed session close."]
        if missing:
            notes.append(f"{missing} of {len(members)} lack comparable closes on both dates.")
        if incompatible:
            notes.append(f"{incompatible} use a different adjustment basis and were excluded.")
        if not weight_covered:
            notes.append("The dated holdings snapshot covers less than 99.5% of SPY by weight.")
        return {**period, "rows": rows, "series": series, "complete": complete,
                "eligible": len(members), "missing": missing, "incompatible": incompatible,
                "basis": basis if ranked else None, "message": " ".join(notes)}
