"""De la base de datos al semaforo.

`core.deterioration` esta probado con diccionarios a mano. Lo que se prueba
aqui es lo que hay entre medias, que es donde se rompen estas cosas sin dar
error: que el "entonces" sea de verdad el dia de la compra y no el de hoy —con
los mismos datos en los dos lados no hay nada que comparar y todo sale verde—,
y que las columnas se repartan bien entre presente y pasado.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from stocks_tracker.app.components.health_panel import ORDEN, diagnosticos
from stocks_tracker.core import db
from stocks_tracker.core.deterioration import Nivel

HOY = date(2026, 8, 10)
COMPRA = date(2025, 2, 3)


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


def sembrar(*, margen_entonces=0.22, margen_hoy=0.14, comprada=COMPRA,
            ticker="AAA", foto_entonces=COMPRA - timedelta(days=20)) -> None:
    """Una posicion abierta, con foto de fundamentales antes y despues.

    Las dos fotos tienen que existir de verdad en fechas distintas: es el unico
    escenario en el que se nota si la consulta trae la que tocaba.

    `foto_entonces` es independiente de `comprada` a proposito: si se calculara
    a partir de ella, mover la compra moveria tambien la foto y no habria forma
    de montar el caso de "comprada antes de que existiera ninguna foto".
    """
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO instruments (ticker, asset_class) VALUES (?, 'equity')",
            [ticker],
        )
        conn.execute(
            "INSERT INTO positions (id, ticker, qty, avg_cost, currency, opened_at, closed_at, note, updated_at) VALUES (?, ?, 10, 100.0, 'EUR', ?, NULL, '', NULL)",
            [f"pos-{ticker}", ticker, comprada],
        )
        for as_of, margen in ((foto_entonces, margen_entonces),
                              (HOY - timedelta(days=5), margen_hoy)):
            conn.execute(
                "INSERT INTO fundamentals_snapshot (ticker, as_of, profit_margin) "
                "VALUES (?, ?, ?)", [ticker, as_of, margen],
            )
        # `current_session` toma la ultima fecha con suficientes instrumentos,
        # asi que hacen falta indicadores de hoy para que el 'hoy' exista.
        for cuando, drawdown, encima in ((comprada, -0.02, True), (HOY, -0.38, False)):
            conn.execute(
                "INSERT INTO indicators_daily (ticker, date, close, drawdown, "
                "above_sma200) VALUES (?, ?, 100.0, ?, ?)",
                [ticker, cuando, drawdown, encima],
            )


def salud(monkeypatch) -> pd.DataFrame:
    from stocks_tracker.app import data_access as da

    return da.get_position_health.__wrapped__()


# ---------------------------------------------------------------------------
# El "entonces" es de entonces
# ---------------------------------------------------------------------------
def test_the_past_column_holds_the_snapshot_from_the_purchase_day(warehouse,
                                                                  monkeypatch):
    """La comprobacion central. Si trajera la foto de hoy en las dos columnas,
    la resta daria cero y TODA la cartera saldria verde sin dar ningun error:
    el fallo mas facil de no ver de todo el semaforo."""
    sembrar()
    fila = salud(monkeypatch).iloc[0]
    assert fila["profit_margin"] == pytest.approx(0.14)
    assert fila["profit_margin_entonces"] == pytest.approx(0.22)


def test_a_snapshot_taken_after_the_purchase_is_not_used_as_the_past(warehouse,
                                                                     monkeypatch):
    """Es la misma regla punto-en-el-tiempo del ranking historico: la foto de
    referencia tiene que ser anterior a la compra, no la siguiente."""
    sembrar()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO fundamentals_snapshot (ticker, as_of, profit_margin) "
            "VALUES ('AAA', ?, 0.99)", [COMPRA + timedelta(days=10)],
        )
    assert salud(monkeypatch).iloc[0]["profit_margin_entonces"] == pytest.approx(0.22)


def test_the_today_column_holds_the_most_recent_snapshot(warehouse, monkeypatch):
    """El contrario del test anterior: si siempre cogiera la mas antigua, aquel
    pasaria sin comprobar nada."""
    sembrar()
    assert salud(monkeypatch).iloc[0]["profit_margin"] == pytest.approx(0.14)


def test_a_position_bought_before_any_snapshot_has_no_past(warehouse, monkeypatch):
    """Y sale nulo, no cero. Un cero diria que el margen ha subido 14 puntos."""
    sembrar(comprada=date(2020, 1, 1))
    assert pd.isna(salud(monkeypatch).iloc[0]["profit_margin_entonces"])


# ---------------------------------------------------------------------------
# De la fila al diagnostico
# ---------------------------------------------------------------------------
def test_the_whole_chain_lights_the_light(warehouse, monkeypatch):
    """Base de datos -> consulta -> reparto de columnas -> semaforo."""
    sembrar()
    d = diagnosticos(salud(monkeypatch))[0]
    assert d.ticker == "AAA"
    assert {"margen", "mm200", "caida"} <= {s.clave for s in d.senales}
    # AMBAR y no ROJO desde la auditoria de falsos positivos: el margen hundido
    # es UN problema (grave, 2 puntos) y `mm200` + `caida` son el precio
    # reflejandolo, que cuenta como uno. Total 3.
    #
    # Los tres motivos se siguen ensenando; lo que ya no pasa es que el precio
    # confirmando un problema lo convierta en varios problemas.
    assert d.nivel is Nivel.AMBAR
    assert d.puntos == 3


def test_a_healthy_position_stays_green(warehouse, monkeypatch):
    """Sin esto, un semaforo que se encendiera siempre pasaria los tests de
    arriba igual de bien."""
    sembrar(margen_entonces=0.21, margen_hoy=0.22)
    with db.connect() as conn:
        conn.execute("UPDATE indicators_daily SET drawdown = -0.03, "
                     "above_sma200 = TRUE WHERE date = ?", [HOY])
    d = diagnosticos(salud(monkeypatch))[0]
    assert d.senales == []
    assert d.nivel is Nivel.VERDE


def test_the_purchase_date_travels_to_the_screen(warehouse, monkeypatch):
    """El texto dice "desde que compraste": sin la fecha, no se puede saber
    desde cuando."""
    sembrar()
    d = diagnosticos(salud(monkeypatch))[0]
    assert pd.Timestamp(d.comparado_con).date() == COMPRA


def test_the_worst_positions_come_first(warehouse, monkeypatch):
    """Ordenar por ticker dejaria lo urgente en mitad de la lista.

    El que se deteriora es el ULTIMO por orden alfabetico a proposito: con la
    peor posicion llamandose "AAA", ordenar por gravedad y ordenar por nombre
    dan el mismo resultado y el test pasaria con el criterio equivocado —que es
    lo que hacia—.
    """
    sembrar(ticker="ZZZ")                                   # se deteriora
    sembrar(ticker="AAA", margen_entonces=0.21, margen_hoy=0.22)
    with db.connect() as conn:
        conn.execute("UPDATE indicators_daily SET drawdown = -0.03, "
                     "above_sma200 = TRUE WHERE ticker = 'AAA'")
    orden = [d.ticker for d in diagnosticos(salud(monkeypatch))]
    assert orden == ["ZZZ", "AAA"]


def test_not_being_able_to_check_ranks_above_being_fine(warehouse, monkeypatch):
    """El gris va antes que el verde: "no se ha podido comprobar" merece mas
    atencion que "comprobado y sin novedad"."""
    assert ORDEN[Nivel.GRIS] < ORDEN[Nivel.VERDE]
    assert ORDEN[Nivel.ROJO] < ORDEN[Nivel.AMBAR] < ORDEN[Nivel.GRIS]


def test_a_closed_position_is_not_diagnosed(warehouse, monkeypatch):
    """Lo que ya vendiste no hay que vigilarlo."""
    sembrar()
    with db.connect() as conn:
        conn.execute("UPDATE positions SET closed_at = ?", [HOY])
    assert salud(monkeypatch).empty


def test_an_empty_portfolio_is_not_an_error(warehouse, monkeypatch):
    assert salud(monkeypatch).empty
    assert diagnosticos(salud(monkeypatch)) == []
