import csv
import io
import json
import math
import os
import re
import tempfile
import time
import uuid
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from threading import RLock
from typing import Literal, get_args
from zoneinfo import ZoneInfo

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from .db import Database, RESEARCH_FUNDS
from .audit import compare_daily_closes, yahoo_daily_reference
from .tradier import ProviderError, Tradier

SYMBOL = re.compile(r"^[A-Z][A-Z0-9.$\-]{0,14}$")
BarInterval = Literal["1d", "1h"]
BAR_INTERVALS = get_args(BarInterval)
BAR_SCHEMA = pa.schema([("time", pa.string()), ("open", pa.float64()), ("high", pa.float64()),
                        ("low", pa.float64()), ("close", pa.float64()), ("volume", pa.int64())])
LOCK = RLock()
MARKET_HISTORY_START = date(2010, 1, 1)
MARKET_HISTORY_SYMBOLS = frozenset(symbol for symbol, _ in RESEARCH_FUNDS)
MARKET_TIMEZONE = ZoneInfo("America/New_York")


def history_bounds(symbol):
    if symbol in MARKET_HISTORY_SYMBOLS:
        return MARKET_HISTORY_START.isoformat(), datetime.now(MARKET_TIMEZONE).date().isoformat()


def history_contains(stamp, bounds):
    return not bounds or bounds[0] <= stamp[:10] <= bounds[1]


@lru_cache(maxsize=32)
def calendar_dates(first_year, last_year):
    import exchange_calendars
    calendar = exchange_calendars.get_calendar("XNYS", start=f"{first_year - 1}-12-01", end=f"{last_year + 1}-01-31")
    return frozenset(str(session.date()) for session in calendar.sessions)


def daily_gaps(rows):
    """Return calendar differences within the observed span, or None if the check cannot run."""
    first, last = rows[0]["time"][:10], rows[-1]["time"][:10]
    start_year, end_year = int(first[:4]), int(last[:4])
    if start_year < 1900 or end_year > 2100 or end_year - start_year > 150:
        return None, None
    try:
        calendar = calendar_dates(start_year, end_year)
        expected = {day for day in calendar if first <= day <= last}
        observed = {row["time"][:10] for row in rows}
        return sorted(expected - observed), sorted(observed - expected)
    except Exception:
        return None, None


def daily_coverage(rows):
    """Compare observed dates with an exchange calendar, without claiming independent certification."""
    missing, unexpected = daily_gaps(rows)
    return (len(missing), len(unexpected)) if missing is not None and unexpected is not None else (None, None)


@lru_cache(maxsize=16)
def nyse_session_hours(first_year, last_year):
    import exchange_calendars
    calendar = exchange_calendars.get_calendar("XNYS", start=f"{first_year - 1}-12-01", end=f"{last_year + 1}-01-31")
    return tuple((index.date().isoformat(), row["open"].to_pydatetime(), row["close"].to_pydatetime())
                 for index, row in calendar.schedule.iterrows())


def intraday_gaps(stamps, interval):
    """Compare Yahoo regular-session bar starts only within the observed span."""
    if interval != "1h" or not stamps:
        return {"checked": False, "reason": "No supported intraday bars are available."}
    if len(stamps) > 100000:
        return {"checked": False, "reason": "The series exceeds the 100,000-bar diagnostic limit."}
    try:
        parsed = []
        for stamp in stamps:
            value = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Timestamp has no timezone")
            parsed.append(value.astimezone(timezone.utc))
        observed = set(parsed)
        first, last = min(observed), max(observed)
        if first.year < 1900 or last.year > 2100 or last.year - first.year > 50:
            return {"checked": False, "reason": "The observed date range exceeds the diagnostic limit."}
        step = timedelta(hours=1)
        expected, by_session = set(), {}
        for day, opening, closing in nyse_session_hours(first.year, last.year):
            if not first.date().isoformat() <= day <= last.date().isoformat():
                continue
            slots = []
            stamp = opening
            while stamp < closing:
                if first <= stamp <= last:
                    expected.add(stamp)
                    slots.append(stamp)
                    if len(expected) > 150000:
                        return {"checked": False, "reason": "The expected grid exceeds the 150,000-bar diagnostic limit."}
                stamp += step
            if slots:
                by_session[day] = slots
        missing, unexpected = sorted(expected - observed), sorted(observed - expected)
        absent, partial = [], []
        for day, slots in by_session.items():
            absent_count = sum(stamp not in observed for stamp in slots)
            if absent_count == len(slots):
                absent.append(day)
            elif absent_count:
                partial.append(day)
        def iso(stamp):
            return stamp.isoformat().replace("+00:00", "Z")
        return {"checked": True, "expected_bars": len(expected), "observed_bars": len(observed),
                "missing_bar_count": len(missing), "unexpected_bar_count": len(unexpected),
                "missing_session_count": len(absent), "partial_session_count": len(partial),
                "duplicate_bar_count": len(parsed) - len(observed),
                "missing_bars": [iso(stamp) for stamp in missing[:500]],
                "unexpected_bars": [iso(stamp) for stamp in unexpected[:500]],
                "missing_sessions": absent[:500], "partial_sessions": partial[:500]}
    except Exception:
        return {"checked": False, "reason": "The stored timestamps could not be compared with the NYSE calendar."}


def safe_json_value(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, list):
        return [safe_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: safe_json_value(item) for key, item in value.items()}
    return value


def valid_symbol(symbol):
    symbol = symbol.upper().strip()
    if not SYMBOL.fullmatch(symbol):
        raise ValueError("Invalid symbol")
    return symbol


def clean_bars(rows, interval, bounds=None):
    clean, seen = [], set()
    outside_range = False
    for row in rows:
        raw = str(row.get("time") or row.get("date") or "")
        try:
            if interval == "1d":
                stamp = date.fromisoformat(raw[:10]).isoformat()
            else:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    raise ValueError("Intraday times need an explicit timezone")
                stamp = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            if not history_contains(stamp, bounds):
                outside_range = True
                continue
            values = [float(row[k]) for k in ("open", "high", "low", "close")]
            volume = int(row["volume"])
        except (ValueError, KeyError, TypeError, OverflowError) as exc:
            raise ValueError("Invalid OHLCV row or timestamp") from exc
        o, h, l, c = values
        if (not all(math.isfinite(v) and v > 0 for v in values) or volume < 0
                or h < max(o, l, c) or l > min(o, h, c) or stamp in seen):
            raise ValueError("Invalid OHLCV bounds, volume, or duplicate timestamp")
        seen.add(stamp)
        clean.append({"time": stamp, "open": o, "high": h, "low": l, "close": c, "volume": volume})
    if not clean:
        if outside_range:
            raise ValueError("No bars fall within January 2010 through today")
        raise ValueError("No valid bars were supplied")
    return sorted(clean, key=lambda r: r["time"])


class MarketStore:
    def __init__(self, db: Database, root: Path):
        self.db, self.root = db, root / "market"
        self.root.mkdir(parents=True, exist_ok=True)
        self.cache = OrderedDict()
        self.quality_cache = OrderedDict()

    def resolve_path(self, stored_path):
        path = (self.root.parent / stored_path).resolve()
        if self.root.resolve() not in path.parents or path.suffix.lower() != ".parquet":
            raise ValueError("Invalid market file reference")
        return path

    def audit_spy_daily(self):
        """Compare bounded closes and retain source-declared actions without changing prices."""
        primary = self.read("SPY", "1d", limit=10000)
        coverage = primary["coverage"]
        if not coverage or not primary["bars"]:
            raise ProviderError("Stored SPY daily bars are required before the adjustment audit")
        end = (date.fromisoformat(primary["bars"][-1]["time"]) + timedelta(days=1)).isoformat()
        reference = yahoo_daily_reference(primary["bars"][0]["time"], end)
        actions = reference["actions"]
        dividends = {item["date"] for item in actions if item["action"] == "dividend"} if actions is not None else None
        result = compare_daily_closes(primary["bars"], reference["prices"], truncated=primary["has_more"], dividend_dates=dividends)
        if not result["matched_dates"]:
            raise ProviderError("The stored series and Yahoo reference have no comparable dates")
        with LOCK, self.db.connect() as conn:
            current = conn.execute("SELECT updated FROM market_files WHERE symbol='SPY' AND interval='1d'").fetchone()
            if not current or current["updated"] != coverage["updated"]:
                raise ProviderError("SPY daily data changed during the audit; it will retry")
            result.update({"actions_available": actions is not None, "reference_dividends": None, "reference_splits": None,
                           "actions_added": 0, "actions_existing": 0, "action_conflicts": 0, "prior_actions_unconfirmed": 0,
                           "duplicate_action_dates": reference["duplicate_action_dates"], "invalid_action_values": reference["invalid_action_values"]})
            if actions is not None:
                source = "Yahoo Finance via yfinance"
                eligible = [item for item in actions if result["primary_start"] <= item["date"] <= result["primary_end"]]
                result["reference_dividends"] = sum(item["action"] == "dividend" for item in eligible)
                result["reference_splits"] = sum(item["action"] == "split" for item in eligible)
                observed = {(item["date"], item["action"]) for item in eligible}
                previous = {(row["ex_date"], row["action"]) for row in conn.execute("""SELECT ex_date,action FROM corporate_actions
                    WHERE symbol='SPY' AND source=? AND ex_date>=? AND ex_date<=?""", (source, result["primary_start"], result["primary_end"]))}
                result["prior_actions_unconfirmed"] = len(previous - observed)
                for item in eligible:
                    existing = conn.execute("SELECT value FROM corporate_actions WHERE symbol='SPY' AND ex_date=? AND action=?",
                                            (item["date"], item["action"])).fetchone()
                    if existing:
                        result["actions_existing"] += 1
                        if not math.isclose(existing["value"], item["value"], rel_tol=1e-9, abs_tol=1e-9):
                            result["action_conflicts"] += 1
                    else:
                        conn.execute("INSERT INTO corporate_actions VALUES(?,?,?,?,?)", ("SPY", item["date"], item["action"], item["value"], source))
                        result["actions_added"] += 1
                issues = (result["action_conflicts"] + result["prior_actions_unconfirmed"] + result["duplicate_action_dates"]
                          + result["invalid_action_values"])
                status = "partial" if issues else "complete"
                message = (f"SPY reference reported {result['reference_dividends']} dividends and {result['reference_splits']} splits; "
                           f"{result['actions_added']} added, {result['action_conflicts']} conflicts, "
                           f"{result['prior_actions_unconfirmed']} prior records not reconfirmed, "
                           f"{result['duplicate_action_dates']} duplicate dates, {result['invalid_action_values']} invalid values")
                conn.execute("INSERT INTO imports(dataset,source,created,status,message) VALUES(?,?,?,?,?)",
                             ("Corporate actions", source, time.time(), status, message))
            conn.execute("INSERT INTO quality_audits(symbol,interval,primary_updated,audited,reference,result) VALUES(?,?,?,?,?,?)",
                         ("SPY", "1d", coverage["updated"], time.time(), "Yahoo Finance via yfinance", json.dumps(result)))
            conn.execute("DELETE FROM quality_audits WHERE symbol='SPY' AND interval='1d' AND id NOT IN "
                         "(SELECT id FROM quality_audits WHERE symbol='SPY' AND interval='1d' ORDER BY id DESC LIMIT 30)")
            self.db.event("database", "adjustment_audited", conn)
        return result

    def enforce_history_window(self):
        """Trim older prices and diagnostics from the active catalog before startup."""
        bounds = history_bounds("SPY")
        symbols = sorted(MARKET_HISTORY_SYMBOLS)
        placeholders = ",".join("?" for _ in symbols)
        with LOCK:
            with self.db.connect() as conn:
                records = list(conn.execute(f"""SELECT * FROM market_files WHERE symbol IN ({placeholders})
                    AND (substr(coverage_start,1,10) < ? OR substr(coverage_end,1,10) > ? OR EXISTS (
                        SELECT 1 FROM market_anomalies AS a WHERE a.symbol=market_files.symbol
                        AND a.interval=market_files.interval AND substr(a.stamp,1,10) GLOB '????-??-??'
                        AND substr(a.stamp,1,10) NOT BETWEEN ? AND ?
                    ))""", (*symbols, *bounds, *bounds)))
            for record in records:
                symbol, interval = record["symbol"], record["interval"]
                old_path = self.resolve_path(record["path"])
                if not old_path.is_file():
                    continue  # Keep missing-file diagnostics visible.
                retained = [row for row in pq.read_table(old_path).to_pylist() if history_contains(row["time"], bounds)]
                if retained:
                    self._publish(symbol, interval, retained, record["source"], record["adjustment"], old_path, bounds=bounds)
                else:
                    with self.db.connect() as conn:
                        conn.execute("DELETE FROM market_files WHERE symbol=? AND interval=?", (symbol, interval))
                        conn.execute("DELETE FROM market_anomalies WHERE symbol=? AND interval=?", (symbol, interval))
                        self.db.event("database", "market_updated", conn, {"symbol": symbol, "interval": interval})
                    self._invalidate(symbol, interval, old_path)
            with self.db.connect() as conn:
                conn.execute(f"""DELETE FROM corporate_actions WHERE symbol IN ({placeholders})
                    AND ex_date NOT BETWEEN ? AND ?""", (*symbols, *bounds))
                conn.execute(f"""DELETE FROM quality_audits WHERE symbol IN ({placeholders})
                    AND CASE WHEN json_valid(result) THEN
                        json_extract(result, '$.primary_start') < ? OR json_extract(result, '$.primary_end') > ?
                    END""",
                    (*symbols, *bounds))

    def write(self, symbol, interval, rows, source, adjustment, replace=False, anomalies=None):
        symbol = valid_symbol(symbol)
        if interval not in BAR_INTERVALS:
            raise ValueError("Unsupported interval")
        if adjustment not in {"unverified", "split_dividend_adjusted"}:
            raise ValueError("Unsupported adjustment declaration")
        bounds = history_bounds(symbol)
        clean = clean_bars(rows, interval, bounds)
        anomalies = [bad for bad in anomalies or [] if not re.match(r"^\d{4}-\d{2}-\d{2}", bad["stamp"])
                     or history_contains(bad["stamp"], bounds)]
        with LOCK:
            with self.db.connect() as conn:
                prior = conn.execute("SELECT * FROM market_files WHERE symbol=? AND interval=?", (symbol, interval)).fetchone()
            if prior and not replace and (prior["source"] != source or prior["adjustment"] != adjustment):
                raise ValueError("Existing series uses a different source or adjustment; replace explicitly")
            old_path = self.resolve_path(prior["path"]) if prior else None
            if prior and old_path and not old_path.exists() and not replace:
                raise ValueError("Existing market file is missing; restore a backup or replace explicitly")
            if prior and old_path and old_path.exists() and not replace:
                old = [row for row in pq.read_table(old_path).to_pylist() if history_contains(row["time"], bounds)]
                merged = {r["time"]: r for r in old}
                merged.update({r["time"]: r for r in clean})
                for bad in anomalies or []:
                    merged.pop(bad["stamp"], None)
                clean = sorted(merged.values(), key=lambda r: r["time"])
            else:
                rejected = {bad["stamp"] for bad in anomalies or []}
                clean = [row for row in clean if row["time"] not in rejected]
            if not clean:
                raise ValueError("No valid bars remain after excluding invalid provider rows")
            return self._publish(symbol, interval, clean, source, adjustment, old_path, replace, anomalies, bounds)

    def _publish(self, symbol, interval, clean, source, adjustment, old_path, replace=False, anomalies=None, bounds=None):
        """Commit a new Parquet generation and catalog entry while holding LOCK."""
        folder = self.root / interval
        folder.mkdir(parents=True, exist_ok=True)
        missing_sessions, unexpected_sessions = daily_coverage(clean) if interval == "1d" else (None, None)
        path = folder / f"{symbol}-{uuid.uuid4().hex}.parquet"
        fd, tmp = tempfile.mkstemp(prefix=".bars-", suffix=".parquet", dir=folder)
        os.close(fd)
        try:
            pq.write_table(pa.Table.from_pylist(clean, schema=BAR_SCHEMA), tmp, compression="zstd")
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        try:
            with self.db.connect() as conn:
                if replace:
                    conn.execute("DELETE FROM market_anomalies WHERE symbol=? AND interval=?", (symbol, interval))
                elif bounds:
                    conn.execute("""DELETE FROM market_anomalies WHERE symbol=? AND interval=?
                        AND substr(stamp,1,10) GLOB '????-??-??' AND substr(stamp,1,10) NOT BETWEEN ? AND ?""",
                        (symbol, interval, *bounds))
                conn.executemany("DELETE FROM market_anomalies WHERE symbol=? AND interval=? AND stamp=?",
                                 [(symbol, interval, row["time"]) for row in clean])
                for bad in anomalies or []:
                    conn.execute("INSERT OR REPLACE INTO market_anomalies VALUES(?,?,?,?,?,?)",
                                 (symbol, interval, bad["stamp"], bad["reason"], json.dumps(bad["raw"], default=str), time.time()))
                anomaly_count = conn.execute("SELECT COUNT(*) FROM market_anomalies WHERE symbol=? AND interval=?", (symbol, interval)).fetchone()[0]
                status = ("invalid bars excluded" if anomaly_count else "gaps detected" if missing_sessions else
                          "non-session dates" if unexpected_sessions else
                          "partial" if bounds and interval == "1d" and clean[0]["time"] > "2010-01-04" else "stored")
                conn.execute("""INSERT INTO market_files(symbol,interval,source,adjustment,coverage_start,coverage_end,row_count,updated,status,path,anomaly_count,missing_sessions,unexpected_sessions)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(symbol,interval) DO UPDATE SET source=excluded.source,adjustment=excluded.adjustment,
                    coverage_start=excluded.coverage_start,coverage_end=excluded.coverage_end,row_count=excluded.row_count,
                    updated=excluded.updated,status=excluded.status,path=excluded.path,anomaly_count=excluded.anomaly_count,
                    missing_sessions=excluded.missing_sessions,unexpected_sessions=excluded.unexpected_sessions""",
                    (symbol, interval, source[:120], adjustment, clean[0]["time"], clean[-1]["time"],
                     len(clean), time.time(), status, str(path.relative_to(self.root.parent)), anomaly_count, missing_sessions, unexpected_sessions))
                conn.execute("INSERT INTO imports(dataset,source,created,status,message) VALUES(?,?,?,?,?)",
                             (f"{symbol} {interval}", source[:120], time.time(), "partial" if anomaly_count else "complete",
                              f"{len(clean)} bars stored; {anomaly_count} invalid bars excluded; {missing_sessions if missing_sessions is not None else 'unchecked'} missing sessions"))
                if symbol == "SPY" and interval == "1d":
                    due = time.time() + 5
                    conn.execute("""UPDATE jobs SET next_run=? WHERE name='adjustments' AND enabled=1
                                 AND status!='running' AND next_run>?""", (due, due))
                self.db.event("database", "market_updated", conn, {"symbol": symbol, "interval": interval})
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        self._invalidate(symbol, interval, old_path)
        return {"symbol": symbol, "interval": interval, "count": len(clean), "start": clean[0]["time"],
                "end": clean[-1]["time"], "adjustment": adjustment, "status": status, "anomaly_count": anomaly_count,
                "missing_sessions": missing_sessions, "unexpected_sessions": unexpected_sessions}

    def _invalidate(self, symbol, interval, old_path):
        for key in list(self.cache):
            if key[0] == symbol and key[1] == interval:
                del self.cache[key]
        for key in list(self.quality_cache):
            if key[0] == symbol and key[1] == interval:
                del self.quality_cache[key]
        if old_path:
            try:
                old_path.unlink(missing_ok=True)
            except OSError:
                pass  # A stale generation may remain; it is not referenced by the catalog.

    def read(self, symbol, interval, start=None, end=None, limit=12000, before=None):
        symbol = valid_symbol(symbol)
        if interval not in BAR_INTERVALS:
            raise ValueError("Unsupported interval")
        bounds = history_bounds(symbol)
        if bounds:
            start = max(start or "", bounds[0])
            end = min(end or bounds[1], bounds[1] if interval == "1d" else bounds[1] + "~")
        with LOCK:
            with self.db.connect() as conn:
                record = conn.execute("SELECT * FROM market_files WHERE symbol=? AND interval=?", (symbol, interval)).fetchone()
            if not record:
                return {"bars": [], "coverage": None, "has_more": False, "next_before": None}
            coverage = {k: record[k] for k in ("source", "adjustment", "coverage_start", "coverage_end", "row_count", "updated", "status", "anomaly_count", "missing_sessions", "unexpected_sessions")}
            path = self.resolve_path(record["path"])
            if not path.is_file():
                return {"bars": [], "coverage": {**coverage, "status": "file missing"}, "has_more": False, "next_before": None}
            cache_key = (symbol, interval, start, end, limit, before, record["updated"])
            if cache_key in self.cache:
                self.cache.move_to_end(cache_key)
                return self.cache[cache_key]
            con = duckdb.connect(":memory:")
            try:
                end_bound = end + "~" if end and interval != "1d" and len(end) == 10 else (end or "~")
                count = min(max(limit, 1), 30000)
                params = [str(path), start or "", end_bound, before or "~", count + 1]
                rows = con.execute("SELECT * FROM read_parquet(?) WHERE time>=? AND time<=? AND time<? ORDER BY time DESC LIMIT ?", params).fetchall()
            finally:
                con.close()
            has_more = len(rows) > count
            bars = [dict(zip(["time", "open", "high", "low", "close", "volume"], row)) for row in reversed(rows[:count])]
            result = {"bars": bars, "coverage": coverage, "has_more": has_more,
                      "next_before": bars[0]["time"] if has_more and bars else None}
            self.cache[cache_key] = result
            while len(self.cache) > 16 or sum(len(item["bars"]) for item in self.cache.values()) > 80000:
                self.cache.popitem(last=False)
            return result

    def daily_endpoint_closes(self, symbols, start, end, adjustment="split_dividend_adjusted"):
        """Read exact daily endpoints in one Parquet query under the generation lock."""
        if not symbols:
            return {}
        prices = {}
        bounds = history_bounds("SPY")
        with LOCK:
            with self.db.connect() as conn:
                files = [dict(r) for r in conn.execute(
                    "SELECT symbol,path FROM market_files WHERE interval='1d' AND (? IS NULL OR adjustment=?)",
                    (adjustment, adjustment)
                ) if r["symbol"] in symbols]
            mapping = {}
            for record in files:
                path = self.resolve_path(record["path"])
                if path.is_file():
                    mapping[os.path.normcase(str(path))] = record["symbol"]
            if mapping:
                con = duckdb.connect(":memory:")
                try:
                    rows = con.execute("SELECT filename,time,close FROM read_parquet(?, filename=true) WHERE time IN (?,?)",
                                       [list(mapping), start, end]).fetchall()
                finally:
                    con.close()
                symbols_by_file = {}
                for filename, stamp, close in rows:
                    if filename not in symbols_by_file:
                        symbols_by_file[filename] = mapping[os.path.normcase(str(Path(filename).resolve()))]
                    symbol = symbols_by_file[filename]
                    if symbol not in MARKET_HISTORY_SYMBOLS or history_contains(stamp, bounds):
                        prices.setdefault(symbol, {})[stamp] = close
        return prices

    def quality(self, symbol, interval, limit=200):
        symbol = valid_symbol(symbol)
        if interval not in BAR_INTERVALS:
            raise ValueError("Unsupported interval")
        limit = min(max(int(limit), 1), 500)
        daily_rows, intraday, pending_stamps, cache_key = None, None, None, None
        with LOCK:
            with self.db.connect() as conn:
                record = conn.execute("SELECT * FROM market_files WHERE symbol=? AND interval=?", (symbol, interval)).fetchone()
                anomalies = [dict(row) for row in conn.execute("""SELECT stamp,reason,raw,detected FROM market_anomalies
                                  WHERE symbol=? AND interval=? ORDER BY stamp LIMIT ?""", (symbol, interval, limit))]
            if not record:
                return {"symbol": symbol, "interval": interval, "coverage": None, "missing_dates": [],
                        "unexpected_dates": [], "anomalies": [], "message": "No series is stored."}
            coverage = {k: record[k] for k in ("source", "adjustment", "coverage_start", "coverage_end", "row_count", "updated", "status", "anomaly_count", "missing_sessions", "unexpected_sessions")}
            path = self.resolve_path(record["path"])
            if not path.is_file():
                return {"symbol": symbol, "interval": interval, "coverage": {**coverage, "status": "file missing"},
                        "missing_dates": [], "unexpected_dates": [], "anomalies": [], "message": "The catalogued market file is missing."}
            if interval == "1d":
                daily_rows = pq.read_table(path, columns=["time"]).to_pylist()
            elif record["source"] != "Yahoo Finance via yfinance":
                intraday = {"checked": False, "reason": "The source's session and timestamp conventions are not known; no bar grid was assumed."}
            elif record["row_count"] > 100000 or pq.ParquetFile(path).metadata.num_rows > 100000:
                intraday = {"checked": False, "reason": "The series exceeds the 100,000-bar diagnostic limit."}
            else:
                cache_key = (symbol, interval, record["updated"])
                intraday = self.quality_cache.get(cache_key)
                if intraday is not None:
                    self.quality_cache.move_to_end(cache_key)
                else:
                    pending_stamps = pq.read_table(path, columns=["time"]).column("time").to_pylist()
        missing, unexpected = daily_gaps(daily_rows) if daily_rows else (None, None)
        if pending_stamps is not None:
            intraday = intraday_gaps(pending_stamps, interval)
            with LOCK, self.db.connect() as conn:
                current = conn.execute("SELECT updated FROM market_files WHERE symbol=? AND interval=?", (symbol, interval)).fetchone()
                if current and current["updated"] == coverage["updated"]:
                    self.quality_cache[cache_key] = intraday
                    while len(self.quality_cache) > 16:
                        self.quality_cache.popitem(last=False)
                else:
                    intraday = {**intraday, "stale": True}
        if intraday:
            intraday = {**intraday, **{name: intraday[name][:limit] for name in
                ("missing_bars", "unexpected_bars", "missing_sessions", "partial_sessions") if name in intraday}}
        for row in anomalies:
            row["raw"] = safe_json_value(json.loads(row["raw"]))
        return {"symbol": symbol, "interval": interval, "coverage": coverage,
                "missing_dates": missing[:limit] if missing is not None else None,
                "unexpected_dates": unexpected[:limit] if unexpected is not None else None,
                "intraday": intraday, "anomalies": anomalies, "limit": limit,
                "message": ("NYSE session comparison covers only the observed span; adjustments require independent verification."
                            if interval == "1d" else
                            "NYSE regular-session bar-start comparison covers the observed span only; adjustments remain unverified."
                            if intraday and intraday.get("checked") else "Intraday continuity has not been checked; adjustments remain unverified.")}

    def import_csv(self, symbol, interval, content, source, adjustment, replace):
        if len(content) > 20 * 1024 * 1024:
            raise ValueError("CSV exceeds the 20 MB import limit")
        rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
        return self.write(symbol, interval, rows, source, adjustment, replace)

    def refresh_daily(self, adapter: Tradier, symbol, earliest=None):
        symbol = valid_symbol(symbol)
        with self.db.connect() as conn:
            prior = conn.execute("SELECT * FROM market_files WHERE symbol=? AND interval='1d'", (symbol,)).fetchone()
        if prior and (prior["source"] != "Tradier" or prior["adjustment"] != "unverified"):
            return None
        bounds = history_bounds(symbol)
        end_today = datetime.now(MARKET_TIMEZONE).date().isoformat()
        if bounds is None and earliest is not None:
            bounds = (earliest, end_today)
        first, end = bounds or ("2000-01-01", end_today)
        start = first
        if prior and prior["coverage_end"]:
            overlap = (date.fromisoformat(prior["coverage_end"][:10]) - timedelta(days=10)).isoformat()
            start = min(end, max(first, overlap))
        rows = adapter.daily(symbol, start, end)
        if not rows:
            raise ProviderError(f"No daily bars returned for {symbol}")
        valid, anomalies, seen = [], [], set()
        for row in rows:
            stamp = str(row.get("date") or "unknown")
            if re.match(r"^\d{4}-\d{2}-\d{2}", stamp) and not history_contains(stamp, bounds):
                continue
            try:
                cleaned = clean_bars([row], "1d")[0]
                if cleaned["time"] in seen:
                    raise ValueError("Duplicate timestamp")
                seen.add(cleaned["time"])
                valid.append(cleaned)
            except ValueError as exc:
                anomalies.append({"stamp": stamp, "reason": str(exc), "raw": row})
        return self.write(symbol, "1d", valid, "Tradier", "unverified", anomalies=anomalies)

    def refresh_yahoo(self, symbol, interval):
        import yfinance as yf
        symbol = valid_symbol(symbol)
        if interval != "1h":
            raise ValueError("Unsupported Yahoo interval")
        with self.db.connect() as conn:
            prior = conn.execute("SELECT * FROM market_files WHERE symbol=? AND interval=?", (symbol, interval)).fetchone()
        if prior and (prior["source"] != "Yahoo Finance via yfinance" or prior["adjustment"] != "unverified"):
            return None
        # Yahoo's lookback is limited. Persist accumulated bars; do not fill earlier gaps.
        max_days = 729
        today = datetime.now(MARKET_TIMEZONE).date()
        if symbol in MARKET_HISTORY_SYMBOLS:
            max_days = min(max_days, (today - MARKET_HISTORY_START).days)
            if max_days < 1:
                raise ProviderError("No hourly lookback is available within the retained market history")
        days = max_days
        if prior and prior["coverage_end"]:
            latest = date.fromisoformat(prior["coverage_end"][:10])
            days = min(max_days, max(5, (today - latest).days + 5))
        frame = yf.Ticker(symbol).history(period=f"{days}d", interval=interval,
                                          auto_adjust=False, actions=False, prepost=False, timeout=20, raise_errors=True)
        if frame.empty:
            raise ProviderError(f"Yahoo returned no {interval} bars for {symbol}")
        rows = []
        for stamp, bar in frame.iterrows():
            rows.append({"time": stamp.isoformat(), "open": bar["Open"], "high": bar["High"],
                         "low": bar["Low"], "close": bar["Close"], "volume": bar["Volume"]})
        return self.write(symbol, interval, rows, "Yahoo Finance via yfinance", "unverified")
