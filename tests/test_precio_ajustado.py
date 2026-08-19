"""Que serie usa cada calculo: precio cotizado o precio ajustado.

No es un detalle de implementacion. Usar la equivocada fabrica senales de cosas
que no ocurrieron, y no da ningun error: solo una senal de mas.

LA REGLA

- **Retornos** (momentum, medias, MACD, RSI, volatilidad, drawdown, fuerza
  relativa) van sobre `adj_close`. Un valor que reparte el 4 % en dividendos no
  ha perdido un 4 %, y medirlo sobre el precio sin ajustar diria que si.
- **Niveles** (maximos y minimos de 52 semanas, soportes, resistencias) van
  sobre `close`. Un maximo anual es un hecho del mercado —el precio al que se
  cruzaron ordenes ese dia— y no cambia porque la empresa reparta un dividendo
  seis meses despues.

EL FALLO QUE HABIA

`dist_52w_high` se calculaba sobre la serie ajustada. Como `adj_close` divide el
PASADO por el dividendo acumulado, el maximo de hace un ano vale hoy menos de lo
que valia, y un valor que no ha vuelto a sus maximos aparece rompiendolos.

El sesgo no es aleatorio: va hacia los valores de dividendo alto, que son
justamente los que mas reparte y los que mas aparecen en un dashboard de
inversion a largo plazo.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from stocks_tracker.core import indicators as ind
from stocks_tracker.core import signals as sig

N = 700
FECHAS = pd.bdate_range("2022-01-03", periods=N)


def _serie_que_no_vuelve_a_maximos() -> np.ndarray:
    """Sube a 130, cae a 120 y se recupera hasta 129. Nunca supera su maximo.

    El pico se coloca en la sesion 460 a proposito: asi sigue dentro de la
    ventana de 252 sesiones al final de la serie. Si quedara fuera, el maximo
    movil seria otro y el escenario no probaria nada.
    """
    return np.concatenate([
        np.linspace(100, 130, 460),
        np.linspace(130, 120, 60),
        np.linspace(120, 129, 180),
    ])


def _ajustar(real: np.ndarray, dividendo: float = 0.012,
             cada: int = 63) -> np.ndarray:
    """Como ajusta un proveedor: divide el pasado por el dividendo acumulado."""
    factor = np.ones(len(real))
    for dia in range(cada, len(real), cada):
        factor[:dia] *= (1 - dividendo)
    return real * factor / factor[-1]


def _barras(cotizado: np.ndarray, ajustado: np.ndarray) -> pd.DataFrame:
    volumen = np.full(len(cotizado), 1_000_000.0)
    volumen[::5] = 5_000_000.0          # picos, que la ruptura exige volumen
    return pd.DataFrame(
        {"open": cotizado, "high": cotizado * 1.001, "low": cotizado * 0.999,
         "close": cotizado, "adj_close": ajustado, "volume": volumen},
        index=FECHAS,
    )


# ---------------------------------------------------------------------------
# El fallo, demostrado
# ---------------------------------------------------------------------------
def test_a_stock_that_never_made_a_new_high_does_not_break_out():
    """EL CASO. Con la serie ajustada, `dist_52w_high` salia en 0,0000 y
    `HIGH_52W_BREAKOUT` se disparaba en una fecha en la que el valor estaba un
    0,77 % POR DEBAJO de su maximo del ano.

    Una ruptura de maximos anuales que no ocurrio en el mercado.
    """
    real = _serie_que_no_vuelve_a_maximos()
    barras = _barras(real, _ajustar(real))

    indicadores = ind.compute_all(barras)
    detectadas = sig.detect(indicadores)
    rupturas = ([] if detectadas.empty
                else detectadas[detectadas["signal_id"] == "HIGH_52W_BREAKOUT"])

    assert real[-1] < real.max(), "el escenario exige que no vuelva a maximos"
    assert len(rupturas) == 0, (
        "se ha disparado una ruptura de maximos anuales en un valor que nunca "
        "supero su maximo"
    )


def test_the_distance_to_the_high_is_measured_on_the_quoted_price():
    """El numero del que sale la senal. Sobre la serie ajustada da 0,0000 —en
    maximos— cuando el precio cotizado esta un 0,77 % por debajo."""
    real = _serie_que_no_vuelve_a_maximos()
    indicadores = ind.compute_all(_barras(real, _ajustar(real)))

    esperado = real[-1] / real[-252:].max() - 1.0
    assert indicadores["dist_52w_high"].iloc[-1] < -0.005
    assert indicadores["dist_52w_high"].iloc[-1] == np.float32(esperado).item() or \
        abs(indicadores["dist_52w_high"].iloc[-1] - esperado) < 1e-9


def test_the_adjusted_series_would_have_lied_here():
    """La contraprueba, y la que documenta el fallo: si `dist_52w_high` se
    midiera sobre la serie ajustada, ese mismo valor apareceria en maximos.

    Sin este test, el anterior podria estar pasando porque el escenario es
    inofensivo, no porque el arreglo funcione.
    """
    real = _serie_que_no_vuelve_a_maximos()
    ajustado = _ajustar(real)

    # Lo que hacia el codigo antes: medir el nivel sobre la serie ajustada.
    como_estaba = ind.distance_to_high(pd.Series(ajustado, index=FECHAS))
    como_esta = ind.distance_to_high(pd.Series(real, index=FECHAS))

    assert como_estaba.iloc[-1] > -0.002, "la serie ajustada lo pone en maximos"
    assert como_esta.iloc[-1] < -0.005, "el precio cotizado dice la verdad"


def test_the_bias_grows_with_the_dividend():
    """No es un error aleatorio: apunta a los valores de dividendo alto, que son
    justamente los que mas salen en un dashboard de inversion a largo plazo."""
    real = _serie_que_no_vuelve_a_maximos()
    serie = pd.Series(real, index=FECHAS)
    sin_ajustar = ind.distance_to_high(serie).iloc[-1]

    distancias = [
        ind.distance_to_high(pd.Series(_ajustar(real, d), index=FECHAS)).iloc[-1]
        for d in (0.002, 0.012, 0.025)
    ]
    assert distancias == sorted(distancias), (
        "cuanto mayor el dividendo, mas cerca de un maximo falso"
    )
    assert all(d >= sin_ajustar for d in distancias)


# ---------------------------------------------------------------------------
# Lo que SI tiene que ir ajustado
# ---------------------------------------------------------------------------
def test_returns_are_measured_on_the_adjusted_series():
    """La otra mitad de la regla. Un valor plano que reparte dividendo NO tiene
    momentum cero: ha rentado lo repartido. Medirlo sobre el precio cotizado
    diria que no se movio, y penalizaria a los valores de dividendo alto en el
    ranking justo por repartir."""
    plano = np.full(N, 100.0)
    ajustado = _ajustar(plano)
    indicadores = ind.compute_all(_barras(plano, ajustado))

    assert indicadores["roc_12m"].iloc[-1] > 0.02, (
        "el retorno tiene que recoger los dividendos"
    )


def test_the_drawdown_is_measured_on_the_adjusted_series():
    """Lo que le duele a quien lo tiene es la caida de su PATRIMONIO, y eso
    incluye los dividendos cobrados.

    El escenario separa las dos lecturas: el precio cotizado baja un 4 %, pero
    con los dividendos repartidos el que lo tuviera va ganando. Medir el
    drawdown sobre el precio cotizado diria que ha perdido, y penalizaria en el
    ranking a los valores de dividendo alto justo por repartir.
    """
    cayendo = np.linspace(100.0, 96.0, N)
    indicadores = ind.compute_all(_barras(cayendo, _ajustar(cayendo)))

    de_cotizado = ind.drawdown(pd.Series(cayendo, index=FECHAS)).iloc[-1]
    de_patrimonio = indicadores["drawdown"].iloc[-1]

    assert de_cotizado < -0.03, "el precio cotizado si cae un 4 %"
    # No exactamente cero: entre un dividendo y el siguiente el precio sigue
    # cayendo, asi que quedan micro-caidas de centesimas. Lo que importa es el
    # orden de magnitud —dos ordenes de diferencia—, no el cero exacto.
    assert de_patrimonio > -0.005, "con los dividendos apenas hay caida"
    assert abs(de_patrimonio) < abs(de_cotizado) / 10


def test_the_distance_to_the_low_is_also_measured_on_the_quoted_price():
    """El minimo anual es un nivel igual que el maximo, y va por el mismo sitio.

    Aqui el ajuste falla en la direccion contraria: empuja los minimos antiguos
    hacia abajo, asi que un valor que SI ha vuelto a minimos parece lejos de
    ellos y la ruptura a la baja no se detecta. Un fallo que quita avisos es
    peor que uno que los sobra, porque no se nota nunca.
    """
    real = np.concatenate([
        np.linspace(120, 80, 460),
        np.linspace(80, 95, 60),
        np.linspace(95, 81, 180),
    ])
    indicadores = ind.compute_all(_barras(real, _ajustar(real)))
    esperado = real[-1] / real[-252:].min() - 1.0
    assert abs(indicadores["dist_52w_low"].iloc[-1] - esperado) < 1e-9

    con_ajuste = ind.distance_to_low(pd.Series(_ajustar(real), index=FECHAS))
    assert con_ajuste.iloc[-1] > indicadores["dist_52w_low"].iloc[-1], (
        "la serie ajustada aleja el precio de su minimo y esconde la ruptura"
    )


# ---------------------------------------------------------------------------
# Que el arreglo llegue de verdad al almacen
# ---------------------------------------------------------------------------
def test_a_signal_that_stops_firing_is_removed(tmp_path, monkeypatch):
    """El upsert actualiza y anade, pero nunca quita.

    Sin esto, las rupturas de maximos falsas que genero el fallo del precio
    ajustado se habrian quedado en la tabla para siempre y el arreglo no habria
    servido de nada sobre un almacen ya existente: la validacion las seguiria
    contando como si nada.
    """
    from stocks_tracker.compute.run_compute import compute_indicators
    from stocks_tracker.core import db

    class Stub:
        warehouse_path = tmp_path / "test.duckdb"

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()

    real = _serie_que_no_vuelve_a_maximos()
    barras = _barras(real, _ajustar(real)).reset_index(names="date")
    barras["ticker"] = "AAA"
    barras["source"] = "test"
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame(
            [{"ticker": "AAA", "asset_class": "equity"}]), keys=["ticker"])
        db.upsert_df(conn, "prices_daily", barras, keys=["ticker", "date"])
        # Una senal vieja que hoy ya no se dispara, como la que dejaba el fallo.
        db.upsert_df(conn, "signals", pd.DataFrame([{
            "ticker": "AAA", "date": FECHAS[-1].date(),
            "signal_id": "HIGH_52W_BREAKOUT", "direction": "bullish",
            "strength": 1.0, "detail": "falsa, de antes del arreglo",
        }]), keys=["ticker", "date", "signal_id"])

    compute_indicators(lookback=None, full=True)

    with db.connect(read_only=True) as conn:
        quedan = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE signal_id = 'HIGH_52W_BREAKOUT'"
        ).fetchone()[0]
    assert quedan == 0, "la senal falsa sigue en la tabla despues de recalcular"


def test_pruning_does_not_touch_dates_outside_the_recomputed_window(tmp_path,
                                                                    monkeypatch):
    """Se borra SOLO dentro del tramo recalculado. Fuera de el no se ha
    calculado nada y no hay con que comparar: barrer mas seria borrar historico
    que sigue siendo bueno."""
    from stocks_tracker.compute.run_compute import _prune_stale_signals
    from stocks_tracker.core import db

    class Stub:
        warehouse_path = tmp_path / "test.duckdb"

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()

    vieja = pd.Timestamp("2019-01-02").date()
    dentro = pd.Timestamp("2024-01-02").date()
    with db.connect() as conn:
        db.upsert_df(conn, "signals", pd.DataFrame([
            {"ticker": "AAA", "date": vieja, "signal_id": "S",
             "direction": "bullish", "strength": 1.0, "detail": ""},
            {"ticker": "AAA", "date": dentro, "signal_id": "S",
             "direction": "bullish", "strength": 1.0, "detail": ""},
        ]), keys=["ticker", "date", "signal_id"])

        indicadores = pd.DataFrame([{"ticker": "AAA", "date": dentro}])
        borradas = _prune_stale_signals(conn, indicadores, pd.DataFrame())
        fechas = [f[0] for f in conn.execute(
            "SELECT date FROM signals ORDER BY date").fetchall()]

    assert borradas == 1
    assert fechas == [vieja], "se ha borrado historico fuera de la ventana"


def test_pruning_with_new_signals_also_respects_the_window(tmp_path, monkeypatch):
    """El mismo limite por el otro camino del codigo.

    `_prune_stale_signals` tiene dos ramas —con senales nuevas y sin ellas— y la
    proteccion de la ventana tiene que estar en las DOS. Probar solo la de
    "sin senales nuevas" deja la otra sin cubrir, que es justo la que se ejecuta
    en el caso normal.
    """
    from stocks_tracker.compute.run_compute import _prune_stale_signals
    from stocks_tracker.core import db

    class Stub:
        warehouse_path = tmp_path / "test.duckdb"

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()

    vieja = pd.Timestamp("2019-01-02").date()
    dentro = pd.Timestamp("2024-01-02").date()
    def fila(f, s):
        return {"ticker": "AAA", "date": f, "signal_id": s,
                "direction": "bullish", "strength": 1.0, "detail": ""}

    with db.connect() as conn:
        db.upsert_df(conn, "signals", pd.DataFrame([
            fila(vieja, "ANTIGUA"), fila(dentro, "SE_QUEDA"),
            fila(dentro, "SE_VA"),
        ]), keys=["ticker", "date", "signal_id"])

        indicadores = pd.DataFrame([{"ticker": "AAA", "date": dentro}])
        nuevas = pd.DataFrame([fila(dentro, "SE_QUEDA")])
        borradas = _prune_stale_signals(conn, indicadores, nuevas)
        quedan = sorted(f[0] for f in conn.execute(
            "SELECT signal_id FROM signals").fetchall())

    assert borradas == 1
    assert quedan == ["ANTIGUA", "SE_QUEDA"], (
        "o se ha borrado historico fuera de la ventana, o no se ha quitado la "
        "senal obsoleta de dentro"
    )
