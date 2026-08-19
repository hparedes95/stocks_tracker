import duckdb
import numpy as np
import pandas as pd
import pytest

from stocks_tracker.backtest import metrics as mx
from stocks_tracker.core.db import upsert_df
from stocks_tracker.core.safe_eval import UnsafeExpressionError, compile_condition, evaluate
from stocks_tracker.providers.base import normalize_ohlcv


def test_normalize_ohlcv_discards_invalid_dates_without_failing():
    frame = pd.DataFrame(
        [
            {"ticker": "AAA", "date": "2026-08-18", "open": 10, "high": 11, "low": 9, "close": 10.5, "adj_close": 10.5, "volume": 100},
            {"ticker": "AAA", "date": "not-a-date", "open": 10, "high": 11, "low": 9, "close": 10.5, "adj_close": 10.5, "volume": 100},
        ]
    )
    result = normalize_ohlcv(frame, "test")
    assert len(result) == 1
    assert result.iloc[0]["date"].isoformat() == "2026-08-18"


def test_safe_eval_rejects_exponentiation():
    with pytest.raises(UnsafeExpressionError):
        compile_condition("10 ** 1000000")
    assert evaluate("10 ** 1000000", {}) is False


def test_safe_eval_still_allows_basic_arithmetic():
    assert evaluate("price > 10 and volume >= 100", {"price": 12, "volume": 100}) is True


def test_upsert_rejects_duplicate_keys_before_writing():
    conn = duckdb.connect(":memory:")
    try:
        conn.execute("CREATE TABLE prices (ticker VARCHAR, date DATE, close DOUBLE)")
        payload = pd.DataFrame(
            [
                {"ticker": "AAA", "date": "2026-08-18", "close": 10.0},
                {"ticker": "AAA", "date": "2026-08-18", "close": 11.0},
            ]
        )
        with pytest.raises(ValueError, match="claves duplicadas"):
            upsert_df(conn, "prices", payload, ["ticker", "date"])
        assert conn.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 0
    finally:
        conn.close()


def test_hac_t_statistic_is_lower_than_iid_t_for_autocorrelated_returns():
    rng = np.random.default_rng(7)
    shocks = rng.normal(0.0005, 0.01, 600)
    values = np.empty_like(shocks)
    values[0] = shocks[0]
    for i in range(1, len(values)):
        values[i] = 0.75 * values[i - 1] + shocks[i]

    iid_t = values.mean() / (values.std(ddof=1) / np.sqrt(len(values)))
    hac_t = mx.hac_t_statistic(values)
    assert np.isfinite(hac_t)
    assert abs(hac_t) < abs(iid_t)
