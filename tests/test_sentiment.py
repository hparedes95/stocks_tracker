"""Tests de la lectura de miedo y codicia y del desglose del semaforo."""

from __future__ import annotations

import pandas as pd
import pytest

from stocks_tracker.app.data_access import regime_components
from stocks_tracker.core import sentiment


# ---------------------------------------------------------------------------
# Escala
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("risk_score", "expected"),
    [(-100, 0.0), (-50, 25.0), (0, 50.0), (50, 75.0), (100, 100.0)],
)
def test_scale_maps_the_extremes_and_the_centre(risk_score, expected):
    assert sentiment.to_fear_greed(risk_score) == expected


def test_scale_is_monotonic():
    """Mas apetito por el riesgo nunca puede dar menos codicia."""
    values = [sentiment.to_fear_greed(s) for s in range(-100, 101, 5)]
    assert values == sorted(values)


def test_scale_clamps_out_of_range_input():
    assert sentiment.to_fear_greed(500) == 100.0
    assert sentiment.to_fear_greed(-500) == 0.0


@pytest.mark.parametrize("bad", [None, float("nan"), "hola", object()])
def test_scale_returns_none_for_unusable_input(bad):
    assert sentiment.to_fear_greed(bad) is None


# ---------------------------------------------------------------------------
# Tramos
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (0, "Miedo extremo"), (24.9, "Miedo extremo"),
        (25, "Miedo"), (44.9, "Miedo"),
        (45, "Neutral"), (54.9, "Neutral"),
        (55, "Codicia"), (74.9, "Codicia"),
        (75, "Codicia extrema"), (100, "Codicia extrema"),
    ],
)
def test_band_boundaries(value, expected):
    assert sentiment.label(value) == expected


def test_label_without_data():
    assert sentiment.label(None) == "Sin datos"
    assert "No hay datos" in sentiment.reading(None)


def test_every_band_has_a_reading():
    for _, _, name in sentiment.bands():
        value = {"Miedo extremo": 10, "Miedo": 35, "Neutral": 50,
                 "Codicia": 65, "Codicia extrema": 90}[name]
        assert sentiment.reading(value), f"El tramo '{name}' no dice nada"


def test_bands_cover_the_whole_scale_without_gaps():
    covered = sentiment.bands()
    assert covered[0][0] == 0.0
    assert covered[-1][1] == 100.0
    for (_, end), (start, _) in zip(
        [(a, b) for a, b, _ in covered], [(a, b) for a, b, _ in covered[1:]],
        strict=False,
    ):
        assert end == start, "Hay un hueco entre tramos"


def test_readings_never_recommend_an_action():
    """El termometro informa; no dice que comprar ni cuando."""
    prohibited = ("compra ", "vende ", "deberias", "recomend", "garantiz")
    for value in (10, 35, 50, 65, 90):
        text = sentiment.reading(value).lower()
        for word in prohibited:
            assert word not in text, f"'{word}' aparece en el tramo de {value}"


# ---------------------------------------------------------------------------
# Desglose del semaforo
# ---------------------------------------------------------------------------
def test_components_are_parsed_and_ordered_by_magnitude():
    row = pd.Series(
        {"components": "{'vix': 12.0, 'amplitud': -80.5, 'cobre_oro': 40.0}"}
    )
    parsed = regime_components(row)
    assert list(parsed) == ["amplitud", "cobre_oro", "vix"]
    assert parsed["amplitud"] == -80.5


def test_components_survive_garbage():
    """Un desglose ilegible no puede tumbar la pagina de macro."""
    for bad in ("", None, "no soy un diccionario", "[1, 2, 3]", "{"):
        assert regime_components(pd.Series({"components": bad})) == {}


def test_components_drop_non_numeric_values():
    row = pd.Series({"components": "{'vix': 12.0, 'raro': 'texto', 'nan_': None}"})
    assert regime_components(row) == {"vix": 12.0}


def test_components_never_execute_code():
    """El campo se lee con literal_eval, no con eval: no ejecuta nada."""
    row = pd.Series({"components": "__import__('os').system('echo comprometido')"})
    assert regime_components(row) == {}
