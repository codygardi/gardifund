import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

BENCHMARKS = [("SPY", "S&P 500"), ("DIA", "Dow Jones Industrial Average"), ("QQQ", "Nasdaq-100")]
SECTORS = [
    ("XLC", "Communication Services"), ("XLY", "Consumer Discretionary"),
    ("XLP", "Consumer Staples"), ("XLE", "Energy"), ("XLF", "Financials"),
    ("XLV", "Health Care"), ("XLI", "Industrials"), ("XLB", "Materials"),
    ("XLRE", "Real Estate"), ("XLK", "Information Technology"), ("XLU", "Utilities"),
]
RESEARCH_FUNDS = BENCHMARKS + SECTORS

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS jobs (
 name TEXT PRIMARY KEY, interval_seconds INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
 next_run REAL NOT NULL, status TEXT NOT NULL DEFAULT 'idle', last_started REAL,
 last_finished REAL, message TEXT, lease_until REAL
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, module TEXT NOT NULL, action TEXT NOT NULL, created REAL NOT NULL, payload TEXT
);
CREATE TABLE IF NOT EXISTS instruments (
 symbol TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, sector TEXT,
 source TEXT NOT NULL, updated REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS migrations (name TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS holdings (
 as_of TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL, sector TEXT,
 weight REAL NOT NULL, source TEXT NOT NULL, PRIMARY KEY(as_of, symbol)
);
CREATE TABLE IF NOT EXISTS holdings_snapshots (
 as_of TEXT PRIMARY KEY, source TEXT NOT NULL, imported REAL NOT NULL, row_count INTEGER NOT NULL,
 weight_sum REAL NOT NULL, note TEXT
);
CREATE TABLE IF NOT EXISTS benchmark_snapshots (
 benchmark TEXT NOT NULL, as_of TEXT NOT NULL, source TEXT NOT NULL, imported REAL NOT NULL,
 row_count INTEGER NOT NULL, weight_sum REAL NOT NULL, PRIMARY KEY(benchmark, as_of)
);
CREATE TABLE IF NOT EXISTS benchmark_holdings (
 benchmark TEXT NOT NULL, as_of TEXT NOT NULL, symbol TEXT NOT NULL, name TEXT NOT NULL,
 weight REAL NOT NULL, PRIMARY KEY(benchmark, as_of, symbol),
 FOREIGN KEY(benchmark, as_of) REFERENCES benchmark_snapshots(benchmark, as_of) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS quotes (
 symbol TEXT PRIMARY KEY, price REAL, change REAL, change_pct REAL, volume INTEGER,
 quote_time TEXT, fetched REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS live_accounts (
 account_id TEXT PRIMARY KEY, payload TEXT NOT NULL, synced REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS live_daily_performance (
 day TEXT PRIMARY KEY, created REAL NOT NULL, equity REAL NOT NULL, cash REAL
);
CREATE TABLE IF NOT EXISTS shadow_state (
 id INTEGER PRIMARY KEY CHECK(id=1), cash_cents INTEGER NOT NULL DEFAULT 0,
 initial_cents INTEGER NOT NULL DEFAULT 0, initialized INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS shadow_positions (
 symbol TEXT PRIMARY KEY, quantity INTEGER NOT NULL CHECK(quantity>0), cost_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_orders (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL, symbol TEXT NOT NULL,
 side TEXT NOT NULL CHECK(side IN ('buy','sell')), quantity INTEGER NOT NULL,
 price_cents INTEGER NOT NULL, realized_cents INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS shadow_performance (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL, cash_cents INTEGER NOT NULL,
 equity_cents INTEGER, realized_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_settlements (
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL, amount_cents INTEGER NOT NULL,
 settles_on TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_requests (
 request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_pending (
 request_id TEXT PRIMARY KEY, created REAL NOT NULL, symbol TEXT NOT NULL, quantity INTEGER NOT NULL,
 category TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', message TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS shadow_exit_limits (
 request_id TEXT PRIMARY KEY, created REAL NOT NULL, symbol TEXT NOT NULL, quantity INTEGER NOT NULL,
 category TEXT NOT NULL, limit_price REAL NOT NULL, expires_on TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'working', message TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS shadow_archives (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shadow_daily_performance (
 day TEXT PRIMARY KEY, created REAL NOT NULL, cash_cents INTEGER NOT NULL, equity_cents INTEGER,
 realized_cents INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS quote_details (
 symbol TEXT PRIMARY KEY, bid REAL, ask REAL, bid_at REAL, ask_at REAL, trade_at REAL, fetched REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS market_session (
 id INTEGER PRIMARY KEY CHECK(id=1), state TEXT NOT NULL, observed REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS setups (
 symbol TEXT PRIMARY KEY, note TEXT NOT NULL DEFAULT '', created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS live_setup_drafts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL, symbol TEXT NOT NULL,
 side TEXT NOT NULL CHECK(side IN ('buy','sell')), quantity INTEGER NOT NULL CHECK(quantity>0),
 price_cents INTEGER NOT NULL CHECK(price_cents>0), note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS market_files (
 symbol TEXT NOT NULL, interval TEXT NOT NULL, source TEXT NOT NULL, adjustment TEXT NOT NULL,
 coverage_start TEXT, coverage_end TEXT, row_count INTEGER NOT NULL DEFAULT 0, updated REAL NOT NULL,
 status TEXT NOT NULL, path TEXT NOT NULL, PRIMARY KEY(symbol, interval)
);
CREATE TABLE IF NOT EXISTS imports (
 id INTEGER PRIMARY KEY AUTOINCREMENT, dataset TEXT NOT NULL, source TEXT NOT NULL,
 created REAL NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS corporate_actions (
 symbol TEXT NOT NULL, ex_date TEXT NOT NULL, action TEXT NOT NULL, value REAL NOT NULL,
 source TEXT NOT NULL, PRIMARY KEY(symbol, ex_date, action)
);
CREATE TABLE IF NOT EXISTS market_anomalies (
 symbol TEXT NOT NULL, interval TEXT NOT NULL, stamp TEXT NOT NULL, reason TEXT NOT NULL,
 raw TEXT NOT NULL, detected REAL NOT NULL, PRIMARY KEY(symbol,interval,stamp)
);
CREATE TABLE IF NOT EXISTS quality_audits (
 id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, interval TEXT NOT NULL,
 primary_updated REAL NOT NULL, audited REAL NOT NULL, reference TEXT NOT NULL, result TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS quality_audits_series ON quality_audits(symbol, interval, audited DESC);
CREATE INDEX IF NOT EXISTS holdings_by_date ON holdings(as_of, sector);
CREATE INDEX IF NOT EXISTS events_by_id ON events(id);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            for table, additions in {
                'shadow_positions': {'category': "TEXT NOT NULL DEFAULT 'stock'", 'opened': 'REAL'},
                'shadow_orders': {'category': "TEXT NOT NULL DEFAULT 'stock'", 'fill_price': 'REAL', 'request_id': 'TEXT',
                                  'order_type':"TEXT NOT NULL DEFAULT 'market'", 'limit_price':'REAL'},
                'shadow_pending': {'check_order':'INTEGER NOT NULL DEFAULT 0'},
                'shadow_exit_limits': {'check_order':'INTEGER NOT NULL DEFAULT 0'},
            }.items():
                existing = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
                for name, declaration in additions.items():
                    if name not in existing:
                        conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {declaration}')
            for table in ('shadow_pending','shadow_exit_limits'):
                conn.execute(f'CREATE INDEX IF NOT EXISTS {table}_queue ON {table}(status,check_order,created,request_id)')
            event_columns = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
            if "payload" not in event_columns:
                conn.execute("ALTER TABLE events ADD COLUMN payload TEXT")
            columns = {r[1] for r in conn.execute("PRAGMA table_info(market_files)")}
            if "anomaly_count" not in columns:
                conn.execute("ALTER TABLE market_files ADD COLUMN anomaly_count INTEGER NOT NULL DEFAULT 0")
            if "missing_sessions" not in columns:
                conn.execute("ALTER TABLE market_files ADD COLUMN missing_sessions INTEGER")
            if "unexpected_sessions" not in columns:
                conn.execute("ALTER TABLE market_files ADD COLUMN unexpected_sessions INTEGER")
            conn.execute("INSERT OR IGNORE INTO shadow_state(id) VALUES (1)")
            migration = 'shadow_cash_account_v2'
            if not conn.execute('SELECT 1 FROM migrations WHERE name=?', (migration,)).fetchone():
                if not conn.execute('SELECT initialized FROM shadow_state WHERE id=1').fetchone()[0]:
                    conn.execute('UPDATE shadow_state SET initialized=1,cash_cents=1000000,initial_cents=1000000 WHERE id=1')
                    conn.execute('INSERT INTO shadow_performance(created,cash_cents,equity_cents,realized_cents) VALUES(?,?,?,0)',
                                 (time.time(), 1000000, 1000000))
                for symbol, _ in SECTORS:
                    conn.execute("UPDATE shadow_positions SET category='sector' WHERE symbol=?", (symbol,))
                conn.execute("UPDATE shadow_positions SET category='etf' WHERE symbol='SPY'")
                conn.execute('INSERT INTO migrations VALUES(?)', (migration,))
            defaults = {"discovery_minutes": 5, "account_minutes": 15, "top_n": 10,
                        "appearance": "light", "market_session": "regular"}
            for key, value in defaults.items():
                conn.execute("INSERT OR IGNORE INTO settings VALUES (?, ?)", (key, json.dumps(value)))
            if json.loads(conn.execute("SELECT value FROM settings WHERE key='top_n'").fetchone()[0]) > 10:
                conn.execute("UPDATE settings SET value='10' WHERE key='top_n'")
            now = time.time()
            for name, seconds, delay in [("holdings", 86400, 0), ("market", 86400, 5), ("benchmark_holdings", 86400, 35),
                                         ("account", 900, 10), ("discovery", 300, 20), ("adjustments", 86400, 30),
                                         ("portfolio", 15, 2)]:
                conn.execute("INSERT OR IGNORE INTO jobs(name,interval_seconds,next_run) VALUES(?,?,?)",
                             (name, seconds, now + delay))
            # The old application default was 30 minutes. Move that old default to the
            # requested five-minute cadence once, while preserving other user choices.
            migration = "discovery_marketwide_v1"
            if not conn.execute("SELECT 1 FROM migrations WHERE name=?", (migration,)).fetchone():
                interval = json.loads(conn.execute("SELECT value FROM settings WHERE key='discovery_minutes'").fetchone()[0])
                if interval == 30:
                    conn.execute("UPDATE settings SET value='5' WHERE key='discovery_minutes'")
                    conn.execute("UPDATE jobs SET interval_seconds=300,next_run=? WHERE name='discovery'", (now,))
                conn.execute("INSERT INTO migrations(name) VALUES(?)", (migration,))
            migration = "discovery_marketwide_status_v1"
            if not conn.execute("SELECT 1 FROM migrations WHERE name=?", (migration,)).fetchone():
                # A status from the retired SPY-only scanner must not be presented
                # as the result of a market-wide run after the upgrade.
                conn.execute("""UPDATE jobs SET status='idle',message='Waiting for a market-session scan',
                    last_started=NULL,last_finished=NULL,next_run=?
                    WHERE name='discovery' AND status!='running' AND message LIKE 'Scanned % SPY holdings%'""", (now,))
                conn.execute("INSERT INTO migrations(name) VALUES(?)", (migration,))
            # Preserve retired series for backups while stopping their refresh jobs.
            conn.execute("UPDATE jobs SET enabled=0 WHERE name LIKE 'sector_intraday:%:30m' AND enabled=1")
            for index, (symbol, _) in enumerate(SECTORS):
                conn.execute("INSERT OR IGNORE INTO jobs(name,interval_seconds,next_run) VALUES(?,?,?)",
                             (f"sector_intraday:{symbol}:1h", 86400, now + 45 + index * 20))
            migration = "spy_only_discovery_v1"
            if not conn.execute("SELECT 1 FROM migrations WHERE name=?", (migration,)).fetchone():
                # Retain legacy reference data, but prevent unrelated background jobs
                # from running now that the workspace is scoped to SPY.
                conn.execute("UPDATE jobs SET enabled=0 WHERE name='universe_otc' OR name LIKE 'sector_reference:%'")
                conn.execute("""UPDATE jobs SET status='idle',message='Waiting for a SPY market-session scan',
                    last_started=NULL,last_finished=NULL,next_run=? WHERE name='discovery' AND status!='running'""", (now,))
                conn.execute("INSERT INTO migrations(name) VALUES(?)", (migration,))
            migration = "benchmark_research_v1"
            if not conn.execute("SELECT 1 FROM migrations WHERE name=?", (migration,)).fetchone():
                conn.execute("UPDATE jobs SET next_run=MIN(next_run,?) WHERE name='market' AND status!='running'", (now + 5,))
                conn.execute("INSERT INTO migrations(name) VALUES(?)", (migration,))
            for symbol, name in [("SPY", "SPDR S&P 500 ETF Trust"), *BENCHMARKS[1:], *SECTORS]:
                conn.execute("INSERT OR IGNORE INTO instruments VALUES(?,?,?,?,?,?)",
                             (symbol, name, "etf", dict(SECTORS).get(symbol), "Fund provider", now))

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def event(self, module: str, action: str, conn=None, payload=None):
        if conn is None:
            with self.connect() as connection:
                self.event(module, action, connection, payload)
            return
        conn.execute("INSERT INTO events(module,action,created,payload) VALUES(?,?,?,?)",
                     (module, action, time.time(), json.dumps(payload) if payload is not None else None))
        conn.execute("DELETE FROM events WHERE id < (SELECT COALESCE(MAX(id),0)-5000 FROM events)")

    def settings(self):
        with self.connect() as conn:
            return {r["key"]: json.loads(r["value"]) for r in conn.execute("SELECT * FROM settings")}
