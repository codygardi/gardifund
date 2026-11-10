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


def test_market_and_etf_rankings_keep_their_universes_and_price_basis(tmp_path):
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
    etfs = performance.get("etfs", 2024, month=2, top_n=1)
    assert etfs["incompatible"] == 1 and etfs["missing"] == 4
    assert etfs["basis"] == "unverified" and etfs["eligible"] == 12
    assert [row["symbol"] for row in etfs["rows"]] == ["XLE"]
    assert len(etfs["series"]) == 1


def test_sector_ranking_uses_dated_weights_and_never_fills_missing_stock_prices(tmp_path):
    db, market, performance = workspace(tmp_path, "2024-02-06T16:00:00+00:00")
    daily(market, "XLE", {"2024-02-01": 100, "2024-02-05": 130})
    assert performance.get("sectors", 2024, month=2)["rows"] == []
    members = [{"symbol": "AAA", "weight": .2, "sector": "Energy"},
               {"symbol": "BBB", "weight": .6, "sector": "Energy"},
               {"symbol": "CCC", "weight": .2, "sector": "Information Technology"}]
    store_holdings(db, "2024-02-01", members, "period start")
    store_holdings(db, "2024-02-02", [{"symbol": "DDD", "weight": 1, "sector": "Energy"}], "later snapshot")
    adjusted = "split_dividend_adjusted"
    daily(market, "AAA", {"2024-02-01": 10, "2024-02-02": 11, "2024-02-05": 12}, adjusted)
    daily(market, "BBB", {"2024-02-01": 10, "2024-02-05": 9})
    daily(market, "CCC", {"2024-02-01": 10, "2024-02-02": 10, "2024-02-05": 10.5}, adjusted)
    partial = performance.get("sectors", 2024, month=2)
    assert [row["name"] for row in partial["rows"]] == ["Information Technology"]
    assert partial["missing"] == 10 and not partial["complete"]

    daily(market, "BBB", {"2024-02-01": 10, "2024-02-05": 9}, adjusted, replace=True)
    result = performance.get("sectors", 2024, month=2)
    assert [row["name"] for row in result["rows"]] == ["Information Technology", "Energy"]
    energy = result["rows"][1]
    assert energy["return_pct"] == pytest.approx(-2.5)
    assert energy["weight"] == pytest.approx(.8) and energy["constituent_count"] == 2
    assert result["basis"] == adjusted and result["missing"] == 9
    assert result["series"][1]["bars"] == [{"time": "2024-02-01", "close": 100},
                                          {"time": "2024-02-02", "close": None},
                                          {"time": "2024-02-05", "close": pytest.approx(97.5)}]
    assert performance.get("etfs", 2024, month=2)["rows"][0]["return_pct"] == pytest.approx(30)

    members[0]["weight"], members[1]["weight"] = .6, .2
    store_holdings(db, "2024-02-01", members, "revised weights")
    result = performance.get("sectors", 2024, month=2, top_n=1)
    assert result["rows"][0]["name"] == "Energy"
    assert result["rows"][0]["return_pct"] == pytest.approx(12.5)


def test_sector_windows_rank_mean_returns_and_skip_gaps_and_unfinished_sessions(tmp_path):
    _, market, performance = workspace(tmp_path, "2024-01-10T16:00:00+00:00")
    sessions = ("2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09", "2024-01-10")
    daily(market, "XLK", dict(zip(sessions, (100, 100, 110, 110, 121, 121, 1000))))
    daily(market, "XLE", dict(zip(sessions, (100, 100, 200, 100, 50, 100, 100))))
    daily(market, "XLP", {sessions[0]: 1, sessions[2]: 100, sessions[3]: 106, sessions[4]: 104, sessions[6]: 900})
    daily(market, "XLF", dict(zip(sessions, (1, 1, 100, 100, 1000, 1000))), "split_dividend_adjusted")
    daily(market, "SPY", dict(zip(sessions, (1, 1, 100, 100, 1000, 1000))))

    ranked = performance.sector_windows(2, 1, 3)
    assert ranked["end"] == "2024-01-09" and ranked["basis"] == "unverified"
    assert ranked["ranked"] == 3 and ranked["incompatible"] == 1 and ranked["missing"] == 7
    assert [row["symbol"] for row in ranked["rows"]] == ["XLK", "XLE", "XLP"]
    assert [row["return_pct"] for row in ranked["rows"]] == pytest.approx([10, 6.25, 4])
    assert [row["window_count"] for row in ranked["rows"]] == [4, 4, 1]
    assert [row["profitable_window_pct"] for row in ranked["rows"]] == [100, 25, 100]
    assert (ranked["rows"][-1]["window_start"], ranked["rows"][-1]["window_end"]) == (sessions[2], sessions[4])
    assert performance.sector_windows(2, 1, 1)["rows"] == ranked["rows"][:1]
    assert len(performance.cache) == 1
    daily(market, "XLE", {sessions[4]: 120})
    assert performance.sector_windows(2, 1, 1)["rows"][0]["symbol"] == "XLE"


def test_sector_window_bounds_and_leap_year_lookback(tmp_path):
    _, _, performance = workspace(tmp_path, "2024-02-29T16:00:00+00:00")
    trailing = performance.sector_windows(5000, 1, 1)
    assert trailing["start"] == "2023-02-28" and trailing["end"] == "2024-02-28"
    assert trailing["rows"] == [] and trailing["possible_windows"] == 0
    retained = performance.sector_windows(5000, 15, 1)
    assert retained["start"] == "2010-01-04" and retained["end"] == "2024-02-28"
    with pytest.raises(ValueError):
        performance.sector_windows(10, 16, 3)
    with pytest.raises(ValueError):
        performance.get("etfs", 2009)
