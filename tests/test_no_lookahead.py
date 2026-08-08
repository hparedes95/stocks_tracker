"""El test que mas bugs caros evita.

Un indicador con look-ahead bias produce backtests preciosos y perdidas reales.
La comprobacion es directa: se altera el futuro y se verifica que el pasado no
se mueve ni un decimal.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stocks_tracker.core import indicators as ind

# Punto de corte: todo lo posterior se perturba, todo lo anterior debe quedar igual.
CUTOFF = 250


def _perturb_future(series: pd.Series, factor: float = 3.0) -> pd.Series:
    out = series.copy()
    out.iloc[CUTOFF:] = out.iloc[CUTOFF:] * factor
    return out


@pytest.mark.parametrize(
    "func",
    [
        lambda s: ind.sma(s, 20),
        lambda s: ind.sma(s, 200),
        lambda s: ind.ema(s, 12),
        lambda s: ind.rsi(s, 14),
        lambda s: ind.macd(s)["macd_hist"],
        lambda s: ind.bollinger(s)["bb_pctb"],
        lambda s: ind.roc(s, 21),
        lambda s: ind.momentum_12_1(s),
        lambda s: ind.realized_volatility(s, 60),
        lambda s: ind.distance_to_high(s),
        lambda s: ind.drawdown(s),
    ],
    ids=[
        "sma20", "sma200", "ema12", "rsi14", "macd_hist", "bb_pctb",
        "roc21", "mom_12_1", "vol60", "dist_52w_high", "drawdown",
    ],
)
def test_indicator_does_not_use_future(trending_series, func):
    baseline = func(trending_series)
    perturbed = func(_perturb_future(trending_series))

    past_base = baseline.iloc[:CUTOFF]
    past_pert = perturbed.iloc[:CUTOFF]

    pd.testing.assert_series_equal(
        past_base, past_pert, check_names=False,
        obj="valores pasados tras alterar el futuro",
    )


def test_compute_all_does_not_use_future(ohlcv):
    """Comprobacion sobre el ensamblado completo, no solo funcion a funcion."""
    baseline = ind.compute_all(ohlcv)

    perturbed_input = ohlcv.copy()
    for col in ("open", "high", "low", "close", "adj_close"):
        perturbed_input.loc[perturbed_input.index[CUTOFF:], col] *= 3.0
    perturbed = ind.compute_all(perturbed_input)

    numeric_cols = [
        c for c in baseline.columns
        if baseline[c].dtype.kind in "fc" and c != "close"
    ]
    for col in numeric_cols:
        base_past = baseline[col].iloc[:CUTOFF]
        pert_past = perturbed[col].iloc[:CUTOFF]
        assert np.allclose(
            base_past.fillna(-999.0), pert_past.fillna(-999.0), equal_nan=True
        ), f"La columna '{col}' cambia en el pasado al alterar el futuro"


def test_forward_shift_would_be_caught():
    """Contraprueba: un indicador con look-ahead deliberado debe fallar el test.

    Si esta comprobacion pasara, el test de arriba no estaria midiendo nada.
    """
    series = pd.Series(np.arange(100, dtype=float))
    leaky = series.shift(-1)  # mira un dia hacia delante

    perturbed = series.copy()
    perturbed.iloc[50:] *= 2
    leaky_perturbed = perturbed.shift(-1)

    assert leaky.iloc[49] != leaky_perturbed.iloc[49]
