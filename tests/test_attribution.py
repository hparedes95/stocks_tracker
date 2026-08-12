"""Atribucion de resultados.

El fallo que importa aqui no es un numero mal redondeado: es dar por merito lo
que fue marea. Ese error se premia a si mismo —hace repetir lo que no funciona
porque parecio funcionar— y no lo corrige nada, porque en un mercado alcista
todo sale bien y en uno bajista todo sale mal.

Por eso casi todos los tests fijan un escenario donde el numero ingenuo (tu
retorno a secas) y el honesto (tu retorno menos el del mercado) apuntan en
direcciones distintas.
"""

from __future__ import annotations

import pytest

from stocks_tracker.core import attribution as attr
from stocks_tracker.core.attribution import Posicion, resumir, veredicto


def pos(**kwargs) -> Posicion:
    base = {"ticker": "AAA", "coste": 1000.0, "retorno": 0.10,
            "retorno_mercado": 0.05, "retorno_sector": 0.07, "dias": 365}
    return Posicion(**{**base, **kwargs})


# ---------------------------------------------------------------------------
# La descomposicion tiene que cuadrar
# ---------------------------------------------------------------------------
def test_the_three_parts_add_up_to_what_you_actually_earned():
    """La propiedad que sostiene todo lo demas. Si no sumaran, habria un resto
    donde esconder cualquier cosa, y los numeros pareceran igual de
    convincentes."""
    p = pos(retorno=0.10, retorno_mercado=0.05, retorno_sector=0.07)
    assert p.cuadra
    assert p.retorno_mercado + p.efecto_sector + p.efecto_seleccion == \
        pytest.approx(p.retorno)


def test_the_sector_effect_is_the_sector_minus_the_market():
    p = pos(retorno_mercado=0.05, retorno_sector=0.07)
    assert p.efecto_sector == pytest.approx(0.02)


def test_the_selection_effect_is_your_stock_minus_its_sector():
    p = pos(retorno=0.10, retorno_sector=0.07)
    assert p.efecto_seleccion == pytest.approx(0.03)


def test_it_still_adds_up_when_everything_is_negative():
    """Un mercado que cae un 20 % y una posicion que cae un 30 % no es un
    acierto por haber "perdido menos que otros": la resta tiene que seguir
    saliendo."""
    p = pos(retorno=-0.30, retorno_mercado=-0.20, retorno_sector=-0.25)
    assert p.cuadra
    assert p.efecto_seleccion == pytest.approx(-0.05)


# ---------------------------------------------------------------------------
# Marea contra merito
# ---------------------------------------------------------------------------
def test_gaining_less_than_the_market_is_losing():
    """El caso central. Un 18 % en un ano en el que el mercado hizo un 25 %
    sale en verde en cualquier pantalla y es peor que no haber hecho nada."""
    r = resumir([pos(retorno=0.18, retorno_mercado=0.25, retorno_sector=0.22)])
    assert r.retorno > 0
    assert r.contra_el_mercado < 0


def test_losing_less_than_the_market_is_winning():
    """Y el contrario, que es el que nadie se apunta: caer un 12 % cuando el
    mercado cayo un 20 % es haber hecho ocho puntos bien."""
    r = resumir([pos(retorno=-0.12, retorno_mercado=-0.20, retorno_sector=-0.18)])
    assert r.retorno < 0
    assert r.contra_el_mercado > 0


def test_a_good_sector_is_not_the_same_as_a_good_stock():
    """Comprar el peor valor del mejor sector puede ganar dinero y aun asi ser
    una mala eleccion de valor. Mezclarlos hace repetir el error."""
    p = pos(retorno=0.30, retorno_mercado=0.10, retorno_sector=0.40)
    assert p.efecto_sector > 0          # el sector, muy bien
    assert p.efecto_seleccion < 0       # el valor dentro del sector, mal


# ---------------------------------------------------------------------------
# Ponderar
# ---------------------------------------------------------------------------
def test_a_big_position_weighs_more_than_a_small_one():
    """Una de 9.000 EUR y otra de 1.000 no pesan igual en lo que ganas. A
    partes iguales, un acierto en la pequena taparia un fallo en la grande."""
    r = resumir([
        pos(ticker="GRANDE", coste=9000.0, retorno=0.00, retorno_sector=0.00),
        pos(ticker="PEQUE", coste=1000.0, retorno=1.00, retorno_sector=0.00),
    ])
    assert r.retorno == pytest.approx(0.10)


def test_a_portfolio_with_no_capital_does_not_divide_by_zero():
    assert resumir([pos(coste=0.0)]).retorno == 0.0


def test_an_empty_portfolio_is_not_an_error():
    r = resumir([])
    assert r.retorno == 0.0 and r.probabilidad_por_azar is None


# ---------------------------------------------------------------------------
# Sin sector de referencia
# ---------------------------------------------------------------------------
def test_without_a_sector_the_two_effects_are_not_separated():
    """No hay ETF para todos los sectores ni sector para todos los valores.
    Repartir a ojo seria inventarse justo lo que se quiere medir."""
    p = pos(retorno=0.10, retorno_mercado=0.05, retorno_sector=None)
    assert p.efecto_sector == 0.0
    assert p.efecto_seleccion == pytest.approx(0.05)
    assert p.cuadra, "sin sector la suma tiene que seguir cuadrando"


def test_without_a_sector_there_is_no_verdict_on_beating_it():
    """`None` y no `False`: "no se sabe" no es "no lo bate"."""
    assert pos(retorno_sector=None).bate_a_su_sector is None


def test_positions_without_a_sector_do_not_count_as_misses():
    """Si contaran como fallo, tener valores sin sector asignado pareceria
    falta de acierto en vez de falta de datos."""
    r = resumir([
        pos(ticker="CON", retorno=0.10, retorno_sector=0.05),
        pos(ticker="SIN", retorno=0.10, retorno_sector=None),
    ])
    assert r.comparables == 1
    assert r.aciertos == 1


# ---------------------------------------------------------------------------
# Cuanto de esto puede ser suerte
# ---------------------------------------------------------------------------
def test_beating_the_sector_in_three_out_of_four_is_a_coin_flip():
    """Sale asi cinco veces de cada dieciseis. Presentarlo como habilidad seria
    el error mas caro de toda la pantalla."""
    r = resumir([pos(retorno=0.10, retorno_sector=0.05) for _ in range(3)]
                + [pos(retorno=0.01, retorno_sector=0.05)])
    assert r.aciertos == 3 and r.comparables == 4
    assert r.probabilidad_por_azar == pytest.approx(5 / 16)


def test_beating_it_in_all_of_them_is_much_less_likely():
    r = resumir([pos(retorno=0.10, retorno_sector=0.05) for _ in range(10)])
    assert r.probabilidad_por_azar == pytest.approx(1 / 1024)


def test_getting_them_all_wrong_is_almost_certain_to_be_beatable():
    """La cola es P(X >= aciertos): con cero aciertos vale 1, porque cualquier
    resultado es "al menos cero". Si estuviera al reves, fallar todo saldria
    como una proeza estadistica."""
    r = resumir([pos(retorno=0.01, retorno_sector=0.05) for _ in range(5)])
    assert r.aciertos == 0
    assert r.probabilidad_por_azar == pytest.approx(1.0)


def test_a_tie_does_not_count_as_beating_it():
    """Igualar al sector no es batirlo, y con costes de por medio es perderlo."""
    r = resumir([pos(retorno=0.05, retorno_sector=0.05)])
    assert r.aciertos == 0


# ---------------------------------------------------------------------------
# Cuando el numero todavia no significa nada
# ---------------------------------------------------------------------------
def test_a_few_positions_over_a_few_months_is_not_enough():
    r = resumir([pos(dias=60) for _ in range(3)])
    assert not r.hay_bastante


def test_many_positions_over_a_long_time_is_enough():
    r = resumir([pos(dias=400) for _ in range(12)])
    assert r.hay_bastante


def test_many_positions_bought_last_week_is_still_not_enough():
    """El numero de posiciones no compensa la falta de tiempo: doce compras de
    hace una semana son doce veces la misma semana de mercado."""
    r = resumir([pos(dias=7) for _ in range(12)])
    assert not r.hay_bastante


def test_a_long_history_with_two_positions_is_not_enough_either():
    """Y el tiempo tampoco compensa la falta de posiciones: dos aciertos en
    diez anos siguen siendo dos observaciones."""
    r = resumir([pos(dias=3000) for _ in range(2)])
    assert not r.hay_bastante


def test_the_median_holding_period_ignores_one_very_old_position():
    """Con la media, una posicion heredada de hace diez anos haria parecer que
    hay historico de sobra. Con la mediana, no."""
    r = resumir([pos(dias=10), pos(dias=20), pos(dias=4000)])
    assert r.dias_mediana == 20


# ---------------------------------------------------------------------------
# El veredicto
# ---------------------------------------------------------------------------
def test_the_verdict_says_it_plainly_when_you_lost_to_the_market():
    """Sin la frase, un +18 % en rojo se lee como un buen ano."""
    r = resumir([pos(retorno=0.18, retorno_mercado=0.25, retorno_sector=0.22,
                     dias=400) for _ in range(10)])
    texto = veredicto(r)
    assert "por detras del mercado" in texto
    assert "el indice" in texto


def test_the_verdict_refuses_to_conclude_without_enough_data():
    r = resumir([pos(dias=30) for _ in range(2)])
    assert "suerte" in veredicto(r)


def test_the_verdict_of_an_empty_portfolio_does_not_crash():
    assert veredicto(resumir([])) == "Sin posiciones que atribuir."


def test_the_whole_summary_adds_up_too():
    """La identidad no puede romperse al agregar: la media ponderada de sumas
    es la suma de medias ponderadas solo si los pesos son los mismos."""
    r = resumir([
        pos(coste=3000.0, retorno=0.20, retorno_mercado=0.10, retorno_sector=0.15),
        pos(coste=1000.0, retorno=-0.05, retorno_mercado=0.10, retorno_sector=0.02),
        pos(coste=500.0, retorno=0.30, retorno_mercado=0.08, retorno_sector=None),
    ])
    assert r.cuadra
    assert r.mercado + r.efecto_sector + r.efecto_seleccion == pytest.approx(r.retorno)


def test_the_thresholds_are_not_accidentally_zero():
    """Con los minimos a cero, `hay_bastante` seria siempre cierto y la
    advertencia no saldria nunca."""
    assert attr.MIN_POSICIONES > 1
    assert attr.MIN_DIAS >= 90
