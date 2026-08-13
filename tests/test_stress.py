"""Stress test de la cartera.

Aqui todos los errores tienen el mismo signo: tranquilizan. Una posicion sin
datos contada como que no se movio, una correlacion de tiempos tranquilos
aplicada a una caida, un escenario rellenado con una estimacion cuando no
llega el historico — las tres hacen que el numero salga mas suave de lo que
seria, y las tres se leen igual de convincentes.

Por eso la mayoria de estos tests comprueban que el modulo se NIEGA a rellenar
huecos.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from stocks_tracker.core import stress
from stocks_tracker.core.stress import (
    Escenario,
    Fuente,
    diversificacion,
    impacto,
)

ESCENARIO = Escenario(id="x", nombre="Prueba", desde=date(2020, 2, 19),
                      hasta=date(2020, 3, 23))


def posicion(ticker="AAA", valor=1000.0, sector="Tecnologia") -> dict:
    return {"ticker": ticker, "valor": valor, "sector": sector}


# ---------------------------------------------------------------------------
# De donde sale cada retorno
# ---------------------------------------------------------------------------
def test_a_stock_with_its_own_history_uses_its_own_history():
    """Lo especifico gana a lo generico: es la diferencia entre "tu cartera en
    2020" y "el mercado en 2020"."""
    r = impacto(ESCENARIO, [posicion()], {"AAA": -0.55}, {"Tecnologia": -0.30},
                retorno_mercado=-0.34)
    assert r.posiciones[0].fuente is Fuente.PROPIA
    assert r.retorno == pytest.approx(-0.55)


def test_a_stock_that_did_not_exist_falls_back_to_its_sector():
    """Muchos valores no cotizaban en 2020. Su sector es la mejor
    aproximacion disponible, y hay que decir que es una aproximacion."""
    r = impacto(ESCENARIO, [posicion()], {}, {"Tecnologia": -0.30},
                retorno_mercado=-0.34)
    assert r.posiciones[0].fuente is Fuente.SECTOR
    assert r.posiciones[0].estimado


def test_without_a_sector_it_falls_back_to_the_index():
    r = impacto(ESCENARIO, [posicion(sector=None)], {}, {},
                retorno_mercado=-0.34)
    assert r.posiciones[0].fuente is Fuente.MERCADO
    assert r.retorno == pytest.approx(-0.34)


def test_a_position_with_no_reference_at_all_is_left_out():
    """Y NO se cuenta como que no se movio. Suponer cero es la unica hipotesis
    que garantiza equivocarse en la direccion que tranquiliza: meteria una
    posicion ilesa en mitad de una caida y bajaria la perdida total."""
    r = impacto(ESCENARIO, [posicion(), posicion(ticker="BBB")],
                {"AAA": -0.50}, {}, retorno_mercado=None)
    assert [p.ticker for p in r.posiciones] == ["AAA"]
    assert r.retorno == pytest.approx(-0.50), (
        "la posicion sin datos ha entrado como si no se hubiera movido"
    )


def test_a_nan_return_does_not_count_as_a_real_one():
    """Un `nan` pasa por `is not None` y despues contamina toda la suma."""
    r = impacto(ESCENARIO, [posicion()], {"AAA": float("nan")},
                {"Tecnologia": -0.30}, retorno_mercado=-0.34)
    assert r.posiciones[0].fuente is Fuente.SECTOR


def test_a_position_worth_nothing_is_skipped():
    r = impacto(ESCENARIO, [posicion(valor=0.0)], {"AAA": -0.5}, {})
    assert r.posiciones == []


# ---------------------------------------------------------------------------
# El impacto en dinero
# ---------------------------------------------------------------------------
def test_the_loss_is_weighted_by_what_each_position_is_worth():
    """9.000 EUR cayendo un 10 % duele mas que 1.000 cayendo un 50 %."""
    r = impacto(ESCENARIO,
                [posicion("GRANDE", 9000.0), posicion("PEQUE", 1000.0)],
                {"GRANDE": -0.10, "PEQUE": -0.50}, {})
    assert r.perdida == pytest.approx(-1400.0)
    assert r.retorno == pytest.approx(-0.14)


def test_the_worst_positions_are_ranked_by_euros_not_by_percent():
    """Un valor que cae un 70 % pesando el 1 % duele menos que uno que cae un
    25 % pesando el 40 %, y es el segundo el que hay que mirar."""
    r = impacto(ESCENARIO,
                [posicion("PEQUE_HUNDIDA", 100.0), posicion("GRANDE_TOCADA", 10000.0)],
                {"PEQUE_HUNDIDA": -0.70, "GRANDE_TOCADA": -0.25}, {})
    assert [p.ticker for p in r.peores] == ["GRANDE_TOCADA", "PEQUE_HUNDIDA"]


def test_doing_better_than_the_index_is_reported():
    r = impacto(ESCENARIO, [posicion()], {"AAA": -0.20}, {},
                retorno_mercado=-0.34)
    assert r.peor_que_el_mercado == pytest.approx(0.14)


def test_without_a_market_reference_there_is_no_comparison():
    r = impacto(ESCENARIO, [posicion()], {"AAA": -0.20}, {})
    assert r.peor_que_el_mercado is None


def test_an_empty_portfolio_does_not_divide_by_zero():
    r = impacto(ESCENARIO, [], {}, {})
    assert r.retorno == 0.0 and r.cobertura == 0.0


# ---------------------------------------------------------------------------
# Cuanto fiarse
# ---------------------------------------------------------------------------
def test_coverage_measures_money_not_positions():
    """Una posicion enorme estimada con el indice envenena el resultado mas
    que cinco pequenas; contarlas a partes iguales lo escondaria."""
    r = impacto(ESCENARIO,
                [posicion("PROPIA", 1000.0), posicion("ESTIMADA", 9000.0)],
                {"PROPIA": -0.3}, {"Tecnologia": -0.3})
    assert r.cobertura == pytest.approx(0.10)
    assert not r.fiable


def test_a_portfolio_with_full_history_is_reliable():
    r = impacto(ESCENARIO, [posicion()], {"AAA": -0.3}, {})
    assert r.cobertura == pytest.approx(1.0)
    assert r.fiable


def test_every_source_has_a_label():
    assert set(stress.ETIQUETA_FUENTE) == set(Fuente)


# ---------------------------------------------------------------------------
# Los escenarios del fichero
# ---------------------------------------------------------------------------
def test_the_scenarios_load_with_real_dates():
    lista = stress.escenarios()
    assert lista
    for e in lista:
        assert isinstance(e.desde, date) and isinstance(e.hasta, date)
        assert e.desde < e.hasta, f"{e.id} tiene las fechas al reves"
        assert e.que_paso, f"{e.id} no explica que paso"


def test_the_covid_window_is_the_real_one():
    """Si las fechas se movieran, el escenario seguiria calculandose y daria un
    numero distinto sin avisar de nada."""
    covid = next(e for e in stress.escenarios() if e.id == "covid_2020")
    assert covid.desde == date(2020, 2, 19)
    assert covid.hasta == date(2020, 3, 23)


def test_a_broken_scenario_does_not_take_down_the_others(monkeypatch):
    monkeypatch.setattr(stress, "get_stress_config", lambda: {
        "escenarios": [
            {"id": "roto"},                       # sin nombre ni fechas
            {"id": "bueno", "nombre": "B", "desde": date(2020, 1, 1),
             "hasta": date(2020, 2, 1)},
        ]
    })
    assert [e.id for e in stress.escenarios()] == ["bueno"]


# ---------------------------------------------------------------------------
# La diversificacion que desaparece
# ---------------------------------------------------------------------------
def matriz(valores) -> pd.DataFrame:
    n = len(valores)
    nombres = [f"T{i}" for i in range(n)]
    return pd.DataFrame(valores, index=nombres, columns=nombres)


def independientes(n: int) -> pd.DataFrame:
    return matriz(np.eye(n))


def clavadas(n: int) -> pd.DataFrame:
    return matriz(np.ones((n, n)))


def test_four_unrelated_positions_are_four_bets():
    d = diversificacion({f"T{i}": 1.0 for i in range(4)}, independientes(4))
    assert d.efectivas_hoy == pytest.approx(4.0)


def test_four_positions_that_move_as_one_are_a_single_bet():
    """El caso que la pantalla existe para ensenar: cuatro valores del mismo
    sector no son cuatro apuestas, son una con cuatro nombres."""
    d = diversificacion({f"T{i}": 1.0 for i in range(4)}, clavadas(4))
    assert d.efectivas_hoy == pytest.approx(1.0)


def test_diversification_disappears_when_correlations_rise():
    """Lo que se pierde justo el dia que hacia falta."""
    d = diversificacion({f"T{i}": 1.0 for i in range(6)}, independientes(6),
                        correlacion_crisis=0.9)
    assert d.efectivas_hoy == pytest.approx(6.0)
    assert d.efectivas_en_crisis < 1.5
    assert d.se_pierde > 4


def test_a_portfolio_already_concentrated_is_flagged():
    d = diversificacion({f"T{i}": 1.0 for i in range(6)}, clavadas(6))
    assert d.ya_esta_concentrada


def test_a_genuinely_diversified_portfolio_is_not_flagged():
    d = diversificacion({f"T{i}": 1.0 for i in range(6)}, independientes(6))
    assert not d.ya_esta_concentrada


def test_weights_matter_as_much_as_correlations():
    """Diez valores sin relacion entre si pero con el 90 % en uno son una
    apuesta, no diez. Sin ponderar, esto saldria como cartera diversificada."""
    pesos = {f"T{i}": (90.0 if i == 0 else 10.0 / 9) for i in range(10)}
    d = diversificacion(pesos, independientes(10))
    assert d.efectivas_hoy < 2.0


def test_a_single_position_has_no_diversification_to_measure():
    """Nulo y no 1.0: no hay nada que medir, que no es lo mismo que estar
    perfectamente concentrado."""
    assert diversificacion({"T0": 1.0}, independientes(1)) is None


def test_positions_missing_from_the_matrix_are_ignored():
    """Un valor recien comprado no tiene historico para correlacionar."""
    d = diversificacion({"T0": 1.0, "T1": 1.0, "NUEVO": 1.0}, independientes(2))
    assert d.n_posiciones == 2


def test_zero_weights_do_not_divide_by_zero():
    assert diversificacion({"T0": 0.0, "T1": 0.0}, independientes(2)) is None


def test_nan_correlations_do_not_poison_the_result():
    """Con una serie corta, la correlacion sale `nan` y contamina toda la
    matriz: el resultado seria `nan` y la pantalla lo pintaria como si fuera
    un numero."""
    m = independientes(3)
    m.iloc[0, 1] = np.nan
    m.iloc[1, 0] = np.nan
    d = diversificacion({f"T{i}": 1.0 for i in range(3)}, m)
    assert np.isfinite(d.efectivas_hoy)


def test_the_crisis_correlation_comes_from_the_config():
    assert 0.5 < float(stress.get_stress_config()["correlacion_en_crisis"]) <= 1.0


# ---------------------------------------------------------------------------
# El titular
# ---------------------------------------------------------------------------
def test_the_headline_says_it_fell_when_it_fell():
    r = impacto(ESCENARIO, [posicion()], {"AAA": -0.35}, {})
    assert "habria caido" in stress.frase_peor(r)


def test_the_headline_does_not_call_a_gain_a_fall():
    """Con un `abs()` delante, una cartera que habria GANADO en todos los
    escenarios sale con "habria caido un 10,9 %". Pasa con carteras defensivas
    y con cualquiera que lleve algo inverso, y es una frase que se cree."""
    r = impacto(ESCENARIO, [posicion()], {"AAA": 0.109}, {})
    frase = stress.frase_peor(r)
    assert "habria caido" not in frase
    assert "ganado" in frase


def test_even_a_gain_does_not_end_on_a_reassuring_note():
    """El peor caso sigue siendo peor que lo peor que ha pasado, y eso hay que
    decirlo tambien —sobre todo— cuando el numero sale bien."""
    r = impacto(ESCENARIO, [posicion()], {"AAA": 0.10}, {})
    assert "peor que lo peor que ha pasado" in stress.frase_peor(r)
