"""De la base de datos a la atribucion.

`core.attribution` esta probado con numeros a mano. Aqui se prueba la consulta,
que es donde se rompe esto sin dar error: si las referencias se midieran desde
una fecha equivocada, saldria un numero limpio, plausible y falso, y encima
diria quien lo hizo bien y quien mal.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from stocks_tracker.app.components.attribution_panel import posiciones_desde
from stocks_tracker.core import db
from stocks_tracker.core.attribution import resumir

HOY = date.today()


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


def precios(conn, ticker: str, puntos: dict) -> None:
    """Inserta precios sin pisar los que ya haya en esa misma fecha.

    `INSERT OR REPLACE` y no `INSERT`: varios tests siembran el mercado a mano
    antes de llamar a `sembrar`, y una clave duplicada abortaria la siembra
    entera dejando un escenario a medias.
    """
    for cuando, precio in puntos.items():
        conn.execute(
            "INSERT OR REPLACE INTO prices_daily (ticker, date, close, adj_close) "
            "VALUES (?, ?, ?, ?)", [ticker, cuando, precio, precio],
        )


QTY = 10.0


def sembrar(*, comprada: date, precio_compra: float = 100.0,
            precio_hoy: float = 120.0, mercado=(200.0, 220.0),
            sector=(400.0, 420.0), ticker: str = "AAA",
            gics: str | None = "Information Technology") -> None:
    """Una posicion con su mercado y su sector en la MISMA ventana.

    Los tres arrancan en precios DISTINTOS a proposito (100, 200 y 400). Si
    coincidieran, confundir una serie con otra daria el mismo numero y los
    tests no notarian la diferencia —que es lo que pasaba—. Los porcentajes
    siguen siendo redondos: +20 % el valor, +10 % el mercado, +5 % el sector.

    `avg_cost` es el precio de compra, no una cifra suelta: si no coincidieran,
    el retorno propio saldria disparado y los tests medirian el desajuste del
    escenario en vez de la consulta —que es lo que hacian—.
    """
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO instruments (ticker, asset_class, gics_sector) "
            "VALUES (?, 'equity', ?)", [ticker, gics],
        )
        conn.execute(
            "INSERT INTO positions VALUES (?, ?, ?, ?, 'EUR', ?, NULL, '', NULL)",
            [f"pos-{ticker}", ticker, QTY, precio_compra, comprada],
        )
        precios(conn, ticker, {comprada: precio_compra, HOY: precio_hoy})
        precios(conn, "SPY", {comprada: mercado[0], HOY: mercado[1]})
        precios(conn, "XLK", {comprada: sector[0], HOY: sector[1]})


def entradas():
    from stocks_tracker.app import data_access as da

    return da.get_attribution_inputs.__wrapped__()


# ---------------------------------------------------------------------------
# La ventana de cada posicion
# ---------------------------------------------------------------------------
def test_the_market_is_measured_from_the_purchase_day(warehouse):
    """La comprobacion central. Medir el mercado desde otra fecha da un numero
    con la misma pinta y sin ningun sentido: una compra de hace un mes no
    compite contra doce meses de indice."""
    comprada = HOY - timedelta(days=200)
    with db.connect() as conn:
        precios(conn, "SPY", {HOY - timedelta(days=400): 50.0})
    sembrar(comprada=comprada, mercado=(200.0, 220.0))
    fila = entradas().iloc[0]
    assert fila["retorno_mercado"] == pytest.approx(0.10), (
        "ha medido el mercado desde antes de la compra"
    )


def test_the_sector_is_measured_from_the_purchase_day_too(warehouse):
    sembrar(comprada=HOY - timedelta(days=200), sector=(400.0, 420.0))
    assert entradas().iloc[0]["retorno_sector"] == pytest.approx(0.05)


def test_your_return_comes_from_your_average_cost(warehouse):
    """No del primer precio de la serie: lo que se atribuye es lo que TU has
    ganado, y eso empieza en lo que pagaste."""
    sembrar(comprada=HOY - timedelta(days=200), precio_hoy=120.0)
    assert entradas().iloc[0]["retorno"] == pytest.approx(0.20)


def test_a_purchase_on_a_holiday_still_finds_a_reference(warehouse):
    """La bolsa cierra fines de semana y festivos. Sin la union ASOF, una
    compra en sabado se quedaria sin comparacion y desapareceria del calculo
    sin que nadie lo notara."""
    comprada = HOY - timedelta(days=200)
    with db.connect() as conn:
        precios(conn, "SPY", {comprada - timedelta(days=3): 200.0, HOY: 220.0})
        precios(conn, "XLK", {comprada - timedelta(days=3): 400.0, HOY: 420.0})
        conn.execute(
            "INSERT INTO instruments (ticker, asset_class, gics_sector) "
            "VALUES ('AAA', 'equity', 'Information Technology')")
        conn.execute(
            "INSERT INTO positions VALUES ('p', 'AAA', 10, 10.0, 'EUR', ?, "
            "NULL, '', NULL)", [comprada])
        precios(conn, "AAA", {comprada: 100.0, HOY: 120.0})
    fila = entradas().iloc[0]
    assert fila["retorno_mercado"] == pytest.approx(0.10)
    assert fila["retorno_sector"] == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# Marea contra merito, de punta a punta
# ---------------------------------------------------------------------------
def test_a_position_that_gained_but_lost_to_the_market(warehouse):
    """El caso que justifica toda la pantalla: +20 % con el mercado en +35 %.
    En cualquier otra vista es una posicion en verde."""
    sembrar(comprada=HOY - timedelta(days=300), precio_hoy=120.0,
            mercado=(200.0, 270.0), sector=(400.0, 520.0))
    r = resumir(posiciones_desde(entradas()))
    assert r.retorno > 0
    assert r.contra_el_mercado < 0
    assert r.cuadra


def test_the_decomposition_survives_the_round_trip(warehouse):
    """Los tres efectos tienen que seguir sumando el retorno despues de pasar
    por SQL, por pandas y por el reparto de columnas."""
    sembrar(comprada=HOY - timedelta(days=300), precio_hoy=140.0,
            mercado=(200.0, 220.0), sector=(400.0, 500.0))
    p = posiciones_desde(entradas())[0]
    assert p.cuadra
    assert p.efecto_sector == pytest.approx(0.15)
    assert p.efecto_seleccion == pytest.approx(0.15)


# ---------------------------------------------------------------------------
# Lo que no se puede comparar
# ---------------------------------------------------------------------------
def test_a_position_without_a_sector_etf_has_no_sector_return(warehouse):
    """No hay ETF para todos los sectores. Sale nulo, no cero: un cero diria
    que el sector se quedo plano, que es una afirmacion sin comprobar."""
    sembrar(comprada=HOY - timedelta(days=200), gics="Sector Inventado")
    assert pd.isna(entradas().iloc[0]["retorno_sector"])


def test_a_position_with_no_sector_at_all_is_still_attributed(warehouse):
    """Un ETF o un indice no tienen sector, y aun asi se pueden comparar con el
    mercado. Tirarlos perderia justo las posiciones mas grandes de mucha gente."""
    sembrar(comprada=HOY - timedelta(days=200), gics=None)
    fila = entradas().iloc[0]
    assert fila["retorno_mercado"] == pytest.approx(0.10)
    assert pd.isna(fila["retorno_sector"])


def test_a_position_without_a_market_reference_is_dropped(warehouse):
    """Sin referencia no hay atribucion. Meterla con cero la contaria como
    "ni mejor ni peor que el mercado", que nadie ha comprobado."""
    comprada = HOY - timedelta(days=200)
    with db.connect() as conn:
        conn.execute("INSERT INTO instruments (ticker, asset_class) "
                     "VALUES ('AAA', 'equity')")
        conn.execute("INSERT INTO positions VALUES ('p', 'AAA', 10, 10.0, "
                     "'EUR', ?, NULL, '', NULL)", [comprada])
        precios(conn, "AAA", {comprada: 100.0, HOY: 120.0})
    assert posiciones_desde(entradas()) == []


def test_a_closed_position_is_not_attributed(warehouse):
    sembrar(comprada=HOY - timedelta(days=200))
    with db.connect() as conn:
        conn.execute("UPDATE positions SET closed_at = ?", [HOY])
    assert entradas().empty


def test_a_position_with_no_cost_does_not_divide_by_zero(warehouse):
    """Una importacion incompleta puede dejar el precio medio a cero."""
    comprada = HOY - timedelta(days=200)
    with db.connect() as conn:
        conn.execute("INSERT INTO instruments (ticker, asset_class) "
                     "VALUES ('AAA', 'equity')")
        conn.execute("INSERT INTO positions VALUES ('p', 'AAA', 10, 0.0, "
                     "'EUR', ?, NULL, '', NULL)", [comprada])
        precios(conn, "AAA", {comprada: 100.0, HOY: 120.0})
        precios(conn, "SPY", {comprada: 200.0, HOY: 220.0})
    assert entradas().empty


def test_two_lots_of_the_same_stock_are_two_decisions(warehouse):
    """Comprar el mismo valor dos veces con seis meses de diferencia son dos
    decisiones con dos ventanas de mercado distintas.

    Agrupandolas habria que inventarse una fecha de entrada comun, y la segunda
    compra se compararia contra un mercado que ya habia pasado: la subida
    anterior a esa compra se le apuntaria como acierto o como fallo sin que
    tuviera nada que ver.
    """
    vieja, nueva = HOY - timedelta(days=400), HOY - timedelta(days=30)
    sembrar(comprada=vieja, precio_compra=100.0, precio_hoy=120.0)
    with db.connect() as conn:
        precios(conn, "SPY", {nueva: 210.0})
        precios(conn, "XLK", {nueva: 410.0})
        precios(conn, "AAA", {nueva: 110.0})
        conn.execute(
            "INSERT INTO positions VALUES ('pos-2', 'AAA', 10, 110.0, 'EUR', "
            "?, NULL, '', NULL)", [nueva])

    # `pd.Timestamp` y no `date`: DuckDB devuelve las fechas como datetime64 al
    # pasar por pandas, y buscar con un `date` no falla — no encuentra nada.
    filas = entradas().set_index(pd.to_datetime(entradas()["opened_at"]))
    vieja, nueva = pd.Timestamp(vieja), pd.Timestamp(nueva)
    assert len(filas) == 2
    # La vieja arrastra todo el recorrido del mercado; la nueva, solo su tramo.
    assert filas.loc[vieja, "retorno_mercado"] == pytest.approx(0.10)
    assert filas.loc[nueva, "retorno_mercado"] == pytest.approx(220 / 210 - 1)
    assert filas.loc[vieja, "dias"] == 400
    assert filas.loc[nueva, "dias"] == 30


def test_a_missing_sector_does_not_reach_the_screen_as_the_word_nan(warehouse):
    """`nan or ""` devuelve `nan`, porque un `nan` es VERDADERO. Sin filtrarlo,
    la columna de sector pinta literalmente la palabra "nan"."""
    sembrar(comprada=HOY - timedelta(days=200), gics=None)
    assert posiciones_desde(entradas())[0].sector == ""


def test_an_empty_portfolio_is_not_an_error(warehouse):
    assert entradas().empty
    assert posiciones_desde(entradas()) == []


# ---------------------------------------------------------------------------
# El peso y el tiempo
# ---------------------------------------------------------------------------
def test_the_weight_is_the_capital_actually_committed(warehouse):
    sembrar(comprada=HOY - timedelta(days=200), precio_compra=100.0)
    assert entradas().iloc[0]["coste"] == pytest.approx(1000.0)


def test_the_holding_period_reaches_the_summary(warehouse):
    """El aviso de "todavia no significa nada" depende de esto: sin los dias,
    una cartera de la semana pasada pareceria un historico."""
    sembrar(comprada=HOY - timedelta(days=45))
    r = resumir(posiciones_desde(entradas()))
    assert r.dias_mediana == 45
    assert not r.hay_bastante


def test_all_legs_are_measured_with_the_same_kind_of_price(warehouse):
    """Tu retorno se mide desde `avg_cost`, que es el precio BRUTO que pagaste.
    Midiendo el indice con el precio ajustado se le regalaban los dividendos
    reinvertidos: el mercado salia por delante en la rentabilidad por dividendo
    del indice —cerca de dos puntos al ano— sin que nada fallara.

    Aqui el ajustado del mercado es mucho menor que su cierre, asi que usar uno
    u otro da retornos muy distintos.
    """
    comprada = HOY - timedelta(days=300)
    with db.connect() as conn:
        conn.execute("INSERT INTO instruments (ticker, asset_class, gics_sector) "
                     "VALUES ('AAA', 'equity', 'Information Technology')")
        conn.execute("INSERT INTO positions VALUES ('p', 'AAA', 10, 100.0, "
                     "'EUR', ?, NULL, '', NULL)", [comprada])
        for cuando, precio in ((comprada, 100.0), (HOY, 120.0)):
            conn.execute(
                "INSERT INTO prices_daily (ticker, date, close, adj_close) "
                "VALUES ('AAA', ?, ?, ?)", [cuando, precio, precio])
        # El mercado sube un 10 % en precio; su serie ajustada sube un 25 %.
        for cuando, cierre, ajustado in ((comprada, 200.0, 160.0),
                                         (HOY, 220.0, 200.0)):
            conn.execute(
                "INSERT INTO prices_daily (ticker, date, close, adj_close) "
                "VALUES ('SPY', ?, ?, ?)", [cuando, cierre, ajustado])

    fila = entradas().iloc[0]
    assert fila["retorno_mercado"] == pytest.approx(0.10), (
        "el mercado se esta midiendo con el precio ajustado y el tuyo no"
    )
