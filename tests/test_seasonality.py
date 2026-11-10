import math

import pytest

from backend.seasonality import indicators, seasonal_windows, yearly_curves


def test_seasonal_windows_align_calendar_starts_and_require_complete_samples():
    sessions = [
        "2021-01-04", "2021-01-05", "2021-01-06",
        "2022-01-03", "2022-01-04", "2022-01-05",
        "2023-01-03", "2023-01-04", "2023-01-05",
        "2024-01-02", "2024-01-03", "2024-01-04",
        "2025-01-02", "2025-01-03", "2025-01-06",
    ]
    prices = [100, 100, 101, 100, 100, 102, 100, 100, 103, 100, None, 104, 100, 100, 150]
    bars = {stamp: {"close": price} for stamp, price in zip(sessions, prices) if price is not None}
    first = seasonal_windows(sessions, bars, range(2021, 2025), 2)[0]
    assert first["start"] == "01-01"
    assert first["samples"] == 3 and first["positive_pct"] == 100
    assert first["mean_pct"] == pytest.approx(2)
    assert first["stddev_pct"] == pytest.approx(1)
    assert first["t_stat"] == pytest.approx(2 * math.sqrt(3))
    assert (first["example_start"], first["example_end"]) == ("2023-01-03", "2023-01-05")
    curves = yearly_curves(sessions, bars, 2025)
    partial = curves[-2]
    assert partial["sessions"] == 2 and partial["expected_sessions"] == 3
    assert partial["points"][1] == ["01-03", None]
    assert curves[-1]["ytd"] is True and curves[-1]["points"][-1][1] == pytest.approx(50)


def test_technical_indicators_use_latest_consecutive_daily_bars():
    sessions = [f"2025-01-{day:02d}" for day in range(1, 17)]
    bars = {stamp: {"close": 100 + index, "high": 101 + index, "low": 99 + index}
            for index, stamp in enumerate(sessions)}
    result = indicators(sessions, bars)
    assert result["as_of"] == sessions[-1] and result["rsi"] == 100
    assert result["stochastic_k"] == pytest.approx(100 * 14 / 15)
    assert result["stochastic_d"] == pytest.approx(100 * 14 / 15)
    del bars[sessions[-2]]
    assert indicators(sessions, bars) == {"as_of": sessions[-1], "rsi": None, "stochastic_k": None, "stochastic_d": None}
