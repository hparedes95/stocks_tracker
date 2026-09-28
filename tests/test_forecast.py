"""Guardas de escenarios: tiempo, calidad, muestra y límites de interpretación."""
import numpy as np
import pandas as pd
import pytest

from stocks_tracker.core.forecast import scenarios


def history(n=2500):
    return pd.DataFrame({
        "date": pd.bdate_range(end="2026-09-24", periods=n),
        "adj_close": 100 * np.exp(np.arange(n) * .0002),
        "source": "yfinance",
    })


@pytest.mark.parametrize("horizon", [21, 63, 126])
def test_episodes_are_non_overlapping_and_never_look_ahead(horizon):
    prices = history()
    result = scenarios(prices, horizon=horizon, as_of="2026-09-24")
    assert not result.reason
    episodes = result.episodes.sort_values("entry_date")
    assert (episodes.entry_date > episodes.signal_date).all()
    assert (episodes.exit_date <= result.as_of).all()
    assert (episodes.entry_date.iloc[1:].to_numpy() >
            episodes.exit_date.iloc[:-1].to_numpy()).all()
    assert result.low <= result.median <= result.high
    for row in episodes.itertuples():
        start = prices.loc[prices.date == row.entry_date, "adj_close"].iloc[0]
        end = prices.loc[prices.date == row.exit_date, "adj_close"].iloc[0]
        assert end / start - 1 == pytest.approx(
            episodes.loc[episodes.entry_date == row.entry_date, "return"].iloc[0])


def test_future_prices_cannot_change_a_past_estimate():
    prices = history()
    cutoff = prices.date.iloc[-200]
    before = scenarios(prices, as_of=cutoff)
    prices.loc[prices.date > cutoff, "adj_close"] *= 1000
    after = scenarios(prices, as_of=cutoff)
    assert before.median == after.median
    pd.testing.assert_frame_equal(before.episodes, after.episodes)


def test_row_order_is_irrelevant():
    prices = history()
    a = scenarios(prices, as_of="2026-09-24")
    b = scenarios(prices.sample(frac=1, random_state=2), as_of="2026-09-24")
    assert a.median == b.median
    pd.testing.assert_frame_equal(a.episodes, b.episodes)


@pytest.mark.parametrize("problem", [
    "synthetic", "mixed", "duplicate", "nan", "infinite", "zero",
    "negative", "source_missing", "blank_source", "date_invalid", "column_missing",
])
def test_unreliable_data_has_no_numeric_forecast(problem):
    prices = history()
    if problem == "synthetic":
        prices["source"] = "synthetic"
    elif problem == "mixed":
        prices.loc[0, "source"] = "stooq"
    elif problem == "duplicate":
        prices = pd.concat([prices, prices.tail(1)])
    elif problem in ("nan", "infinite", "zero", "negative"):
        prices.loc[0, "adj_close"] = {
            "nan": np.nan, "infinite": np.inf, "zero": 0, "negative": -10}[problem]
    elif problem == "source_missing":
        prices.loc[0, "source"] = None
    elif problem == "blank_source":
        prices["source"] = ""
    elif problem == "date_invalid":
        prices.loc[0, "date"] = pd.NaT
    else:
        prices = prices.drop(columns="adj_close")
    result = scenarios(prices, as_of="2026-09-24")
    assert result.reason
    assert result.median is None
    assert result.historical_up_frequency is None


def test_stale_and_short_series_abstain():
    assert scenarios(history(), as_of="2026-10-15").median is None
    result = scenarios(history(600), as_of="2026-09-24")
    assert result.observations < 8
    assert result.median is None
    assert result.reason


def test_small_sample_does_not_show_a_frequency():
    result = scenarios(history(900), as_of="2026-09-24")
    assert result.median is not None
    assert 8 <= result.observations < 20
    assert result.historical_up_frequency is None


def test_unsupported_horizon_is_rejected():
    with pytest.raises(ValueError):
        scenarios(history(), horizon=-1)


def test_empty_data_is_not_a_prediction():
    assert scenarios(pd.DataFrame()).median is None
