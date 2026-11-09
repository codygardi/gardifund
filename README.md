# GARDiFUND

A single-user Windows investment workspace. FastAPI serves the compiled React application and control API on `127.0.0.1`. One worker in the same process runs persisted jobs even when the browser is closed. SQLite uses WAL for account, settings, schedules, metadata and events. Market bars are stored in Parquet and queried with DuckDB. The backend adapter reads Tradier data. **Live order submission is disabled.**

Viewers on enrolled devices can open a read-only workspace over your private Cloudflare Tunnel. The Windows app supplies cached portfolio and chart data while it runs. [Private Tunnel setup](deploy/private-cloudflare.md)

## Install and run

Requirements: Windows, Python 3.12, Node.js 20 or newer, and a Tradier production API key. From PowerShell in this directory:

```powershell
.\scripts\install.ps1
```

The installer creates a virtual environment, installs the tested versions in `requirements.lock`, builds the UI, and creates `.env` only if it does not exist. Set `PRODUCTION_API_KEY` in `.env`. Run `.\.venv\Scripts\python.exe scripts\set-setup-password.py` to set the local Live screening password: the script stores a salted scrypt hash as `SETUPS_PASSWORD_HASH` and removes the old plaintext `SETUPS_PIN` setting. Restart after changing it. Credentials, password hashes, local account data, backups, and dependencies are excluded by `.gitignore`; credentials and password hashes are never serialized to the browser.

For a **local-only development session**, run:

```powershell
.\scripts\start.ps1 -LocalDev
```

Open `http://127.0.0.1:8765/`. The control listener accepts loopback clients and loopback hostnames and rejects common proxy headers. Stop a foreground session with Ctrl+C. Running `.\scripts\start.ps1` without `-LocalDev` enables Cloudflare viewing mode. In that mode, `GARDIFUND_PRIVATE_VIEW_IP` selects the private port 80 viewer once Access settings are present. With the private IP unset, the viewing listener binds to loopback on port 8766 for local verification. Both listeners belong to one process and one background worker. An OS lock protects the data directory from a second worker.

The full seven-module workspace is available on this Windows PC. Other devices use Cloudflare One Client and the private viewing address to open Overview, Charts and Back Test.

If the `GARDiFUND` scheduled task is already running, stop it with `Stop-ScheduledTask -TaskName GARDiFUND` before starting a separate session. Afterward, resume the background task with `Start-ScheduledTask -TaskName GARDiFUND`. The task provides local control and a Cloudflare listener that remains inaccessible without valid Access configuration.

The worker fetches dated SPY and sector ETF holdings, Tradier account data, SPY/sector daily bars, Yahoo SPY intraday bars, and discovery quotes. Hourly and 30-minute sector ETF history runs in staggered jobs; selecting missing sector comparisons in Charts brings those series forward in the queue. Each series is refreshed incrementally on its own persisted schedule. Jobs can be queued and their status viewed in Settings. Discovery defaults to every five minutes, editable in Settings, and starts only when Tradier's market clock reports `premarket` or `open`; a manually queued scan follows the same session rule. The clock is checked during longer scans; if the session ends, the incomplete scan is discarded. Account sync defaults to every 15 minutes. Discovery is prioritized when due, uses a start-to-start cadence, and skips missed slots if a provider is slow. The single worker checks for a due scan between individual holdings, account, and market-history provider requests; an already-running request can still delay the scan until it returns or times out. Settings and next-run times survive restart. An interrupted job is recovered when the worker reacquires its lock. The machine and application must remain running for scheduled jobs to execute, but the browser may be closed. Portfolio marks refresh on a separate 15-second persisted job during premarket and the regular session. This job also checks queued Shadow entries and working limit exits. The single worker yields to due Discovery and portfolio work between longer provider requests. UI updates stream through SSE; quote acquisition is polling, not a tick-by-tick exchange feed. A slow in-flight provider request can delay a refresh. Resource refreshes are scoped so portfolio updates do not reload stored chart history.

### Start on Windows sign-in

Run the following once in your own Windows account (Cloudflare can be configured later):

```powershell
.\scripts\register-startup.ps1
```

This registers a Task Scheduler task named `GARDiFUND` for the current user's sign-in, in Cloudflare mode, with restart-on-failure. It refuses to replace a task with the same name. It is not a system boot service and does not run before sign-in. The launcher uses a Windows process job so stopping the task also terminates its Python worker descendants. Manage or remove it in Task Scheduler. Run `cloudflared` separately as a Windows service or another managed process; see Cloudflare's [Windows service instructions](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/as-a-service/windows/).

## Cloudflare Tunnel, Access, and MFA

Use the existing Windows Tunnel with one private `/32` route, an Access application for HTTP port 80, and Cloudflare One Client on the viewing devices. The setup uses a private address and an Access policy requiring MFA. Keep router port forwarding off. [Private Tunnel setup](deploy/private-cloudflare.md)

The private viewer accepts connections from the Tunnel connector on the same PC. It validates RS256 Access JWT signatures, issuer, audience and expiry for the UI, assets, views and SSE. `/healthz` returns basic readiness only. The viewer rejects `/api/*` and all mutations, including requests with valid JWTs. Overview shares a projection of Live and Shadow metrics, Charts reads stored market data, and Back Test is a placeholder. Account identifiers, raw broker payloads, private notes, archived ledgers and credentials stay local. SSE strips private event details and closes at token expiry or after one minute. The separate loopback control listener rejects proxied requests and checks the origin of mutations. [Cloudflare origin JWT validation](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/)

## Data behavior and limits

- **Live:** the production key reads accounts, balances, current positions and current-session orders. Account IDs are masked in the API response; the local SQLite database and backups still contain private account information. Orders are read-only.
- **Shadow cash account:** starts at **$10,000**. Overview has no position-entry form; entries are made in Investing → Setups and exits in Investing → Positions. Whole-share market buys use a fresh ask and market sells use a fresh bid, with valid bid/ask timestamps no older than 120 seconds. Missing, stale or crossed quotes never produce a fill; freshness is rechecked after the final market-session request. Orders are idempotent by request ID. Only settled cash may fund buys; no margin, shorting or borrowing is modeled. The local Enter by symbol action remains available even while temporary Discovery lists are empty after a restart. Off-hours entries queue for the next normal session and survive restarts; cash and quotes are checked at fill time. Sale proceeds use a conservative T+1 simulation that skips NYSE closures and US federal holidays; actual broker settlement calendars can differ. [Investor.gov describes T+1](https://www.investor.gov/introduction-investing/investing-basics/glossary/settling-securities-transactions-t1). Fees, order-book priority, market impact and automatic dividend/split adjustments to simulated holdings are not modeled. Open positions use cached last prices for valuation; unavailable marks make equity unavailable. Daily snapshots persist and the most recent 10,000 intraday snapshots are retained.
- **Discovery universe:** the worker scans every constituent of the latest dated SPY holdings snapshot, SPY itself, and the 11 tradable sector ETFs. Holdings are sourced from State Street or a user-imported dated snapshot. Requests are paced below Tradier's published market-data limit; a quota or provider failure leaves the previous completed scan intact and is shown in job status. The scan reports symbols without usable quotes. Tradier's slash-form share-class symbols are normalized to dot form. Any additional live/shadow account positions are quoted solely to keep Overview valuations truthful and never enter the Discovery lists. Missing quotes are cleared rather than reused. Legacy broad-market directory data from an earlier configuration is left on disk but is not scanned or displayed.
- **Temporary filters:** three SPY-related Discovery lists—constituent stocks, SPY and its 11 sector ETFs, and the 11 sectors represented by those ETFs—live in application memory and are atomically replaced after a successful scan; they are cleared on application restart. The left column selects exactly one list and applies its own filters: constituent sector and individual SPY weight for stocks; fund and SPY/sector group for ETFs; sector, direction and sector SPY weight for sectors. Search, price, volume and daily-change ranges are also available. Sector weights sum the snapshot's classified constituent weights; when classification or reported total weight is incomplete, they are marked partial and excluded from weight filters. Last-trade timestamps are shown when Tradier supplies them; a last price can be from an earlier session, particularly in premarket. The right-hand results are temporary, not saved setups. Sector membership is inferred only when a stock appears in exactly one same-date State Street sector ETF holdings file, or is declared by an imported snapshot. It is a membership proxy, not an independently verified GICS classification.
- **Setups and positions:** both Discovery and Setups use two columns and separate stock/ETF/sector filters. Setups adds turnover and bid/ask spread screening over the last completed Discovery snapshot. Shadow is unlocked. Live screening requires the local password, with a 15-minute HttpOnly SameSite session and cooldown after failed attempts. Live order submission is deliberately disabled: open/close real positions in Tradier and synchronize them here. The local Positions view has Sectors/ETFs/Stocks tabs, Live/Shadow labels, average entry price, cached mark, P/L and P/L %. A sector position is an actual sector ETF, not an untradable index. Entries retain their chosen category. A symbol already held in one Shadow category cannot be simultaneously held in another. Previous local notes/drafts remain in the database and backups; their obsolete write APIs have been removed.
- **Daily:** Tradier history is requested from 2000 and incrementally refreshed with an overlap. OHLCV values are validated. Provider rows with invalid bounds, timestamps, volume or duplicates are excluded and recorded as anomalies instead of repaired. Database → Market coverage → Inspect shows missing dates and the excluded source rows. Daily dates within the observed span are compared with the NYSE calendar, with missing and non-session dates reported. The calendar check is not an independent audit or proof of coverage before the first or after the last bar. Tradier's [historical pricing endpoint](https://docs.tradier.com/market-data/get-historical-pricing) describes split-adjusted prices that are not dividend-adjusted; stored data remains marked `unverified` because individual bars and adjustments have not been independently certified.
- **Adjustment comparison:** the worker periodically compares up to 10,000 stored SPY daily closes from 2000 onward with Yahoo's reported Close and Adj Close on overlapping dates. Database shows the number of comparable and unmatched dates, median absolute percentage differences, the number within 0.01%, and up to 20 dates with the largest differences; duplicate reference dates are excluded. It also counts how often a difference falls on the previous available reference date before a reported dividend, without assuming a causal relationship. The Yahoo reference is used only for this comparison and never fills or replaces Tradier bars. When the stored SPY series changes, the previous result is marked stale and a new comparison is queued; a comparison is discarded if the series changes while it runs. Agreement is evidence of similarity, not independent certification of corporate-action adjustments or total returns. The latest 30 summaries are retained; a comparison can also be queued locally in Database.
- **Corporate actions:** the same comparison records source-declared SPY dividends and splits returned by Yahoo in a separate dataset. Existing records, including user-imported actions, are never overwritten or deleted by this automatic import. Conflicting values, invalid or duplicate reference entries, and previously reported actions absent from a later response are flagged for review; missing action columns are not treated as evidence that there were no events. These records do not automatically adjust prices and are not independently certified.
- **Hourly and 30-minute:** SPY and sector ETF bars come from Yahoo Finance through `yfinance`, with regular-session data, explicit unadjusted download settings and incremental overlap. Database → Market coverage → Inspect compares Yahoo bar start times with an NYSE regular-session grid, including daylight-saving changes and early closes, within the observed span. It reports missing and unexpected starts in UTC, and flags a result if the series changed during the check. The first and last sessions can be partial; dates outside the observed span and adjustments are not validated. Checks are cached and bounded to 100,000 observed and 150,000 expected bars. For other sources the app does not assume a timestamp or session convention. Yahoo's available intraday lookback is limited and may change; stored coverage begins only where the provider supplied bars. It does **not** provide a complete hourly/30-minute history from 2000. The [yfinance project](https://github.com/ranaroussi/yfinance) notes Yahoo data is intended for personal use and remains subject to Yahoo's terms. A suitably licensed historical provider is needed for the missing older bars. Confirm the data providers' redistribution permissions before making market data available to other people through the remote view.
- **Chart paging:** the market chart initially loads at most 12,000 SPY bars. Use **Load earlier bars** to page backward through the stored series, including older imported intraday history; page cursors are exclusive, so adjacent pages do not duplicate a bar. Coverage labels describe the entire stored series, not just the page currently loaded.
- **Sector comparisons:** the chart uses tradable sector ETFs, normalized to their first common available close with SPY. Missing intraday series are queued for background import and remain blank if the provider has no usable data. Sector top stocks are ranked by the latest cached day-change quote, not an invented recommendation or signal. The large chart switches among Market, Sectors, ETFs and Stocks. ETFs select SPY or one of its 11 sector funds. Annual Stocks ranks Top 1–10 using an exact first-trading-session SPY constituent snapshot and declared-adjusted prices at the first and last completed session closes. It reports missing constituents, never substitutes today’s constituents for a past year, and labels incomplete rankings. Annual winners use hindsight. Each year’s Top N selection is saved in the browser.
- **Attribution:** requires a holdings/weights snapshot dated **exactly** on the requested start date and provider-declared split-and-dividend-adjusted daily closes at both start and end for each constituent. It uses start-date weights, never current constituents for an old date. A selected sector's contributions are normalized by its start-date constituent weights; all-sector contributions use SPY weights. Missing prices/classifications and insufficient snapshot weight make results incomplete. Adjustment declarations are not independently certified and corporate actions are not automatically applied. Dated historical holdings and verified adjusted constituent bars generally require an additional licensed dataset; the app does not manufacture them.
- **News and Back Test Engine:** intentional placeholders; there are no synthetic stories or backtest results.

## Market and limit exits

Shadow exits are accepted only during a verified normal NYSE session, including early closes. A **Market** exit uses the current fresh bid. A **Limit** exit is a day order: its shares are reserved, it waits for a fresh bid at or above the limit, and it expires at the regular-session close. The worker checks at most 20 queued entries and 20 working exits per portfolio run on its 15-second schedule, rotating in persisted order. With many orders an individual limit is checked less often, so an intraday touch between checks may not fill; this is a quote-based simulation, not an exchange matching engine. Working limits survive restarts and can be cancelled from Positions. Queued entries, working limits, fills, cancellations and rejections synchronize into Overview’s decision activity with their actual status. Cancel a working limit before selling its reserved shares another way. Live entry/exit controls remain disabled; use Tradier for real orders.

The local **Reset Shadow** button archives the current simulated ledger, cancels pending entries and limits, and starts a new $10,000 account. It never changes Tradier. Previous ledgers remain in `shadow_archives` and backups. Live equity growth is a change from the first retained daily snapshot and includes deposits and withdrawals. Light mode is the default for new workspaces. The header theme switch saves the browser's choice.

## Checking local and remote access

```powershell
.\scripts\check-connections.ps1
.\scripts\check-connections.ps1 -RequirePrivateView
```

The local endpoints should return 200 and JSON. The configured private viewer must return 401 for an anonymous request. Use `-RequirePrivateView` to fail the check when the private IP or Access settings are missing. Finish verification from an enrolled device on another network by completing MFA and observing live updates in Overview and Charts. [Verification steps](deploy/private-cloudflare.md#4-verify-the-connection)

## Import formats

Use Database → Import a CSV. Files must be UTF-8 and include the headers shown below. Each example is illustrative only. An import is validated before its database transaction; invalid bars reject a manual import. A series with a different source or adjustment can be overwritten only by selecting **Replace existing series**. Importing a holdings snapshot for an existing date replaces that date's snapshot.

Daily OHLCV (`time` is a date); intraday `time` must be ISO 8601 with a UTC offset or `Z`:

```csv
time,open,high,low,close,volume
2024-01-02,100,102,99,101,100000
```

Dated holdings: `weight` is a fraction of the whole fund, not a percentage. A blank sector remains unclassified; named sectors must match the labels in the UI. Use one `as_of` date per file.

```csv
as_of,symbol,name,sector,weight
2024-01-02,EXAMPLE,Example Company,Information Technology,0.01
```

Corporate actions are stored as source-declared records for reference. `split` values are new shares per old share; `dividend` values are cash per share. They are not silently applied to prices.

```csv
symbol,ex_date,action,value
EXAMPLE,2024-01-02,split,2
```

## Backup and restore

Settings → **Create backup** produces a ZIP in `data/backups/`. It uses SQLite's online backup API under the market-file lock and includes the SQLite snapshot and Parquet files; it excludes `.env` and tunnel credentials. Backups contain sensitive financial information. Copy them to an appropriately protected location and test restoration.

To restore: stop the app and its scheduled task, make a separate copy of the current `data` directory, extract a trusted backup into an empty staging directory, then place its `gardifund.sqlite3` and `market/` inside the intended `data` directory. Preserve your separately protected `.env`; remove any old `gardifund.sqlite3-wal` and `-shm` files only after the app is fully stopped and the old data has been backed up. Start the app and check Database and Settings. Parquet paths in new backups are relative to the data directory.

## Verification

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm test
npm run build
```

The tests cover signed Access JWT claims/signatures, token-bound SSE expiry, local responsiveness during slow signing-key retrieval, actual local and remote socket separation, spoofed hosts and proxy headers, read-only projection and mutation restrictions, secret exclusion, live/shadow separation, transactional simulated orders, SPY universe isolation and batching, temporary scan replacement, session gating, cooperative scheduling and recovery, valid and invalid imports, anomaly quarantine, adjustment comparisons, dated attribution, SSE events and backup contents. Real provider availability, verified historical constituents and adjusted prices, and Cloudflare MFA/domain provisioning still require the relevant accounts and network access. Additional coverage includes fresh bid/ask execution, settled-cash restrictions, duplicate/concurrent orders, queued entries, market/limit exits, early-close expiry, share reservation, reset archives and annual rankings. Run `.\.venv\Scripts\python.exe scripts\stress-test.py` for a bounded 330-request, 16-client load against isolated synthetic data with real local/remote sockets, a signed test JWT and one active worker. Its report is written to ignored `data/verification-stress.json`; the temporary dataset is removed. `scripts/qa-server.py` is an isolated synthetic visual-test server on 18765/18766 with test-only automatic access; it never reads `.env` or reaches providers and must not be used as a tunnel origin.

## Verification status

The local implementation is verified. Signed-in remote verification and full historical coverage require the remaining account configuration and data below. The latest local evidence snapshot is in ignored `data/verification-audit.json`.

| Requirement | Current evidence and limits |
| --- | --- |
| Windows-native modular monolith | `package.json`, `requirements.lock`, `backend/server.py`, `db.py`, `market.py`, `worker.py`, and `tradier.py` implement the requested stack. Local HTTP and the Windows startup task work; one worker owns the data-directory lock. |
| Overview, Live/Shadow separation and local controls | Overview displays balances, positions, performance and order activity. Position entry is in Investing. Integration tests cover isolated ledgers, broker synchronization and reset archives; no live order endpoint exists. |
| Tradier credential boundary | `PRODUCTION_API_KEY` stays in the ignored backend environment. The adapter exposes read-only broker calls. Secret-value scans and backup tests exclude the key and password hash from the UI/build and backup database. |
| SPY Discovery and Setup screening | The current dated SPY universe, SPY and 11 sector ETFs are scanned in paced batches. Per-list two-column filters, additional Setup screens, temporary scan replacement, premarket/regular gating and persisted schedules are implemented and tested. |
| Shadow cash and execution | A new/reset account starts with $10,000. Fresh bid/ask execution, settled-cash limits, off-hours queued entries, duplicate requests, market/day-limit exits, share reservations and early-close expiry are covered by `tests/test_execution.py`. Quote age is rechecked after the final session request. Persisted rotating batches prevent waiting orders from starving later orders. |
| Local Live lock and real trading | The local password is stored as a salted scrypt hash; sessions expire and failed attempts are limited. **Live submission remains disabled**, following the explicit constraint and the user's decision to trade in Tradier and synchronize positions. In-app real-money execution is not claimed. |
| Position categories, P/L, persistence and synchronization | Sectors/ETFs/Stocks tabs label Live/Shadow positions, average entry price and cached P/L. Restarts preserve holdings and working limits. The portfolio job refreshes marks on its 15-second schedule, and SSE refreshes the affected views. This is polled market data, not an exchange tick stream. |
| Charts and historical winners | Four chart modes, ETF/sector selection, year selection and Top 1–10 controls are implemented. Tests require dated constituents and declared-adjusted prices. Actual annual winner coverage is unavailable where those inputs are missing; current constituents are never substituted for historical membership. |
| News and Back Test | Intentional placeholders. News is local only; no invented stories or backtest results are displayed. |
| Database, sources and data quality | Catalogs, import validation, source/freshness records, corporate actions, quarantine, exchange-calendar checks and adjustment comparisons are implemented. Observed SPY daily coverage has 27 rejected source rows/session gaps. Hourly coverage starts in December 2023 and has 16 missing expected starts within its observed range. These are flagged, not filled. Adjustments remain unverified. |
| Settings and appearance | Connections, schedules, sessions/providers, Shadow reset, storage/backups and access settings are local. Light mode is the default. Saved theme and annual Top N choices persist. |
| Private viewing | The IP option binds the viewer to one private IPv4 on port 80 after Access is configured. The viewer checks the source address and JWT independently and rejects local modules and mutations. Socket and streaming tests exercise the origin using signed test JWTs. A real Access sign-in and MFA challenge require the private route and application in your Cloudflare account. |
| Startup, backups and recovery | The scheduled task runs the local service. Interrupted jobs recover under the worker lock. A post-upgrade backup restored with SQLite integrity `ok`, all 36 referenced Parquet files, queue-progress columns and no embedded credentials. The upgrade preserved existing cash, positions and request history. |
| Disconnected and expired-session feedback | HTML responses, sign-in redirects and incomplete JSON responses are rejected as unavailable data. Overview hides account metrics until its data is available; missing positions are not presented as zero holdings. Regression tests cover response decoding and render the real React Overview without data to reject invented zero counts. |
| Tests, load and cleanup | Run the Python and JavaScript suites, production build, and 330-request load test on Windows. The load check exercises the local and viewing listeners with 16 clients and verifies one worker, WAL and SQLite integrity. It removes its temporary dataset. |

Remote verification needs the private route, Access policy with MFA, local team URL and audience settings, and an enrolled viewer's device. Complete annual rankings and attribution need point-in-time SPY constituents and adjusted constituent prices for the requested dates. Older intraday history requires a suitably licensed dataset.
