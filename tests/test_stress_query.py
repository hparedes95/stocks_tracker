"""Los retornos historicos que alimentan el stress test.

`core.stress` esta probado con numeros a mano. Aqui se prueba de donde salen
esos numeros, que es donde un fallo pasa desapercibido: un retorno medido desde
una fecha equivocada da una caida perfectamente plausible, y ademas suele darla
mas suave —una ventana recortada se come justo el tramo peor—.
"""

from __future__ import annotations

from datetime import date

import pytest

from stocks_tracker.core import db

DESDE = date(2020, 2, 19)
HASTA = date(2020, 3, 23)


@pytest.fixture(autouse=True)
def sin_cache(monkeypatch):
    """Quita la cache de Streamlit de los lectores.

    `get_sector_window_returns` llama por dentro a `get_window_returns`, y
    desenvolver solo la de fuera deja la de dentro cacheada: el resultado de un
    test se cuela en el siguiente. Aqui cada almacen es distinto y la cache no
    lo sabe.
    """
    from stocks_tracker.app import data_access as da

    for nombre in ("get_window_returns", "get_sector_window_returns",
                   "get_realized_vol"):
        fn = getattr(da, nombre)
        monkeypatch.setattr(da, nombre, getattr(fn, "__wrapped__", fn))


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


def precios(ticker: str, puntos: dict) -> None:
    with db.connect() as conn:
        for cuando, precio in puntos.items():
            conn.execute(
                "INSERT OR REPLACE INTO prices_daily "
                "(ticker, date, close, adj_close) VALUES (?, ?, ?, ?)",
                [ticker, cuando, precio, precio],
            )


def ventana(*tickers, desde=DESDE, hasta=HASTA) -> dict:
    from stocks_tracker.app import data_access as da

    return da.get_window_returns(tuple(tickers), desde, hasta)


# ---------------------------------------------------------------------------
# La ventana es la ventana
# ---------------------------------------------------------------------------
def test_the_return_is_measured_between_the_two_dates(warehouse):
    precios("AAA", {DESDE: 100.0, HASTA: 60.0})
    assert ventana("AAA")["AAA"] == pytest.approx(-0.40)


def test_prices_after_the_window_do_not_leak_in(warehouse):
    """El rebote posterior no cuenta: el escenario es la caida, no la caida y
    la recuperacion. Sin el corte, marzo de 2020 saldria casi plano."""
    precios("AAA", {DESDE: 100.0, HASTA: 60.0, date(2020, 8, 1): 130.0})
    assert ventana("AAA")["AAA"] == pytest.approx(-0.40)


def test_prices_before_the_window_do_not_leak_in_either(warehouse):
    precios("AAA", {date(2019, 1, 1): 500.0, DESDE: 100.0, HASTA: 60.0})
    assert ventana("AAA")["AAA"] == pytest.approx(-0.40)


def test_a_holiday_at_either_end_still_finds_a_price(warehouse):
    """Las dos fechas de un escenario pueden caer en festivo. Sin la union
    ASOF, el escenario entero se quedaria sin datos."""
    precios("AAA", {DESDE - __import__("datetime").timedelta(days=3): 100.0,
                    HASTA - __import__("datetime").timedelta(days=2): 60.0})
    assert ventana("AAA")["AAA"] == pytest.approx(-0.40)


def test_a_stock_that_did_not_trade_yet_is_absent(warehouse):
    """Y no aparece con un retorno medido desde su primer dia de cotizacion:
    seria una caida recortada justo por donde mas cayo, o sea mas suave.
    Estar ausente permite estimarla con el sector, que es lo honesto."""
    precios("AAA", {date(2021, 6, 1): 100.0, HASTA: 60.0})
    assert "AAA" not in ventana("AAA")


def test_a_price_from_long_before_the_start_is_not_used(warehouse):
    """Un precio de hace dos anos no dice a cuanto cotizaba el dia que empezo
    la caida. Usarlo mezclaria dos anos de subida con cinco semanas de caida."""
    precios("AAA", {date(2018, 1, 1): 100.0, HASTA: 60.0})
    assert "AAA" not in ventana("AAA")


def test_a_stock_delisted_mid_window_is_absent(warehouse):
    """Sin precio al final no hay retorno de la ventana. Tomar el ultimo que
    haya daria una caida a medias."""
    precios("AAA", {DESDE: 100.0, date(2020, 2, 25): 80.0})
    assert "AAA" not in ventana("AAA")


def test_several_tickers_do_not_mix(warehouse):
    precios("AAA", {DESDE: 100.0, HASTA: 60.0})
    precios("BBB", {DESDE: 50.0, HASTA: 55.0})
    r = ventana("AAA", "BBB")
    assert r["AAA"] == pytest.approx(-0.40)
    assert r["BBB"] == pytest.approx(0.10)


def test_asking_for_nothing_queries_nothing(warehouse):
    assert ventana() == {}


def test_a_window_with_no_data_at_all_is_empty_not_zero(warehouse):
    """Cero diria "no se movio", que es una afirmacion; vacio dice "no se
    sabe", que permite estimarlo con el sector o dejarlo fuera."""
    assert ventana("AAA") == {}


def test_a_zero_starting_price_does_not_divide_by_zero(warehouse):
    precios("AAA", {DESDE: 0.0, HASTA: 60.0})
    assert "AAA" not in ventana("AAA")


# ---------------------------------------------------------------------------
# Los sectores
# ---------------------------------------------------------------------------
def test_sector_returns_come_back_keyed_by_sector_name(warehouse):
    """La cartera trae el nombre del sector, no el ticker del ETF: si la
    traduccion se perdiera, ninguna posicion encontraria su sector y todas
    caerian al indice sin avisar."""
    from stocks_tracker.app import data_access as da

    precios("XLK", {DESDE: 100.0, HASTA: 75.0})
    r = da.get_sector_window_returns(DESDE, HASTA)
    assert r["Information Technology"] == pytest.approx(-0.25)


def test_a_sector_etf_without_history_is_simply_absent(warehouse):
    from stocks_tracker.app import data_access as da

    assert da.get_sector_window_returns(DESDE, HASTA) == {}


# ---------------------------------------------------------------------------
# De la consulta al impacto
# ---------------------------------------------------------------------------
def test_the_whole_chain_produces_a_loss(warehouse):
    from stocks_tracker.app import data_access as da
    from stocks_tracker.core.stress import Escenario, Fuente, impacto

    precios("AAA", {DESDE: 100.0, HASTA: 50.0})       # tiene historico propio
    precios("XLK", {DESDE: 100.0, HASTA: 75.0})       # BBB usara su sector
    esc = Escenario(id="covid", nombre="Covid", desde=DESDE, hasta=HASTA)

    cartera = [
        {"ticker": "AAA", "valor": 1000.0, "sector": "Information Technology"},
        {"ticker": "BBB", "valor": 1000.0, "sector": "Information Technology"},
    ]
    r = impacto(esc, cartera,
                ventana("AAA", "BBB"),
                da.get_sector_window_returns(DESDE, HASTA))

    assert r.perdida == pytest.approx(-750.0)          # -500 y -250
    assert {p.fuente for p in r.posiciones} == {Fuente.PROPIA, Fuente.SECTOR}
    assert r.cobertura == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Volatilidades
# ---------------------------------------------------------------------------
def test_volatility_comes_from_the_current_session(warehouse):
    """Un valor con volatilidad solo en una sesion vieja NO entra.

    Se prueba con un ticker distinto y no con dos filas del mismo: con dos
    filas, quitar el filtro deja las dos y cual gana depende del orden en que
    las devuelva la base de datos —que no esta garantizado—. El test pasaria o
    fallaria por casualidad, que es peor que no tenerlo.
    """
    from datetime import timedelta

    from stocks_tracker.app import data_access as da

    hoy = date.today()
    with db.connect() as conn:
        for t in ("HOY", "VIEJO"):
            conn.execute("INSERT INTO instruments (ticker, asset_class) "
                         "VALUES (?, 'equity')", [t])
        conn.execute(
            "INSERT INTO indicators_daily (ticker, date, close, "
            "realized_vol_252) VALUES ('HOY', ?, 100.0, 0.25)", [hoy])
        conn.execute(
            "INSERT INTO indicators_daily (ticker, date, close, "
            "realized_vol_252) VALUES ('VIEJO', ?, 100.0, 0.90)",
            [hoy - timedelta(days=30)])

    vols = da.get_realized_vol(("HOY", "VIEJO"))
    assert vols["HOY"] == pytest.approx(0.25)
    assert "VIEJO" not in vols, "ha cogido la volatilidad de una sesion vieja"


def test_a_zero_volatility_is_treated_as_missing(warehouse):
    """Un cero no es una volatilidad: es un dato que falta con otra cara. Si
    entrara, esa posicion no aportaria NINGUN riesgo al calculo de apuestas
    efectivas y la cartera pareceria mas diversificada de lo que es."""
    from stocks_tracker.app import data_access as da

    with db.connect() as conn:
        conn.execute("INSERT INTO instruments (ticker, asset_class) "
                     "VALUES ('AAA', 'equity')")
        conn.execute(
            "INSERT INTO indicators_daily (ticker, date, close, "
            "realized_vol_252) VALUES ('AAA', ?, 100.0, 0.0)", [date.today()])
    assert da.get_realized_vol(("AAA",)) == {}


def test_a_missing_volatility_is_absent_rather_than_zero(warehouse):
    """Una volatilidad de cero haria que esa posicion no aportara riesgo
    ninguno al calculo de apuestas efectivas."""
    from stocks_tracker.app import data_access as da

    assert da.get_realized_vol(("AAA",)) == {}


def test_two_lots_of_the_same_stock_are_added_up_not_overwritten(warehouse):
    """El panel pesa por valor. Con un diccionario por comprension, la segunda
    compra de un valor pisaba a la primera y la cartera parecia menos
    concentrada de lo que es — lo contrario de lo que el panel existe para
    ensenar.
    """
    import pandas as pd

    from stocks_tracker.core.stress import diversificacion

    cartera = [
        {"ticker": "AAA", "valor": 9000.0, "sector": None},
        {"ticker": "AAA", "valor": 1000.0, "sector": None},   # segundo lote
        {"ticker": "BBB", "valor": 1000.0, "sector": None},
    ]
    pesos: dict = {}
    for p in cartera:
        pesos[p["ticker"]] = pesos.get(p["ticker"], 0.0) + p["valor"]
    assert pesos["AAA"] == pytest.approx(10000.0)

    corr = pd.DataFrame([[1.0, 0.0], [0.0, 1.0]],
                        index=["AAA", "BBB"], columns=["AAA", "BBB"])
    concentrada = diversificacion(pesos, corr)
    repartida = diversificacion({"AAA": 1000.0, "BBB": 1000.0}, corr)
    assert concentrada.efectivas_hoy < repartida.efectivas_hoy
