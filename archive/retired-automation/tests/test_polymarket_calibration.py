"""Tests del estudio de calibracion de Polymarket.

Aqui no hay ninguna llamada a la red: se construyen muestras con una
calibracion conocida y se comprueba que el estudio la encuentra. Es la unica
forma de saber que la respuesta es correcta, porque con datos reales no hay
con que comparar —si el estudio se equivoca, se equivoca en silencio y con
aspecto de rigor—.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta

import pytest

from stocks_tracker.trading.polymarket_calibration import (
    MIN_SAMPLE,
    Observation,
    brier_score,
    brier_skill_score,
    calibration_buckets,
    collect_observations,
    evaluate,
    expected_calibration_error,
    exploitable_runs,
    render,
    wilson_interval,
)


def muestra_calibrada(n: int = 2000, semilla: int = 7) -> list[Observation]:
    """Un mercado que acierta: de los contratos a 0,30, el 30 % ocurre."""
    rng = random.Random(semilla)
    out = []
    for i in range(n):
        p = rng.choice([0.03, 0.08, 0.15, 0.27, 0.42, 0.58, 0.72, 0.85, 0.92, 0.97])
        out.append(Observation(str(i), "?", p, rng.random() < p))
    return out


def muestra_sesgada(n: int = 2000, semilla: int = 7,
                    sesgo: float = 0.10) -> list[Observation]:
    """Sesgo favorito-outsider: lo improbable se paga de mas.

    Un contrato a 0,10 ocurre en realidad el 10 % - `sesgo` de las veces, o
    sea que esta caro. Es el patron clasico de los mercados de apuestas y el
    unico que este estudio deberia dar por aprovechable.
    """
    rng = random.Random(semilla)
    out = []
    for i in range(n):
        p = rng.choice([0.03, 0.08, 0.15, 0.27, 0.42, 0.58, 0.72, 0.85, 0.92, 0.97])
        real = p - sesgo if p < 0.35 else p
        out.append(Observation(str(i), "?", p, rng.random() < max(0.0, real)))
    return out


# ---------------------------------------------------------------------------
# Estadistica
# ---------------------------------------------------------------------------
def test_a_perfect_forecast_scores_zero():
    obs = [Observation("1", "?", 1.0, True), Observation("2", "?", 0.0, False)]
    assert brier_score(obs) == 0.0


def test_a_coin_flip_scores_a_quarter():
    obs = [Observation("1", "?", 0.5, True), Observation("2", "?", 0.5, False)]
    assert brier_score(obs) == 0.25


def test_the_skill_score_does_not_reward_guessing_the_base_rate():
    """Acertar el 90 % de las veces no tiene merito si el 90 % de los mercados
    resuelven que si. Sin esta comparacion, un mercado de eventos casi seguros
    pareceria clarividente."""
    obs = [Observation(str(i), "?", 0.9, True) for i in range(90)]
    obs += [Observation(str(i), "?", 0.9, False) for i in range(90, 100)]
    # Predecir siempre 0,9 es exactamente la frecuencia base: cero merito.
    assert brier_skill_score(obs) == pytest.approx(0.0, abs=0.01)


def test_wilson_does_not_claim_certainty_with_zero_successes():
    """La formula normal daria un intervalo de anchura cero: "la probabilidad
    es exactamente 0, con total certeza". En los extremos, que es donde se
    busca el sesgo, eso convierte el ruido en un hallazgo."""
    low, high = wilson_interval(0, 30)
    assert low == 0.0
    assert high > 0.05, "afirma certeza absoluta con 30 casos"


def test_wilson_stays_inside_zero_and_one():
    for exitos, n in [(0, 5), (5, 5), (1, 100), (99, 100)]:
        low, high = wilson_interval(exitos, n)
        assert 0.0 <= low <= high <= 1.0


def test_wilson_narrows_with_more_data():
    ancho_pocos = math.dist(*[[x] for x in wilson_interval(5, 10)])
    ancho_muchos = math.dist(*[[x] for x in wilson_interval(500, 1000)])
    assert ancho_muchos < ancho_pocos


# ---------------------------------------------------------------------------
# Los tramos
# ---------------------------------------------------------------------------
def test_every_observation_lands_in_exactly_one_bucket():
    """Un precio de 1,0 caeria fuera de todos los tramos si el ultimo no
    incluyera su extremo, y esas observaciones desaparecerian de la muestra
    sin que nada lo dijera."""
    obs = [Observation(str(i), "?", p, True)
           for i, p in enumerate([0.0, 0.05, 0.5, 0.95, 1.0])]
    assert sum(b.n for b in calibration_buckets(obs)) == len(obs)


def test_a_calibrated_market_shows_almost_no_deviation():
    buckets = [b for b in calibration_buckets(muestra_calibrada()) if b.n >= 25]
    assert expected_calibration_error(buckets) < 0.03, (
        "encuentra desviacion donde el mercado acierta"
    )


def test_a_biased_market_shows_the_deviation():
    buckets = [b for b in calibration_buckets(muestra_sesgada()) if b.n >= 25]
    assert expected_calibration_error(buckets) > 0.03


def test_the_deviation_points_the_right_way():
    """Con sesgo favorito-outsider lo improbable esta CARO: ocurre menos de lo
    que dice el precio, o sea desvio negativo. Si saliera al reves, el bot
    compraria justo lo que deberia vender."""
    buckets = [b for b in calibration_buckets(muestra_sesgada()) if b.n >= 25]
    bajos = [b for b in buckets if b.predicted_mean < 0.35]
    assert bajos and all(b.gap < 0 for b in bajos), (
        "el signo del desvio esta invertido"
    )


# ---------------------------------------------------------------------------
# Rachas: un tramo suelto no es un hallazgo
# ---------------------------------------------------------------------------
def test_a_single_deviant_bucket_is_not_a_finding():
    """Con diez tramos, que uno se salga del intervalo es lo esperable por
    azar. Aceptarlo es la forma mas comun de encontrar ventaja donde no la
    hay.

    Los tramos se construyen a mano en vez de salir de una muestra aleatoria:
    con una muestra, que salga exactamente un tramo desviado depende de la
    semilla, y el dia que salgan dos el test dejaria de comprobar nada sin
    fallar.
    """
    from stocks_tracker.trading.polymarket_calibration import Bucket

    normal = Bucket(0.35, 0.50, 100, 0.42, 0.43, 0.34, 0.53)     # dentro del IC
    desviado = Bucket(0.50, 0.65, 100, 0.58, 0.40, 0.31, 0.50)   # fuera, solo
    otro_normal = Bucket(0.65, 0.80, 100, 0.72, 0.71, 0.62, 0.79)

    assert desviado.significant and not normal.significant
    assert exploitable_runs([normal, desviado, otro_normal], min_gap=0.02) == [], (
        "un tramo suelto se toma por un hallazgo"
    )


def test_two_adjacent_buckets_in_the_same_direction_are_a_finding():
    buckets = [b for b in calibration_buckets(muestra_sesgada(n=4000)) if b.n >= 25]
    rachas = exploitable_runs(buckets, min_gap=0.02)
    assert rachas, "no encuentra un sesgo que si esta"
    assert all(len(r) >= 2 for r in rachas)


def test_opposite_directions_do_not_form_a_run():
    """Un tramo por arriba y el siguiente por abajo no es un sesgo: es ruido
    con dos signos. Encadenarlos daria una racha inventada."""
    from stocks_tracker.trading.polymarket_calibration import Bucket

    arriba = Bucket(0.1, 0.2, 100, 0.15, 0.30, 0.22, 0.39)
    abajo = Bucket(0.2, 0.35, 100, 0.28, 0.13, 0.08, 0.21)
    assert exploitable_runs([arriba, abajo], min_gap=0.02) == []


# ---------------------------------------------------------------------------
# El veredicto, que esta invertido
# ---------------------------------------------------------------------------
def comprobacion(report, nombre: str):
    """La comprobacion concreta, por nombre.

    Mirar solo `report.passed` no basta: una muestra suele suspender varias a
    la vez, asi que el informe seguiria suspendiendo aunque la comprobacion
    que se quiere probar estuviera del reves. Es como se cuela un examen que
    da la respuesta correcta por el motivo equivocado.
    """
    for c in report.checks:
        if c.name == nombre:
            return c
    raise AssertionError(f"no existe la comprobacion {nombre!r}: "
                         f"{[c.name for c in report.checks]}")


DESVIACION = "Desviacion media (precio vs realidad)"


def test_a_calibrated_market_fails_the_gate():
    """Es lo contrario del examen de acciones. Un mercado que acierta no deja
    ventaja: se gana 0,70 el 30 % de las veces y se pierde 0,30 el 70 %, o sea
    cero, y negativo tras la horquilla. Aprobarlo mandaria a operar a perder."""
    report = evaluate(muestra_calibrada(), max_spread_pct=5.0)
    assert not report.passed
    assert not comprobacion(report, DESVIACION).passed, (
        "da por buena la desviacion de un mercado que acierta"
    )


def test_a_biased_market_passes_the_gate():
    report = evaluate(muestra_sesgada(n=4000, sesgo=0.12), max_spread_pct=2.0)
    assert report.passed, [
        (c.name, c.observed, c.required) for c in report.checks if not c.passed
    ]


def test_a_bias_smaller_than_the_spread_is_not_tradeable():
    """Una ventaja del 2 % con una horquilla del 5 % no es una ventaja: es una
    perdida con pasos intermedios."""
    sesgada = muestra_sesgada(n=4000, sesgo=0.02)
    report = evaluate(sesgada, max_spread_pct=5.0)
    assert not report.passed
    assert not comprobacion(report, DESVIACION).passed, (
        "acepta una desviacion menor que la horquilla"
    )
    # Y la MISMA muestra con una horquilla pequena si supera esa comprobacion:
    # sin esto, el test pasaria tambien con una regla que rechazase siempre.
    barata = evaluate(sesgada, max_spread_pct=0.5)
    assert comprobacion(barata, DESVIACION).passed, (
        "rechaza la desviacion independientemente del coste"
    )


def test_too_small_a_sample_is_blocked_not_scored():
    """Con pocos datos las cifras existirian igual, y una tabla con numeros
    invita a mirarlos. Se bloquea para que no haya nada que interpretar."""
    report = evaluate(muestra_sesgada(n=50), max_spread_pct=2.0)
    assert not report.passed
    assert report.blockers
    assert str(MIN_SAMPLE) in report.blockers[0]


def test_the_report_says_what_a_failure_means():
    """"No aprobado" aqui no es un fallo del bot: es la respuesta correcta."""
    obs = muestra_calibrada()
    texto = render(evaluate(obs, max_spread_pct=5.0), obs)
    assert "NO hay desviacion aprovechable" in texto
    assert "no se opera" in texto.lower()


# ---------------------------------------------------------------------------
# Recogida: de donde salen los numeros
# ---------------------------------------------------------------------------
class FakeReader:
    """Devuelve mercados y precios fijos, sin red."""

    def __init__(self, mercados, historia):
        self.mercados = mercados
        self.historia = historia
        self.momentos: list[datetime] = []

    def resolved_markets(self, **kwargs):
        return self.mercados

    def price_at(self, token_id, when, history=None):
        self.momentos.append(when)
        anteriores = [p for t, p in self.historia if t <= when]
        return anteriores[-1] if anteriores else None


def mercado(outcomes=("Yes", "No"), ganador="Yes", fin=None):
    from stocks_tracker.trading.brokers.polymarket_public import PredictionMarket

    precios = (1.0, 0.0) if ganador == outcomes[0] else (0.0, 1.0)
    return PredictionMarket(
        market_id="1", question="?", slug="s", condition_id="c",
        outcomes=tuple(outcomes), prices=precios, token_ids=("111", "222"),
        end_date=fin or datetime(2026, 6, 1, tzinfo=UTC),
        liquidity=10000, volume=50000, spread=0.01, closed=True, active=False,
    )


HISTORIA = [
    (datetime(2026, 5, 1, tzinfo=UTC), 0.30),
    (datetime(2026, 5, 20, tzinfo=UTC), 0.40),   # 12 dias antes del fin
    (datetime(2026, 5, 31, tzinfo=UTC), 0.98),   # ya resuelto de hecho
]


def test_the_price_is_taken_before_the_resolution_not_at_the_end():
    """El dia antes de resolverse un mercado ya vale casi 1. Medir ahi no mide
    una prediccion, mide un hecho ya ocurrido, y el estudio saldria brillante
    midiendo nada."""
    lector = FakeReader([mercado()], HISTORIA)
    obs = collect_observations(lector, days_before=7)
    assert len(obs) == 1
    assert obs[0].predicted == 0.40, "ha usado el precio de despues"
    assert lector.momentos[0] == datetime(2026, 5, 25, tzinfo=UTC)


def test_the_first_token_is_not_assumed_to_be_yes():
    """El historico es del PRIMER token, que no siempre es el "si". Si es el
    "no", su precio es el complementario; confundirlo invierte media muestra y
    el estudio da justo lo contrario."""
    lector = FakeReader([mercado(outcomes=("No", "Yes"), ganador="Yes")], HISTORIA)
    obs = collect_observations(lector, days_before=7)
    assert obs[0].predicted == pytest.approx(0.60), "ha tomado el 'no' por 'si'"
    assert obs[0].happened is True


def test_a_market_without_a_price_back_then_is_dropped():
    """Rellenar con el precio final meteria el resultado dentro de la
    prediccion."""
    lector = FakeReader(
        [mercado(fin=datetime(2026, 1, 1, tzinfo=UTC))], HISTORIA
    )
    assert collect_observations(lector, days_before=7) == []


def test_a_price_already_at_the_extreme_is_dropped():
    """Un precio de 0 o 1 en la fecha de corte significa que el mercado ya
    estaba resuelto de hecho: no es una prediccion."""
    historia = [(datetime(2026, 5, 1, tzinfo=UTC), 1.0)]
    lector = FakeReader([mercado()], historia)
    assert collect_observations(lector, days_before=7) == []


def test_the_cutoff_moves_with_the_requested_days():
    lector = FakeReader([mercado()], HISTORIA)
    collect_observations(lector, days_before=30)
    assert lector.momentos[0] == datetime(2026, 6, 1, tzinfo=UTC) - timedelta(days=30)
