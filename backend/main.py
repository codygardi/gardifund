import asyncio
import csv
import io
import json
import math
import sqlite3
import tempfile
import time
import zipfile
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from . import shadow
from .config import ROOT, Settings
from .db import Database, SECTORS
from .discovery import MarketDiscovery
from .market import LOCK, MarketStore, valid_symbol
from .security import AccessGuard
from .setup_lock import LiveSetupLock
from .services import SECTOR_NAMES, attribution, discovery_rows, enrich_positions, store_holdings
from .tradier import Tradier, ProviderError
from .worker import Worker
from .execution import regular_session
from .annual_stocks import AnnualStocks


class SettingsUpdate(BaseModel):
    discovery_minutes: int | None = Field(default=None, ge=5, le=1440)
    account_minutes: int | None = Field(default=None, ge=5, le=1440)
    top_n: int | None = Field(default=None, ge=1, le=10)
    appearance: Literal["dark", "light"] | None = None
    market_session: Literal["regular"] | None = None


class SetupUnlock(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class SectorHistoryRequest(BaseModel):
    interval: Literal["1h", "30m"]
    symbols: list[str] = Field(min_length=1, max_length=len(SECTORS))


class ShadowOrder(BaseModel):
    model_config = {'extra': 'forbid'}
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0, le=1_000_000, strict=True)
    category: Literal['sector', 'etf', 'stock']
    request_id: UUID
    order_type: Literal['market', 'limit'] = 'market'
    limit_price: float | None = Field(default=None, gt=0, le=1_000_000_000, allow_inf_nan=False)


class ShadowReset(BaseModel):
    confirmation: Literal['RESET SHADOW']
    request_id: UUID


def without_account_identifiers(value):
    if isinstance(value, list):
        return [without_account_identifiers(item) for item in value]
    if isinstance(value, dict):
        return {key: without_account_identifiers(item) for key, item in value.items()
                if key.lower() not in {"account_id", "account_number"}}
    return value


def create_app(settings: Settings | None = None, adapter=None):
    settings = settings or Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.db_path)
    market = MarketStore(db, settings.data_dir)
    adapter = adapter or Tradier(settings.production_api_key)
    discovery_store = MarketDiscovery(db, adapter)
    worker = Worker(db, adapter, market, discovery_store)
    annual_stocks = AnnualStocks(db, market)
    setup_lock = LiveSetupLock(settings.setups_password_hash)

    @asynccontextmanager
    async def lifespan(_app):
        if settings.start_worker:
            worker.start()
        yield
        if settings.start_worker:
            worker.stop()
        if hasattr(adapter, "close"):
            adapter.close()

    app = FastAPI(title="GARDiFUND", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    access_guard = AccessGuard(settings)
    app.middleware("http")(access_guard)
    app.state.access_guard = access_guard
    app.state.db, app.state.market, app.state.worker, app.state.discovery, app.state.setup_lock = db, market, worker, discovery_store, setup_lock

    @app.exception_handler(ProviderError)
    async def provider_unavailable(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=503)

    @app.exception_handler(ValueError)
    async def handle_value_error(request, exc):
        if request.url.path.startswith("/api/import/"):
            with db.connect() as conn:
                conn.execute("INSERT INTO imports(dataset,source,created,status,message) VALUES(?,?,?,?,?)",
                             (request.url.path.rsplit("/", 1)[-1], "User import", time.time(), "failed", str(exc)[:300]))
                db.event("database", "import_failed", conn)
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.get("/healthz")
    def health():
        return {"status": "ok"}

    @app.get("/scope", include_in_schema=False)
    def scope(request: Request):
        return {"access_scope": request.state.access_scope}

    @app.get("/api/state")
    def state(request: Request):
        with db.connect() as conn:
            jobs = [dict(r) for r in conn.execute("SELECT * FROM jobs WHERE name NOT LIKE 'sector_intraday:%' AND name NOT LIKE 'sector_reference:%' AND name!='universe_otc' ORDER BY name")]
            sector_jobs = [dict(r) for r in conn.execute("SELECT name,status,message,last_finished,next_run FROM jobs WHERE name LIKE 'sector_intraday:%'")]
            latest = conn.execute("SELECT * FROM holdings_snapshots ORDER BY as_of DESC LIMIT 1").fetchone()
            last_id = conn.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
            session = conn.execute('SELECT state,observed FROM market_session WHERE id=1').fetchone()
        session = dict(session) if session else {'state':'unknown', 'observed':None}
        now = worker.execution.clock()
        session['regular_open'] = session['state'] == 'open' and session['observed'] is not None and -5 <= now - session['observed'] < 90 and regular_session(now)
        scan = discovery_store.snapshot()
        scan_status = {key: value for key, value in scan.items() if key != "lists"}
        scan_status["progress"] = discovery_store.progress()
        return {"access_scope": request.state.access_scope, "settings": db.settings(), "jobs": jobs, 'session': session, "sectors": [{"symbol": s, "name": n} for s, n in SECTORS],
                "holdings": dict(latest) if latest else None, "event_id": last_id,
                "discovery": scan_status,
                "sector_history": {"total": len(sector_jobs), "complete": sum(j["status"] == "complete" for j in sector_jobs),
                                   "running": sum(j["status"] == "running" for j in sector_jobs),
                                   "errors": sum(j["status"] == "error" for j in sector_jobs),
                                   "failures": [{"series": j["name"].removeprefix("sector_intraday:"), "message": j["message"],
                                                 "next_run": j["next_run"]} for j in sector_jobs if j["status"] == "error"]},
                "connection": {"tradier_configured": bool(settings.production_api_key), "auth_mode": settings.auth_mode,
                               "access_configured": access_guard.jwks is not None,
                               "private_view_ip": settings.private_view_ip if settings.auth_mode == "cloudflare" else "",
                               "live_orders_enabled": False}}

    @app.get("/api/events")
    async def events(request: Request):
        try:
            raw_cursor = request.headers.get("last-event-id") or request.query_params.get("after")
            if raw_cursor is None:
                with db.connect() as conn:
                    cursor = conn.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
            else:
                cursor = max(0, int(raw_cursor))
        except ValueError:
            cursor = 0

        async def stream():
            nonlocal cursor
            heartbeat = time.monotonic()
            expires = heartbeat + 60
            access_expiry = request.state.access_expires if request.state.access_scope == "remote" else None

            def remaining():
                duration = expires - time.monotonic()
                return min(duration, access_expiry - time.time()) if access_expiry is not None else duration

            if remaining() <= 0:
                return
            yield ": connected\n\n"
            while remaining() > 0 and not await request.is_disconnected():
                with db.connect() as conn:
                    rows = conn.execute("SELECT * FROM events WHERE id>? ORDER BY id LIMIT 100", (cursor,)).fetchall()
                for row in rows:
                    if remaining() <= 0:
                        return
                    cursor = row["id"]
                    event = dict(row)
                    if event["payload"]:
                        event["payload"] = json.loads(event["payload"])
                    if request.state.access_scope == "remote":
                        payload = event.get("payload") or {}
                        if (event["module"] == "charts" and event["action"] == "market_updated"
                                and payload.get("symbol") in {"SPY", *(s for s, _ in SECTORS)}
                                and payload.get("interval") in {"1d", "1h", "30m"}):
                            event["payload"] = {"symbol": payload["symbol"], "interval": payload["interval"]}
                        else:
                            module = ('overview' if event['module'] == 'overview' or event['module'] == 'investing' and event['action'].startswith('shadow_') else
                                      'charts' if event['module'] == 'database' or event['action'] == 'discovery_completed' else 'status')
                            event = {"id": cursor, "module": module, "action": "updated", "created": event["created"], "payload": None}
                    yield f"id: {cursor}\ndata: {json.dumps(event)}\n\n"
                if remaining() > 0 and time.monotonic() - heartbeat > 12:
                    yield ": heartbeat\n\n"
                    heartbeat = time.monotonic()
                await asyncio.sleep(max(0, min(2, remaining())))
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

    @app.get("/api/overview")
    def overview(mode: Literal["live", "shadow"] = "live"):
        if mode == "shadow":
            return shadow.snapshot(db, worker.execution.clock())
        with db.connect() as conn:
            accounts = []
            for row in conn.execute("SELECT * FROM live_accounts ORDER BY account_id"):
                account = without_account_identifiers(json.loads(row["payload"]))
                account["masked_id"] = "••••" + row["account_id"][-4:]
                account["synced"] = row["synced"]
                accounts.append(account)
        for account in accounts:
            account["positions"] = enrich_positions(db, account.get("positions", []))
        with db.connect() as conn:
            performance = [dict(r) for r in conn.execute('SELECT * FROM live_daily_performance ORDER BY day DESC LIMIT 3650')]
        return {"accounts": accounts, 'daily_performance': list(reversed(performance))}

    @app.post("/api/shadow/orders")
    def shadow_order(body: ShadowOrder):
        return worker.execution.submit(body.symbol, body.side, body.quantity, body.category, str(body.request_id),
                                       order_type=body.order_type, limit_price=body.limit_price)

    @app.post('/api/shadow/reset')
    def shadow_reset(body: ShadowReset):
        return worker.execution.reset(str(body.request_id))

    @app.post('/api/shadow/pending/{request_id}/cancel')
    def cancel_shadow_entry(request_id: UUID):
        return worker.execution.cancel(str(request_id))

    @app.get("/api/discovery")
    def market_discovery():
        # This is a single in-memory scan, replaced atomically after a successful run.
        return discovery_store.snapshot()

    @app.get("/api/setup-access")
    def setup_access(request: Request):
        remaining = setup_lock.remaining(request)
        return {"unlocked": remaining > 0, "expires_in": math.ceil(remaining), "configured": bool(setup_lock.password_hash)}

    @app.post("/api/setup-access/unlock")
    def unlock_setups(body: SetupUnlock, request: Request, response: Response):
        if request.state.access_scope != "local":
            raise HTTPException(status_code=403, detail="Live setups are available locally only.")
        token = setup_lock.unlock(body.password)
        response.set_cookie(setup_lock.cookie_name, token, max_age=setup_lock.lifetime, httponly=True,
                            samesite="strict", path="/api")
        return {"unlocked": True, "expires_in": setup_lock.lifetime}

    @app.post("/api/setup-access/lock")
    def lock_setups(request: Request, response: Response):
        setup_lock.revoke(request)
        response.delete_cookie(setup_lock.cookie_name, path="/api", httponly=True, samesite="strict")
        return {"unlocked": False}

    @app.get('/api/setups/live')
    def live_screening(request: Request):
        setup_lock.require(request)
        return discovery_store.snapshot()

    @app.get("/api/bars")
    def bars(symbol: str = "SPY", interval: Literal["1d", "1h", "30m"] = "1d",
             start: str | None = None, end: str | None = None, limit: int = Query(12000, ge=1, le=30000),
             before: str | None = None):
        return market.read(symbol, interval, start, end, limit, before)

    @app.get("/api/market/quality")
    def market_quality(symbol: str, interval: Literal["1d", "1h", "30m"] = "1d", limit: int = Query(200, ge=1, le=500)):
        return market.quality(symbol, interval, limit)

    @app.post("/api/market/sector-history")
    def request_sector_history(body: SectorHistoryRequest):
        allowed = {symbol for symbol, _ in SECTORS}
        symbols = list(dict.fromkeys(valid_symbol(symbol) for symbol in body.symbols))
        if any(symbol not in allowed for symbol in symbols):
            raise ValueError("Only the 11 sector ETFs can be queued here")
        now, queued, running, retrying = time.time(), [], [], []
        with db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for symbol in symbols:
                name = f"sector_intraday:{symbol}:{body.interval}"
                job = conn.execute("SELECT status,last_finished,next_run FROM jobs WHERE name=?", (name,)).fetchone()
                if not job:
                    conn.execute("INSERT INTO jobs(name,interval_seconds,next_run) VALUES(?,?,?)", (name, 86400, now))
                    queued.append(symbol)
                    continue
                if job["status"] == "running":
                    running.append(symbol)
                elif job["status"] == "error" and job["last_finished"] and job["last_finished"] > now - 300:
                    retrying.append(symbol)
                elif job["status"] == "idle" and job["next_run"] <= now + 1:
                    queued.append(symbol)
                else:
                    conn.execute("UPDATE jobs SET enabled=1,next_run=?,status='idle',message='Queued from Charts' WHERE name=?", (now, name))
                    queued.append(symbol)
            if queued:
                db.event("settings", "sector_history_queued", conn)
        return {"queued": queued, "running": running, "retrying": retrying}

    @app.get("/api/sectors")
    def sectors(q: str = "", top_n: int = Query(10, ge=1, le=10)):
        universe = discovery_rows(db)["rows"]
        by_symbol = {r["symbol"]: r for r in universe}
        output = []
        for symbol, name in SECTORS:
            if q and q.lower() not in (symbol + " " + name).lower():
                continue
            stocks = [r for r in universe if r["kind"] == "stock" and r["sector"] == name and r["change_pct"] is not None]
            stocks.sort(key=lambda r: r["change_pct"], reverse=True)
            output.append({"symbol": symbol, "name": name, "quote": by_symbol.get(symbol), "top": stocks[:top_n], "classified_count": len(stocks)})
        return {"rows": output, "note": "Sector labels inferred from same-date sector ETF membership. Top stocks ranked by latest cached daily quote change."}

    @app.get('/api/top-stocks')
    @app.get('/view/top-stocks', include_in_schema=False)
    def top_stocks(year:int = Query(ge=2000, le=2100), top_n:int = Query(10, ge=1, le=10)):
        return annual_stocks.get(year, top_n)

    @app.get("/api/attribution")
    def attribution_api(start: str, end: str, sector: str = "", limit: int = Query(10, ge=1, le=50)):
        if sector and sector not in SECTOR_NAMES:
            raise ValueError("Unknown sector")
        return attribution(db, market, start, end, sector or None, limit)

    @app.get("/api/database")
    def database():
        with db.connect() as conn:
            files = [dict(r) for r in conn.execute("SELECT symbol,interval,source,adjustment,coverage_start,coverage_end,row_count,updated,status,anomaly_count,missing_sessions,unexpected_sessions FROM market_files ORDER BY symbol,interval")]
            audit_row = conn.execute("""SELECT audited,primary_updated,reference,result FROM quality_audits
                                     WHERE symbol='SPY' AND interval='1d' ORDER BY id DESC LIMIT 1""").fetchone()
            snapshots = [dict(r) for r in conn.execute("SELECT * FROM holdings_snapshots ORDER BY as_of DESC LIMIT 100")]
            imports = [dict(r) for r in conn.execute("SELECT * FROM imports ORDER BY id DESC LIMIT 100")]
            counts = {"instruments": conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0],
                      "holdings_snapshots": conn.execute("SELECT COUNT(*) FROM holdings_snapshots").fetchone()[0],
                      "corporate_actions": conn.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0],
                      "accounts": conn.execute("SELECT COUNT(*) FROM live_accounts").fetchone()[0],
                      "shadow_orders": conn.execute("SELECT COUNT(*) FROM shadow_orders").fetchone()[0]}
            counts['shadow_archives'] = conn.execute('SELECT COUNT(*) FROM shadow_archives').fetchone()[0]
            counts['shadow_pending'] = conn.execute("SELECT COUNT(*) FROM shadow_pending WHERE status='pending'").fetchone()[0]
            instrument_fresh = conn.execute("SELECT MAX(updated) FROM instruments").fetchone()[0]
            instrument_sources = [r[0] for r in conn.execute("SELECT DISTINCT source FROM instruments ORDER BY source LIMIT 5")]
            account_fresh = conn.execute("SELECT MAX(synced) FROM live_accounts").fetchone()[0]
            shadow_fresh = conn.execute("SELECT MAX(created) FROM shadow_orders").fetchone()[0]
            action_range = conn.execute("SELECT MIN(ex_date),MAX(ex_date) FROM corporate_actions").fetchone()
            last_action_import = conn.execute("SELECT created,status FROM imports WHERE dataset='Corporate actions' ORDER BY id DESC LIMIT 1").fetchone()
            action_fresh = last_action_import["created"] if last_action_import else None
            action_sources = [r[0] for r in conn.execute("SELECT DISTINCT source FROM corporate_actions ORDER BY source LIMIT 5")]
            snapshot_range = conn.execute("SELECT MIN(as_of),MAX(as_of),MAX(imported) FROM holdings_snapshots").fetchone()
            unclassified = conn.execute("SELECT COUNT(*) FROM holdings WHERE as_of=? AND sector IS NULL", (snapshots[0]["as_of"],)).fetchone()[0] if snapshots else 0
            market_sources = sorted({f["source"] for f in files})
        audit = None
        if audit_row:
            current = next((f["updated"] for f in files if f["symbol"] == "SPY" and f["interval"] == "1d"), None)
            audit = {"audited": audit_row["audited"], "primary_updated": audit_row["primary_updated"],
                     "reference": audit_row["reference"], "stale": current != audit_row["primary_updated"],
                     **json.loads(audit_row["result"])}
        last_account_activity = max((stamp for stamp in (account_fresh, shadow_fresh) if stamp is not None), default=None)
        catalog = [
            {"dataset": "Market", "source": ", ".join(market_sources) or "Not imported", "coverage": "See individual series below",
             "freshness": max((f["updated"] for f in files), default=None), "status": "Available; quality checks below" if files else "Not imported"},
            {"dataset": "Instruments", "source": ", ".join(instrument_sources) or "Not imported", "coverage": f"{counts['instruments']} reference records",
             "freshness": instrument_fresh, "status": "Available" if counts['instruments'] else "Not imported"},
            {"dataset": "Discovery universe", "source": snapshots[0]["source"] if snapshots else "Not imported",
             "coverage": f"{snapshots[0]['row_count']} SPY constituents dated {snapshots[0]['as_of']}; SPY and 11 sector ETFs" if snapshots else "No dated SPY snapshot",
             "freshness": snapshots[0]["imported"] if snapshots else None,
             "status": f"{unclassified} unclassified constituents" if snapshots else "Awaiting holdings"},
            {"dataset": "Sector constituents", "source": snapshots[0]["source"] if snapshots else "Not imported",
             "coverage": f"{snapshot_range[0]} to {snapshot_range[1]}" if snapshot_range[0] else "No dated snapshots",
             "freshness": snapshot_range[2], "status": "Dated snapshots" if snapshot_range[0] else "Not imported"},
            {"dataset": "Corporate actions", "source": ", ".join(action_sources) or "Not imported",
             "coverage": f"{action_range[0]} to {action_range[1]}" if action_range[0] else "No actions imported",
             "freshness": action_fresh, "status": ("Latest import needs review" if last_action_import and last_action_import["status"] != "complete"
                                                      else "Provider-declared" if action_range[0] else "Not imported")},
            {"dataset": "Accounts", "source": "Tradier and separate shadow cash ledger", "coverage": f"{counts['accounts']} live account snapshots; {counts['shadow_orders']} shadow fills; {counts['shadow_pending']} pending; {counts['shadow_archives']} archived accounts",
             "freshness": last_account_activity, "status": "Synchronized" if account_fresh else "Awaiting live sync"},
            {"dataset": "News", "source": "Not connected", "coverage": "None", "freshness": None, "status": "Placeholder"},
            {"dataset": "Back tests", "source": "Not connected", "coverage": "None", "freshness": None, "status": "Placeholder"},
        ]
        return {"market": files, "snapshots": snapshots, "imports": imports, "counts": counts, "audit": audit,
                "catalog": catalog,
                "notes": ["Coverage endpoints do not prove continuity; gaps and provider adjustments remain unverified.",
                          "News and backtest datasets are placeholders."]}

    @app.get("/api/database/actions")
    def action_records(symbol: str = "SPY", limit: int = Query(50, ge=1, le=100), before_date: str | None = None,
                       before_action: Literal["dividend", "split"] | None = None):
        symbol = valid_symbol(symbol)
        if (before_date is None) != (before_action is None):
            raise ValueError("Both action cursor fields are required")
        if before_date is not None:
            before_date = date.fromisoformat(before_date).isoformat()
        with db.connect() as conn:
            rows = [dict(row) for row in conn.execute("""SELECT symbol,ex_date,action,value,source FROM corporate_actions
                WHERE symbol=? AND (? IS NULL OR ex_date<? OR (ex_date=? AND action<?))
                ORDER BY ex_date DESC,action DESC LIMIT ?""",
                (symbol, before_date, before_date, before_date, before_action, limit + 1))]
        has_more = len(rows) > limit
        rows = rows[:limit]
        cursor = {"date": rows[-1]["ex_date"], "action": rows[-1]["action"]} if has_more else None
        return {"symbol": symbol, "rows": rows, "next_cursor": cursor}

    @app.patch("/api/settings")
    def update_settings(body: SettingsUpdate):
        values = body.model_dump(exclude_none=True)
        with db.connect() as conn:
            for key, value in values.items():
                conn.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, json.dumps(value)))
                if key in {"discovery_minutes", "account_minutes"}:
                    name = "discovery" if key == "discovery_minutes" else "account"
                    conn.execute("UPDATE jobs SET interval_seconds=?,next_run=? WHERE name=?", (value * 60, time.time() + value * 60, name))
            db.event("settings", "settings_updated", conn)
        return db.settings()

    @app.post("/api/jobs/{name}/run")
    def run_job(name: Literal["holdings", "market", "account", "discovery", "adjustments", "portfolio"]):
        with db.connect() as conn:
            job = conn.execute("SELECT status FROM jobs WHERE name=?", (name,)).fetchone()
            if job["status"] == "running":
                raise ValueError("This job is already running")
            conn.execute("UPDATE jobs SET next_run=?,enabled=1,status='idle',message='Queued' WHERE name=?", (time.time(), name))
            db.event("settings", "job_queued", conn)
        return {"queued": name}

    @app.post("/api/import/bars")
    async def import_bars(file: UploadFile = File(), symbol: str = Form(), interval: Literal["1d", "1h", "30m"] = Form(),
                          source: str = Form(), adjustment: Literal["unverified", "split_dividend_adjusted"] = Form(),
                          replace: bool = Form(False)):
        if not source.strip():
            raise ValueError("Source is required")
        content = await file.read(20 * 1024 * 1024 + 1)
        return market.import_csv(symbol, interval, content, source.strip(), adjustment, replace)

    @app.post("/api/import/holdings")
    async def import_holdings(file: UploadFile = File(), source: str = Form()):
        content = await file.read(5 * 1024 * 1024 + 1)
        if len(content) > 5 * 1024 * 1024 or not source.strip():
            raise ValueError("A source and CSV under 5 MB are required")
        try:
            rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
            dates = {r["as_of"] for r in rows}
            if len(dates) != 1:
                raise ValueError("CSV must contain exactly one as_of date")
            return store_holdings(db, dates.pop(), rows, source.strip(), "User-imported snapshot; classification and weights are provider-declared.")
        except (UnicodeDecodeError, KeyError):
            raise ValueError("Invalid holdings CSV; see import documentation") from None

    @app.post("/api/import/actions")
    async def import_actions(file: UploadFile = File(), source: str = Form()):
        content = await file.read(5 * 1024 * 1024 + 1)
        if len(content) > 5 * 1024 * 1024 or not source.strip():
            raise ValueError("A source and CSV under 5 MB are required")
        try:
            rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
            if not rows or len(rows) > 50000:
                raise ValueError("A corporate actions CSV must contain 1–50,000 rows")
            clean, seen = [], set()
            for row in rows:
                symbol = valid_symbol(row["symbol"])
                day = date.fromisoformat(row["ex_date"]).isoformat()
                action = row["action"].lower()
                value = float(row["value"])
                key = (symbol, day, action)
                if action not in {"split", "dividend"} or not math.isfinite(value) or value <= 0 or key in seen:
                    raise ValueError("Invalid action, value, or duplicate corporate action")
                seen.add(key)
                clean.append((*key, value, source.strip()[:120]))
        except (UnicodeDecodeError, KeyError):
            raise ValueError("Invalid corporate actions CSV; see import documentation") from None
        with db.connect() as conn:
            conn.executemany("INSERT OR REPLACE INTO corporate_actions VALUES(?,?,?,?,?)", clean)
            conn.execute("INSERT INTO imports(dataset,source,created,status,message) VALUES(?,?,?,?,?)",
                         ("Corporate actions", source.strip()[:120], time.time(), "complete", f"{len(clean)} source-declared actions imported"))
            db.event("database", "actions_imported", conn)
        return {"count": len(clean)}

    @app.get("/api/backups")
    def backups():
        folder = settings.data_dir / "backups"
        return {"backups": [{"name": p.name, "size": p.stat().st_size, "created": p.stat().st_mtime}
                            for p in sorted(folder.glob("gardifund-*.zip"), reverse=True)][:50] if folder.exists() else []}

    @app.post("/api/backups")
    def make_backup():
        folder = settings.data_dir / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        name = datetime.now(timezone.utc).strftime("gardifund-%Y%m%dT%H%M%S%fZ.zip")
        path = folder / name
        with LOCK, tempfile.TemporaryDirectory(dir=settings.data_dir) as temp:
            copy = Path(temp) / "gardifund.sqlite3"
            with db.connect() as conn:
                market_paths = [market.resolve_path(r[0]) for r in conn.execute("SELECT path FROM market_files")]
            if any(not file.is_file() for file in market_paths):
                raise ValueError("A market file is missing; restore it before creating a backup")
            source = sqlite3.connect(settings.db_path)
            target = sqlite3.connect(copy)
            try:
                source.backup(target)
            finally:
                source.close()
                target.close()
            try:
                with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.write(copy, "gardifund.sqlite3")
                    for file in market_paths:
                        archive.write(file, str(file.relative_to(settings.data_dir.resolve())).replace("\\", "/"))
                    archive.writestr("README.txt", "Contains private account data. Restore into the data directory while GARDiFUND is stopped. Credentials are not included.\n")
            except BaseException:
                path.unlink(missing_ok=True)
                raise
        db.event("database", "backup_created")
        return {"name": name, "size": path.stat().st_size}

    # The WAN surface is a set of explicit projections. It never exposes the
    # local control API, account identifiers, settings, database or private notes.
    @app.get("/view/state", include_in_schema=False)
    def view_state(request: Request):
        raw = state(request)
        return {"access_scope": "remote", "settings": {k: raw["settings"].get(k) for k in ("appearance", "top_n")},
                "sectors": raw["sectors"], "event_id": raw["event_id"],
                "connection": {"auth_mode": "cloudflare", "live_orders_enabled": False}}

    @app.get("/view/events", include_in_schema=False)
    async def view_events(request: Request):
        return await events(request)

    @app.get("/view/overview", include_in_schema=False)
    def view_overview(mode: Literal["live", "shadow"] = "live"):
        raw = overview(mode)
        positions = [p for p in portfolio_positions()['rows'] if p['mode'] == mode]
        def project_orders(rows):
            text_fields = {'symbol', 'side', 'status', 'create_date', 'order_type'}
            numeric_fields = {'created', 'quantity', 'price_cents', 'fill_price', 'avg_fill_price', 'price', 'limit_price'}
            return [{k: (v[:160] if isinstance(v, str) else None) if k in text_fields else
                        (v if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None)
                     for k, v in row.items() if k in text_fields | numeric_fields} for row in rows if isinstance(row, dict)]
        if mode == "shadow":
            ledger = raw["state"]
            order_count = raw['activity_count']
            return {"summary": {"initialized": bool(ledger["initialized"]), "cash": ledger["cash_cents"] / 100 if ledger["initialized"] else None,
                                "initial": ledger["initial_cents"] / 100 if ledger["initialized"] else None,
                                "equity": raw["equity_cents"] / 100 if raw["equity_cents"] is not None and ledger["initialized"] else None,
                                "settled_cash":ledger['settled_cash_cents'] / 100, 'unsettled_cash':ledger['unsettled_cash_cents'] / 100,
                                "realized": raw["realized_cents"] / 100, "open_positions": len(raw["positions"]), "order_count": order_count},
                    'positions':positions, 'orders':project_orders(raw['activity']),
                    "performance": [{"created": p["created"], "equity_cents": p["equity_cents"]} for p in raw["performance"]],
                    'daily_performance':raw['daily_performance']}
        accounts = raw["accounts"]
        def total(field):
            values = [(a.get("balance") or {}).get(field) for a in accounts]
            value = sum(values) if values and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values) else None
            return value if value is not None and math.isfinite(value) else None
        return {"summary": {"account_count": len(accounts), "equity": total("total_equity"), "cash": total("total_cash"),
                            "open_positions": sum(len(a.get("positions") or []) for a in accounts),
                            "order_count": sum(len(a.get("orders") or []) for a in accounts),
                            "synced": min((a["synced"] for a in accounts), default=None)},
                'positions':positions, 'orders':project_orders([o for account in accounts for o in account.get('orders', [])]),
                'daily_performance':raw['daily_performance']}

    @app.get("/api/positions")
    def portfolio_positions():
        def project(position, fields):
            output = {}
            for key in fields:
                value = position.get(key)
                if key in {"symbol", "name", "kind", "sector", "category"}:
                    output[key] = value[:160] if isinstance(value, str) else None
                else:
                    output[key] = value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None
            return output
        fields = ("symbol", "name", "kind", "sector", "quantity", "cost_basis", "price", 'fetched')
        live = overview("live")["accounts"]
        rows = []
        for account in live:
            for position in account.get('positions', []):
                if not isinstance(position, dict):
                    continue
                opened = None
                try:
                    stamp = datetime.fromisoformat(str(position.get('date_acquired', '')).replace('Z', '+00:00'))
                    opened = stamp.timestamp() if stamp.tzinfo else None
                except ValueError:
                    pass
                rows.append({**project(position, fields), 'mode':'live', 'opened':opened})
        for position in overview("shadow")["positions"]:
            cost = position.get("cost_cents")
            rows.append({**project(position, fields), "mode": "shadow", "category":position.get('category'), 'opened':position.get('opened'),
                         "cost_basis": cost / 100 if cost is not None else None})
        with db.connect() as conn:
            reserved = {r[0]:r[1] for r in conn.execute("SELECT symbol,SUM(quantity) FROM shadow_exit_limits WHERE status='working' GROUP BY symbol")}
        for position in rows:
            position['reserved_quantity'] = reserved.get(position['symbol'],0) if position['mode'] == 'shadow' else 0
            position['available_quantity'] = position['quantity'] - position['reserved_quantity'] if position['quantity'] is not None else None
            if not position.get('category'):
                position['category'] = ('sector' if position['symbol'] in {s for s, _ in SECTORS} else
                                        'etf' if position['kind'] == 'etf' or position['symbol'] == 'SPY' else
                                        'stock' if position['kind'] == 'stock' else 'unclassified')
            quantity, basis, price = position['quantity'], position['cost_basis'], position['price']
            entry = abs(basis / quantity) if quantity and basis is not None else None
            pnl = (price - entry) * quantity if price is not None and entry is not None else None
            position.update({'entry_price':entry, 'pl':pnl, 'pl_pct':pnl / abs(basis) * 100 if pnl is not None and basis else None,
                             'market_value':price * quantity if price is not None and quantity is not None else None})
            for field, value in position.items():
                if isinstance(value, float) and not math.isfinite(value):
                    position[field] = None
        return {"rows": rows, "note": "Live positions come from Tradier; Shadow positions are simulated. Quotes may be stale."}

    def view_symbol(symbol):
        value = valid_symbol(symbol)
        if value not in {"SPY", *(s for s, _ in SECTORS)}:
            raise HTTPException(status_code=403, detail="This series is available locally only.")
        return value

    def public_coverage(coverage):
        return {**coverage, "source": "Stored market data"} if coverage else None

    @app.get("/view/bars", include_in_schema=False)
    def view_bars(symbol: str = "SPY", interval: Literal["1d", "1h", "30m"] = "1d", start: str | None = None,
                  end: str | None = None, limit: int = Query(12000, ge=1, le=30000), before: str | None = None):
        raw = market.read(view_symbol(symbol), interval, start, end, limit, before)
        return {**raw, "coverage": public_coverage(raw["coverage"])}

    @app.get("/view/sectors", include_in_schema=False)
    def view_sectors(q: str = "", top_n: int = Query(10, ge=1, le=50)):
        return sectors(q, top_n)

    @app.get("/view/attribution", include_in_schema=False)
    def view_attribution(start: str, end: str, sector: str = "", limit: int = Query(10, ge=1, le=50)):
        return attribution_api(start, end, sector, limit)

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        dist = (ROOT / "dist").resolve()
        candidate = (dist / path).resolve()
        if candidate.is_file() and dist in candidate.parents:
            # Windows MIME registry entries commonly map .js to text/plain; module scripts
            # require a JavaScript MIME type, especially with nosniff enabled.
            media_type = "text/javascript" if candidate.suffix.lower() in {".js", ".mjs"} else None
            return FileResponse(candidate, media_type=media_type)
        index = dist / "index.html"
        if index.is_file() and not path.startswith("api/"):
            return FileResponse(index)
        return JSONResponse({"detail": "Not found"}, status_code=404)

    return app
