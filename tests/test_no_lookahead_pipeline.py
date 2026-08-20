"""Cambiar TODO el futuro y exigir que el pasado no se mueva.

La propiedad fundamental del sistema, escrita como una sola frase:

    Lo que se calcula para el dia `t` no puede depender de nada posterior a `t`.

`tests/test_no_lookahead.py` ya lo comprueba sobre los indicadores y sobre los
precios. Aqui se lleva al pipeline entero y a las CUATRO fuentes que alimentan
una decision, porque una fuga puede entrar por cualquiera de ellas y ninguna
daria error:

  1. precios
  2. volumen
  3. fundamentales
  4. composicion del universo

El metodo es el mismo para las cuatro y no depende de entender el calculo:
se ejecuta el pipeline, se destroza absolutamente todo lo posterior a `t` —no se
perturba, se sustituye por valores disparatados— y se vuelve a ejecutar. Si algo
de `t` cambia, hay una fuga, y da igual donde este.

Por que un test asi vale mas que revisar el codigo: una fuga temporal no da
error, no sale en pantalla y no rompe ningun otro test. El unico sintoma es un
backtest sospechosamente bueno, que es el resultado mas facil de creerse.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stocks_tracker.core import db

# Fecha de corte. Todo lo posterior se destroza; nada anterior se toca.
CORTE = pd.Timestamp("2023-06-30")
N_SESIONES = 500
TICKERS = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH")


@pytest.fixture
def almacen(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


def _precios() -> pd.DataFrame:
    """Series con tendencia y ruido, suficientes para que salgan indicadores."""
    rng = np.random.default_rng(7)
    fechas = pd.bdate_range("2022-01-03", periods=N_SESIONES)
    filas = []
    for i, ticker in enumerate(TICKERS):
        paso = rng.normal(0.0004 * (i + 1), 0.015, N_SESIONES)
        cierre = 100.0 * np.exp(np.cumsum(paso))
        filas.append(pd.DataFrame({
            "ticker": ticker, "date": fechas, "close": cierre,
            "open": cierre * 0.999, "high": cierre * 1.01, "low": cierre * 0.99,
            "adj_close": cierre, "volume": rng.integers(1e6, 5e6, N_SESIONES),
            "source": "test",
        }))
    return pd.concat(filas, ignore_index=True)


def _sembrar(precios: pd.DataFrame, fundamentales: pd.DataFrame,
             pertenencia: pd.DataFrame) -> None:
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": t, "asset_class": "equity", "gics_sector": "Tech",
             "is_active": True} for t in TICKERS
        ]), keys=["ticker"])
        db.upsert_df(conn, "prices_daily", precios, keys=["ticker", "date"])
        if not fundamentales.empty:
            db.upsert_df(conn, "fundamentals_snapshot", fundamentales,
                         keys=["ticker", "as_of"])
        conn.execute("DELETE FROM universe_membership")
        if not pertenencia.empty:
            conn.register("_m", pertenencia)
            conn.execute("INSERT INTO universe_membership SELECT * FROM _m")
            conn.unregister("_m")


def _fundamentales() -> pd.DataFrame:
    """Dos fotos por ticker: una antes del corte y otra despues."""
    filas = []
    for i, ticker in enumerate(TICKERS):
        for j, as_of in enumerate((pd.Timestamp("2022-06-01"),
                                   pd.Timestamp("2023-09-01"))):
            filas.append({"ticker": ticker, "as_of": as_of.date(),
                          "trailing_pe": 15.0 + i + j, "roe": 0.10 + 0.01 * i,
                          "profit_margin": 0.12, "completeness": 0.8})
    return pd.DataFrame(filas)


def _pertenencia() -> pd.DataFrame:
    return pd.DataFrame([
        {"universe": "SP100", "ticker": t, "valid_from": pd.Timestamp("2022-01-03").date(),
         "valid_to": None} for t in TICKERS
    ])


def _calcular() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Ejecuta el pipeline y devuelve lo calculado HASTA el corte."""
    from stocks_tracker.compute.run_compute import compute_indicators

    compute_indicators(lookback=None, full=True)
    with db.connect(read_only=True) as conn:
        indicadores = conn.execute(
            "SELECT ticker, date, close, sma50, sma200, rsi14, macd_hist, "
            "atr14, dist_52w_high, drawdown, above_sma200, golden_cross, "
            "rel_volume_20, obv, realized_vol_20 "
            "FROM indicators_daily WHERE date <= ? ORDER BY ticker, date",
            [CORTE.date()],
        ).fetchdf()
        senales = conn.execute(
            "SELECT ticker, date, signal_id, direction, strength FROM signals "
            "WHERE date <= ? ORDER BY ticker, date, signal_id",
            [CORTE.date()],
        ).fetchdf()
    return indicadores, senales


def _destrozar_precios(precios: pd.DataFrame) -> pd.DataFrame:
    """Sustituye el futuro por disparates. No lo perturba: lo sustituye.

    Multiplicar por 1,01 podria pasar desapercibido si la fuga fuera pequena.
    Un salto de dos ordenes de magnitud lo delata seguro.
    """
    roto = precios.copy()
    futuro = roto["date"] > CORTE
    for columna in ("open", "high", "low", "close", "adj_close"):
        roto.loc[futuro, columna] = 9999.0
    return roto


# ---------------------------------------------------------------------------
# Las cuatro fuentes
# ---------------------------------------------------------------------------
def test_changing_every_future_price_does_not_move_a_single_past_value(almacen):
    """La fuente principal. Si un indicador de `t` mirase un solo precio
    posterior, un salto a 9.999 lo movería sin remedio."""
    precios = _precios()
    _sembrar(precios, _fundamentales(), _pertenencia())
    ind_antes, sen_antes = _calcular()

    _sembrar(_destrozar_precios(precios), _fundamentales(), _pertenencia())
    ind_despues, sen_despues = _calcular()

    assert not ind_antes.empty, "el escenario tiene que producir indicadores"
    pd.testing.assert_frame_equal(ind_antes, ind_despues)
    pd.testing.assert_frame_equal(sen_antes, sen_despues)


def test_changing_every_future_volume_does_not_move_a_single_past_value(almacen):
    """El volumen entra en `rel_volume_20`, en el OBV y en la senal de pico de
    volumen. Es la fuente que se olvida al revisar a mano, porque no se piensa
    en ella como en un precio."""
    precios = _precios()
    _sembrar(precios, _fundamentales(), _pertenencia())
    ind_antes, sen_antes = _calcular()

    roto = precios.copy()
    roto.loc[roto["date"] > CORTE, "volume"] = 999_999_999
    _sembrar(roto, _fundamentales(), _pertenencia())
    ind_despues, sen_despues = _calcular()

    pd.testing.assert_frame_equal(ind_antes, ind_despues)
    pd.testing.assert_frame_equal(sen_antes, sen_despues)


def test_a_future_fundamentals_snapshot_does_not_change_a_past_score(almacen):
    """La fuga mas cara de todas y la mas invisible: puntuar 2022 con los
    balances de 2023. La estrategia "sabe" que empresas iban a publicar buenos
    numeros, y cualquier ranking de calidad o valor parece clarividente."""
    from stocks_tracker.compute.run_compute import fundamentals_as_of

    precios = _precios()
    _sembrar(precios, _fundamentales(), _pertenencia())
    fechas = [pd.Timestamp("2023-01-15").date()]

    with db.connect(read_only=True) as conn:
        antes = fundamentals_as_of(conn, fechas).sort_values("ticker")

    # Se cambia la foto POSTERIOR al corte, que es la que no debe usarse.
    futuros = _fundamentales()
    posterior = futuros["as_of"] == pd.Timestamp("2023-09-01").date()
    futuros.loc[posterior, "trailing_pe"] = 1.0
    futuros.loc[posterior, "roe"] = 9.99
    _sembrar(precios, futuros, _pertenencia())

    with db.connect(read_only=True) as conn:
        despues = fundamentals_as_of(conn, fechas).sort_values("ticker")

    assert not antes.empty, "el escenario tiene que devolver fundamentales"
    pd.testing.assert_frame_equal(
        antes.reset_index(drop=True), despues.reset_index(drop=True)
    )


def _con_una_senal_por_ticker(cuando: pd.Timestamp) -> None:
    with db.connect() as conn:
        db.upsert_df(conn, "signals", pd.DataFrame([
            {"ticker": t, "date": cuando.date(), "signal_id": "prueba",
             "direction": "bullish", "strength": 1.0, "detail": ""}
            for t in TICKERS
        ]), keys=["ticker", "date", "signal_id"])


def _universo_del_backtest(cuando: pd.Timestamp) -> set[str]:
    """Quien entra en el backtest punto-en-el-tiempo esa fecha.

    Pasa por `load_data(pit=True)`, que es EL CAMINO QUE SE EJECUTA de verdad,
    y no por una consulta paralela escrita para el test.

    Antes esto llamaba a un `membership.miembros_en()` que no usaba nadie mas.
    El test pasaba, pero lo que demostraba era que una funcion muerta respetaba
    la regla de intervalos; del filtro que de verdad recorta el universo —un
    JOIN dentro de `load_data`— no decia nada. Dos escrituras de la misma regla
    y el test vigilando la que no se ejecuta.
    """
    from stocks_tracker.backtest.run_backtest import load_data

    _, senales = load_data(pit=True)
    if senales.empty:
        return set()
    return set(senales.loc[
        pd.to_datetime(senales["date"]) == cuando, "ticker"
    ])


def test_a_future_membership_change_does_not_change_a_past_universe(almacen):
    """Que un valor entre o salga del indice manana no puede cambiar quien
    estaba dentro ayer. Si lo cambiara, la composicion historica se reescribiria
    sola y el universo punto-en-el-tiempo no serviria para nada."""
    precios = _precios()
    _sembrar(precios, _fundamentales(), _pertenencia())
    _con_una_senal_por_ticker(CORTE)
    antes = _universo_del_backtest(CORTE)

    # Mitad salen y entra uno nuevo, todo DESPUES del corte.
    futura = _pertenencia()
    futura.loc[futura["ticker"].isin(TICKERS[:4]), "valid_to"] = (
        CORTE + pd.Timedelta(days=30)
    ).date()
    futura = pd.concat([futura, pd.DataFrame([{
        "universe": "SP100", "ticker": "ZZZ",
        "valid_from": (CORTE + pd.Timedelta(days=30)).date(), "valid_to": None,
    }])], ignore_index=True)
    _sembrar(precios, _fundamentales(), futura)
    despues = _universo_del_backtest(CORTE)

    assert antes == set(TICKERS)
    assert antes == despues


# ---------------------------------------------------------------------------
# Las contrapruebas: si el test no cazara la trampa, no mediria nada
# ---------------------------------------------------------------------------
def test_the_test_catches_a_deliberate_price_leak(almacen):
    """Contraprueba de la primera. Se calcula un indicador tramposo —una media
    CENTRADA, que mira medio futuro— y se comprueba que el metodo lo detecta.
    Sin esto, los tests de arriba podrian estar pasando porque no miden nada.
    """
    precios = _precios()
    serie = precios[precios["ticker"] == "AAA"].set_index("date")["close"]

    tramposo_antes = serie.rolling(21, center=True).mean()
    roto = _destrozar_precios(precios)
    serie_rota = roto[roto["ticker"] == "AAA"].set_index("date")["close"]
    tramposo_despues = serie_rota.rolling(21, center=True).mean()

    hasta = tramposo_antes.index <= CORTE
    assert not np.allclose(
        tramposo_antes[hasta].dropna().to_numpy()[-5:],
        tramposo_despues[hasta].dropna().to_numpy()[-5:],
    ), "el metodo no detecta una fuga evidente: no esta midiendo nada"


def test_the_test_catches_a_deliberate_fundamentals_leak(almacen):
    """Contraprueba de la tercera: la union INGENUA —la foto mas reciente sin
    mirar la fecha— si cambia cuando cambia el futuro."""
    precios = _precios()
    _sembrar(precios, _fundamentales(), _pertenencia())

    def ingenua():
        with db.connect(read_only=True) as conn:
            return conn.execute(
                "SELECT ticker, trailing_pe FROM fundamentals_snapshot f "
                "QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker "
                "ORDER BY as_of DESC) = 1 ORDER BY ticker"
            ).fetchdf()

    antes = ingenua()
    futuros = _fundamentales()
    futuros.loc[futuros["as_of"] == pd.Timestamp("2023-09-01").date(),
                "trailing_pe"] = 1.0
    _sembrar(precios, futuros, _pertenencia())

    assert not antes.equals(ingenua()), (
        "la union ingenua deberia contaminarse; si no, el escenario no sirve "
        "para demostrar que la buena esta bien"
    )
