import numpy as np
import pandas as pd

from stocks_tracker.backtest.metrics import benjamini_hochberg, mean_p_value
from stocks_tracker.backtest.engine import forward_returns


def test_mean_p_value_is_small_for_clear_positive_mean():
    values = np.array([0.10] * 120)
    p = mean_p_value(values)
    assert p < 1e-10


def test_benjamini_hochberg_controls_adjusted_values_monotonically():
    raw = np.array([0.001, 0.01, 0.02, 0.20])
    q = benjamini_hochberg(raw)
    assert np.all(q >= raw)
    assert np.all(np.diff(q) >= 0)
    assert q[0] <= 0.004


def test_forward_returns_are_invariant_to_future_price_changes():
    base = pd.DataFrame({
        "ticker": ["AAA"] * 6,
        "date": pd.date_range("2020-01-01", periods=6),
        "adj_close": [100, 101, 102, 103, 104, 105],
    })
    changed = base.copy()
    changed.loc[changed["date"] > pd.Timestamp("2020-01-03"), "adj_close"] = [999, 1, 999]

    a = forward_returns(base, horizons=(1,)).loc[lambda x: x.date <= pd.Timestamp("2020-01-02")]
    b = forward_returns(changed, horizons=(1,)).loc[lambda x: x.date <= pd.Timestamp("2020-01-02")]
    pd.testing.assert_frame_equal(a.reset_index(drop=True), b.reset_index(drop=True))
