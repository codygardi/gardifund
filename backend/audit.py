"""Bounded, non-destructive comparison of stored SPY closes with a second provider."""
from datetime import date
import math
import statistics

from .tradier import ProviderError


def positive_number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def compare_daily_closes(primary, reference, truncated=False, dividend_dates=None):
    """Compare dates only; reference prices are never stored as replacement bars.

    ``reference`` contains (ISO date, provider Close, provider Adj Close).
    Duplicate dates are excluded rather than resolved by guesswork.
    """
    if not primary:
        raise ValueError("No stored SPY daily bars are available for comparison")
    first, last = primary[0]["time"], primary[-1]["time"]
    dates, duplicates, seen = {}, set(), set()
    for stamp, close, adjusted in reference:
        try:
            stamp = date.fromisoformat(str(stamp)).isoformat()
        except ValueError:
            continue
        if not first <= stamp <= last:
            continue
        if stamp in seen:
            dates.pop(stamp, None)
            duplicates.add(stamp)
            continue
        seen.add(stamp)
        close, adjusted = positive_number(close), positive_number(adjusted)
        if close is not None:
            dates[stamp] = (close, adjusted)
    if not dates:
        raise ProviderError("Yahoo supplied no usable daily reference closes in the stored range")
    close_errors, adjusted_errors, matched_dates, outliers = [], [], [], []
    primary_dates = {row["time"] for row in primary}
    for row in primary:
        pair = dates.get(row["time"])
        if not pair:
            continue
        value = positive_number(row["close"])
        if value is None:
            continue
        matched_dates.append(row["time"])
        difference = abs(value / pair[0] - 1) * 100
        close_errors.append(difference)
        if difference > .01:
            outliers.append({"date": row["time"], "difference_pct": difference})
        if pair[1] is not None:
            adjusted_errors.append(abs(value / pair[1] - 1) * 100)

    def summary(values):
        return {"count": len(values), "median_difference_pct": statistics.median(values) if values else None,
                "within_001_pct": sum(value <= .01 for value in values)}

    ordered = sorted(dates)
    previous_dividend_dates = ({ordered[i - 1] for i in range(1, len(ordered)) if ordered[i] in dividend_dates}
                               if dividend_dates is not None else None)
    return {"primary_start": first, "primary_end": last, "primary_rows": len(primary),
            "reference_start": min(dates), "reference_end": max(dates), "reference_rows": len(dates),
            "overlap_start": min(matched_dates) if matched_dates else None,
            "overlap_end": max(matched_dates) if matched_dates else None,
            "matched_dates": len(matched_dates), "primary_without_reference": len(primary_dates - dates.keys()),
            "reference_without_primary": len(dates.keys() - primary_dates), "duplicate_reference_dates": len(duplicates),
            "truncated": truncated, "close": summary(close_errors), "adjusted_close": summary(adjusted_errors),
            "outliers_before_dividend": sum(item["date"] in previous_dividend_dates for item in outliers) if previous_dividend_dates is not None else None,
            "outliers": sorted(outliers, key=lambda item: (-item["difference_pct"], item["date"]))[:20]}


def yahoo_daily_reference(start, end):
    """Fetch reported closes for comparison and source-declared SPY actions."""
    import yfinance as yf
    try:
        frame = yf.Ticker("SPY").history(start=start, end=end, interval="1d", auto_adjust=False,
                                         actions=True, repair=False, timeout=20, raise_errors=True)
        if frame.empty or "Close" not in frame.columns:
            raise ProviderError("Yahoo supplied no usable daily reference closes")
        prices, actions, seen, duplicates = [], {}, set(), set()
        invalid_action_values = 0
        actions_available = {"Dividends", "Stock Splits"}.issubset(frame.columns)
        for stamp, row in frame.iterrows():
            day = stamp.date().isoformat()
            prices.append((day, row.get("Close"), row.get("Adj Close")))
            if not actions_available:
                continue
            if day in seen:
                actions.pop(day, None)
                duplicates.add(day)
                continue
            seen.add(day)
            records = []
            for column, kind in (("Dividends", "dividend"), ("Stock Splits", "split")):
                raw = row.get(column)
                try:
                    value = float(raw) if not isinstance(raw, bool) else math.nan
                except (TypeError, ValueError, OverflowError):
                    value = math.nan
                if not math.isfinite(value) or value < 0:
                    invalid_action_values += 1
                elif value > 0:
                    records.append({"date": day, "action": kind, "value": value})
            actions[day] = records
        return {"prices": prices, "actions": [record for records in actions.values() for record in records] if actions_available else None,
                "duplicate_action_dates": len(duplicates), "invalid_action_values": invalid_action_values}
    except ProviderError:
        raise
    except Exception:
        raise ProviderError("Yahoo daily reference could not be retrieved for the adjustment audit") from None
