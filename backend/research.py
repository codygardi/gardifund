"""Dated benchmark holdings, company watchlists, and on-demand daily bars."""
from collections import defaultdict
from datetime import date, datetime
from html import unescape
from math import fsum, isfinite
import re
import time

import httpx

from .db import BENCHMARKS, RESEARCH_FUNDS, SECTORS
from .market import MARKET_TIMEZONE, valid_symbol
from .services import workbook
from .tradier import ProviderError

BENCHMARK_SOURCES = {"DIA": "State Street DIA holdings", "QQQ": "Invesco QQQ holdings"}
WATCHLISTS = BENCHMARKS[1:] + SECTORS
COMPANY_HISTORY_JOB = "research_daily:"
TOP_COMPANIES = 10
QQQ_HOLDINGS_URL = "https://dng-api.invesco.com/cache/v1/accounts/en_US/shareclasses/QQQ/holdings/fund"
EQUITY_TYPES = {"Common Stock", "American Depository Receipt", "American Depository Receipt - NY"}


def refresh_benchmark_holdings(db, benchmark):
    if benchmark == "DIA":
        as_of, rows = workbook("DIA")
    elif benchmark == "QQQ":
        try:
            response = httpx.get(QQQ_HOLDINGS_URL, params={"idType": "ticker", "productType": "ETF"}, timeout=20)
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                raise ValueError("Holdings response exceeds the size limit")
            data = response.json()
            if data.get("cusip") not in {"QQQ", "46090E103"}:
                raise ValueError("Unexpected fund identifier")
            as_of = data["effectiveDate"]
            rows = [{"symbol": row["ticker"], "name": unescape(row["issuerName"]),
                     "weight": float(row["percentageOfTotalNetAssets"]) / 100}
                    for row in data["holdings"] if row.get("securityTypeName") in EQUITY_TYPES and row.get("ticker")]
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
            raise ProviderError("Could not load or validate QQQ holdings from Invesco.") from None
    else:
        raise ValueError("Choose DIA or QQQ.")
    return store_benchmark_holdings(db, benchmark, as_of, rows, BENCHMARK_SOURCES[benchmark], automatic=True)


def store_benchmark_holdings(db, benchmark, as_of, rows, source, automatic=False):
    as_of = date.fromisoformat(as_of).isoformat()
    today = datetime.now(MARKET_TIMEZONE).date().isoformat()
    if benchmark not in BENCHMARK_SOURCES or as_of > today or not 1 <= len(rows) <= 500:
        raise ValueError("Provide a dated DIA or QQQ snapshot with up to 500 holdings.")
    source = source[:120]
    holdings = {}
    for row in rows:
        symbol = valid_symbol(row["symbol"])
        name = str(row.get("name") or symbol).strip()[:160] or symbol
        weight = float(row["weight"])
        if symbol in holdings or not isfinite(weight) or not 0 < weight <= 1:
            raise ValueError("Holdings must have unique tickers and valid decimal weights.")
        holdings[symbol] = (name, weight)
    total = fsum(weight for _, weight in holdings.values())
    if total > 1.01:
        raise ValueError("Holdings weights exceed 100%.")
    if automatic and (total < 0.95 or len(holdings) < (30 if benchmark == "DIA" else 80)):
        raise ProviderError(f"{benchmark} provider returned an incomplete equity snapshot.")
    now = time.time()
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute("SELECT * FROM benchmark_snapshots WHERE benchmark=? AND as_of=?", (benchmark, as_of)).fetchone()
        if automatic and previous and previous["source"] != source:
            return {"as_of": as_of, "count": previous["row_count"], "weight": previous["weight_sum"]}
        conn.execute("INSERT OR REPLACE INTO benchmark_snapshots VALUES(?,?,?,?,?,?)",
                     (benchmark, as_of, source, now, len(holdings), total))
        conn.executemany("INSERT INTO benchmark_holdings VALUES(?,?,?,?,?)",
                         [(benchmark, as_of, symbol, name, weight) for symbol, (name, weight) in holdings.items()])
        conn.executemany("INSERT OR IGNORE INTO instruments VALUES(?,?,?,?,?,?)",
                         [(symbol, name, "stock", None, source, now) for symbol, (name, _) in holdings.items()])
        conn.execute("INSERT INTO imports(dataset,source,created,status,message) VALUES(?,?,?,?,?)",
                     (f"{benchmark} constituents", source, now, "complete" if total >= 0.95 else "partial",
                      f"{len(holdings)} holdings dated {as_of}; weight {total:.2%}"))
        db.event("database", "benchmark_holdings_updated", conn)
    return {"as_of": as_of, "count": len(holdings), "weight": total}


def _company_name(name):
    return re.sub(r"\s+(?:class|cl\.?)\s+[A-Z0-9-]+$", "", name.strip(), flags=re.IGNORECASE)


def _top_companies(rows):
    issuers = defaultdict(list)
    for row in rows:
        issuer = re.sub(r"[^\w]", "", _company_name(row["name"]).casefold()) or row["symbol"]
        issuers[issuer].append(row)
    companies = []
    for classes in issuers.values():
        classes.sort(key=lambda row: (-row["weight"], row["symbol"]))
        main = classes[0]
        companies.append({"symbol": main["symbol"], "name": _company_name(main["name"]),
                          "weight": fsum(row["weight"] for row in classes),
                          "tickers": [row["symbol"] for row in classes]})
    return sorted(companies, key=lambda row: (-row["weight"], row["symbol"]))[:TOP_COMPANIES]


def _watchlists(conn):
    spy = conn.execute("SELECT * FROM holdings_snapshots ORDER BY as_of DESC LIMIT 1").fetchone()
    classified = defaultdict(list)
    if spy:
        for row in conn.execute("SELECT symbol,name,weight,sector FROM holdings WHERE as_of=?", (spy["as_of"],)):
            classified[row["sector"]].append(row)
    groups = []
    for symbol, name in WATCHLISTS:
        if symbol in BENCHMARK_SOURCES:
            snapshot = conn.execute("SELECT * FROM benchmark_snapshots WHERE benchmark=? ORDER BY as_of DESC LIMIT 1",
                                    (symbol,)).fetchone()
            members = list(conn.execute("SELECT symbol,name,weight FROM benchmark_holdings WHERE benchmark=? AND as_of=?",
                                        (symbol, snapshot["as_of"]))) if snapshot else []
            unclassified = None
        else:
            snapshot, members = spy, classified[name]
            unclassified = len(classified[None])
        groups.append({"symbol": symbol, "name": name, "as_of": snapshot["as_of"] if snapshot else None,
                       "source": snapshot["source"] if snapshot else BENCHMARK_SOURCES.get(symbol, "Dated SPY holdings"),
                       "updated": snapshot["imported"] if snapshot else None, "holdings": len(members),
                       "unclassified": unclassified, "weight_sum": snapshot["weight_sum"] if snapshot else None,
                       "companies": _top_companies(members)})
    return groups


def research_catalog(db):
    with db.connect() as conn:
        conn.execute("BEGIN")
        groups = _watchlists(conn)
        coverage = {row["symbol"]: dict(row) for row in conn.execute("""SELECT symbol,source,adjustment,coverage_start,
            coverage_end,row_count,updated,status,missing_sessions FROM market_files WHERE interval='1d'""")}
        jobs = {row["name"].removeprefix(COMPANY_HISTORY_JOB): dict(row) for row in conn.execute(
            "SELECT name,status,enabled,message FROM jobs WHERE name LIKE 'research_daily:%'")}
    symbols = set()
    for group in groups:
        for company in group["companies"]:
            symbol = company["symbol"]
            symbols.add(symbol)
            job = jobs.get(symbol)
            company["coverage"] = coverage.get(symbol)
            status = "stored" if company["coverage"] else "missing"
            if job and job["enabled"]:
                status = "running" if job["status"] == "running" else "queued"
            elif job and job["status"] == "error":
                status = "error"
            company["history_status"] = status
            company["history_message"] = job["message"] if job and job["status"] == "error" else None
    return {"funds": [{"symbol": symbol, "name": name, "coverage": coverage.get(symbol)} for symbol, name in RESEARCH_FUNDS],
            "groups": groups, "unique_tickers": len(symbols), "tickers_with_history": sum(symbol in coverage for symbol in symbols)}


def queue_company_history(db, group):
    group = valid_symbol(group)
    now = time.time()
    queued, pending = 0, 0
    with db.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        watchlist = next((row for row in _watchlists(conn) if row["symbol"] == group), None)
        if not watchlist or not watchlist["companies"]:
            raise ValueError("Choose a watchlist with dated company holdings.")
        scheduled = conn.execute("SELECT MAX(next_run) FROM jobs WHERE enabled=1 AND name LIKE 'research_daily:%'").fetchone()[0]
        slot = max(now, scheduled + 3) if scheduled is not None else now
        for company in watchlist["companies"]:
            job = COMPANY_HISTORY_JOB + company["symbol"]
            existing = conn.execute("SELECT enabled FROM jobs WHERE name=?", (job,)).fetchone()
            if existing and existing["enabled"]:
                pending += 1
                continue
            conn.execute("""INSERT INTO jobs(name,interval_seconds,enabled,next_run,status,message)
                VALUES(?,86400,1,?,'idle','Queued for daily history')
                ON CONFLICT(name) DO UPDATE SET enabled=1,next_run=excluded.next_run,status='idle',message=excluded.message""",
                (job, slot))
            queued += 1
            slot += 3
        if queued:
            db.event("database", "company_history_queued", conn)
    return {"queued": queued, "pending": pending}
