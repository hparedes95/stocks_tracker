"""Tests de los indicadores tecnicos."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stocks_tracker.core import indicators as ind


def test_sma_leaves_leading_nans(trending_series):
    """Los primeros n-1 valores deben ser NaN, no una media de dos datos."""
    result = ind.sma(trending_series, 20)
    assert result.iloc[:19].isna().all()
    assert result.iloc[19:].notna().all()
    # Comprobacion directa contra la media de la ventana.
    expected = trending_series.iloc[:20].mean()
    assert result.iloc[19] == pytest.approx(expected)


def test_rsi_bounds_and_known_case():
    """El RSI vive entre 0 y 100; una serie solo alcista lo lleva a 100."""
    rising = pd.Series(np.arange(100, 160, dtype=float))
    result = ind.rsi(rising, 14)
    valid = result.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()
    # Sin ninguna bajada, la perdida media es cero y el RSI es 100 por definicion.
    assert valid.iloc[-1] == pytest.approx(100.0)


def test_rsi_uses_wilder_not_sma():
    """Wilder usa alpha = 1/period, no la EMA estandar 2/(period+1)."""
    rng = np.random.default_rng(3)
    series = pd.Series(100 + rng.normal(0, 1, 200).cumsum())

    delta = series.diff()
    gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
    wilder_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    wilder_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    expected = 100 - 100 / (1 + wilder_gain / wilder_loss)

    result = ind.rsi(series, 14)
    pd.testing.assert_series_equal(
        result.dropna(), expected.dropna(), check_names=False, rtol=1e-9
    )


def test_macd_histogram_is_line_minus_signal(trending_series):
    macd = ind.macd(trending_series)
    diff = macd["macd"] - macd["macd_signal"]
    pd.testing.assert_series_equal(
        macd["macd_hist"].dropna(), diff.dropna(), check_names=False
    )


def test_atr_is_positive(ohlcv):
    result = ind.atr(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14).dropna()
    assert (result > 0).all()


def test_adx_in_range(ohlcv):
    result = ind.adx(ohlcv["high"], ohlcv["low"], ohlcv["close"], 14)
    adx = result["adx14"].dropna()
    assert (adx >= 0).all() and (adx <= 100).all()


def test_momentum_12_1_excludes_last_month(trending_series):
    """El momentum 12-1 debe ignorar el ultimo mes.

    Se altera solo el ultimo mes de la serie: el valor de la fecha final no
    puede cambiar, porque esa ventana esta excluida por construccion.
    """
    baseline = ind.momentum_12_1(trending_series)

    modified = trending_series.copy()
    modified.iloc[-ind.SESSIONS_MONTH:] *= 1.5

    result = ind.momentum_12_1(modified)
    assert result.iloc[-1] == pytest.approx(baseline.iloc[-1])


def test_drawdown_is_non_positive(trending_series):
    result = ind.drawdown(trending_series).dropna()
    assert (result <= 1e-12).all()


def test_consecutive_true_counts_streaks():
    flags = pd.Series([False, True, True, True, False, True, True])
    result = ind.consecutive_true(flags)
    assert result.tolist() == [0, 1, 2, 3, 0, 1, 2]


def test_relative_volume_around_one(ohlcv):
    result = ind.relative_volume(ohlcv["volume"], 20).dropna()
    # Con volumen aleatorio uniforme la media del ratio ronda 1.
    assert 0.7 < result.mean() < 1.4


def test_compute_all_produces_expected_columns(ohlcv):
    result = ind.compute_all(ohlcv)
    required = {
        "sma20", "sma50", "sma200", "macd", "rsi14", "atr14", "bb_upper",
        "realized_vol_252", "mom_12_1", "dist_52w_high", "drawdown",
        "above_sma200", "golden_cross", "days_above_sma200",
    }
    assert required.issubset(result.columns)
    assert len(result) == len(ohlcv)


def test_compute_all_handles_empty_frame():
    assert ind.compute_all(pd.DataFrame()).empty


def test_the_yearly_high_needs_a_full_year_of_sessions():
    """Con media ventana, el numero se llama "distancia al maximo anual" y es
    la distancia al maximo de seis meses: siempre MAS CERCA de cero que la
    verdad. Un valor que se hundio hace ocho meses aparecia "en maximos".

    No se queda en la ficha: alimenta la lista de rupturas de maximo anual y el
    porcentaje de valores cerca de maximos, que es una pieza del semaforo de
    riesgo. Un valor recien anadido inflaba los tres.
    """
    # Cae desde 200 hasta 100 y se queda plano: al cabo de medio ano el maximo
    # de la media ventana es 100 y parece que esta en maximos.
    serie = pd.Series([200.0] * 20 + [100.0] * 200)
    dist = ind.distance_to_high(serie, window=252)

    assert dist.notna().sum() == 0, (
        "con menos de un ano de sesiones no hay maximo anual que valga"
    )

    largo = pd.Series([200.0] * 20 + [100.0] * 240)
    completo = ind.distance_to_high(largo, window=252)
    assert completo.dropna().iloc[-1] == pytest.approx(-0.5), (
        "con el ano completo el maximo real sigue siendo 200"
    )


def test_the_max_drawdown_also_needs_the_whole_window():
    """Medida sobre medio periodo la caida maxima sale menor que la real, que
    es el error que tranquiliza."""
    serie = pd.Series([100.0] * 130)
    assert ind.max_drawdown(serie, window=252).notna().sum() == 0
