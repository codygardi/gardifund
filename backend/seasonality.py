"""Calendar-aligned, close-to-close research from completed daily sessions."""
from bisect import bisect_left
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta
from math import fsum, isfinite, sqrt
from statistics import stdev
from threading import RLock
import time

from .market import MARKET_HISTORY_START, MARKET_TIMEZONE, nyse_session_hours

MIN_YEARS = 3
MAX_YEARS = 15
MAX_SEASONAL_WINDOW = 63
ANCHOR_DAYS = [(date(2001, 1, 1) + timedelta(days=offset)).isoformat()[5:] for offset in range(365)]


def _return_pct(entry, close):
    value = (close / entry - 1) * 100
    return value if isfinite(value) else None


def seasonal_windows(sessions, bars, years, days):
    missing = [0]
    for stamp in sessions:
        missing.append(missing[-1] + (stamp not in bars))
    rows, observed_starts = [], set()
    for anchor in ANCHOR_DAYS:
        returns, starts = [], []
        for year in years:
            index = bisect_left(sessions, f"{year}-{anchor}")
            end = index + days
            if end >= len(sessions) or sessions[index][:4] != str(year) or missing[end + 1] != missing[index]:
                continue
            value = _return_pct(bars[sessions[index]]["close"], bars[sessions[end]]["close"])
            if value is not None:
                starts.append(sessions[index])
                returns.append(value)
        count = len(returns)
        # Consecutive weekend anchors can select exactly the same windows.
        sample = tuple(starts)
        if count < MIN_YEARS or sample in observed_starts:
            continue
        observed_starts.add(sample)
        average = fsum(value / count for value in returns)
        deviation = stdev(returns)
        score = average / (deviation / sqrt(count)) if deviation else None
        rows.append({"start": anchor, "mean_pct": average, "stddev_pct": deviation,
                     "t_stat": score if score is not None and isfinite(score) else None,
                     "positive_pct": 100 * sum(value > 0 for value in returns) / count, "samples": count,
                     "example_start": starts[-1], "example_end": sessions[bisect_left(sessions, starts[-1]) + days]})
    return rows


def yearly_curves(sessions, bars, current_year):
    by_year = defaultdict(list)
    for stamp in sessions:
        by_year[int(stamp[:4])].append(stamp)
    curves = []
    for year, stamps in by_year.items():
        available = [stamp for stamp in stamps if stamp in bars]
        if not available:
            continue
        first, last = available[0], available[-1]
        entry = bars[first]["close"]
        curves.append({"year": year, "start": first, "end": last, "sessions": len(available),
                       "expected_sessions": len(stamps), "ytd": year == current_year,
                       "points": [[stamp[5:], _return_pct(entry, bars[stamp]["close"]) if stamp in bars else None]
                                  for stamp in stamps if first <= stamp <= last]})
    return curves


def indicators(sessions, bars):
    # Start again after a missing close and report the date of the latest usable bar.
    run = []
    as_of = None
    contiguous = []
    for stamp in sessions:
        if stamp in bars:
            contiguous.append(bars[stamp])
            run, as_of = contiguous, stamp
        else:
            contiguous = []
    rsi, k, d = None, None, None
    if len(run) >= 15:
        moves = [right["close"] - left["close"] for left, right in zip(run, run[1:])]
        gain = fsum(max(0.0, move) / 14 for move in moves[:14])
        loss = fsum(max(0.0, -move) / 14 for move in moves[:14])
        for move in moves[14:]:
            gain = gain * (13 / 14) + max(0.0, move) / 14
            loss = loss * (13 / 14) + max(0.0, -move) / 14
        rsi = 100 / (1 + loss / gain) if gain else (0.0 if loss else 50.0)
    stochastic = []
    for end in range(max(14, len(run) - 2), len(run) + 1):
        window = run[end - 14:end]
        low, high = min(bar["low"] for bar in window), max(bar["high"] for bar in window)
        stochastic.append(100 * ((window[-1]["close"] - low) / (high - low)) if high > low else None)
    if stochastic:
        k = stochastic[-1]
        if len(stochastic) == 3 and all(value is not None for value in stochastic):
            d = fsum(stochastic) / 3
    return {"as_of": as_of, "rsi": rsi, "stochastic_k": k, "stochastic_d": d}


class Seasonality:
    def __init__(self, market, clock=time.time):
        self.market = market
        self.clock = clock
        self.cache = OrderedDict()
        self.lock = RLock()

    def get(self, symbol, years=MAX_YEARS, days=21):
        if not MIN_YEARS <= years <= MAX_YEARS or not 1 <= days <= MAX_SEASONAL_WINDOW:
            raise ValueError(f"Choose {MIN_YEARS}–{MAX_YEARS} years and 1–{MAX_SEASONAL_WINDOW} trading days.")
        now = self.clock()
        current_year = datetime.fromtimestamp(now, MARKET_TIMEZONE).year
        first_year = max(MARKET_HISTORY_START.year, current_year - years)
        start = f"{first_year}-01-01"
        closes_at = {stamp: closing.timestamp() for stamp, _, closing in nyse_session_hours(first_year, current_year)
                     if stamp >= start and closing.timestamp() <= now}
        sessions = list(closes_at)
        last_session = sessions[-1] if sessions else None
        data = self.market.read(symbol, "1d", start=start, end=last_session, limit=6000) if sessions else {"bars": [], "coverage": None}
        coverage = data["coverage"]
        key = (symbol, years, days, current_year, last_session, coverage["updated"] if coverage else None)
        with self.lock:
            if key not in self.cache:
                stored_at = coverage["updated"] if coverage else now
                bars = {bar["time"]: bar for bar in data["bars"]
                        if bar["time"] in closes_at and closes_at[bar["time"]] <= stored_at}
                self.cache[key] = {"symbol": symbol, "lookback_years": years, "trading_days": days,
                                   "first_year": first_year, "last_completed_year": current_year - 1,
                                   "latest_session": last_session, "coverage": coverage,
                                   "years": yearly_curves(sessions, bars, current_year),
                                   "windows": seasonal_windows(sessions, bars, range(first_year, current_year), days),
                                   "indicators": indicators(sessions, bars)}
                if len(self.cache) > 8:
                    self.cache.popitem(last=False)
            self.cache.move_to_end(key)
            return self.cache[key]
