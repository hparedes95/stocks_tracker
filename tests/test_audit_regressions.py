"""Regression tests for bugs found by the aggressive audit."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stocks_tracker.core.scoring import zscore_by_group
from stocks_tracker.trading.brokers.base import OrderRequest
from stocks_tracker.trading.brokers.simulated import SimulatedBroker, _Holding


def test_small_group_fallback_uses_full_universe():
    """A tiny peer group must be scored against the whole universe.

    Winsorization is switched off on purpose. With the default (0.02, 0.98)
    the expected value could only be written by re-running `winsorize`, and a
    test that recomputes the implementation confirms nothing. Off, the number
    is plain arithmetic over the 23 rows, and the property under test —which
    population the small group is compared against— does not depend on it.
    """
    df = pd.DataFrame({
        "sector": ["small"] * 3 + ["large"] * 20,
        "metric": [1.0, 2.0, 3.0] + list(np.linspace(10.0, 29.0, 20)),
    })
    result = zscore_by_group(df, "metric", "sector", winsor=(0.0, 1.0),
                             min_group=8, robust=False)
    expected = (3.0 - df["metric"].mean()) / df["metric"].std(ddof=0)
    assert result.iloc[2] == pytest.approx(expected)
    # El signo es lo que separa las dos lecturas: dentro de su propio grupo
    # (1, 2, 3) el valor 3.0 es el MAXIMO y saldria positivo. Contra el
    # universo entero esta cerca del fondo, y sale negativo.
    assert result.iloc[2] < 0


def _bars(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(
        rows,
        columns=["date", "ticker", "open", "high", "low", "close"],
    ).assign(volume=1_000_000)


def test_pending_sells_are_reserved_and_cannot_oversell():
    broker = SimulatedBroker(
        _bars([
            ("2024-01-02", "AAA", 100, 101, 99, 100),
            ("2024-01-03", "AAA", 100, 101, 99, 100),
        ]),
        initial_cash=1000,
        slippage_bps=0,
    )
    broker._holdings["AAA"] = _Holding(qty=10, avg_entry_price=100)
    broker.submit_order(OrderRequest(symbol="AAA", side="sell", qty=6, client_order_id="s1"))
    with pytest.raises(Exception, match="disponibles"):
        broker.submit_order(OrderRequest(symbol="AAA", side="sell", qty=5, client_order_id="s2"))


def test_partial_intraday_sales_count_one_day_trade():
    broker = SimulatedBroker(
        _bars([
            ("2024-01-02", "AAA", 100, 101, 99, 100),
            ("2024-01-03", "AAA", 100, 101, 99, 100),
        ]),
        initial_cash=1000,
        slippage_bps=0,
    )
    broker._holdings["AAA"] = _Holding(qty=10, avg_entry_price=100)
    broker._opened_today.setdefault(broker.current_date, set()).add("AAA")
    broker.submit_order(OrderRequest(symbol="AAA", side="sell", qty=5, client_order_id="s1"))
    broker._process_pending()
    assert broker.daytrade_count() == 0
    broker.submit_order(OrderRequest(symbol="AAA", side="sell", qty=5, client_order_id="s2"))
    broker._process_pending()
    assert broker.daytrade_count() == 1
