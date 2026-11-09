from datetime import datetime

import pytest

from backend.db import Database
from backend.market import MarketStore
from backend.performance import PeriodPerformance
from backend.services import store_holdings


def workspace(tmp_path, clock):
    db = Database(tmp_path / "db.sqlite3")
    market = MarketStore(db, tmp_path)
    return db, market, PeriodPerformance(db, market, lambda: datetime.fromisoformat(clock).timestamp())


def daily(market, symbol, closes, adjustment="unverified", replace=False):
    bars = [{"time": day, "open": close, "high": close, "low": close, "close": close, "volume": 1}
            for day, close in closes.items()]
    market.write(symbol, "1d", bars, "declared source", adjustment, replace=replace)


def test_annual_stock_ranking_requires_dated_members_and_adjusted_endpoints(tmp_path):
    db, market, performance = workspace(tmp_path, "2024-01-04T22:00:00+00:00")
    assert performance.get("stocks", 2024)["rows"] == []
    members = [{"symbol": "AAA", "name": "A", "weight": .5, "sector": "Energy"},
               {"symbol": "BBB", "name": "B", "weight": .5, "sector": "Energy"}]
    store_holdings(db, "2024-01-02", members, "dated")
    store_holdings(db, "2024-01-03", [{"symbol": "CCC", "weight": 1, "sector": "Energy"}], "later snapshot")
    daily(market, "AAA", {"2024-01-02": 10, "2024-01-04": 12}, "split_dividend_adjusted")
    daily(market, "BBB", {"2024-01-02": 10, "2024-01-04": 15})

    partial = performance.get("stocks", 2024)
    assert partial["complete"] is False and partial["missing"] == 1
    assert [row["symbol"] for row in partial["rows"]] == ["AAA"]
    assert partial["rows"][0]["return_pct"] == pytest.approx(20)
    assert "CCC" not in str(partial) and "declared source" not in str(partial)

    daily(market, "BBB", {"2024-01-02": 10, "2024-01-04": 15}, "split_dividend_adjusted", replace=True)
    full = performance.get("stocks", 2024, top_n=1)
    assert full["complete"] is True and full["missing"] == 0
    assert [row["symbol"] for row in full["rows"]] == ["BBB"]
    assert len(full["series"]) == 1 and full["series"][0]["bars"][-1]["close"] == 15
    assert len(performance.cache) == 2
    assert performance.get("stocks", 2024, month=1)["month"] == 1
    assert performance.get("stocks", 2024)["month"] is None
    with pytest.raises(ValueError):
        performance.get("stocks", 2024, top_n=6)
    with pytest.raises(ValueError):
        performance.get("stocks", 2025)


def test_monthly_stocks_use_the_month_snapshot_and_last_completed_session(tmp_path):
    db, market, performance = workspace(tmp_path, "2024-02-06T16:00:00+00:00")
    store_holdings(db, "2024-01-02", [{"symbol": "AAA", "weight": 1, "sector": "Energy"}], "January")
    store_holdings(db, "2024-02-01", [{"symbol": "BBB", "weight": 1, "sector": "Energy"}], "February")
    daily(market, "AAA", {"2024-01-02": 10, "2024-02-05": 12}, "split_dividend_adjusted")
    daily(market, "BBB", {"2024-02-01": 20, "2024-02-03": 80, "2024-02-05": 25, "2024-02-06": 40},
          "split_dividend_adjusted")

    annual = performance.get("stocks", 2024)
    february = performance.get("stocks", 2024, month=2)
    assert [row["symbol"] for row in annual["rows"]] == ["AAA"]
    assert [row["symbol"] for row in february["rows"]] == ["BBB"]
    assert (february["start"], february["end"]) == ("2024-02-01", "2024-02-05")
    assert february["rows"][0]["return_pct"] == pytest.approx(25)
    assert [bar["time"] for bar in february["series"][0]["bars"]] == ["2024-02-01", "2024-02-05"]
    with pytest.raises(ValueError):
        performance.get("stocks", 2024, month=3)


def test_market_sector_and_etf_rankings_keep_their_universes_and_price_basis(tmp_path):
    _, market, performance = workspace(tmp_path, "2024-02-06T16:00:00+00:00")
    for symbol, end in {"SPY": 105, "XLE": 130, "XLK": 120, "XLI": 115,
                        "XLV": 112, "XLY": 110, "XLP": 108}.items():
        daily(market, symbol, {"2024-02-01": 100, "2024-02-05": end})
    daily(market, "XLF", {"2024-02-01": 100, "2024-02-05": 200}, "split_dividend_adjusted")
    daily(market, "XLU", {"2024-02-01": 100, "2024-02-02": 110})

    benchmark = performance.get("market", 2024, month=2)
    assert [row["symbol"] for row in benchmark["rows"]] == ["SPY"]
    assert benchmark["rows"][0]["return_pct"] == pytest.approx(5)
    assert benchmark["complete"]
    sectors = performance.get("sectors", 2024, month=2)
    etfs = performance.get("etfs", 2024, month=2, top_n=1)
    assert [row["symbol"] for row in sectors["rows"]] == ["XLE", "XLK", "XLI", "XLV", "XLY"]
    assert sectors["incompatible"] == 1 and sectors["missing"] == 4
    assert sectors["basis"] == etfs["basis"] == "unverified"
    assert etfs["eligible"] == sectors["eligible"] + 1
    assert [row["symbol"] for row in etfs["rows"]] == ["XLE"]
    assert len(etfs["series"]) == 1
