"""Tests de fuerza relativa, rotacion, niveles y del componente de graficos."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stocks_tracker.app.components import lwc
from stocks_tracker.core import relative as rel


# ---------------------------------------------------------------------------
# Fuerza relativa y rotacion
# ---------------------------------------------------------------------------
def test_relative_strength_flat_when_series_tracks_benchmark():
    """Moverse igual que el mercado debe dar una linea plana en 100."""
    idx = pd.bdate_range("2024-01-01", periods=120)
    benchmark = pd.Series(np.linspace(100, 140, 120), index=idx)
    series = benchmark * 2.5  # mismo comportamiento, distinto nivel

    rs = rel.relative_strength(series, benchmark)
    assert np.allclose(rs.to_numpy(), 100.0)


def test_relative_strength_rises_when_outperforming():
    idx = pd.bdate_range("2024-01-01", periods=120)
    benchmark = pd.Series(np.linspace(100, 110, 120), index=idx)
    series = pd.Series(np.linspace(100, 150, 120), index=idx)

    rs = rel.relative_strength(series, benchmark)
    assert rs.iloc[-1] > rs.iloc[0]


@pytest.mark.parametrize(
    ("ratio", "momentum", "expected"),
    [
        (101.0, 101.0, rel.LEADING),
        (101.0, 99.0, rel.WEAKENING),
        (99.0, 99.0, rel.LAGGING),
        (99.0, 101.0, rel.IMPROVING),
        (float("nan"), 100.0, "Sin datos"),
    ],
)
def test_quadrant_assignment(ratio, momentum, expected):
    assert rel.quadrant(ratio, momentum) == expected


def test_rotation_table_produces_a_row_per_series():
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2023-01-02", periods=400)
    benchmark = pd.Series(100 * np.exp(rng.normal(0.0003, 0.008, 400).cumsum()), index=idx)
    prices = pd.DataFrame(
        {
            "XLK": benchmark * np.exp(rng.normal(0.0002, 0.004, 400).cumsum()),
            "XLE": benchmark * np.exp(rng.normal(-0.0002, 0.004, 400).cumsum()),
        },
        index=idx,
    )

    table = rel.rotation_table(prices, benchmark)
    assert len(table) == 2
    assert set(table.columns) >= {"nombre", "ratio", "momentum", "cuadrante"}
    assert table["cuadrante"].isin(
        [rel.LEADING, rel.WEAKENING, rel.LAGGING, rel.IMPROVING]
    ).all()
    # La estela debe tener varios puntos para poder dibujarse.
    assert all(len(t) > 1 for t in table["estela_ratio"])


def test_rotation_table_skips_short_series():
    idx = pd.bdate_range("2024-01-01", periods=40)
    benchmark = pd.Series(np.linspace(100, 105, 40), index=idx)
    prices = pd.DataFrame({"CORTA": np.linspace(50, 55, 40)}, index=idx)
    assert rel.rotation_table(prices, benchmark).empty


# ---------------------------------------------------------------------------
# Correlacion
# ---------------------------------------------------------------------------
def test_average_correlation_is_high_when_series_move_together():
    idx = pd.bdate_range("2023-01-02", periods=200)
    rng = np.random.default_rng(5)
    common = rng.normal(0, 0.01, 200)
    returns = pd.DataFrame(
        {f"T{i}": common + rng.normal(0, 0.001, 200) for i in range(6)}, index=idx
    )
    corr = rel.average_pairwise_correlation(returns, window=60)
    assert not corr.empty
    assert corr.iloc[-1] > 0.85


def test_average_correlation_is_low_when_series_are_independent():
    idx = pd.bdate_range("2023-01-02", periods=200)
    rng = np.random.default_rng(6)
    returns = pd.DataFrame(
        {f"T{i}": rng.normal(0, 0.01, 200) for i in range(6)}, index=idx
    )
    corr = rel.average_pairwise_correlation(returns, window=60)
    assert not corr.empty
    assert abs(corr.iloc[-1]) < 0.35


def test_average_correlation_needs_enough_series():
    idx = pd.bdate_range("2024-01-01", periods=100)
    returns = pd.DataFrame({"A": np.zeros(100), "B": np.zeros(100)}, index=idx)
    assert rel.average_pairwise_correlation(returns, window=60).empty


# ---------------------------------------------------------------------------
# Soportes y resistencias
# ---------------------------------------------------------------------------
def test_support_resistance_finds_repeated_levels():
    """Un nivel tocado tres veces debe salir por delante de uno tocado una."""
    pattern = [100, 105, 110, 105, 100, 105, 110, 105, 100, 105, 110, 105]
    values = np.array(pattern * 8, dtype=float)
    high = pd.Series(values + 0.5)
    low = pd.Series(values - 0.5)

    supports, resistances = rel.support_resistance(high, low, order=3)
    assert supports and resistances
    # El maximo repetido ronda 110 y el minimo repetido ronda 100.
    assert any(abs(r - 110.5) < 2 for r in resistances)
    assert any(abs(s - 99.5) < 2 for s in supports)


def test_support_resistance_handles_short_series():
    short = pd.Series([1.0, 2.0, 3.0])
    assert rel.support_resistance(short, short) == ([], [])


def test_nearest_levels_picks_the_ones_surrounding_the_price():
    supports = [90.0, 95.0, 80.0]
    resistances = [110.0, 105.0, 120.0]
    support, resistance = rel.nearest_levels(100.0, supports, resistances)
    assert support == 95.0
    assert resistance == 105.0


def test_nearest_levels_returns_none_when_no_level_on_a_side():
    support, resistance = rel.nearest_levels(65.0, [70.0], [70.0])
    assert support is None
    assert resistance == 70.0


def test_nearest_levels_ignores_far_away_levels():
    """Un soporte un 44% por debajo no condiciona nada: mejor no darlo.

    Es el caso real que aparecio con datos sinteticos: el nivel mas tocado del
    ano puede estar lejisimos del precio de hoy y no ser una referencia util.
    """
    support, resistance = rel.nearest_levels(166.0, [94.0], [400.0])
    assert support is None
    assert resistance is None


def test_nearest_levels_accepts_levels_within_range():
    support, resistance = rel.nearest_levels(100.0, [94.0, 85.0], [108.0, 150.0])
    assert support == 94.0
    assert resistance == 108.0


# ---------------------------------------------------------------------------
# Componente de graficos
# ---------------------------------------------------------------------------
def test_vendored_library_is_present():
    """Sin la libreria vendorizada los graficos propios no existen."""
    assert lwc.library_available(), (
        "Falta app/static/lightweight-charts.standalone.production.js"
    )


def test_to_lwc_time_is_consistent_across_input_types():
    """Datos y marcadores DEBEN usar el mismo formato o no se encuentran."""
    expected = "2024-03-15"
    for value in [
        "2024-03-15",
        pd.Timestamp("2024-03-15"),
        pd.Timestamp("2024-03-15 16:30:00"),
        pd.Timestamp("2024-03-15").date(),
    ]:
        assert lwc.to_lwc_time(value) == expected


def test_to_lwc_time_rejects_garbage():
    assert lwc.to_lwc_time(None) is None
    assert lwc.to_lwc_time("no es una fecha") is None
    assert lwc.to_lwc_time(float("nan")) is None


def test_snap_moves_to_the_previous_available_session():
    """Una senal en festivo debe caer sobre la sesion anterior, no perderse."""
    sessions = ["2024-03-11", "2024-03-12", "2024-03-15"]
    assert lwc.snap_to_sessions("2024-03-14", sessions) == "2024-03-12"
    assert lwc.snap_to_sessions("2024-03-12", sessions) == "2024-03-12"
    # Anterior a toda la serie: no hay donde ponerlo.
    assert lwc.snap_to_sessions("2024-01-01", sessions) is None


def test_every_marker_lands_on_an_existing_session():
    """Blinda el fallo silencioso de la v5: un marcador sin punto desaparece.

    Se incluyen fechas en fin de semana y fuera de rango a proposito.
    """
    sessions = pd.bdate_range("2024-01-01", periods=60).strftime("%Y-%m-%d").tolist()
    signals = pd.DataFrame(
        {
            "date": [
                "2024-01-06",   # sabado
                "2024-01-07",   # domingo
                "2024-01-15",   # lunes normal
                "2023-06-01",   # anterior a toda la serie
                "2024-02-20",   # dentro de rango
            ],
            "signal_id": ["GOLDEN_CROSS", "DEATH_CROSS", "HIGH_52W_BREAKOUT",
                          "NEW_DOWNTREND", "PULLBACK_IN_UPTREND"],
            "direction": ["bullish", "bearish", "bullish", "bearish", "bullish"],
        }
    )

    markers = lwc.markers_from_signals(signals, sessions)
    assert markers, "Deberia haber generado marcadores"
    session_set = set(sessions)
    for marker in markers:
        assert marker.time in session_set, (
            f"El marcador {marker.time} no cae sobre ninguna sesion y "
            "la libreria lo descartaria en silencio"
        )
    # La fecha anterior a la serie no puede colarse.
    assert len(markers) == 4


def test_markers_are_capped_and_filtered():
    """Con muchas senales, ni se pintan todas ni llevan texto: taparian el precio."""
    sessions = pd.bdate_range("2024-01-01", periods=200).strftime("%Y-%m-%d").tolist()
    signals = pd.DataFrame(
        {
            "date": sessions,
            "signal_id": ["MACD_BULL_CROSS"] * 100 + ["GOLDEN_CROSS"] * 100,
            "direction": ["bullish"] * 200,
        }
    )

    markers = lwc.markers_from_signals(signals, sessions)
    assert len(markers) <= lwc.MAX_MARKERS
    # Con tantas, ninguna lleva etiqueta.
    assert all(m.text == "" for m in markers)
    # Y el filtro se queda con las senales de cambio de estado.
    assert len(markers) > 0


def test_few_markers_keep_their_labels():
    sessions = pd.bdate_range("2024-01-01", periods=60).strftime("%Y-%m-%d").tolist()
    signals = pd.DataFrame(
        {
            "date": [sessions[10], sessions[30]],
            "signal_id": ["GOLDEN_CROSS", "DEATH_CROSS"],
            "direction": ["bullish", "bearish"],
        }
    )
    markers = lwc.markers_from_signals(
        signals, sessions, labels={"GOLDEN_CROSS": "Cruce dorado"}
    )
    assert len(markers) == 2
    assert markers[0].text == "Cruce dorado"


def test_empty_signals_produce_no_markers():
    sessions = ["2024-01-02"]
    assert lwc.markers_from_signals(pd.DataFrame(), sessions) == []
    assert lwc.markers_from_signals(None, sessions) == []


def test_sessions_of_matches_candle_times():
    ohlcv = pd.DataFrame(
        {
            "date": pd.bdate_range("2024-01-01", periods=5),
            "open": [1.0] * 5, "high": [2.0] * 5, "low": [0.5] * 5,
            "close": [1.5] * 5, "volume": [100] * 5,
        }
    )
    sessions = lwc.sessions_of(ohlcv)
    assert len(sessions) == 5
    assert sessions[0] == "2024-01-01"
