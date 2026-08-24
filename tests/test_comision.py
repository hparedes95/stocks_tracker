"""La comision no sabia escribir el modelo mas comun en Espana.

EL FALLO, ENCONTRADO EN LA AUDITORIA FINANCIERA

    comision = max(comision_fija_eur, porcentual, comision_minima_eur)

Un `max` de tres solo puede expresar "el mayor de estos", y casi ningun broker
espanol cobra asi. ING, Trade Republic y DEGIRO cobran una parte FIJA MAS un
porcentaje:

    ING: 8 EUR + 0,10 %
    Compra de 20.000 EUR -> 8 + 20 = 28 EUR de comision.
    max(8, 20, 1) devolvia 20 EUR. Ocho euros de menos, un 29 % por debajo.

Y esa cifra no es decorativa: alimenta el "cuanto tiene que subir para no perder
dinero" y el coste de ida y vuelta, que es justo el numero con el que se decide
si una compra pequena merece la pena. Subestimar la comision hace que una
operacion que no sale a cuenta lo parezca.

Ademas, en aquella formula `comision_fija_eur` y `comision_minima_eur` eran EL
MISMO MANDO con dos nombres: gana el mayor de los dos y el otro no hace nada
nunca. Quien pusiera una fija de 8 y una minima de 1 tenia la minima muerta, y
al reves igual.

ARREGLO

    comision = max(fija + porcentual, minima)

La fija se suma, la minima es un suelo, y cada campo significa una cosa. Un
broker que solo cobre porcentaje pone la fija a cero.

COMPATIBILIDAD: con la configuracion que se entrega (fija 1, pct 0, minima 1) el
resultado es identico, max(1 + 0, 1) = 1.
"""

from __future__ import annotations

import pytest

from stocks_tracker.core import costs


@pytest.fixture
def broker(monkeypatch):
    """Deja fijar la tarifa del broker sin tocar `config/costs.yaml`."""
    def poner(fija: float, pct: float, minima: float,
              divisa: float = 0.0, canon: float = 0.0):
        monkeypatch.setattr(costs, "_broker", lambda: {
            "comision_fija_eur": fija,
            "comision_pct": pct,
            "comision_minima_eur": minima,
            "cambio_divisa_pct": divisa,
            "canon_pct": canon,
        })
    return poner


# ---------------------------------------------------------------------------
# El fallo
# ---------------------------------------------------------------------------
def test_fijo_mas_porcentaje_se_suman(broker):
    """EL CASO EXACTO. ING: 8 EUR + 0,10 % sobre 20.000 EUR son 28, no 20."""
    broker(fija=8.0, pct=0.10, minima=1.0)

    assert costs.coste_operacion(20_000.0).comision == pytest.approx(28.0)


def test_el_minimo_es_un_suelo_y_no_un_tercer_candidato(broker):
    """Con 8 + 0,10 % sobre 100 EUR salen 8,10; un minimo de 12 lo sube a 12.
    Antes el minimo competia con la fija y una de las dos sobraba siempre."""
    broker(fija=8.0, pct=0.10, minima=12.0)

    assert costs.coste_operacion(100.0).comision == pytest.approx(12.0)


def test_un_broker_de_solo_porcentaje_con_minimo(broker):
    """El otro modelo comun: 0,20 % con un minimo de 2 EUR."""
    broker(fija=0.0, pct=0.20, minima=2.0)

    assert costs.coste_operacion(5_000.0).comision == pytest.approx(10.0)
    assert costs.coste_operacion(100.0).comision == pytest.approx(2.0)


def test_una_tarifa_plana_sigue_siendo_plana(broker):
    broker(fija=1.0, pct=0.0, minima=0.0)

    assert costs.coste_operacion(50.0).comision == pytest.approx(1.0)
    assert costs.coste_operacion(50_000.0).comision == pytest.approx(1.0)


def test_la_configuracion_que_se_entrega_no_cambia_de_resultado(broker):
    """El arreglo no puede mover el numero de quien no ha tocado nada."""
    broker(fija=1.0, pct=0.0, minima=1.0)

    assert costs.coste_operacion(1_000.0).comision == pytest.approx(1.0)


def test_un_importe_de_cero_no_paga_comision(broker):
    """Ni la fija ni el minimo: no hay operacion que cobrar."""
    broker(fija=8.0, pct=0.10, minima=12.0)

    assert costs.coste_operacion(0.0).comision == 0.0


# ---------------------------------------------------------------------------
# La consecuencia: el liston que hay que superar
# ---------------------------------------------------------------------------
def test_el_liston_de_ida_y_vuelta_recoge_la_comision_completa(broker):
    """Es donde se ve el dano. Con 8 + 0,10 % sobre 1.000 EUR la comision son
    9 EUR, no 8: el 0,90 % por operacion y el 1,80 % de ida y vuelta."""
    broker(fija=8.0, pct=0.10, minima=1.0)

    op = costs.coste_operacion(1_000.0)

    assert op.comision == pytest.approx(9.0)
    assert op.pct == pytest.approx(0.90)
    assert op.ida_y_vuelta_pct == pytest.approx(1.80)


def test_el_cambio_de_divisa_sigue_yendo_aparte(broker):
    """No se toca: se cobra sobre el importe entero y solo fuera del euro."""
    broker(fija=1.0, pct=0.0, minima=1.0, divisa=0.25)

    assert costs.coste_operacion(1_000.0, "USD").cambio_divisa == pytest.approx(2.5)
    assert costs.coste_operacion(1_000.0, "EUR").cambio_divisa == 0.0
