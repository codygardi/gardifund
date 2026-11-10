"""Temporary SPY constituent, ETF and sector snapshots for Discovery."""
import json
import threading
import time

from .db import SECTORS
from .market import valid_symbol
from .quotes import quote_fields, store_quotes
from .services import sector_day_moves
from .shadow import record_performance
from .tradier import ProviderError


class MarketDiscovery:
    """Keeps three SPY-related lists in memory until the next successful scan."""
    def __init__(self, db, adapter):
        self.db, self.adapter = db, adapter
        self.lock = threading.Lock()
        self.current = None
        self._progress = None

    def snapshot(self):
        with self.lock:
            return self.current or {"lists": {"stocks": [], "etfs": [], "sectors": []},
                                    "completed": None, "message": "Waiting for a SPY market-session scan."}

    def progress(self):
        with self.lock:
            return dict(self._progress) if self._progress else None

    def scan(self, stop_event=None, session=None, session_check=None, check_every=30):
        with self.lock:
            self._progress = {"phase": "preparing", "processed": 0, "total": None, "started": time.time()}
        try:
            result = self._scan(stop_event, session, session_check, check_every)
        finally:
            with self.lock:
                self._progress = None
        self.db.event("investing", "discovery_completed")
        return result

    def _scan(self, stop_event=None, session=None, session_check=None, check_every=30):
        with self.db.connect() as conn:
            conn.execute("BEGIN")
            snapshot = conn.execute("SELECT * FROM holdings_snapshots ORDER BY as_of DESC LIMIT 1").fetchone()
            if not snapshot:
                raise ProviderError("A dated SPY holdings snapshot is required before discovery")
            holdings = [dict(r) for r in conn.execute("SELECT symbol,name,sector,weight FROM holdings WHERE as_of=? ORDER BY symbol", (snapshot["as_of"],))]
            if not holdings:
                raise ProviderError("The latest SPY holdings snapshot is empty")
            account_symbols = {r[0] for r in conn.execute("SELECT symbol FROM shadow_positions")}
            for account in conn.execute("SELECT payload FROM live_accounts"):
                for position in json.loads(account[0]).get("positions", []):
                    if isinstance(position, dict) and isinstance(position.get("symbol"), str):
                        account_symbols.add(position["symbol"])
        sector_map = dict(SECTORS)
        primary = list(dict.fromkeys([*(h["symbol"] for h in holdings), "SPY", *sector_map]))
        primary_set = set(primary)
        extras, unsupported = [], 0
        for raw in account_symbols:
            try:
                symbol = valid_symbol(raw)
                if symbol not in primary_set:
                    extras.append(symbol)
            except (ValueError, AttributeError):
                unsupported += 1
        # Extra account marks keep the Overview truthful; they never enter the SPY lists.
        symbols = primary + sorted(set(extras))
        requested_set = set(symbols)
        started, quotes, last_bucket = time.time(), {}, -1
        next_session_check = time.monotonic() + check_every
        for offset in range(0, len(symbols), 100):
            if stop_event and stop_event.is_set():
                raise ProviderError("Discovery interrupted; the previous snapshot is retained")
            if session_check and time.monotonic() >= next_session_check:
                if session_check().get("state") not in {"premarket", "open"}:
                    raise ProviderError("Market session ended during discovery; the previous snapshot is retained")
                next_session_check = time.monotonic() + check_every
            batch = symbols[offset:offset + 100]
            for quote in self.adapter.quotes(batch):
                symbol = str(quote.get("symbol") or "").upper()
                if symbol in requested_set:
                    quotes[symbol] = quote
            processed = offset + len(batch)
            bucket = min(10, processed * 10 // len(symbols))
            if bucket > last_bucket:
                with self.lock:
                    self._progress = {"phase": "scanning", "processed": processed, "total": len(symbols), "started": started}
                self.db.event("investing", "discovery_progress")
                last_bucket = bucket
        now = time.time()
        unclassified = sum(1 for h in holdings if not h["sector"])
        stocks = [{"symbol": h["symbol"], "name": h["name"], "kind": "stock", "sector": h["sector"],
                   "weight": h["weight"], **quote_fields(quotes.get(h["symbol"], {}))} for h in holdings]
        etfs = []
        for symbol in ["SPY", *sector_map]:
            quote = quotes.get(symbol, {})
            sector = sector_map.get(symbol)
            etfs.append({"symbol": symbol, "name": str(quote.get("description") or ("SPDR S&P 500 ETF Trust" if symbol == "SPY" else f"{sector} sector ETF"))[:160],
                         "kind": "etf", "sector": sector, "group": "market" if symbol == "SPY" else "sector",
                         **quote_fields(quote)})
        sector_session, moves = sector_day_moves(stocks, snapshot)
        sectors = [{**etf, **move} for etf, move in zip(etfs[1:], moves, strict=True)]
        with self.db.connect() as conn:
            store_quotes(conn, list(quotes.values()), symbols, now)
            record_performance(conn, now)
        result = {"lists": {"stocks": stocks, "etfs": etfs, "sectors": sectors}, "started": started,
                  "completed": now, "session": session, "requested": len(primary),
                  "quoted": sum(quote_fields(quotes.get(symbol, {}))["price"] is not None for symbol in primary),
                  "account_marks_requested": len(set(extras)), "unsupported_account_symbols": unsupported,
                  "snapshot": dict(snapshot), "unclassified": unclassified, "sector_session": sector_session,
                  "sector_weights_complete": moves[0]["weight_complete"],
                  "message": "Temporary SPY-related lists; no curated results are saved."}
        with self.lock:
            self.current = result
        return result
