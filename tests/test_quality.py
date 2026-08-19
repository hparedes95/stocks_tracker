"""Comprobaciones de calidad de los datos.

El fallo que estas pruebas persiguen no es un dato feo: es un dato que cambia
sin avisar y deja los calculos anteriores sin poder reproducirse. Yahoo
reescribe series hacia atras, no da ningun error y en pantalla no se nota nada.
El unico sintoma es que un backtest de hace tres meses ya no sale igual, y como
nadie repite backtests viejos, no se nota nunca.

La distincion que se hace mal casi siempre —y que aqui tiene test propio— es
entre `close` y `adj_close`. La primera no cambia jamas; la segunda cambia en
cada dividendo y es normal. Confundirlas da un aviso por cada reparto o ningun
aviso nunca, y las dos cosas acaban en lo mismo: dejar de mirar.
"""

from __future__ import annotations

import pandas as pd
import pytest

from stocks_tracker.core import quality as q


def _precios(filas: list[dict]) -> pd.DataFrame:
    base = {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
            "adj_close": 100.0, "volume": 1_000_000}
    return pd.DataFrame([{**base, **f} for f in filas])


def _serie(ticker: str, fechas, cierre: float = 100.0, **extra) -> pd.DataFrame:
    return _precios([{"ticker": ticker, "date": d, "open": cierre,
                      "high": cierre + 1, "low": cierre - 1, "close": cierre,
                      **extra} for d in fechas])


# ---------------------------------------------------------------------------
# Reescritura del pasado: la comprobacion que importa
# ---------------------------------------------------------------------------
def test_a_rewritten_close_price_is_detected():
    """El caso central. El cierre de un dia concreto es un hecho: si cambia,
    algo va mal en el proveedor y todo lo calculado antes deja de valer."""
    viejo = _precios([{"ticker": "AAA", "date": "2024-03-01", "close": 174.52}])
    nuevo = _precios([{"ticker": "AAA", "date": "2024-03-01", "close": 171.10}])
    r = q.revisiones(nuevo, viejo)
    assert len(r) == 1
    assert r.iloc[0]["campo"] == "close"
    assert r.iloc[0]["antes"] == pytest.approx(174.52)
    assert r.iloc[0]["ahora"] == pytest.approx(171.10)


def test_a_rewritten_adjusted_close_is_NOT_reported():
    """`adj_close` se reajusta con cada dividendo y cada split: cambia hacia
    atras por diseno. Avisar de eso seria una falsa alarma en cada reparto, y
    una alarma que salta siempre deja de leerse a las dos semanas.

    Es la mitad de la comprobacion que casi nadie hace bien: o se vigila
    `adj_close` y salta con cada dividendo, o no se vigila nada.
    """
    viejo = _precios([{"ticker": "AAA", "date": "2024-03-01",
                       "close": 174.52, "adj_close": 174.52}])
    nuevo = _precios([{"ticker": "AAA", "date": "2024-03-01",
                       "close": 174.52, "adj_close": 173.10}])
    assert q.revisiones(nuevo, viejo).empty


def test_rounding_noise_is_not_a_rewrite():
    """El ultimo decimal se mueve al ir y volver por JSON. Con tolerancia cero
    saltaria la alarma en cada descarga y el aviso no significaria nada."""
    viejo = _precios([{"ticker": "AAA", "date": "2024-03-01", "close": 174.520000}])
    nuevo = _precios([{"ticker": "AAA", "date": "2024-03-01", "close": 174.520003}])
    assert q.revisiones(nuevo, viejo).empty


def test_a_change_just_above_the_tolerance_is_a_rewrite():
    """La contraprueba del anterior: si la tolerancia se comiera cambios
    reales, la comprobacion no detectaria nada y nadie se enteraria."""
    viejo = _precios([{"ticker": "AAA", "date": "2024-03-01", "close": 100.0}])
    nuevo = _precios([{"ticker": "AAA", "date": "2024-03-01", "close": 100.5}])
    assert len(q.revisiones(nuevo, viejo)) == 1


def test_a_rewritten_volume_is_detected():
    viejo = _precios([{"ticker": "AAA", "date": "2024-03-01", "volume": 1_000_000}])
    nuevo = _precios([{"ticker": "AAA", "date": "2024-03-01", "volume": 1_500_000}])
    r = q.revisiones(nuevo, viejo)
    assert list(r["campo"]) == ["volume"]


def test_a_volume_of_zero_on_both_sides_is_not_a_rewrite():
    """Dividir la diferencia por el valor viejo daria infinito cuando el viejo
    es cero, y TODAS las filas con volumen cero saldrian reescritas."""
    viejo = _precios([{"ticker": "AAA", "date": "2024-03-01", "volume": 0}])
    nuevo = _precios([{"ticker": "AAA", "date": "2024-03-01", "volume": 0}])
    assert q.revisiones(nuevo, viejo).empty


def test_a_volume_going_from_zero_to_something_is_a_rewrite():
    viejo = _precios([{"ticker": "AAA", "date": "2024-03-01", "volume": 0}])
    nuevo = _precios([{"ticker": "AAA", "date": "2024-03-01", "volume": 900_000}])
    assert len(q.revisiones(nuevo, viejo)) == 1


def test_new_dates_are_not_rewrites():
    """Lo normal de una descarga incremental son filas nuevas. Si contaran como
    reescritura, cada actualizacion diaria dispararia el bloqueo."""
    viejo = _precios([{"ticker": "AAA", "date": "2024-03-01"}])
    nuevo = _precios([{"ticker": "AAA", "date": "2024-03-02"},
                      {"ticker": "AAA", "date": "2024-03-03"}])
    assert q.revisiones(nuevo, viejo).empty


@pytest.mark.parametrize("lado", ["nuevo", "viejo"])
def test_a_missing_value_is_not_a_rewrite(lado):
    """Que falte un dato es un hueco, y de eso avisa otra comprobacion.
    Tratarlo como reescritura mezclaria dos problemas distintos.

    Se prueban los dos lados: falta en lo que llega y falta en lo guardado. La
    proteccion es una sola —el NaN que se propaga hasta la comparacion— y tiene
    que valer para las dos direcciones.
    """
    nan = float("nan")
    viejo = _precios([{"ticker": "AAA", "date": "2024-03-01",
                       "close": nan if lado == "viejo" else 100.0}])
    nuevo = _precios([{"ticker": "AAA", "date": "2024-03-01",
                       "close": nan if lado == "nuevo" else 100.0}])
    assert q.revisiones(nuevo, viejo).empty


def test_a_price_that_appears_where_there_was_none_is_not_a_rewrite():
    """Rellenar un hueco es lo contrario de reescribir: no habia nada que
    cambiar. Si contara, cada reparacion de datos dispararia el bloqueo."""
    viejo = _precios([{"ticker": "AAA", "date": "2024-03-01", "close": float("nan"),
                       "open": float("nan"), "high": float("nan"), "low": float("nan")}])
    nuevo = _precios([{"ticker": "AAA", "date": "2024-03-01", "close": 100.0}])
    assert q.revisiones(nuevo, viejo).empty


def test_an_empty_warehouse_has_nothing_to_compare():
    nuevo = _precios([{"ticker": "AAA", "date": "2024-03-01"}])
    assert q.revisiones(nuevo, pd.DataFrame()).empty
    assert q.revisiones(pd.DataFrame(), nuevo).empty


# ---------------------------------------------------------------------------
# Precios imposibles
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fila,motivo", [
    ({"high": 95.0, "low": 99.0}, "maximo por debajo del minimo"),
    ({"close": 150.0}, "cierre por encima del maximo"),
    ({"close": 50.0}, "cierre por debajo del minimo"),
    ({"open": 150.0}, "apertura por encima del maximo"),
    ({"close": -5.0}, "precio no positivo"),
])
def test_impossible_bars_are_caught(fila, motivo):
    """No son datos discutibles, son datos que no pueden existir. Un indicador
    calculado sobre ellos da un numero de aspecto razonable."""
    p = _precios([{"ticker": "AAA", "date": "2024-03-01", **fila}])
    malas = q.incoherencias_ohlc(p)
    assert len(malas) == 1
    assert malas.iloc[0]["motivo"] == motivo


def test_a_normal_bar_is_not_flagged():
    p = _precios([{"ticker": "AAA", "date": "2024-03-01",
                   "open": 100.0, "high": 102.0, "low": 98.0, "close": 101.0}])
    assert q.incoherencias_ohlc(p).empty


def test_a_flat_bar_is_valid():
    """Apertura, maximo, minimo y cierre iguales pasa en valores muy ilíquidos
    y en dias de subasta. Marcarlo llenaria el registro de falsas alarmas."""
    p = _precios([{"ticker": "AAA", "date": "2024-03-01", "open": 50.0,
                   "high": 50.0, "low": 50.0, "close": 50.0}])
    assert q.incoherencias_ohlc(p).empty


def test_a_bar_with_missing_values_is_not_called_impossible():
    """Un nulo no es una incoherencia: es un hueco, y lo cuenta otra
    comprobacion. Si contara aqui, un dia sin datos se leeria como que el
    proveedor manda precios imposibles."""
    p = _precios([{"ticker": "AAA", "date": "2024-03-01", "high": float("nan")}])
    assert q.incoherencias_ohlc(p).empty


# ---------------------------------------------------------------------------
# Huecos y desaparecidos
# ---------------------------------------------------------------------------
def test_the_market_calendar_comes_from_the_data():
    fechas = pd.bdate_range("2024-01-01", periods=10)
    p = pd.concat([_serie(t, fechas) for t in ("AAA", "BBB", "CCC")])
    assert q.calendario_del_mercado(p) == set(fechas)


def test_the_calendar_counts_instruments_and_not_rows():
    """Si contara filas, un ticker duplicado por error convertiria en sesion un
    dia en el que solo el estuvo. El calendario dice cuando abrio el mercado, y
    eso lo decide cuantos instrumentos distintos negociaron."""
    fechas = list(pd.bdate_range("2024-01-01", periods=10))
    p = pd.concat([_serie(t, fechas) for t in ("AAA", "BBB", "CCC")])
    raro = pd.Timestamp("2024-01-13")
    duplicado = pd.concat([_serie("AAA", [raro])] * 5)   # cinco filas, un ticker
    assert raro not in q.calendario_del_mercado(pd.concat([p, duplicado]))


def test_a_day_when_almost_nobody_traded_is_not_a_session():
    """Si un solo ticker tiene precio un dia, ese dia no fue sesion: es un dato
    suelto. Contarlo como sesion haria que todos los demas parecieran tener un
    hueco el mismo dia."""
    fechas = pd.bdate_range("2024-01-01", periods=10)
    p = pd.concat([_serie(t, fechas) for t in ("AAA", "BBB", "CCC")])
    raro = _serie("AAA", [pd.Timestamp("2024-01-13")])   # un sabado
    calendario = q.calendario_del_mercado(pd.concat([p, raro]))
    assert pd.Timestamp("2024-01-13") not in calendario


def test_missing_sessions_lower_the_coverage():
    fechas = list(pd.bdate_range("2024-01-01", periods=20))
    completos = pd.concat([_serie(t, fechas) for t in ("AAA", "BBB")])
    con_huecos = _serie("CCC", fechas[:5] + fechas[10:])
    todo = pd.concat([completos, con_huecos])
    cobertura = q.huecos(todo).set_index("ticker")["cobertura"]
    assert cobertura["AAA"] == pytest.approx(1.0)
    assert cobertura["CCC"] < 0.8


def test_sessions_before_a_ticker_existed_are_not_gaps():
    """Una empresa que salio a bolsa el mes pasado no tiene un hueco de diez
    anos. Si contaran, cualquier valor reciente pareceria datos rotos."""
    fechas = list(pd.bdate_range("2024-01-01", periods=20))
    viejos = pd.concat([_serie(t, fechas) for t in ("AAA", "BBB")])
    nuevo = _serie("NUEVA", fechas[15:])
    cobertura = q.huecos(pd.concat([viejos, nuevo])).set_index("ticker")["cobertura"]
    assert cobertura["NUEVA"] == pytest.approx(1.0)


def test_a_ticker_that_stopped_arriving_is_reported():
    fechas = list(pd.bdate_range("2024-01-01", periods=40))
    vivos = pd.concat([_serie(t, fechas) for t in ("AAA", "BBB")])
    muerto = _serie("ZZZ", fechas[:20])
    idos = q.desaparecidos(pd.concat([vivos, muerto]))
    assert list(idos["ticker"]) == ["ZZZ"]


def test_a_ticker_that_is_still_arriving_is_not_reported():
    fechas = list(pd.bdate_range("2024-01-01", periods=40))
    p = pd.concat([_serie(t, fechas) for t in ("AAA", "BBB")])
    assert q.desaparecidos(p).empty


def test_a_long_weekend_does_not_make_a_ticker_disappear():
    """Con un umbral de una o dos sesiones, cualquier festivo raro de una bolsa
    europea daria la alarma y el aviso dejaria de significar nada."""
    fechas = list(pd.bdate_range("2024-01-01", periods=40))
    vivos = pd.concat([_serie(t, fechas) for t in ("AAA", "BBB")])
    con_puente = _serie("CCC", fechas[:-3])
    assert q.desaparecidos(pd.concat([vivos, con_puente])).empty


def test_zero_volume_sessions_are_counted():
    fechas = list(pd.bdate_range("2024-01-01", periods=10))
    p = _serie("AAA", fechas)
    p.loc[p.index[:3], "volume"] = 0
    conteo = q.volumen_cero(p)
    assert conteo.iloc[0]["sesiones_sin_volumen"] == 3


# ---------------------------------------------------------------------------
# El veredicto y la puerta
# ---------------------------------------------------------------------------
def test_impossible_prices_block_the_computation():
    """Es lo unico, junto con la reescritura masiva, que para el calculo:
    invalida el resultado en vez de ensuciarlo."""
    p = _precios([{"ticker": "AAA", "date": "2024-03-01", "high": 90.0, "low": 99.0}])
    assert q.bloqueantes(q.evaluar(p))


def test_a_missing_ticker_warns_but_does_not_block():
    """Un ticker que dejo de llegar ensucia el ranking; no invalida el calculo.
    Una puerta que se cierra a menudo se acaba abriendo por costumbre."""
    fechas = list(pd.bdate_range("2024-01-01", periods=40))
    vivos = pd.concat([_serie(t, fechas) for t in ("AAA", "BBB")])
    muerto = _serie("ZZZ", fechas[:20])
    hallazgos = q.evaluar(pd.concat([vivos, muerto]))
    assert any(h.check == "ticker_desaparecido" for h in hallazgos)
    assert not q.bloqueantes(hallazgos)


def test_a_massive_rewrite_blocks_but_a_single_one_only_warns():
    """Una fila cambiada puede ser una correccion legitima de un error puntual.
    El 1 % del lote es que la serie entera es otra."""
    revisadas = pd.DataFrame([{"ticker": "AAA", "date": pd.Timestamp("2024-03-01"),
                               "campo": "close", "antes": 100.0, "ahora": 90.0,
                               "cambio": 0.1}])
    solo_una = q.evaluar(pd.DataFrame(), revisadas, filas_lote=1000)
    muchas = q.evaluar(pd.DataFrame(), pd.concat([revisadas] * 50), filas_lote=1000)
    assert not q.bloqueantes(solo_una)
    assert q.bloqueantes(muchas)


def test_the_warning_says_what_it_means_and_not_just_a_number():
    """"34 filas revisadas" se ignora. Lo que hay que decir es que los
    resultados anteriores ya no se reproducen, que es la consecuencia."""
    revisadas = pd.DataFrame([{"ticker": "AAA", "date": pd.Timestamp("2024-03-01"),
                               "campo": "close", "antes": 174.52, "ahora": 171.10,
                               "cambio": 0.02}])
    detalle = q.evaluar(pd.DataFrame(), revisadas, filas_lote=10)[0].detail
    assert "reproducir" in detalle
    assert "174.5" in detalle and "171.1" in detalle


def test_clean_data_produces_no_findings():
    """La contraprueba de todo lo demas: si siempre saliera algo, ninguno de
    los tests de arriba estaria probando lo que dice."""
    fechas = list(pd.bdate_range("2024-01-01", periods=40))
    p = pd.concat([_serie(t, fechas) for t in ("AAA", "BBB", "CCC")])
    assert q.evaluar(p) == []


# ---------------------------------------------------------------------------
# La puerta, de extremo a extremo
# ---------------------------------------------------------------------------
@pytest.fixture
def almacen(tmp_path, monkeypatch):
    from stocks_tracker.core import db

    class Stub:
        warehouse_path = tmp_path / "test.duckdb"

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return db


def _sembrar(db, precios: pd.DataFrame) -> None:
    with db.connect() as conn:
        db.upsert_df(conn, "prices_daily", precios, keys=["ticker", "date"])


def test_the_gate_lets_clean_data_through(almacen):
    fechas = list(pd.bdate_range("2024-01-01", periods=40))
    _sembrar(almacen, pd.concat([_serie(t, fechas) for t in ("AAA", "BBB", "CCC")]))
    from stocks_tracker.compute.run_compute import puerta_de_calidad

    assert puerta_de_calidad() is True


def test_the_gate_stops_the_computation_on_impossible_prices(almacen):
    """La prueba de extremo a extremo: una barra imposible en el almacen tiene
    que impedir que se calcule. Sin esto, los indicadores se calculan sobre
    ella y dan un numero de aspecto normal."""
    fechas = list(pd.bdate_range("2024-01-01", periods=40))
    datos = pd.concat([_serie(t, fechas) for t in ("AAA", "BBB", "CCC")])
    rota = datos.index[0]
    datos.loc[rota, "high"] = 10.0      # maximo por debajo del minimo
    datos.loc[rota, "low"] = 500.0
    _sembrar(almacen, datos)

    from stocks_tracker.compute.run_compute import puerta_de_calidad

    assert puerta_de_calidad() is False


def test_the_gate_records_what_it_checked_even_when_everything_passes(almacen):
    """Guardar solo los problemas deja una tabla en la que no se distingue "se
    comprobo y estaba bien" de "no se comprobo". Esa diferencia es justo la que
    hace falta el dia que algo se rompe."""
    from stocks_tracker.core.quality import COMPROBACIONES

    fechas = list(pd.bdate_range("2024-01-01", periods=40))
    _sembrar(almacen, pd.concat([_serie(t, fechas) for t in ("AAA", "BBB")]))
    from stocks_tracker.compute.run_compute import puerta_de_calidad

    puerta_de_calidad()
    with almacen.connect(read_only=True) as conn:
        filas = conn.execute(
            "SELECT check_name, passed FROM data_quality"
        ).fetchdf()
    assert set(filas["check_name"]) == set(COMPROBACIONES)
    assert filas["passed"].all()


def test_an_empty_warehouse_does_not_block(almacen):
    """Antes de la primera descarga no hay nada que comprobar. Bloquear ahi
    dejaria el programa sin poder arrancar nunca."""
    from stocks_tracker.compute.run_compute import puerta_de_calidad

    assert puerta_de_calidad() is True


def test_the_thresholds_are_not_accidentally_permissive():
    """Con los umbrales al maximo, ninguna comprobacion saltaria nunca."""
    assert 0 < q.TOLERANCIA_REVISION < 0.05
    assert 0 < q.MAX_REVISADAS < 0.5
    assert q.SESIONES_PARA_DESAPARECIDO >= 3
    assert 0.5 < q.MIN_COBERTURA_CALENDARIO <= 1.0
