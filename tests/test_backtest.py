"""Tests del motor de validacion.

El mas importante es `test_forward_returns_do_not_peek`: comprueba que la
entrada esta retardada un dia. Sin ese retardo, el backtest compra al mismo
cierre que genero la senal, cosa imposible en la practica, y los resultados
salen preciosos mientras el dinero real se pierde.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from stocks_tracker.backtest import engine as eng
from stocks_tracker.backtest import metrics as mx


# ---------------------------------------------------------------------------
# Metricas
# ---------------------------------------------------------------------------
def test_hit_rate_counts_positive_returns():
    assert mx.hit_rate([0.1, -0.05, 0.02, -0.01]) == 0.5
    assert mx.hit_rate([0.1, 0.2]) == 1.0
    assert np.isnan(mx.hit_rate([]))


def test_sharpe_matches_manual_computation():
    rng = np.random.default_rng(1)
    values = rng.normal(0.001, 0.01, 500)
    expected = values.mean() / values.std(ddof=1) * np.sqrt(252)
    assert mx.sharpe(values) == pytest.approx(expected)


def test_sharpe_is_nan_without_dispersion():
    assert np.isnan(mx.sharpe([0.01] * 50))


def test_max_drawdown_on_known_curve():
    """Sube a 120, cae a 90: la peor caida es del 25%."""
    equity = np.array([100.0, 120.0, 90.0, 110.0])
    assert mx.max_drawdown(equity) == pytest.approx(-0.25)


def test_equity_curve_compounds():
    curve = mx.equity_curve([0.10, 0.10])
    assert curve.iloc[-1] == pytest.approx(1.21)


def test_information_coefficient_detects_perfect_ordering():
    scores = [1, 2, 3, 4, 5, 6]
    forward = [0.01, 0.02, 0.03, 0.04, 0.05, 0.06]
    assert mx.information_coefficient(scores, forward) == pytest.approx(1.0)
    assert mx.information_coefficient(scores, forward[::-1]) == pytest.approx(-1.0)


def test_ic_information_ratio_rewards_consistency():
    """Mismo IC medio, distinta estabilidad: gana el consistente."""
    steady = [0.05, 0.04, 0.06, 0.05, 0.05]
    erratic = [0.40, -0.30, 0.35, -0.25, 0.05]
    assert mx.ic_information_ratio(steady) > mx.ic_information_ratio(erratic)


def test_apply_costs_charges_both_legs():
    """Ida y vuelta: 10 pb por pata son 20 pb en total."""
    result = mx.apply_costs([0.05], cost_bps=10.0)
    assert result[0] == pytest.approx(0.05 - 0.002)


def test_summarize_event_computes_excess_over_benchmark():
    returns = [0.03, 0.01, -0.02, 0.04]
    benchmark = [0.01, 0.01, 0.01, 0.01]
    result = mx.summarize_event(returns, benchmark)
    assert result.n_obs == 4
    assert result.avg_excess == pytest.approx(np.mean(np.array(returns) - 0.01))
    assert result.hit_rate == 0.75


def test_small_sample_is_never_significant():
    result = mx.summarize_event([0.05] * 10)
    assert not result.is_significant


# ---------------------------------------------------------------------------
# Retornos futuros: el punto critico
# ---------------------------------------------------------------------------
def _linear_prices(n: int = 60, step: float = 1.0) -> pd.DataFrame:
    """Serie perfectamente lineal: hace verificables los retornos a mano."""
    dates = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame(
        {"ticker": "AAA", "date": dates, "adj_close": 100.0 + step * np.arange(n)}
    )


def test_forward_returns_enter_the_next_day():
    """Entrada al cierre de t+1, salida al cierre de t+1+h.

    Con precios 100, 101, 102... el retorno a 5 sesiones desde la fecha 0 debe
    ser 106/101 - 1, no 105/100 - 1.
    """
    prices = _linear_prices()
    fwd = eng.forward_returns(prices, horizons=(5,))
    expected = 106.0 / 101.0 - 1.0
    assert fwd.iloc[0]["fwd_5"] == pytest.approx(expected)

    # Y NO el valor que saldria comprando al cierre de la propia senal.
    naive = 105.0 / 100.0 - 1.0
    assert fwd.iloc[0]["fwd_5"] != pytest.approx(naive)


def test_forward_returns_do_not_peek():
    """Alterar el pasado no puede cambiar un retorno futuro ya calculado.

    Es la comprobacion inversa a la del look-ahead de los indicadores: aqui lo
    que se verifica es que el retorno de la fecha `t` depende solo de precios
    POSTERIORES a `t`, y de ninguno anterior.
    """
    prices = _linear_prices()
    baseline = eng.forward_returns(prices, horizons=(5,))

    perturbed = prices.copy()
    perturbed.loc[:20, "adj_close"] *= 0.5  # se destroza el pasado

    result = eng.forward_returns(perturbed, horizons=(5,))

    # Las fechas posteriores al tramo alterado no deben cambiar.
    assert result.iloc[30]["fwd_5"] == pytest.approx(baseline.iloc[30]["fwd_5"])


def test_a_naive_implementation_would_fail_the_delay_test():
    """Contraprueba: si el test no cazara la trampa, no estaria midiendo nada."""
    prices = _linear_prices()
    close = prices["adj_close"]
    # Version tramposa: compra al mismo cierre que genero la senal.
    cheating = (close.shift(-5) / close - 1.0).iloc[0]
    honest = eng.forward_returns(prices, horizons=(5,)).iloc[0]["fwd_5"]
    assert cheating != pytest.approx(honest)


def test_forward_returns_are_nan_at_the_end():
    """Al final de la serie no hay futuro que medir: debe ser NaN, no cero."""
    prices = _linear_prices(n=30)
    fwd = eng.forward_returns(prices, horizons=(5,))
    assert pd.isna(fwd.iloc[-1]["fwd_5"])
    assert pd.isna(fwd.iloc[-5]["fwd_5"])


def test_forward_returns_handle_multiple_tickers():
    dates = pd.bdate_range("2024-01-01", periods=40)
    prices = pd.concat(
        [
            pd.DataFrame({"ticker": "AAA", "date": dates,
                          "adj_close": 100.0 + np.arange(40)}),
            pd.DataFrame({"ticker": "BBB", "date": dates,
                          "adj_close": 50.0 - 0.5 * np.arange(40)}),
        ]
    )
    fwd = eng.forward_returns(prices, horizons=(5,))
    assert set(fwd["ticker"]) == {"AAA", "BBB"}
    # AAA sube y BBB baja: los signos deben ser opuestos.
    aaa = fwd[fwd["ticker"] == "AAA"].iloc[0]["fwd_5"]
    bbb = fwd[fwd["ticker"] == "BBB"].iloc[0]["fwd_5"]
    assert aaa > 0 > bbb


# ---------------------------------------------------------------------------
# Referencia: el error que hace ganar a senales opuestas
# ---------------------------------------------------------------------------
def test_universe_benchmark_cancels_common_drift():
    """La referencia correcta es el propio universo, no un indice externo.

    Si todos los valores comparten una deriva, comparar contra el universo la
    elimina. Es lo que impide que una senal alcista y su opuesta salgan ambas
    ganadoras solo porque el universo entero subia.
    """
    dates = pd.bdate_range("2024-01-01", periods=60)
    prices = pd.concat(
        [
            pd.DataFrame({"ticker": t, "date": dates,
                          "adj_close": 100.0 * (1.01 ** np.arange(60))})
            for t in ("AAA", "BBB", "CCC")
        ]
    )
    fwd = eng.forward_returns(prices, horizons=(5,))
    bench = eng.universe_forward_returns(fwd, (5,))

    merged = fwd.merge(bench, on="date")
    excess = (merged["fwd_5"] - merged["bench_5"]).dropna()
    # Todos se mueven igual: el exceso debe ser cero.
    assert np.allclose(excess.to_numpy(), 0.0, atol=1e-12)


def test_event_study_measures_excess_not_raw_return():
    dates = pd.bdate_range("2024-01-01", periods=80)
    # Un valor que sube el doble que el resto.
    fast = pd.DataFrame({"ticker": "FAST", "date": dates,
                         "adj_close": 100.0 * (1.02 ** np.arange(80))})
    slow = pd.concat(
        [
            pd.DataFrame({"ticker": t, "date": dates,
                          "adj_close": 100.0 * (1.01 ** np.arange(80))})
            for t in ("S1", "S2", "S3")
        ]
    )
    prices = pd.concat([fast, slow])
    fwd = eng.forward_returns(prices, horizons=(5,))
    bench = eng.universe_forward_returns(fwd, (5,))

    signals = pd.DataFrame(
        {"ticker": "FAST", "date": dates[:40], "signal_id": "TEST",
         "direction": "bullish"}
    )
    event, detail = eng.event_study(signals, fwd, 5, bench, cost_bps=0.0)

    assert event.n_obs > 0
    assert event.avg_excess > 0        # bate al universo
    assert event.avg_return > event.avg_excess  # el bruto incluye la deriva comun
    assert not detail.empty


# ---------------------------------------------------------------------------
# Clasificacion de evidencia
# ---------------------------------------------------------------------------
def _fechas(n: int) -> pd.DatetimeIndex:
    """Una fecha distinta por evento.

    El caso mas favorable posible: cada evento es su propio dia, asi que no hay
    nada que agrupar y estos tests siguen midiendo la clasificacion y no el
    recuento de observaciones, que se prueba aparte en test_multiple_testing.
    """
    return pd.bdate_range("2020-01-01", periods=n)


def test_small_sample_is_labelled_no_data():
    event = mx.summarize_event([0.05] * 20)
    label, reason = eng.classify_evidence(event, float("nan"), [])
    assert label == eng.NO_DATA
    assert "anecdota" in reason


def test_negative_excess_is_not_validated():
    rng = np.random.default_rng(2)
    returns = rng.normal(-0.01, 0.02, 200)
    event = mx.summarize_event(returns, dates=_fechas(200))
    label, reason = eng.classify_evidence(event, 0.5, [])
    assert label == eng.NOT_VALIDATED
    assert "No bate a la referencia" in reason


def test_consistent_positive_signal_is_validated():
    rng = np.random.default_rng(4)
    returns = rng.normal(0.012, 0.02, 400)
    event = mx.summarize_event(returns, dates=_fechas(400))
    folds = [
        eng.FoldResult(f"V{i}", pd.Timestamp("2024-01-01"), pd.Timestamp("2024-06-01"),
                       100, 0.01, 0.6, float("nan"), float("nan"))
        for i in range(3)
    ]
    label, _ = eng.classify_evidence(event, 0.5, folds)
    assert label == eng.VALIDATED


def test_signal_that_only_works_in_one_window_is_weak():
    """Funcionar en un solo tramo del historico no es funcionar."""
    rng = np.random.default_rng(9)
    returns = rng.normal(0.012, 0.02, 400)
    event = mx.summarize_event(returns, dates=_fechas(400))
    folds = [
        eng.FoldResult("V1", pd.Timestamp("2022-01-01"), pd.Timestamp("2022-06-01"),
                       100, 0.05, 0.7, float("nan"), float("nan")),
        eng.FoldResult("V2", pd.Timestamp("2023-01-01"), pd.Timestamp("2023-06-01"),
                       100, -0.01, 0.4, float("nan"), float("nan")),
        eng.FoldResult("V3", pd.Timestamp("2024-01-01"), pd.Timestamp("2024-06-01"),
                       100, -0.02, 0.4, float("nan"), float("nan")),
    ]
    label, reason = eng.classify_evidence(event, 0.5, folds)
    assert label == eng.WEAK
    assert "ventanas" in reason


# ---------------------------------------------------------------------------
# Deciles y ventanas
# ---------------------------------------------------------------------------
def test_decile_spread_is_positive_when_score_orders_returns():
    """Si el score ordena bien, el decil superior debe rendir mas que el inferior."""
    rng = np.random.default_rng(7)
    rows = []
    for date in pd.bdate_range("2024-01-01", periods=30):
        for i in range(60):
            score = rng.normal()
            rows.append(
                {"ticker": f"T{i}", "date": date, "composite": score,
                 "fwd_5": 0.01 * score + rng.normal(0, 0.002)}
            )
    frame = pd.DataFrame(rows)
    scores = frame[["ticker", "date", "composite"]]
    fwd = frame[["ticker", "date", "fwd_5"]]

    deciles = eng.decile_portfolios(scores, fwd, 5)
    assert not deciles.empty
    assert eng.decile_spread(deciles) > 0


def test_rank_ic_detects_a_useful_score():
    rng = np.random.default_rng(8)
    rows = []
    for date in pd.bdate_range("2024-01-01", periods=20):
        for i in range(40):
            score = rng.normal()
            rows.append(
                {"ticker": f"T{i}", "date": date, "composite": score,
                 "fwd_5": 0.02 * score + rng.normal(0, 0.005)}
            )
    frame = pd.DataFrame(rows)
    ic = eng.rank_ic(frame[["ticker", "date", "composite"]],
                     frame[["ticker", "date", "fwd_5"]], 5)
    assert not ic.empty
    assert ic.mean() > 0.5


def test_walk_forward_splits_into_windows():
    dates = pd.bdate_range("2022-01-01", periods=300)
    events = pd.DataFrame(
        {"date": dates, "retorno": np.linspace(-0.02, 0.02, 300), "referencia": 0.0}
    )
    folds = eng.walk_forward(events, n_folds=3)
    assert len(folds) == 3
    assert folds[0].start < folds[-1].start
    # Con retornos crecientes, la ultima ventana debe ir mejor que la primera.
    assert folds[-1].avg_excess > folds[0].avg_excess


def test_walk_forward_handles_empty_input():
    assert eng.walk_forward(pd.DataFrame()) == []


def test_t_statistic_does_not_explode_when_every_return_is_the_same():
    """Con un solo valor en el universo todos los eventos dan el mismo exceso,
    la desviacion tipica es ruido de coma flotante y el t salia del orden de
    10^16. Impreso en una tabla, ese numero desacredita toda la tabla."""
    from stocks_tracker.backtest.metrics import t_statistic

    assert math.isnan(t_statistic([-0.002] * 47))
    assert math.isnan(t_statistic([-0.002 + i * 1e-19 for i in range(47)]))

    # Y sigue midiendo cuando hay dispersion de verdad.
    rng = np.random.default_rng(0)
    assert t_statistic(rng.normal(0.01, 0.02, 200)) > 2
