"""Tests de costes e impuestos.

Aqui un fallo no rompe nada: sale un numero plausible y equivocado, y sobre el
se decide que comprar. Por eso cada test fija un caso con la cuenta hecha a
mano en el docstring.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from stocks_tracker.core import costs


# ---------------------------------------------------------------------------
# Comprar y vender
# ---------------------------------------------------------------------------
def test_the_fx_fee_applies_to_the_whole_amount():
    """Es el coste que mas sorprende: el cambio de divisa no se cobra sobre el
    beneficio sino sobre el importe entero. En 1.000 EUR al 0,25 % son 2,50 de
    ida y otros 2,50 de vuelta, mas que muchas comisiones."""
    c = costs.coste_operacion(1000.0, "USD")
    assert c.cambio_divisa == pytest.approx(2.50)


def test_buying_in_euros_has_no_fx_fee():
    assert costs.coste_operacion(1000.0, "EUR").cambio_divisa == 0.0


def test_the_minimum_commission_applies_to_small_orders():
    """Una compra de 50 EUR con comision minima de 1 EUR cuesta el 2 %: hay que
    verlo antes de comprar, no despues."""
    c = costs.coste_operacion(50.0, "EUR")
    assert c.comision >= 1.0
    assert c.pct >= 2.0


def test_the_round_trip_is_what_you_have_to_recover():
    """Mirar solo la compra hace parecer barata una operacion que cuesta el
    doble: nadie compra para no vender nunca."""
    c = costs.coste_operacion(1000.0, "USD")
    assert c.ida_y_vuelta_pct == pytest.approx(c.pct * 2)


def test_a_zero_amount_does_not_divide_by_zero():
    c = costs.coste_operacion(0.0)
    assert c.pct == 0.0


# ---------------------------------------------------------------------------
# Dividendos
# ---------------------------------------------------------------------------
def test_a_us_dividend_is_not_what_it_says():
    """Un 3 % en EE. UU. no renta un 3 %: renta 2,55 % tras la retencion del
    15 %. Compararlo con un 3 % britanico —que no retiene— es comparar dos
    cosas distintas creyendo que son la misma."""
    d = costs.dividendo_neto(3.0, "US")
    assert d.neto_inmediato_pct == pytest.approx(2.55)


def test_without_the_w8ben_form_it_is_much_worse():
    """30 % en vez de 15. El formulario caduca cada tres anos y cuando caduca
    la retencion sube sin avisar."""
    con = costs.dividendo_neto(3.0, "US", con_w8ben=True)
    sin = costs.dividendo_neto(3.0, "US", con_w8ben=False)
    assert sin.neto_inmediato_pct < con.neto_inmediato_pct
    assert sin.perdido_pct == pytest.approx(15.0), (
        "lo retenido por encima del convenio no se deduce en la declaracion"
    )


def test_what_the_treaty_does_not_cover_is_lost_for_good():
    """Suiza retiene el 35 % y el convenio solo permite deducir el 15: ese 20
    no vuelve salvo que reclames al pais de origen, un tramite que en
    cantidades pequenas cuesta mas de lo que devuelve."""
    d = costs.dividendo_neto(3.0, "CH")
    assert d.perdido_pct == pytest.approx(20.0)


def test_a_country_that_does_not_withhold_keeps_it_all():
    d = costs.dividendo_neto(3.0, "GB")
    assert d.neto_inmediato_pct == pytest.approx(3.0)
    assert d.perdido_pct == 0.0


def test_an_unknown_country_uses_a_prudent_assumption():
    """Suponer cero retencion daria una rentabilidad optimista sobre un dato
    que no se tiene."""
    assert costs.dividendo_neto(3.0, "PAIS_RARO").retencion_pct > 0


def test_the_deduction_never_exceeds_what_was_withheld():
    """Francia retiene el 12,8 % y el convenio permitiria deducir hasta el
    15: solo se puede deducir lo que de verdad te quitaron.

    Se prueba con Francia y no con Reino Unido: alli retencion y limite son
    ambos cero, asi que tomar uno u otro da el mismo numero y el test pasaria
    con la formula equivocada. Hace falta un pais donde no coincidan.
    """
    d = costs.dividendo_neto(3.0, "FR")
    assert d.retencion_pct == pytest.approx(12.8)
    assert d.recuperable_pct == pytest.approx(12.8), (
        "se esta deduciendo el limite del convenio en vez de lo retenido"
    )
    assert d.perdido_pct == 0.0


# ---------------------------------------------------------------------------
# IRPF
# ---------------------------------------------------------------------------
def test_the_first_bracket_is_nineteen_percent():
    """5.000 EUR de ganancia, todos en el primer tramo: 950 EUR."""
    assert costs.impuesto_plusvalia(5000.0) == pytest.approx(950.0)


def test_the_brackets_apply_to_the_whole_year_not_to_each_sale():
    """Con 5.000 ya ganados este ano, otros 5.000 no tributan al 19 % entero:
    1.000 caben en el primer tramo (19 %) y 4.000 pasan al segundo (21 %), o
    sea 190 + 840 = 1.030.

    Sin contar lo previo, cada venta se calcularia desde cero y el numero
    saldria bajo justo cuando mas importa.
    """
    assert costs.impuesto_plusvalia(5000.0, ganancias_previas_eur=5000.0) == \
        pytest.approx(1030.0)


def test_a_loss_pays_no_tax():
    assert costs.impuesto_plusvalia(-1000.0) == 0.0


def test_a_loss_does_not_refund_tax_on_earlier_gains():
    """Sin la guarda, restar la cuota de una base menor a la de las ganancias
    previas da un numero NEGATIVO: la funcion diria que la operacion te
    devuelve dinero.

    Compensar perdidas con ganancias existe, pero tiene sus propias reglas
    —limites, orden, cuatro anos de arrastre— y no es lo que calcula esto.
    Devolver un negativo aqui seria inventarse una devolucion.

    Con cero ganancias previas el fallo no se ve, y por eso este caso lleva
    ganancias previas: es el unico que lo distingue.
    """
    assert costs.impuesto_plusvalia(-1000.0, ganancias_previas_eur=5000.0) == 0.0


def test_the_effective_rate_rises_with_the_amount():
    """Y no de golpe: es progresivo por tramos, no un salto."""
    bajo = costs.tipo_efectivo_pct(5000.0)
    alto = costs.tipo_efectivo_pct(100000.0)
    assert 19.0 <= bajo < alto <= 30.0


# ---------------------------------------------------------------------------
# La regla de los dos meses
# ---------------------------------------------------------------------------
def venta(dias_atras: int, perdida: float = 500.0) -> dict:
    return {"closed_at": date.today() - timedelta(days=dias_atras),
            "perdida_eur": perdida}


def test_rebuying_within_two_months_blocks_the_loss():
    """Vendes en perdidas para hacer caja fiscal, recompras a los diez dias
    porque el valor te sigue gustando, y te quedas sin la compensacion que
    buscabas. Es el error mas facil de cometer sin enterarse."""
    r = costs.comprobar_dos_meses("AAPL", [venta(10)])
    assert r.bloquea
    assert r.avisos[0].dias_que_faltan == 50


def test_after_two_months_you_are_free():
    r = costs.comprobar_dos_meses("AAPL", [venta(70)])
    assert not r.bloquea


def test_the_warning_says_when_you_can_buy_again():
    """Decir "no compres" sin decir hasta cuando obliga a echar cuentas a mano
    justo cuando se quiere decidir rapido."""
    r = costs.comprobar_dos_meses("AAPL", [venta(10)])
    aviso = r.avisos[0]
    assert aviso.libre_el > date.today()
    assert (aviso.libre_el - aviso.vendido_el).days == 60


def test_a_sale_with_no_date_is_ignored_instead_of_crashing():
    assert not costs.comprobar_dos_meses("AAPL", [{"perdida_eur": 100}]).bloquea


def test_unlisted_securities_have_a_one_year_window():
    """Para no cotizados la ventana es de un ano, no de dos meses."""
    assert costs.comprobar_dos_meses("X", [venta(200)], cotizado=False).bloquea
    assert not costs.comprobar_dos_meses("X", [venta(200)], cotizado=True).bloquea


# ---------------------------------------------------------------------------
# El resumen
# ---------------------------------------------------------------------------
def test_the_summary_says_how_much_it_has_to_rise_to_break_even():
    """El numero mas util: convierte los costes en el liston que hay que
    superar. Una operacion que sube un 0,4 % con costes de ida y vuelta del
    0,7 % pierde dinero aunque la pantalla la pinte en verde."""
    r = costs.resumen(1000.0, "USD", dividendo_bruto_pct=3.0, pais="US")
    assert r.cuanto_tiene_que_subir_pct == pytest.approx(0.70)
    assert r.dividendo.neto_inmediato_pct == pytest.approx(2.55)


def test_the_summary_without_a_dividend_says_so():
    assert costs.resumen(1000.0).dividendo is None
