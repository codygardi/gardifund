"""Bounded rankings for SPY, its sector funds and dated constituent baskets."""
from calendar import monthrange
from collections import OrderedDict, defaultdict
from datetime import datetime, timezone
from math import fsum, isfinite
from threading import RLock
from typing import Literal
import time

import numpy as np

from .db import SECTORS
from .market import LOCK, MARKET_HISTORY_START, MARKET_TIMEZONE, nyse_session_hours


PerformanceMode = Literal["market", "sectors", "etfs", "stocks"]
MAX_WINDOW_DAYS = 5000
ADJUSTED = "split_dividend_adjusted"
UNVERIFIED = "unverified"
SPY = {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "sector": None}
SECTOR_FUNDS = [{"symbol": symbol, "name": sector, "sector": sector} for symbol, sector in SECTORS]
SECTOR_SYMBOLS = {fund["symbol"] for fund in SECTOR_FUNDS}


def _average_return_by_day(closes, sessions, starts, trading_days, final_return):
    """Use the same starting closes and complete windows for every offset."""
    prices = np.array([closes.get(day, np.nan) for day in sessions], dtype=float)
    indexes = np.asarray(starts, dtype=np.intp)
    entries = prices[indexes]
    curve = [0.0]
    # Work one day at a time instead of allocating a windows-by-days matrix.
    with np.errstate(over="ignore", invalid="ignore"):
        for day in range(1, trading_days):
            returns = (prices[indexes + day] / entries - 1) * 100
            average = float(np.sum(returns / entries.size))
            curve.append(average if isfinite(average) else None)
    curve.append(final_return)
    return curve


class PeriodPerformance:
    def __init__(self, db, market, clock=time.time):
        self.db, self.market, self.clock = db, market, clock
        self.cache = OrderedDict()
        self.lock = RLock()

    def _cached(self, key, calculate):
        if key not in self.cache:
            self.cache[key] = calculate()
            while len(self.cache) > 8:
                self.cache.popitem(last=False)
        self.cache.move_to_end(key)
        return self.cache[key]

    def get(self, mode: PerformanceMode, year: int, month: int | None = None, top_n: int = 5):
        now = self.clock()
        today = datetime.fromtimestamp(now, timezone.utc)
        first_year = MARKET_HISTORY_START.year if mode in {"market", "etfs"} else 2000
        if not first_year <= year <= today.year or not 1 <= top_n <= 5:
            raise ValueError(f"Choose a year from {first_year} onward and a Top N from 1–5.")
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
            conn.execute("BEGIN")
            snapshot = None
            if mode in {"sectors", "stocks"}:
                snapshot = conn.execute("SELECT imported, weight_sum FROM holdings_snapshots WHERE as_of=?", (start,)).fetchone()
                if not snapshot:
                    return {**empty, "message": f"A SPY holdings snapshot dated {start} is required for this period."}
                fields = "symbol, name, sector, weight" if mode == "sectors" else "symbol, name, sector"
                members = [dict(row) for row in conn.execute(
                    f"SELECT {fields} FROM holdings WHERE as_of=? LIMIT 1001", (start,))]
                if len(members) > 1000:
                    raise ValueError("The constituent universe exceeds the 1,000-symbol calculation limit.")
            elif mode == "market":
                members = [SPY]
            else:
                members = [SPY, *SECTOR_FUNDS]
            symbols = {row["symbol"] for row in members}
            generations = tuple(tuple(row) for row in conn.execute(
                "SELECT symbol, updated, adjustment FROM market_files WHERE interval='1d' ORDER BY symbol"
            ) if row["symbol"] in symbols)
            key = (mode, year, month, start, end, snapshot["imported"] if snapshot else None, generations)
            result = self._cached(key, lambda: self._rank_sectors(period, members, sessions, snapshot) if mode == "sectors"
                                  else self._rank(period, members, symbols, generations, set(sessions), snapshot))
            return {**result, "rows": result["rows"][:top_n], "series": result["series"][:top_n]}

    def sector_windows(self, trading_days: int, lookback_years: int, top_n: int):
        now = self.clock()
        today = datetime.fromtimestamp(now, MARKET_TIMEZONE).date()
        max_years = today.year - MARKET_HISTORY_START.year + 1
        if not (1 <= trading_days <= MAX_WINDOW_DAYS and 1 <= lookback_years <= max_years and 1 <= top_n <= 3):
            raise ValueError(f"Choose 1–{MAX_WINDOW_DAYS} trading days, 1–{max_years} years, and 1–3 sectors.")
        year = today.year - lookback_years
        start = (MARKET_HISTORY_START if year < MARKET_HISTORY_START.year else
                 today.replace(year=year, day=min(today.day, monthrange(year, today.month)[1])))
        first_day = start.isoformat()
        sessions = [day for day, _, closing in nyse_session_hours(start.year, today.year)
                    if day >= first_day and closing.timestamp() <= now]
        period = {"start": sessions[0] if sessions else None, "end": sessions[-1] if sessions else None,
                  "trading_days": trading_days, "lookback_years": lookback_years,
                  "possible_windows": max(0, len(sessions) - trading_days)}
        if not period["possible_windows"]:
            return {**period, "rows": [], "series": [], "basis": None, "ranked": 0, "missing": len(SECTOR_FUNDS), "incompatible": 0,
                    "message": f"{trading_days} trading days requires {trading_days + 1} completed sessions; "
                               f"{len(sessions)} fall within this lookback."}
        with self.lock, LOCK, self.db.connect() as conn:
            generations = tuple(tuple(row) for row in conn.execute(
                "SELECT symbol, updated, adjustment FROM market_files WHERE interval='1d' ORDER BY symbol"
            ) if row["symbol"] in SECTOR_SYMBOLS)
            key = ("sector_windows", trading_days, lookback_years, period["start"], period["end"], generations)
            result = self._cached(key, lambda: self._rank_sector_windows(period, sessions, generations))
            return {**result, "rows": result["rows"][:top_n], "series": result["series"][:top_n]}

    def _rank_sector_windows(self, period, sessions, generations):
        days = period["trading_days"]
        prices = self.market.daily_closes_between(SECTOR_SYMBOLS, sessions[0], sessions[-1])
        basis_by_symbol = {symbol: adjustment for symbol, _, adjustment in generations}
        groups = {ADJUSTED: [], UNVERIFIED: []}
        starts_by_symbol = {}
        for fund in SECTOR_FUNDS:
            symbol = fund["symbol"]
            basis = basis_by_symbol.get(symbol)
            if basis not in groups:
                continue
            closes = prices.get(symbol, {})
            returns, starts, streak, first, last = [], [], 0, None, None
            for index, day in enumerate(sessions):
                close = closes.get(day)
                if close is None or not isfinite(close) or close <= 0:
                    streak = 0
                    continue
                streak += 1
                if streak <= days:
                    continue
                previous = sessions[index - days]
                change = (close / closes[previous] - 1) * 100
                if isfinite(change):
                    returns.append(change)
                    starts.append(index - days)
                    first, last = first or previous, day
            if returns:
                starts_by_symbol[symbol] = starts
                count = len(returns)
                profitable_count = sum(value > 0 for value in returns)
                groups[basis].append({**fund, "return_pct": fsum(value / count for value in returns),
                                      "profitable_window_pct": 100 * profitable_count / count,
                                      "window_count": count, "window_start": first, "window_end": last})

        basis = ADJUSTED if len(groups[ADJUSTED]) >= len(groups[UNVERIFIED]) else UNVERIFIED
        ranked = sorted(groups[basis], key=lambda row: (-row["return_pct"], row["symbol"]))
        series = []
        for row in ranked[:3]:
            symbol = row["symbol"]
            curve = _average_return_by_day(prices[symbol], sessions, starts_by_symbol[symbol], days, row["return_pct"])
            series.append({"symbol": symbol, "return_pct_by_day": curve})
        incompatible = len(groups[UNVERIFIED if basis == ADJUSTED else ADJUSTED])
        return {**period, "rows": ranked, "series": series, "basis": basis if ranked else None, "ranked": len(ranked),
                "missing": len(SECTOR_FUNDS) - len(ranked) - incompatible, "incompatible": incompatible,
                "message": None if ranked else "No sector ETF has enough consecutive daily closes for this window."}

    def _rank_sectors(self, period, members, sessions, snapshot):
        start, end = period["start"], period["end"]
        sectors = defaultdict(list)
        for member in members:
            if member["sector"]:
                sectors[member["sector"]].append(member)
        prices = self.market.daily_closes({h["symbol"] for group in sectors.values() for h in group}, sessions, ADJUSTED)
        ranked, by_sector = [], {}
        gap_count = 0
        for _, name in SECTORS:
            holdings = sectors[name]
            if not holdings or any(not {start, end}.issubset(prices.get(h["symbol"], {})) for h in holdings):
                continue
            weight = sum(h["weight"] for h in holdings)
            weighted = [(prices[h["symbol"]], h["weight"] / weight) for h in holdings]
            bars = []
            for day in sessions:
                close = None
                if all(day in quotes for quotes, _ in weighted):
                    value = sum(w * (quotes[day] / quotes[start]) for quotes, w in weighted) * 100
                    close = value if isfinite(value) and value > 0 else None
                bars.append({"time": day, "close": close})
            if bars[0]["close"] is None or bars[-1]["close"] is None:
                continue
            bars[0]["close"] = 100.0
            gap_count += sum(bar["close"] is None for bar in bars)
            ranked.append({"symbol": name, "name": name, "sector": name, "weight": weight,
                           "constituent_count": len(holdings), "return_pct": bars[-1]["close"] - 100})
            by_sector[name] = {"symbol": name, "bars": bars}
        ranked.sort(key=lambda row: (-row["return_pct"], row["name"]))
        rows = ranked[:5]
        missing = len(SECTORS) - len(ranked)
        snapshot_complete = snapshot["weight_sum"] >= .995 and all(h["sector"] for h in members)
        notes = ["Returns use start-date SPY weights within each sector and provider-declared split- and dividend-adjusted closes."]
        if missing:
            notes.append(f"{missing} of {len(SECTORS)} sectors lack classified holdings or adjusted closes for every constituent on both dates.")
        if gap_count:
            notes.append("Series with missing constituent closes show observed points only.")
        if not snapshot_complete:
            notes.append("The start-date holdings weights or sector classifications are incomplete.")
        return {**period, "rows": rows, "series": [by_sector[row["name"]] for row in rows],
                "complete": not missing and not gap_count and snapshot_complete, "eligible": len(SECTORS),
                "missing": missing, "basis": ADJUSTED if rows else None, "message": " ".join(notes)}

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
