"""The only component that knows the Tradier credential or base URL."""
import re
import threading
import time
import httpx


def items(value):
    if value is None or value == "null":
        return []
    return value if isinstance(value, list) else [value]


class ProviderError(RuntimeError):
    pass


def provider_symbol(symbol):
    # Nasdaq's ACT directory uses '$' for preferred shares; Tradier's
    # symbol lookup returns those shares with an apostrophe instead.
    return symbol.replace(".", "/").replace("$", "'")


def local_symbol(symbol):
    return symbol.replace("/", ".").replace("'", "$")


class Tradier:
    def __init__(self, key: str, transport=None, market_interval=0.7):
        self.key = key
        self.transport = transport
        self.market_interval = market_interval
        self._market_lock = threading.Lock()
        self._last_market_request = 0.0
        self._client = httpx.Client(base_url="https://api.tradier.com/v1", timeout=30, transport=transport,
                                    headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})

    def close(self):
        self._client.close()

    def get(self, path: str, params=None):
        if not self.key:
            raise ProviderError("Tradier production API key is not configured")
        try:
            if path.startswith("/markets/"):
                # Leave capacity below Tradier's published per-minute market-data limit.
                # The one worker owns the adapter; the lock also protects explicit calls.
                with self._market_lock:
                    remaining = self.market_interval - (time.monotonic() - self._last_market_request)
                    if remaining > 0:
                        time.sleep(remaining)
                    self._last_market_request = time.monotonic()
                    response = self._client.get(path, params=params)
            else:
                response = self._client.get(path, params=params)
            if response.status_code in {400, 429} and "Quota Violation" in response.text:
                raise ProviderError("Tradier market-data quota reached; the job will retry")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"Tradier returned HTTP {exc.response.status_code}") from None
        except (httpx.HTTPError, ValueError):
            raise ProviderError("Tradier could not be reached or returned invalid data") from None

    def market_clock(self):
        clock = self.get("/markets/clock").get("clock")
        if not isinstance(clock, dict) or clock.get("state") not in {"premarket", "open", "postmarket", "closed"}:
            raise ProviderError("Tradier returned an invalid market clock")
        return clock

    def accounts(self, checkpoint=None):
        def account_get(path, params=None):
            if checkpoint:
                checkpoint()
            return self.get(path, params)

        profile = account_get("/user/profile").get("profile") or {}
        snapshots = []
        for account in items(profile.get("account")):
            account_id = str(account.get("account_number", ""))
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", account_id):
                raise ProviderError("Tradier returned an invalid account identifier")
            root = f"/accounts/{account_id}"
            balance = account_get(root + "/balances").get("balances") or {}
            if not isinstance(balance, dict):
                raise ProviderError("Tradier returned an invalid balance response")
            if "balances" in balance and isinstance(balance["balances"], dict):
                balance = balance["balances"]
            positions = account_get(root + "/positions").get("positions") or {}
            orders = account_get(root + "/orders", {"limit": 1000, "page": 1}).get("orders") or {}
            if positions == "null":
                positions = {}
            if orders == "null":
                orders = {}
            if not isinstance(positions, dict) or not isinstance(orders, dict):
                raise ProviderError("Tradier returned an invalid positions or orders response")
            position_list = items(positions.get("position"))
            order_list = items(orders.get("order"))
            try:
                total_pages = int(orders.get("total_pages") or 1)
            except (TypeError, ValueError):
                raise ProviderError("Tradier returned invalid order pagination") from None
            if not 1 <= total_pages <= 100:
                raise ProviderError("Tradier order pagination exceeds the safety limit")
            for page in range(2, total_pages + 1):
                extra = account_get(root + "/orders", {"limit": 1000, "page": page}).get("orders") or {}
                if not isinstance(extra, dict):
                    raise ProviderError("Tradier returned invalid order pagination")
                order_list.extend(items(extra.get("order")))
            for record in position_list + order_list:
                if isinstance(record, dict) and isinstance(record.get("symbol"), str):
                    record["symbol"] = local_symbol(record["symbol"])
            snapshots.append({"account_id": account_id, "classification": account.get("classification"),
                              "balance": balance, "positions": position_list, "orders": order_list})
        return snapshots

    def quotes(self, symbols):
        result = []
        for start in range(0, len(symbols), 100):
            batch = symbols[start:start + 100]
            body = self.get("/markets/quotes", {"symbols": ",".join(provider_symbol(s) for s in batch), "greeks": "false"})
            container = body.get("quotes") or {}
            if container == "null":
                container = {}
            if not isinstance(container, dict):
                raise ProviderError("Tradier returned an invalid quote response")
            for quote in items(container.get("quote")):
                if isinstance(quote, dict) and isinstance(quote.get("symbol"), str):
                    quote["symbol"] = local_symbol(quote["symbol"])
                    result.append(quote)
        return result

    def daily(self, symbol, start, end):
        body = self.get("/markets/history", {"symbol": provider_symbol(symbol), "interval": "daily", "start": start, "end": end})
        return items((body.get("history") or {}).get("day"))
