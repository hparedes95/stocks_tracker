"""El diario de decisiones.

Lo que se prueba aqui es sobre todo lo que el modulo se NIEGA a hacer. La
tentacion evidente al escribir esto es deducir el veredicto del resultado —si
subio, acierto— y eso destruye justo lo que el diario existe para conservar:
que una compra suba no dice que la decision fuera buena, dice que subio.

El otro fallo caro es el signo. Sin invertirlo en las decisiones de no comprar
y de vender, esquivar una ruina se archiva como fracaso y el diario ensena
exactamente lo contrario de lo que paso.
"""

from __future__ import annotations

import types
from datetime import date, datetime

import pytest

from stocks_tracker.core import journal as j
from stocks_tracker.core.journal import (
    Accion,
    Balance,
    Entrada,
    Veredicto,
    calibracion_por_conviccion,
    pendientes,
)

HOY = date(2026, 8, 12)


def entrada(**kwargs) -> Entrada:
    base = {
        "id": "x", "created_at": datetime(2026, 1, 1), "ticker": "AAA",
        "accion": Accion.COMPRAR, "tesis": "por esto", "horizonte_dias": 90,
        "conviccion": 3, "precio": 100.0, "precio_mercado": 200.0,
    }
    return Entrada(**{**base, **kwargs})


# ---------------------------------------------------------------------------
# El resultado NO es el veredicto
# ---------------------------------------------------------------------------
def test_the_module_never_derives_a_verdict_from_the_outcome():
    """La regla que sostiene todo el diario. Si existiera una funcion que
    convierte un numero en veredicto, se usaria, y entonces el diario diria
    "acierto" cada vez que algo sube: exactamente el sesgo que viene a corregir.

    Se comprueba sobre el modulo entero para que anadirla mas adelante rompa
    este test en vez de pasar desapercibida.
    """
    e = entrada()
    assert e.resultado(150.0) > 0
    assert e.veredicto is None, "el resultado no puede rellenar el veredicto"

    # Solo funciones: `Veredicto` es el enum de opciones, que es una lista de
    # posibilidades y no una deduccion.
    sospechosas = [n for n in dir(j)
                   if "veredicto" in n.lower()
                   and isinstance(getattr(j, n), types.FunctionType)]
    assert sospechosas == [], (
        f"{sospechosas} parece deducir el veredicto; esa parte la pone una "
        "persona, porque es la que el sesgo se lleva por delante"
    )


def test_a_decision_stays_unreviewed_until_a_person_reviews_it():
    assert not entrada().revisada
    assert entrada(veredicto=Veredicto.SUERTE).revisada


# ---------------------------------------------------------------------------
# El signo de las decisiones de no hacer nada
# ---------------------------------------------------------------------------
def test_not_buying_something_that_collapsed_was_a_good_decision():
    """El caso que mas facil es programar al reves. Decidiste no comprar y el
    valor se hundio un 40 %: tu decision te ahorro ese 40 %. Sin invertir el
    signo, esquivar una ruina se archiva como fracaso."""
    e = entrada(accion=Accion.NO_COMPRAR)
    assert e.movimiento(60.0) == pytest.approx(-0.40)
    assert e.resultado(60.0) == pytest.approx(0.40)


def test_not_buying_something_that_doubled_was_a_bad_decision():
    e = entrada(accion=Accion.NO_COMPRAR)
    assert e.resultado(200.0) == pytest.approx(-1.0)


def test_selling_before_a_crash_counts_as_a_good_decision():
    e = entrada(accion=Accion.VENDER)
    assert e.resultado(70.0) == pytest.approx(0.30)


def test_waiting_works_the_same_way_as_not_buying():
    """Esperar es no comprar con otro nombre; si no compartieran signo, la
    misma decision se calificaria distinta segun como se etiquete."""
    assert entrada(accion=Accion.ESPERAR).resultado(60.0) == \
        entrada(accion=Accion.NO_COMPRAR).resultado(60.0)


def test_buying_keeps_the_plain_sign():
    assert entrada(accion=Accion.COMPRAR).resultado(150.0) == pytest.approx(0.50)


def test_every_action_is_classified_as_normal_or_inverse():
    """Anadir una accion nueva sin decidir su signo la dejaria contando como
    compra en silencio, que es lo peor que puede pasar aqui."""
    for accion in Accion:
        assert (accion in j.ACCIONES_INVERSAS) is not (accion is Accion.COMPRAR)


# ---------------------------------------------------------------------------
# Descontar la marea
# ---------------------------------------------------------------------------
def test_gaining_less_than_the_market_is_not_a_win():
    """Comprar y ganar un 10 % en un tramo en el que el mercado hizo un 25 %
    no demuestra nada. Sin descontarlo, cada decision se califica por el ano
    que le toco."""
    e = entrada(precio=100.0, precio_mercado=200.0)
    assert e.resultado(110.0) == pytest.approx(0.10)
    assert e.resultado_relativo(110.0, 250.0) == pytest.approx(-0.15)


def test_not_buying_is_also_measured_against_the_market():
    """Y aqui el mercado tambien se invierte: no comprar mientras todo sube es
    peor que no comprar mientras todo cae, aunque el valor haga lo mismo."""
    e = entrada(accion=Accion.NO_COMPRAR, precio=100.0, precio_mercado=200.0)
    subiendo = e.resultado_relativo(90.0, 240.0)
    cayendo = e.resultado_relativo(90.0, 160.0)
    assert subiendo == pytest.approx(0.10 - (-0.20))
    assert cayendo < subiendo


def test_without_a_market_snapshot_there_is_no_relative_result():
    """Nulo y no cero: cero afirmaria que el mercado se quedo plano."""
    assert entrada(precio_mercado=None).resultado_relativo(150.0, 250.0) is None


# ---------------------------------------------------------------------------
# Datos que faltan
# ---------------------------------------------------------------------------
def test_a_decision_with_no_price_has_no_outcome():
    """Una decision de "no comprar" sobre algo que no seguimos puede no tener
    precio. Sin guarda, se dividiria entre None."""
    assert entrada(precio=None).resultado(150.0) is None


def test_a_zero_price_does_not_divide_by_zero():
    assert entrada(precio=0.0).resultado(150.0) is None


def test_an_infinite_price_is_not_an_outcome():
    assert entrada().resultado(float("inf")) is None


def test_no_price_today_means_no_outcome_yet():
    assert entrada().resultado(None) is None


# ---------------------------------------------------------------------------
# Cuando toca revisar
# ---------------------------------------------------------------------------
def test_a_decision_is_due_when_its_own_deadline_passes():
    """El plazo lo pone quien decide, no el programa: revisar a los 90 dias una
    tesis a tres anos solo produce ruido."""
    e = entrada(created_at=datetime(2026, 1, 1), horizonte_dias=90)
    assert e.vence_el() == date(2026, 4, 1)
    assert e.toca_revisar(HOY)


def test_a_decision_within_its_horizon_is_not_due_yet():
    e = entrada(created_at=datetime(2026, 8, 1), horizonte_dias=90)
    assert not e.toca_revisar(HOY)


def test_an_already_reviewed_decision_stops_asking():
    e = entrada(created_at=datetime(2026, 1, 1), veredicto=Veredicto.ERROR)
    assert not e.toca_revisar(HOY)


def test_the_oldest_pending_review_comes_first():
    """Ordenar al reves dejaria las mas viejas —las que peor se recuerdan y
    mas urge releer— al final de la lista."""
    vieja = entrada(id="vieja", created_at=datetime(2025, 1, 1))
    nueva = entrada(id="nueva", created_at=datetime(2026, 2, 1))
    assert [e.id for e in pendientes([nueva, vieja], HOY)] == ["vieja", "nueva"]


def test_a_zero_horizon_is_due_immediately_and_does_not_go_backwards():
    e = entrada(created_at=datetime(2026, 8, 12), horizonte_dias=0)
    assert e.vence_el() == HOY


def test_a_negative_horizon_does_not_move_the_deadline_into_the_past():
    """Un formulario mal rellenado no puede hacer que la fecha de revision
    retroceda."""
    e = entrada(created_at=datetime(2026, 8, 12), horizonte_dias=-500)
    assert e.vence_el() == HOY


# ---------------------------------------------------------------------------
# El balance
# ---------------------------------------------------------------------------
def test_luck_is_counted_apart_from_skill():
    """El numero mas incomodo del diario: cuantos de tus aciertos fueron por
    algo que no habias escrito."""
    b = Balance([
        entrada(veredicto=Veredicto.ACIERTO),
        entrada(veredicto=Veredicto.SUERTE),
        entrada(veredicto=Veredicto.SUERTE),
        entrada(veredicto=Veredicto.ERROR),
    ])
    assert b.buenos_resultados == 3
    assert b.por_suerte == pytest.approx(2 / 3)


def test_bad_luck_counts_as_good_process():
    """Cambiar el metodo porque una decision bien tomada salio mal es aprender
    lo contrario de lo que paso."""
    b = Balance([entrada(veredicto=Veredicto.MALA_SUERTE)])
    assert b.buen_proceso == 1
    assert b.buenos_resultados == 0


def test_with_nothing_that_went_well_there_is_no_luck_ratio():
    """Nulo y no cero: cero diria "ninguno de tus aciertos fue suerte", que con
    cero aciertos no significa nada."""
    b = Balance([entrada(veredicto=Veredicto.ERROR)])
    assert b.por_suerte is None


def test_an_empty_journal_is_not_an_error():
    b = Balance([])
    assert b.total == 0 and b.por_suerte is None


def test_every_verdict_has_a_description():
    """La pantalla las usa todas; si faltara una saldria un KeyError."""
    assert set(j.DESCRIPCION_VEREDICTO) == set(Veredicto)


def test_the_four_verdicts_split_outcome_from_reason():
    """Los dos ejes del 2x2 tienen que ser independientes: si "salio bien" y
    "buen proceso" agruparan lo mismo, no habria dos ejes sino uno, y el caso
    que importa —salio bien por suerte— desapareceria."""
    assert j.BUENOS != j.PROCESO_BUENO
    assert Veredicto.SUERTE in j.BUENOS
    assert Veredicto.SUERTE not in j.PROCESO_BUENO
    assert Veredicto.MALA_SUERTE in j.PROCESO_BUENO
    assert Veredicto.MALA_SUERTE not in j.BUENOS


# ---------------------------------------------------------------------------
# La conviccion
# ---------------------------------------------------------------------------
def test_conviction_is_checked_against_what_actually_happened():
    """Si las decisiones muy convencidas no salen mejor que las dudosas, la
    conviccion no mide nada — y es la que hace apostar mas fuerte."""
    cal = calibracion_por_conviccion([
        entrada(conviccion=5, veredicto=Veredicto.ERROR),
        entrada(conviccion=5, veredicto=Veredicto.ERROR),
        entrada(conviccion=1, veredicto=Veredicto.ACIERTO),
    ])
    assert cal[5]["acierta"] == 0.0
    assert cal[1]["acierta"] == 1.0


def test_conviction_levels_with_no_decisions_are_absent_not_zero():
    """Un nivel sin decisiones con un 0 % pintado parece un historial de
    fracasos donde no hay historial."""
    cal = calibracion_por_conviccion([entrada(conviccion=3,
                                              veredicto=Veredicto.ACIERTO)])
    assert set(cal) == {3}
