"""El umbral de barras imposibles cambiaba de significado segun quien llamara.

EL FALLO, ENCONTRADO EN LA AUDITORIA FINANCIERA

`quality.evaluar` calcula `fraccion = barras_malas / len(precios)` y la compara
con un umbral unico del 0,1 %. Pero `len(precios)` es una cosa distinta en cada
uno de los dos sitios que la llaman:

- La PUERTA DE CALIDAD le pasa el almacen entero (del orden de 170.000 barras).
  Ahi 34 barras raras son el 0,02 %: por debajo del umbral, y bien, porque una
  rareza suelta del proveedor no invalida una descarga.
- La INGESTA le pasa el LOTE que acaba de descargar (91 filas en una noche
  normal). Ahi UNA sola barra mala ya es el 1,1 %, diez veces el umbral, y el
  hallazgo salia como GRAVE —el que para el calculo— practicamente cada noche.

Un semaforo que siempre esta en rojo deja de mirarse. Asi es como se pierde una
puerta de calidad: no se desactiva, se ignora.

Y ademas el mensaje MENTIA. Decia literalmente "Son el 37.36 % del almacen"
cuando ese 37 % era del lote de 91 filas. La cifra estaba bien calculada y la
etiqueta era falsa, que es peor que un numero mal: quien lo lea va a buscar una
averia en 63.000 barras que no existe.

Arreglo: dos umbrales, uno por denominador, y el denominador se NOMBRA en el
mensaje.
"""

from __future__ import annotations

import pandas as pd

from stocks_tracker.core import quality as q


def _precios(filas: list[dict]) -> pd.DataFrame:
    base = {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
            "adj_close": 100.0, "volume": 1_000_000}
    return pd.DataFrame([{**base, **f} for f in filas])


def _lote(n_sanas: int, n_malas: int = 1) -> pd.DataFrame:
    """Un lote con `n_malas` barras imposibles (maximo por debajo del minimo)."""
    fechas = pd.bdate_range("2024-03-01", periods=n_sanas + n_malas)
    filas = []
    for i, d in enumerate(fechas):
        mala = i < n_malas
        filas.append({"ticker": "AAA", "date": d,
                      "high": 90.0 if mala else 101.0,
                      "low": 99.0 if mala else 99.0})
    return _precios(filas)


def _hallazgo(precios: pd.DataFrame, **kw) -> q.Hallazgo:
    fuera = [h for h in q.evaluar(precios, instrumentos_ohlc={"AAA"}, **kw)
             if h.check == "ohlc_incoherente"]
    assert fuera, "la comprobacion no ha llegado a ejecutarse"
    return fuera[0]


# ---------------------------------------------------------------------------
# El fallo: una barra mala en una ingesta incremental
# ---------------------------------------------------------------------------
def test_una_barra_mala_en_un_lote_pequeno_no_para_la_noche():
    """EL CASO EXACTO. 91 filas, una barra rara: el 1,1 %.

    Con el umbral del almacen (0,1 %) esto se declaraba GRAVE y paraba el
    calculo. Con el del lote (5 %) avisa y sigue, que es lo proporcionado: la
    barra se aparta, ese ticker se queda sin ATR y la regla 13 del gestor de
    riesgo veta la orden. La proteccion por valor ya actua sin parar nada.
    """
    lote = _lote(n_sanas=90, n_malas=1)

    h = _hallazgo(lote, ambito=q.AMBITO_LOTE)

    assert h.severity == q.AVISO, (
        "una sola barra rara en la descarga de una noche esta parando el calculo"
    )


def test_una_descarga_mayoritariamente_mala_si_para_el_calculo():
    """El umbral del lote no puede ser una barra libre: el 37 % de las filas
    imposibles no es una rareza del proveedor, es una descarga entera mal."""
    lote = _lote(n_sanas=60, n_malas=34)

    h = _hallazgo(lote, ambito=q.AMBITO_LOTE)

    assert h.severity == q.BLOQUEA
    assert q.bloqueantes([h])


def test_sobre_el_almacen_el_umbral_sigue_siendo_el_estricto():
    """La puerta de calidad no se relaja. Sobre el almacen entero, un 1 % de
    barras imposibles SI es una averia: son cientos de barras."""
    almacen = _lote(n_sanas=90, n_malas=1)

    h = _hallazgo(almacen, ambito=q.AMBITO_ALMACEN)

    assert h.severity == q.BLOQUEA, "el umbral del almacen se ha aflojado"


def test_por_defecto_se_mide_contra_el_almacen():
    """Quien no diga el ambito no puede quedarse con el umbral permisivo por
    accidente: el que no lo dice es la puerta de calidad."""
    almacen = _lote(n_sanas=90, n_malas=1)

    assert _hallazgo(almacen).severity == q.BLOQUEA


# ---------------------------------------------------------------------------
# El mensaje: la cifra correcta con la etiqueta falsa
# ---------------------------------------------------------------------------
def test_el_mensaje_nombra_el_lote_cuando_mide_el_lote():
    """Decia "el 37 % del almacen" midiendo 91 filas. Quien lo lea buscara una
    averia mucho mayor de la que hay."""
    h = _hallazgo(_lote(n_sanas=60, n_malas=34), ambito=q.AMBITO_LOTE)

    assert "del lote descargado" in h.detail
    assert "del almacen" not in h.detail, "el mensaje sigue diciendo almacen"


def test_el_mensaje_nombra_el_almacen_cuando_mide_el_almacen():
    h = _hallazgo(_lote(n_sanas=90, n_malas=1), ambito=q.AMBITO_ALMACEN)

    assert "del almacen" in h.detail


def test_el_mensaje_cita_el_umbral_que_de_verdad_se_ha_aplicado():
    """Citar el 0,10 % cuando el que ha saltado es el 5,00 % deja el aviso sin
    poder comprobarse: los numeros del mensaje no cuadran entre si."""
    h = _hallazgo(_lote(n_sanas=60, n_malas=34), ambito=q.AMBITO_LOTE)

    assert "5.00%" in h.detail
    assert "0.10%" not in h.detail


# ---------------------------------------------------------------------------
# Que la ingesta lo diga de verdad
# ---------------------------------------------------------------------------
def test_la_ingesta_declara_que_lo_que_pasa_es_un_lote():
    """El arreglo no vale de nada si el unico sitio que mide un lote sigue sin
    decirlo. Se comprueba sobre el codigo real, no sobre un doble."""
    import inspect

    from stocks_tracker.ingest import run_ingest

    fuente = inspect.getsource(run_ingest.ingest_prices)

    assert "ambito=quality.AMBITO_LOTE" in fuente, (
        "la ingesta vuelve a medir el lote con el umbral del almacen"
    )
