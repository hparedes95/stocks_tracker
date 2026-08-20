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


def _serie(ticker: str, fechas, cierre: float = 100.0, plana: bool = False,
           **extra) -> pd.DataFrame:
    """Una serie sana: coherente Y CON RUIDO.

    El ruido no es adorno. Antes esta ayuda devolvia el mismo cierre 40 dias
    seguidos y eso se llamaba "datos limpios"; `serie_sin_ruido` lo llama, con
    razon, una serie congelada. Un precio real no repite el mismo retorno hasta
    el octavo decimal.

    La variacion es determinista —nada de aleatoriedad en los tests— y esta
    redondeada al centimo, como cotiza de verdad.

    `plana=True` devuelve la linea plana de antes, para los tests que necesitan
    un cierre conocido y constante.
    """
    filas = []
    for i, d in enumerate(fechas):
        precio = cierre if plana else round(cierre * (1 + ((i * 37) % 101 - 50) / 5000), 2)
        filas.append({"ticker": ticker, "date": d, "open": precio,
                      "high": precio + 1, "low": precio - 1, "close": precio,
                      **extra})
    return _precios(filas)


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
# Volumen provisional: lo que disparo la falsa alarma en la primera instalacion
# ---------------------------------------------------------------------------
def _revision(campo: str, dias_atras: int, hoy="2024-03-20") -> pd.DataFrame:
    fecha = pd.Timestamp(hoy) - pd.Timedelta(days=dias_atras)
    return pd.DataFrame([
        {"ticker": "^GSPC", "date": pd.Timestamp(hoy), "campo": "close",
         "antes": 100.0, "ahora": 100.0, "cambio": 0.0},
        {"ticker": "^GSPC", "date": fecha, "campo": campo,
         "antes": 6.355e8, "ahora": 2.981e9, "cambio": 0.79},
    ])


def test_yesterdays_volume_being_finalised_is_not_a_rewrite():
    """LO QUE DISPARO LA FALSA ALARMA. El volumen de una sesion se publica
    PROVISIONAL mientras el mercado esta abierto y se consolida despues del
    cierre, con lo que liquida tarde.

    En la primera instalacion real, el volumen del ^GSPC del dia anterior paso
    de 6,4e+08 a 3,0e+09 —el salto de provisional a consolidado— y la
    comprobacion lo trato como si el proveedor hubiera reescrito el historico,
    bloqueando el calculo.
    """
    relevantes = q.revisiones_relevantes(_revision("volume", dias_atras=1))
    assert "volume" not in list(relevantes["campo"])


def test_an_old_volume_revision_IS_a_rewrite():
    """Pasada la ventana de consolidacion ya no hay nada provisional. Que
    cambie el volumen de hace un mes es otra cosa, y hay que decirlo."""
    relevantes = q.revisiones_relevantes(_revision("volume", dias_atras=30))
    assert "volume" in list(relevantes["campo"])


def test_a_recent_PRICE_revision_is_always_a_rewrite():
    """El perdon es solo para el volumen. El precio al que cotizo algo ayer no
    es provisional: si cambia, algo va mal."""
    revisadas = pd.DataFrame([{
        "ticker": "^GSPC", "date": pd.Timestamp("2024-03-19"), "campo": "close",
        "antes": 5100.0, "ahora": 5080.0, "cambio": 0.004,
    }])
    assert len(q.revisiones_relevantes(revisadas)) == 1


def test_routine_volume_adjustments_are_still_reported_as_info():
    """Perdonar no es callar. Si no se dijera nada, un cambio de volumen
    inesperado seria indistinguible de que no hubiera pasado nada."""
    hallazgos = q.evaluar(pd.DataFrame(), _revision("volume", dias_atras=1),
                          filas_lote=15)
    assert not q.bloqueantes(hallazgos)
    assert any(h.check == "precios_revisados" and h.severity == q.INFO
               for h in hallazgos)


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
    assert q.bloqueantes(q.evaluar(p, instrumentos_ohlc={"AAA"}))


def test_impossible_prices_in_a_currency_pair_do_not_block_anything():
    """EL FALLO DE LA PRIMERA INSTALACION REAL. 411 sesiones incoherentes en
    EURUSD=X impedian calcular las 600 acciones del universo.

    De una divisa solo se usa el cierre. Que el maximo y el minimo no cuadren
    es una rareza CONOCIDA de Yahoo —sus OHLC de FX vienen de feeds distintos—
    y no invalida ningun calculo. Bloquear el programa entero por eso es
    desproporcionado, y una puerta que se cierra sin motivo se acaba abriendo
    con --ignorar-calidad por costumbre.
    """
    p = _precios([{"ticker": "EURUSD=X", "date": "2024-03-01",
                   "close": 1.08, "high": 1.07, "low": 1.09,
                   "open": 1.08, "adj_close": 1.08}])
    hallazgos = q.evaluar(p, instrumentos_ohlc={"AAA", "BBB"})
    assert not q.bloqueantes(hallazgos), "una divisa no puede parar el calculo"
    assert any(h.check == "ohlc_incoherente" and h.severity == q.AVISO
               for h in hallazgos), "pero tiene que decirse"


def test_the_blocking_message_names_the_affected_tickers():
    """Sin los nombres, "411 sesiones con precios imposibles" obliga a bucear en
    la tabla para saber que hay que volver a descargar."""
    p = _precios([
        {"ticker": "AAA", "date": "2024-03-01", "high": 90.0, "low": 99.0},
        {"ticker": "BBB", "date": "2024-03-01", "high": 90.0, "low": 99.0},
    ])
    detalle = q.evaluar(p, instrumentos_ohlc={"AAA", "BBB"})[0].detail
    assert "AAA" in detalle and "BBB" in detalle


def test_a_missing_ticker_warns_but_does_not_block():
    """Un ticker que dejo de llegar ensucia el ranking; no invalida el calculo.
    Una puerta que se cierra a menudo se acaba abriendo por costumbre."""
    fechas = list(pd.bdate_range("2024-01-01", periods=40))
    vivos = pd.concat([_serie(t, fechas) for t in ("AAA", "BBB")])
    muerto = _serie("ZZZ", fechas[:20])
    hallazgos = q.evaluar(pd.concat([vivos, muerto]))
    assert any(h.check == "ticker_desaparecido" for h in hallazgos)
    assert not q.bloqueantes(hallazgos)


def _revisadas(n_barras: int, campos_por_barra: int = 1) -> pd.DataFrame:
    campos = ["close", "open", "high", "low", "volume"][:campos_por_barra]
    return pd.DataFrame([
        {"ticker": "AAA", "date": pd.Timestamp("2024-03-01") + pd.Timedelta(days=i),
         "campo": c, "antes": 100.0, "ahora": 90.0, "cambio": 0.1}
        for i in range(n_barras) for c in campos
    ])


def test_a_massive_rewrite_blocks_but_a_single_one_only_warns():
    """Una barra cambiada puede ser una correccion legitima de un error
    puntual. El 1 % del lote es que la serie entera es otra."""
    solo_una = q.evaluar(pd.DataFrame(), _revisadas(1), filas_lote=1000)
    muchas = q.evaluar(pd.DataFrame(), _revisadas(50), filas_lote=1000)
    assert not q.bloqueantes(solo_una)
    assert q.bloqueantes(muchas)


def test_the_blocking_fraction_counts_bars_and_not_fields():
    """`revisiones` devuelve una fila por (ticker, fecha, CAMPO), asi que una
    sola barra reescrita produce hasta cinco. `filas_lote` cuenta barras.

    Dividiendo lo uno por lo otro, la fraccion salia hasta 5 veces inflada: una
    correccion del 0,25 % del lote disparaba el bloqueo del 1 % y la
    comprobacion que existe para avisar de un problema del proveedor paraba el
    programa por un problema que no existia.
    """
    una_barra_cinco_campos = _revisadas(1, campos_por_barra=5)
    assert len(una_barra_cinco_campos) == 5
    hallazgos = q.evaluar(pd.DataFrame(), una_barra_cinco_campos, filas_lote=400)
    assert not q.bloqueantes(hallazgos), "1 barra de 400 es 0,25 %, no debe bloquear"
    assert any("1 barras" in h.detail for h in hallazgos)


def test_a_tiny_incremental_batch_cannot_trip_the_block():
    """EL OTRO FALLO DE LA PRIMERA INSTALACION REAL. La descarga incremental
    diaria son 15 filas, asi que UNA barra corregida es el 6,7 % y disparaba el
    bloqueo del 1 %.

    Hacen falta las dos cosas: fraccion alta Y un numero absoluto de barras que
    no se explique por un descuido puntual. Con solo la fraccion, la descarga de
    todos los dias se bloquea sola.
    """
    hallazgos = q.evaluar(pd.DataFrame(), _revisadas(3), filas_lote=15)
    assert not q.bloqueantes(hallazgos), "3 barras de 15 no son una reescritura"
    assert any(h.severity == q.AVISO for h in hallazgos), "pero se avisa"


def test_a_real_mass_rewrite_still_blocks():
    """La contraprueba: si el minimo absoluto tapara todo, la comprobacion mas
    valiosa del modulo dejaria de servir."""
    hallazgos = q.evaluar(pd.DataFrame(), _revisadas(40), filas_lote=400)
    assert q.bloqueantes(hallazgos)


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


def _sembrar(db, precios: pd.DataFrame, asset_class: str = "equity") -> None:
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": t, "asset_class": asset_class}
            for t in precios["ticker"].unique()
        ]), keys=["ticker"])
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
    from stocks_tracker.core.quality import COMPROBACIONES_DEL_ALMACEN

    fechas = list(pd.bdate_range("2024-01-01", periods=40))
    _sembrar(almacen, pd.concat([_serie(t, fechas) for t in ("AAA", "BBB")]))
    from stocks_tracker.compute.run_compute import puerta_de_calidad

    puerta_de_calidad()
    with almacen.connect(read_only=True) as conn:
        filas = conn.execute(
            "SELECT check_name, passed FROM data_quality"
        ).fetchdf()
    assert set(filas["check_name"]) == set(COMPROBACIONES_DEL_ALMACEN)
    assert filas["passed"].all()


def test_the_gate_does_not_claim_to_have_checked_for_rewrites(almacen):
    """`precios_revisados` solo se puede comprobar DURANTE la ingesta,
    comparando con lo que habia antes de sobrescribirlo. La puerta marcaba esa
    comprobacion como pasada en cada calculo sin poder mirar nada.

    No era cosmetico: la pagina 8 ensena el registro mas reciente de cada
    comprobacion, asi que ese "pasado" falso TAPABA el hallazgo bloqueante que
    la ingesta acababa de escribir. El aviso de que el proveedor ha reescrito
    el historico —la comprobacion mas valiosa de todas— desaparecia de la
    pantalla en cuanto se ejecutaba el calculo.
    """
    fechas = list(pd.bdate_range("2024-01-01", periods=40))
    _sembrar(almacen, pd.concat([_serie(t, fechas) for t in ("AAA", "BBB")]))
    from stocks_tracker.compute.run_compute import puerta_de_calidad

    puerta_de_calidad()
    with almacen.connect(read_only=True) as conn:
        nombres = {r[0] for r in conn.execute(
            "SELECT DISTINCT check_name FROM data_quality").fetchall()}
    assert "precios_revisados" not in nombres


def test_an_ingest_finding_is_not_hidden_by_a_later_compute(almacen):
    """El caso completo, de extremo a extremo: la ingesta detecta una
    reescritura y despues se ejecuta el calculo. El hallazgo tiene que seguir
    siendo el registro mas reciente de esa comprobacion."""
    from stocks_tracker.compute.run_compute import puerta_de_calidad
    from stocks_tracker.core.quality import Hallazgo, guardar

    fechas = list(pd.bdate_range("2024-01-01", periods=40))
    _sembrar(almacen, pd.concat([_serie(t, fechas) for t in ("AAA", "BBB")]))

    with almacen.connect() as conn:
        guardar(conn, [Hallazgo("precios_revisados", q.BLOQUEA, None, None,
                                "el proveedor reescribio 300 barras")],
                "run-ingesta", ["precios_revisados"])
    puerta_de_calidad()

    with almacen.connect(read_only=True) as conn:
        fila = conn.execute(
            """
            SELECT passed, severity FROM data_quality d
            WHERE check_name = 'precios_revisados'
              AND checked_at = (SELECT MAX(x.checked_at) FROM data_quality x
                                WHERE x.check_name = 'precios_revisados')
            """
        ).fetchone()
    assert fila == (False, q.BLOQUEA), "el calculo ha tapado el aviso de la ingesta"


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


def test_the_gate_does_not_stop_for_a_currency_pair(almacen):
    """El caso de la primera instalacion real, de extremo a extremo.

    Con 411 sesiones incoherentes en EURUSD=X, el calculo de las 600 acciones
    no llegaba a ejecutarse. De una divisa solo se usa el cierre: se avisa y se
    sigue.
    """
    fechas = list(pd.bdate_range("2024-01-01", periods=40))
    datos = pd.concat([_serie(t, fechas) for t in ("EURUSD=X", "DX-Y.NYB")])
    rota = datos.index[0]
    datos.loc[rota, "high"] = 1.07
    datos.loc[rota, "low"] = 1.09
    _sembrar(almacen, datos, asset_class="fx")

    from stocks_tracker.compute.run_compute import puerta_de_calidad

    assert puerta_de_calidad() is True


def test_the_gate_exits_with_a_code_the_caller_can_see(almacen, monkeypatch):
    """Un fallo que no se propaga es un fallo que nadie ve.

    `run_compute` salia con codigo 0 aunque la puerta lo parase, asi que el
    instalador de Windows daba el paso por bueno y anunciaba "la portada ya
    muestra el mercado de verdad" DESPUES de que el calculo no se hubiera
    ejecutado. Se vio en la primera instalacion real.
    """
    import sys

    from stocks_tracker.compute import run_compute as rc

    fechas = list(pd.bdate_range("2024-01-01", periods=40))
    datos = pd.concat([_serie(t, fechas) for t in ("AAA", "BBB", "CCC")])
    rota = datos.index[0]
    datos.loc[rota, "high"] = 10.0
    datos.loc[rota, "low"] = 500.0
    _sembrar(almacen, datos)

    monkeypatch.setattr(sys, "argv", ["run_compute"])
    with pytest.raises(SystemExit) as salida:
        rc.main()
    assert salida.value.code == rc.EXIT_BAD_DATA
    assert rc.EXIT_BAD_DATA != 0, "un codigo 0 se lee como exito"


def test_a_healthy_warehouse_does_not_exit_with_an_error(almacen, monkeypatch):
    """La contraprueba: si siempre saliera con error, la automatizacion nocturna
    se marcaria como fallida todas las noches y el aviso dejaria de leerse."""
    import sys

    from stocks_tracker.compute import run_compute as rc

    fechas = list(pd.bdate_range("2024-01-01", periods=40))
    _sembrar(almacen, pd.concat([_serie(t, fechas) for t in ("AAA", "BBB")]))

    monkeypatch.setattr(sys, "argv", ["run_compute", "--only", "indicators"])
    rc.main()          # no debe lanzar SystemExit
