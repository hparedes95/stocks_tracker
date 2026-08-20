"""Cuando una barra "imposible" es solo redondeo, y cuanto se sale de verdad.

Segunda instalacion real bloqueada, y por mi culpa dos veces seguidas:

  ohlc_incoherente: 4 sesiones con precios imposibles en 4 valores que SI usan
  el rango del dia. La primera: HUBB el 2021-05-05, apertura por debajo del
  minimo. Afectados: DE, HUBB, LMT, XLRE. 3 de ellas son de las ultimas 10
  sesiones.

Dos fallos distintos ahi dentro:

1. El mensaje no dice POR CUANTO se sale. Una barra que incumple por una
   diezmillonesima y otra que incumple por un 300 % daban exactamente el mismo
   texto, y con ese texto no hay forma —ni para quien lo lee ni para el codigo
   que decide si parar— de distinguirlas. Yahoo sirve los cuatro precios por
   caminos distintos y los redondea distinto: una accion que abre justo en su
   minimo llega con `open = 512.46` y `low = 512.4599914550781`, el mismo
   numero escrito con dos precisiones, y `open < low` es True.

2. Parar el calculo de 600 empresas porque tres tienen una barra rara castiga a
   las 597 que no tienen nada. La proteccion que importa ya existe y es por
   valor: sin ATR, el gestor de riesgo veta la orden.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stocks_tracker.core import quality


def barra(**campos) -> dict:
    base = {"ticker": "AAA", "date": date(2026, 8, 18),
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5}
    base.update(campos)
    return base


def evaluar(*barras) -> pd.DataFrame:
    return quality.incoherencias_ohlc(pd.DataFrame(list(barras)))


# ---------------------------------------------------------------------------
# El redondeo del proveedor no es una barra rota
# ---------------------------------------------------------------------------

def test_el_mismo_numero_con_dos_precisiones_no_es_una_incoherencia():
    """El caso literal de Yahoo: el minimo redondeado a dos decimales y la
    apertura con la precision de un float32 promovido a float64.

    El orden importa y la primera version de este test lo tenia al reves: con
    `open` POR ENCIMA de `low` no hay nada que incumplir —una apertura puede
    estar por encima del minimo, faltaria mas— y el test pasaba con tolerancia
    cero. No probaba nada. La incoherencia solo aparece cuando la apertura
    queda por DEBAJO del minimo, aunque sea por una millonesima.
    """
    fila = barra(open=512.4599914550781, low=512.46, high=515.0, close=513.0)
    assert fila["open"] < fila["low"], "el escenario no reproduce el problema"

    assert evaluar(fila).empty, (
        "una diferencia de 8e-6 sobre 512 sigue contando como precio imposible"
    )


def test_una_incoherencia_de_verdad_sigue_saltando():
    """El contrario, para que el de arriba no pase por el motivo equivocado."""
    malas = evaluar(barra(open=90.0, low=99.0))

    assert len(malas) == 1
    assert malas.iloc[0]["motivo"] == "apertura por debajo del minimo"


def test_la_tolerancia_es_relativa_al_precio():
    """1e-5 sobre 512 es redondeo; sobre 0,004 es un error del 25 %. Con una
    tolerancia absoluta, el penny stock roto pasaria desapercibido."""
    caro = barra(ticker="CARO", open=512.46, low=512.46001, high=515.0, close=513.0)
    barato = barra(ticker="BARATO", open=0.004, low=0.00401, high=0.005, close=0.0045)

    afectados = set(evaluar(caro, barato)["ticker"])

    assert afectados == {"BARATO"}


def test_la_tolerancia_deja_sitio_al_error_real_mas_pequeno():
    """Los precios se cotizan en centimos: la discrepancia real mas pequena en
    una accion de 100 es de 0,01, o sea 1e-4 relativo. El umbral tiene que
    quedar claramente por debajo o taparia errores de verdad."""
    error_real_mas_pequeno = 0.01 / 100.0     # un centimo sobre una accion de 100
    margen = error_real_mas_pequeno / quality.TOLERANCIA_OHLC

    assert margen >= 100, (
        f"solo quedan {margen:.0f}x entre la tolerancia y el error real mas "
        "pequeno que importa: se esta acercando a tapar errores de verdad"
    )


def test_un_precio_a_cero_no_admite_tolerancia():
    """No hay contra que medir un cero en relativo, y ademas no es redondeo."""
    malas = evaluar(barra(low=0.0))

    assert len(malas) == 1
    assert malas.iloc[0]["motivo"] == "precio no positivo"
    assert malas.iloc[0]["desvio"] == 1.0


def test_un_precio_negativo_se_lleva_la_etiqueta_por_encima_de_todo():
    """Un cierre negativo incumple ADEMAS "cierre por debajo del minimo", y esa
    regla se comprueba antes. Si la marca del precio no positivo respetara el
    turno, la barra saldria etiquetada con la consecuencia en vez de con la
    causa: lo primero que hay que decir de un precio de -5 es que es negativo.
    """
    fila = barra(close=-5.0)
    malas = evaluar(fila)

    assert malas.iloc[0]["motivo"] == "precio no positivo"


# ---------------------------------------------------------------------------
# Cuanto se sale
# ---------------------------------------------------------------------------

def test_el_hallazgo_dice_por_cuanto_se_sale():
    """Sin la magnitud no hay forma de juzgar el aviso."""
    malas = evaluar(barra(close=150.0, high=100.0, low=99.0, open=100.0))

    assert malas.iloc[0]["desvio"] == pytest.approx((150.0 - 100.0) / 150.0)


def test_el_desvio_es_el_peor_aunque_el_motivo_sea_otro():
    """Una barra puede incumplir varias reglas a la vez, y las dos columnas
    responden a preguntas distintas.

    El MOTIVO es una etiqueta que alguien lee y agrupa, asi que sigue un orden
    fijo de importancia. Quedarse con "la regla de mayor desvio" hacia que la
    etiqueta bailase entre ejecuciones cuando dos violaciones iban parejas, y
    una etiqueta inestable no sirve ni para buscar ni para contar.

    El DESVIO decide si esto para el calculo, y ahi lo que importa es la peor
    violacion que tenga la barra, la nombre o no la etiqueta.
    """
    # `low > close` se incumple por poco; `open > high` por mucho.
    fila = barra(open=400.0, high=101.0, low=99.0, close=98.9)

    malas = evaluar(fila)

    assert malas.iloc[0]["motivo"] == "cierre por debajo del minimo", (
        "el motivo ya no sigue el orden de importancia"
    )
    assert malas.iloc[0]["desvio"] == pytest.approx((400.0 - 101.0) / 400.0), (
        "el desvio se ha quedado con la violacion que nombra la etiqueta en vez "
        "de con la peor"
    )


def test_el_desvio_llega_al_mensaje():
    precios = pd.DataFrame([barra(close=150.0, high=100.0)])
    hallazgos = [h for h in quality.evaluar(precios, instrumentos_ohlc={"AAA"})
                 if h.check == "ohlc_incoherente"]

    assert "33.33" in hallazgos[0].detail, (
        f"el mensaje no dice por cuanto se sale: {hallazgos[0].detail}"
    )


# ---------------------------------------------------------------------------
# Una barra rara reciente ya no para el calculo de todo el universo
# ---------------------------------------------------------------------------

def serie(ticker: str, dias: int) -> pd.DataFrame:
    fechas = pd.bdate_range(date(2026, 1, 5), periods=dias)
    return pd.DataFrame({"ticker": ticker, "date": fechas, "open": 100.0,
                         "high": 101.0, "low": 99.0, "close": 100.5,
                         "volume": 1e6})


def universo(malos: dict) -> pd.DataFrame:
    """Veinte valores; los de `malos` con el cierre disparado en su ultima sesion."""
    precios = pd.concat([serie(f"T{i}", 400) for i in range(20)], ignore_index=True)
    ultima = precios["date"].max()
    for ticker in malos:
        fila = (precios["ticker"] == ticker) & (precios["date"] == ultima)
        precios.loc[fila, "close"] = 500.0
    return precios


def hallazgo(precios: pd.DataFrame) -> quality.Hallazgo:
    todos = [h for h in quality.evaluar(precios,
                                        instrumentos_ohlc={f"T{i}" for i in range(20)})
             if h.check == "ohlc_incoherente"]
    assert len(todos) == 1
    return todos[0]


def test_tres_barras_recientes_no_paran_el_calculo_de_las_demas():
    """El caso de la instalacion: DE, LMT y XLRE con una barra rara de esta
    semana dejaban sin ranking a las otras 597 empresas."""
    h = hallazgo(universo({"T0", "T1", "T2"}))

    assert h.severity == quality.AVISO, (
        "tres barras raras vuelven a impedir calcular el universo entero"
    )


def test_pero_se_dice_que_esos_valores_se_quedan_sin_stop():
    """No parar no es callarse. Sin ATR el bot no abre posicion en ellos, y eso
    hay que decirlo."""
    h = hallazgo(universo({"T0", "T1", "T2"}))

    assert "sin ATR" in h.detail
    assert "ultimas 10 sesiones" in h.detail


def test_muchas_barras_imposibles_siguen_parando_el_calculo():
    """El unico motivo que queda para parar: tantas que la descarga entera es
    sospechosa."""
    precios = pd.concat([serie(f"T{i}", 400) for i in range(20)], ignore_index=True)
    for f in sorted(precios["date"].unique())[:60]:
        precios.loc[(precios["ticker"] == "T0") & (precios["date"] == f), "close"] = 500.0

    assert hallazgo(precios).severity == quality.BLOQUEA


def test_las_divisas_siguen_sin_bloquear_nunca():
    """Regresion de la averia anterior a esta: 411 sesiones raras de EURUSD=X
    —un par de divisas del que solo se usa el cierre— impedian calcular las
    600 acciones."""
    precios = pd.concat([serie(f"T{i}", 400) for i in range(20)]
                        + [serie("EURUSD=X", 400)], ignore_index=True)
    for f in sorted(precios["date"].unique())[:50]:
        fila = (precios["ticker"] == "EURUSD=X") & (precios["date"] == f)
        precios.loc[fila, "close"] = 500.0

    ohlc = [h for h in quality.evaluar(precios,
                                       instrumentos_ohlc={f"T{i}" for i in range(20)})
            if h.check == "ohlc_incoherente"]

    assert [h.severity for h in ohlc] == [quality.AVISO]
