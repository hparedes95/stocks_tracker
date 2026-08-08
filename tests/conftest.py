"""Fixtures compartidas. Ningun test toca la red ni el almacen real."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def trending_series() -> pd.Series:
    """Serie con tendencia alcista clara y ruido moderado."""
    rng = np.random.default_rng(42)
    n = 400
    trend = np.linspace(100, 180, n)
    noise = rng.normal(0, 2.0, n).cumsum() * 0.3
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.Series(trend + noise, index=idx, name="adj_close")


@pytest.fixture
def ohlcv(trending_series: pd.Series) -> pd.DataFrame:
    """OHLCV coherente construido alrededor de la serie de cierre."""
    rng = np.random.default_rng(7)
    close = trending_series
    spread = np.abs(rng.normal(0, 0.01, len(close))) + 0.003
    high = close * (1 + spread)
    low = close * (1 - spread)
    open_ = close.shift(1).fillna(close.iloc[0])
    open_ = open_.clip(lower=low, upper=high)
    volume = pd.Series(
        rng.integers(1_000_000, 5_000_000, len(close)), index=close.index
    ).astype(float)
    return pd.DataFrame(
        {
            "open": open_, "high": high, "low": low, "close": close,
            "adj_close": close, "volume": volume,
        }
    )


@pytest.fixture
def scoring_frame() -> pd.DataFrame:
    """Universo sintetico con dos sectores y fundamentales parcialmente vacios."""
    rng = np.random.default_rng(11)
    n = 40
    sectors = ["Tecnologia"] * 20 + ["Banca"] * 20
    return pd.DataFrame(
        {
            "ticker": [f"T{i:03d}" for i in range(n)],
            "gics_sector": sectors,
            "trailing_pe": np.concatenate(
                [rng.normal(30, 6, 20), rng.normal(11, 2, 20)]
            ),
            "price_to_book": rng.uniform(0.5, 8, n),
            "roe": rng.normal(0.15, 0.06, n),
            "profit_margin": rng.normal(0.12, 0.05, n),
            "mom_12_1": rng.normal(0.10, 0.20, n),
            "roc_6m": rng.normal(0.05, 0.15, n),
            "rs_vs_bench_3m": rng.normal(0.0, 0.08, n),
            "realized_vol_252": rng.uniform(0.15, 0.55, n),
            "max_dd_1y": -rng.uniform(0.05, 0.45, n),
            "atr_pct": rng.uniform(0.01, 0.05, n),
            "dividend_yield": rng.uniform(0.0, 0.06, n),
            "payout_ratio": rng.uniform(0.1, 1.2, n),
            "revenue_growth_yoy": rng.normal(0.08, 0.12, n),
            "earnings_growth_yoy": rng.normal(0.10, 0.20, n),
            "net_debt_to_ebitda": rng.uniform(-1, 5, n),
            "operating_margin": rng.normal(0.18, 0.07, n),
            "ev_to_ebitda": rng.uniform(4, 30, n),
            "price_to_sales": rng.uniform(0.4, 12, n),
            "fcf_yield": rng.normal(0.04, 0.03, n),
            "technical_raw": rng.normal(0.0, 0.4, n),
        }
    )
