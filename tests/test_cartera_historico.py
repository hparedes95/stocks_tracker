"""Reimportar el extracto no puede borrar lo que vendiste.

EL FALLO, ENCONTRADO EN LA AUDITORIA FINANCIERA

`replace_positions` empezaba por:

    DELETE FROM positions WHERE closed_at IS NULL

Un extracto es una foto completa, asi que lo que no sale en el ya no lo tienes.
Pero "ya no lo tienes" significa QUE LO VENDISTE, y eso es un hecho que hay que
guardar, no una fila que sobra.

Con el borrado:

- Una posicion vendida entre dos importaciones desaparecia entera. No quedaba
  cerrada: quedaba borrada. `get_closed_sales` no la veia nunca, y con ella se
  vaciaba el historico de ventas y la base de la regla de los dos meses —que
  necesita saber que vendiste con perdida y cuando—.
- `opened_at` se reescribia a HOY en cada importacion, incluso en posiciones de
  hace dos anos. "Dias en cartera" dejaba de ser cierto para siempre.

Reproducido antes de arreglarlo: comprar AAPL el 1/03, venderla, reimportar ->
cero filas de AAPL y cero ventas cerradas. La operacion evaporada.

Es el peor tipo de fallo de los que persigue este proyecto: no da error, no
falla ningun calculo, y lo que se pierde son datos que no se pueden reconstruir
desde ningun sitio.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stocks_tracker.core import db

MARZO = date(2026, 3, 1)


@pytest.fixture
def almacen(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}
        ui: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    import streamlit as st

    st.cache_data.clear()
    return Stub


def _extracto(*filas) -> pd.DataFrame:
    return pd.DataFrame([
        {"ticker": t, "qty": q, "avg_cost": c, "currency": "EUR"}
        for t, q, c in filas
    ])


def _abiertas(conn):
    return conn.execute(
        "SELECT ticker, qty, avg_cost, opened_at FROM positions "
        "WHERE closed_at IS NULL ORDER BY ticker"
    ).fetchall()


def _cerradas(conn):
    return conn.execute(
        "SELECT ticker, closed_at FROM positions "
        "WHERE closed_at IS NOT NULL ORDER BY ticker"
    ).fetchall()


def _con_aapl_desde_marzo():
    from stocks_tracker.app import data_access as da

    da.add_position("AAPL", 10, 100.0, "EUR")
    with db.connect() as conn:
        conn.execute("UPDATE positions SET opened_at = ?", [MARZO])


# ---------------------------------------------------------------------------
# El fallo
# ---------------------------------------------------------------------------
def test_lo_vendido_se_cierra_y_no_se_borra(almacen):
    """EL CASO EXACTO. Compras AAPL, la vendes, y el extracto siguiente ya no la
    trae. Tiene que quedar CERRADA, no desaparecer."""
    from stocks_tracker.app import data_access as da

    _con_aapl_desde_marzo()

    da.replace_positions(_extracto(("MSFT", 5, 300.0)))

    with db.connect(read_only=True) as conn:
        assert [f[0] for f in _abiertas(conn)] == ["MSFT"]
        cerradas = _cerradas(conn)

    assert cerradas == [("AAPL", date.today())], (
        "la venta se ha borrado en vez de cerrarse: el historico se pierde"
    )


def test_una_posicion_que_sigue_conserva_su_fecha_de_entrada(almacen):
    """`opened_at` se reescribia a hoy en CADA importacion. Con eso, "dias en
    cartera" no volvia a ser cierto nunca, y la regla de los dos meses perdia
    su base."""
    from stocks_tracker.app import data_access as da

    _con_aapl_desde_marzo()

    da.replace_positions(_extracto(("AAPL", 10, 100.0)))

    with db.connect(read_only=True) as conn:
        assert _abiertas(conn) == [("AAPL", 10.0, 100.0, MARZO)]


def test_se_actualiza_cantidad_y_precio_medio(almacen):
    """Comprar mas del mismo valor cambia las dos cosas, y el extracto ya trae
    el precio medio recalculado por el broker."""
    from stocks_tracker.app import data_access as da

    _con_aapl_desde_marzo()

    da.replace_positions(_extracto(("AAPL", 25, 112.5)))

    with db.connect(read_only=True) as conn:
        assert _abiertas(conn) == [("AAPL", 25.0, 112.5, MARZO)]
        assert _cerradas(conn) == []


def test_un_valor_nuevo_entra_con_la_fecha_de_hoy(almacen):
    from stocks_tracker.app import data_access as da

    da.replace_positions(_extracto(("NVDA", 3, 900.0)))

    with db.connect(read_only=True) as conn:
        assert _abiertas(conn) == [("NVDA", 3.0, 900.0, date.today())]


def test_reimportar_el_mismo_extracto_no_cambia_nada(almacen):
    """La operacion mas comun, y la que delataria un borrado o un duplicado."""
    from stocks_tracker.app import data_access as da

    _con_aapl_desde_marzo()
    da.replace_positions(_extracto(("AAPL", 10, 100.0)))
    da.replace_positions(_extracto(("AAPL", 10, 100.0)))

    with db.connect(read_only=True) as conn:
        assert _abiertas(conn) == [("AAPL", 10.0, 100.0, MARZO)]
        assert _cerradas(conn) == []


def test_varios_lotes_del_mismo_valor_dejan_uno_y_cierran_el_resto(almacen):
    """LA LIMITACION, comprobada en vez de dada por supuesta.

    El extracto trae UNA linea agregada por valor y no hay forma de repartirla
    entre varios lotes. Se conserva el `opened_at` mas antiguo —el que de verdad
    marca cuanto llevas dentro— y los sobrantes se cierran.
    """
    from stocks_tracker.app import data_access as da

    _con_aapl_desde_marzo()
    da.add_position("AAPL", 5, 120.0, "EUR")     # segundo lote, con fecha de hoy

    da.replace_positions(_extracto(("AAPL", 15, 106.67)))

    with db.connect(read_only=True) as conn:
        abiertas = _abiertas(conn)
        assert len(abiertas) == 1
        assert abiertas[0][3] == MARZO, "se ha perdido la fecha de entrada mas antigua"
        assert len(_cerradas(conn)) == 1


def test_consolidar_lotes_no_es_una_venta(almacen):
    """LA REGRESION QUE INTRODUJO EL ARREGLO DE ARRIBA, encontrada en revision.

    Al cambiar el borrado por un cierre, los lotes que se fusionan pasaron a
    quedar cerrados igual que una venta, y `get_closed_sales` no sabia
    distinguirlos.

    Reproducido: dos lotes de AAPL (10 a 100 el 1/03 y 5 a 120 hoy), el extracto
    trae la linea agregada de 15 titulos, el precio de hoy es 90.

        posiciones abiertas: 1        <- correcto, sigue entera
        VENTAS que ve el historico: 1 <- 5 titulos a -25 %
        la regla de los dos meses ve 150 EUR de perdida

    No se ha vendido nada. El aviso fiscal saltaba sobre una posicion intacta, y
    el resultado acumulado de las ventas quedaba contaminado con una operacion
    que no existio.

    Se arregla marcando POR QUE se cerro cada fila: `venta` o `consolidacion`.
    """
    from stocks_tracker.app import data_access as da

    _con_aapl_desde_marzo()
    da.add_position("AAPL", 5, 120.0, "EUR")
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "AAPL", "asset_class": "equity", "is_active": True},
        ]), keys=["ticker"])
        db.upsert_df(conn, "prices_daily", pd.DataFrame([
            {"ticker": "AAPL", "date": date.today(), "open": 90.0, "high": 90.0,
             "low": 90.0, "close": 90.0, "adj_close": 90.0, "volume": 1_000,
             "source": "yfinance"},
        ]), keys=["ticker", "date"])

    da.replace_positions(_extracto(("AAPL", 15, 106.67)))

    assert da.get_closed_sales("AAPL").empty, (
        "consolidar dos lotes esta apareciendo como una venta que nadie hizo"
    )

    from stocks_tracker.app.components import cost_panel
    ventas, _ = cost_panel._ventas_recientes("AAPL")
    assert ventas == [], (
        "la regla de los dos meses salta por una consolidacion de lotes"
    )


def test_el_motivo_del_cierre_queda_escrito(almacen):
    """Vender y consolidar se guardan distinto, o no hay forma de separarlos
    despues."""
    from stocks_tracker.app import data_access as da

    _con_aapl_desde_marzo()
    da.add_position("AAPL", 5, 120.0, "EUR")
    da.add_position("MSFT", 3, 300.0, "EUR")

    # AAPL sigue (se consolida), MSFT desaparece (se vende).
    da.replace_positions(_extracto(("AAPL", 15, 106.67)))

    with db.connect(read_only=True) as conn:
        motivos = dict(conn.execute(
            "SELECT ticker, closed_reason FROM positions "
            "WHERE closed_at IS NOT NULL"
        ).fetchall())

    assert motivos == {"AAPL": da.CIERRE_CONSOLIDACION, "MSFT": da.CIERRE_VENTA}


def test_una_venta_de_verdad_sigue_llegando_al_historico(almacen):
    """Contraprueba del arreglo: filtrar las consolidaciones no puede llevarse
    por delante las ventas, que es justo lo que este modulo protege."""
    from stocks_tracker.app import data_access as da

    _con_aapl_desde_marzo()
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "AAPL", "asset_class": "equity", "is_active": True},
        ]), keys=["ticker"])
        db.upsert_df(conn, "prices_daily", pd.DataFrame([
            {"ticker": "AAPL", "date": date.today(), "open": 90.0, "high": 90.0,
             "low": 90.0, "close": 90.0, "adj_close": 90.0, "volume": 1_000,
             "source": "yfinance"},
        ]), keys=["ticker", "date"])

    da.replace_positions(_extracto(("MSFT", 5, 300.0)))

    assert len(da.get_closed_sales("AAPL")) == 1


def test_los_cierres_antiguos_sin_motivo_cuentan_como_ventas(almacen):
    """Compatibilidad. Las filas cerradas antes de que existiera la columna
    tienen `closed_reason` NULL, y entonces todos los cierres eran ventas.
    Tratarlas como consolidaciones vaciaria el historico de golpe."""
    from stocks_tracker.app import data_access as da

    _con_aapl_desde_marzo()
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "AAPL", "asset_class": "equity", "is_active": True},
        ]), keys=["ticker"])
        db.upsert_df(conn, "prices_daily", pd.DataFrame([
            {"ticker": "AAPL", "date": date.today(), "open": 90.0, "high": 90.0,
             "low": 90.0, "close": 90.0, "adj_close": 90.0, "volume": 1_000,
             "source": "yfinance"},
        ]), keys=["ticker", "date"])
        conn.execute("UPDATE positions SET closed_at = ?, closed_reason = NULL",
                     [date.today()])

    assert len(da.get_closed_sales("AAPL")) == 1


def test_un_extracto_vacio_no_borra_la_cartera(almacen):
    """Un fichero mal leido no puede vaciar la cartera: se prefiere no hacer
    nada a destruir lo que hay."""
    from stocks_tracker.app import data_access as da

    _con_aapl_desde_marzo()

    assert da.replace_positions(pd.DataFrame()) == 0

    with db.connect(read_only=True) as conn:
        assert len(_abiertas(conn)) == 1


def test_la_venta_llega_al_historico_de_resultados(almacen):
    """La consecuencia que importa: que `get_closed_sales` la vea.

    Es la funcion que alimenta el resultado de cada venta y, a traves de el, la
    perdida en euros que necesita la regla de los dos meses.
    """
    from stocks_tracker.app import data_access as da

    _con_aapl_desde_marzo()
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "AAPL", "asset_class": "equity", "is_active": True},
        ]), keys=["ticker"])
        db.upsert_df(conn, "prices_daily", pd.DataFrame([
            {"ticker": "AAPL", "date": date.today(), "open": 110.0, "high": 111.0,
             "low": 109.0, "close": 110.0, "adj_close": 110.0, "volume": 1_000,
             "source": "yfinance"},
        ]), keys=["ticker", "date"])

    da.replace_positions(_extracto(("MSFT", 5, 300.0)))

    ventas = da.get_closed_sales("AAPL")

    assert len(ventas) == 1, "la venta no aparece en el historico de resultados"
    assert ventas.iloc[0]["resultado_pct"] == pytest.approx(10.0)
