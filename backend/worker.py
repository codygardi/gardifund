import os
import logging
import sqlite3
import threading
import time

from .db import SECTORS
from .discovery import MarketDiscovery
from .execution import ShadowExecution
from .services import refresh_holdings, sync_accounts

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, db, adapter, market, discovery_store=None):
        self.db, self.adapter, self.market = db, adapter, market
        self.discovery_store = discovery_store or MarketDiscovery(db, adapter)
        self.execution = ShadowExecution(db, adapter)
        self.stop_event = threading.Event()
        self.thread = None
        self.lock_file = None
        self.pending_recovery = set()

    def start(self):
        path = self.db.path.parent / "worker.lock"
        handle = open(path, "a+b")
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise RuntimeError("Another GARDiFUND worker already owns this data directory") from None
        self.lock_file = handle
        with self.db.connect() as conn:
            conn.execute("UPDATE jobs SET status='idle',next_run=?,lease_until=NULL,message='Recovered after restart' WHERE status='running'", (time.time(),))
        self.thread = threading.Thread(target=self.run, name="gardifund-worker", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=40)
        if self.lock_file and not (self.thread and self.thread.is_alive()):
            self.lock_file.close()

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.recover_pending()
                job, now = self.claim_due()
                if not job:
                    self.stop_event.wait(2)
                    continue
                self.run_claimed(job, now)
            except sqlite3.Error:
                # A transient lock or storage failure must not silently kill the
                # sole scheduler. Retry without recording sensitive SQL or data.
                logger.warning("Scheduler storage unavailable; retrying")
                self.stop_event.wait(2)

    def recover_pending(self):
        if not self.pending_recovery:
            return
        names = set(self.pending_recovery)
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            changed = 0
            for name in names:
                changed += conn.execute("""UPDATE jobs SET status='idle',next_run=?,lease_until=NULL,
                    message='Recovered after a temporary storage error' WHERE name=? AND status='running'""",
                                        (time.time(), name)).rowcount
            if changed:
                self.db.event("settings", "job_recovered", conn)
        self.pending_recovery.difference_update(names)

    def claim_due(self, discovery_only=False):
        now = time.time()
        with self.db.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            job = conn.execute("""SELECT * FROM jobs WHERE enabled=1 AND status!='running' AND next_run<=?
                AND (?=0 OR name IN ('discovery','portfolio'))
                ORDER BY CASE WHEN name='discovery' THEN 0 WHEN name='portfolio' THEN 1 ELSE 2 END, next_run LIMIT 1""", (now, int(discovery_only))).fetchone()
            if job:
                conn.execute("UPDATE jobs SET status='running',last_started=?,lease_until=? WHERE name=?", (now, now + 3600, job["name"]))
                self.db.event("settings", "job_started", conn)
        return job, now

    def run_claimed(self, job, started):
        try:
            message = self.execute(job["name"])
            status = "complete"
        except Exception as exc:
            # Never persist provider response bodies, URLs containing credentials, or tracebacks.
            from .tradier import ProviderError
            message = str(exc)[:300] if isinstance(exc, (ProviderError, ValueError)) else f"{type(exc).__name__}: task failed"
            status = "error"
        try:
            self.finish_job(job, started, status, message)
        except sqlite3.Error:
            self.pending_recovery.add(job["name"])
            raise

    def finish_job(self, job, started, status, message):
        with self.db.connect() as conn:
            interval = conn.execute("SELECT interval_seconds FROM jobs WHERE name=?", (job["name"],)).fetchone()[0]
            finished = time.time()
            # Keep discovery on a start-to-start cadence; skip missed slots if a
            # slow provider makes a scan take longer than the configured interval.
            if job["name"] == "discovery" and status == "complete":
                next_run = started + (int((finished - started) // interval) + 1) * interval
            else:
                next_run = finished + (min(interval, 300) if status == "error" else interval)
            conn.execute("UPDATE jobs SET status=?,message=?,last_finished=?,next_run=?,lease_until=NULL WHERE name=?",
                         (status, message, finished, next_run, job["name"]))
            self.db.event("settings", "job_finished", conn)

    def checkpoint(self):
        """Yield between provider requests to a due scan, on this same worker thread."""
        if self.stop_event.is_set():
            raise ValueError("Interrupted; will retry after restart")
        job, started = self.claim_due(discovery_only=True)
        if job:
            self.run_claimed(job, started)
        if self.stop_event.is_set():
            raise ValueError("Interrupted; will retry after restart")

    def execute(self, name):
        if name == 'portfolio':
            return self.execution.refresh()
        if name == "holdings":
            result = refresh_holdings(self.db, checkpoint=self.checkpoint)
            return f"{result['count']} holdings; {result['classified']} classified, dated {result['as_of']}"
        if name == "account":
            return sync_accounts(self.db, self.adapter, checkpoint=self.checkpoint)
        if name == "adjustments":
            self.checkpoint()
            result = self.market.audit_spy_daily()
            return f"Compared {result['matched_dates']} SPY daily closes with Yahoo reference data"
        if name == "discovery":
            clock = self.adapter.market_clock()
            state = clock["state"]
            if state not in {"premarket", "open"}:
                return f"Waiting for premarket; market is {state}"
            result = self.discovery_store.scan(self.stop_event, session=state, session_check=self.adapter.market_clock)
            return f"Scanned {result['requested']} SPY-related symbols; {result['quoted']} had a usable last price"
        if name.startswith("sector_intraday:"):
            parts = name.split(":")
            if len(parts) != 3 or parts[1] not in {symbol for symbol, _ in SECTORS} or parts[2] != "1h":
                raise ValueError("Unknown sector history job")
            if self.stop_event.is_set():
                raise ValueError("Interrupted; will retry after restart")
            result = self.market.refresh_yahoo(parts[1], parts[2])
            if result is None:
                return f"Kept imported {parts[1]} {parts[2]} series"
            return f"Updated {parts[1]} {parts[2]}: {result['count']} bars"
        if name == "market":
            errors, count, kept = [], 0, 0
            for symbol in ["SPY", *(s for s, _ in SECTORS)]:
                self.checkpoint()
                try:
                    result = self.market.refresh_daily(self.adapter, symbol)
                    if result is None:
                        kept += 1
                    else:
                        count += 1
                except Exception:
                    errors.append(symbol + " daily")
            self.checkpoint()
            try:
                result = self.market.refresh_yahoo("SPY", "1h")
                if result is None:
                    kept += 1
                else:
                    count += 1
            except Exception:
                errors.append("SPY hourly")
            message = f"Updated {count} series"
            if kept:
                message += f"; kept {kept} imported series"
            if errors:
                raise ValueError(message + "; unavailable: " + ", ".join(errors))
            return message
        raise ValueError("Unknown job")
