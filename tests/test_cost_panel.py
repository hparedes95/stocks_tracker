"""Que ventas pasadas activan el aviso de la regla de los dos meses.

`positions` guarda a que precio se compro pero NO a que precio se vendio, asi
que si la venta fue con perdidas no se sabe: se estima con el cierre del dia.
Aqui se prueban las dos formas de equivocarse, que no son simetricas:

- Avisar de mas: molesta.
- Callar un aviso: el usuario recompra y pierde la compensacion fiscal que
  buscaba, y no se entera hasta la declaracion.

Por eso "no se sabe" avisa y solo se calla cuando la ganancia es clara.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from stocks_tracker.app.components.cost_panel import (
    MARGEN_ESTIMACION_PCT,
    _ventas_recientes,
)
from stocks_tracker.core import db


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


def sembrar(*, dias_atras: int, avg_cost: float, cierre: float | None,
            qty: float = 10.0, desfase_precio: int = 0) -> date:
    """Una venta cerrada hace `dias_atras` y el precio de aquel dia.

    `desfase_precio` retrasa la ultima sesion con precio respecto a la venta,
    para probar el hueco de fin de semana y el de un valor sin cotizar.
    """
    vendida = date.today() - timedelta(days=dias_atras)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO positions VALUES (?, 'AAA', ?, ?, 'EUR', ?, ?, '', NULL)",
            [f"p{dias_atras}", qty, avg_cost,
             vendida - timedelta(days=30), vendida],
        )
        if cierre is not None:
            conn.execute(
                "INSERT INTO prices_daily (ticker, date, close, adj_close) "
                "VALUES ('AAA', ?, ?, ?)",
                [vendida - timedelta(days=desfase_precio), cierre, cierre],
            )
    return vendida


def ventas(monkeypatch):
    """Llama al lector saltando la cache de Streamlit.

    Sin esto el segundo test del fichero leeria el resultado del primero.
    """
    from stocks_tracker.app import data_access as da

    monkeypatch.setattr(da, "get_closed_sales",
                        da.get_closed_sales.__wrapped__, raising=False)
    return _ventas_recientes("AAA")


# ---------------------------------------------------------------------------
# Cuando SI hay que avisar
# ---------------------------------------------------------------------------
def test_a_recent_sale_at_a_loss_raises_the_warning(warehouse, monkeypatch):
    """El caso que existe para esto: vendiste en perdidas hace poco y ahora
    estas mirando la ficha para recomprar."""
    sembrar(dias_atras=10, avg_cost=100.0, cierre=80.0)
    lista, estimado = ventas(monkeypatch)
    assert len(lista) == 1
    assert estimado[lista[0]["closed_at"]] == pytest.approx(-20.0)
    assert lista[0]["perdida_eur"] == pytest.approx(200.0)   # (100-80) * 10


def test_a_sale_with_no_price_that_day_warns_instead_of_assuming_a_gain(
    warehouse, monkeypatch
):
    """Sin precio no se sabe el resultado. Tratar "no se sabe" como ganancia
    haria desaparecer el aviso justo en el caso peor documentado: un valor que
    dejo de cotizar, que casi siempre se vendio con perdidas."""
    sembrar(dias_atras=10, avg_cost=100.0, cierre=None)
    lista, estimado = ventas(monkeypatch)
    assert len(lista) == 1
    assert estimado[lista[0]["closed_at"]] is None


def test_a_price_too_old_is_not_used_as_an_estimate(warehouse, monkeypatch):
    """Un precio de hace un mes no dice a que vendiste. Usarlo daria una
    estimacion inventada con pinta de dato."""
    sembrar(dias_atras=10, avg_cost=100.0, cierre=80.0, desfase_precio=30)
    lista, estimado = ventas(monkeypatch)
    assert len(lista) == 1
    assert estimado[lista[0]["closed_at"]] is None, (
        "ha estimado con un precio de un mes antes de la venta"
    )


def test_a_weekend_gap_still_finds_the_price(warehouse, monkeypatch):
    """Vender un lunes festivo no puede dejar sin estimacion: el viernes
    anterior sirve. Sin esta tolerancia casi ninguna venta se estimaria."""
    sembrar(dias_atras=10, avg_cost=100.0, cierre=80.0, desfase_precio=3)
    _, estimado = ventas(monkeypatch)
    assert list(estimado.values()) == [pytest.approx(-20.0)]


def test_a_result_near_break_even_still_warns(warehouse, monkeypatch):
    """El cierre no es el precio de ejecucion y `avg_cost` no lleva comisiones:
    un +1 % estimado pudo ser una perdida real de verdad."""
    sembrar(dias_atras=10, avg_cost=100.0, cierre=101.0)
    lista, _ = ventas(monkeypatch)
    assert len(lista) == 1
    assert MARGEN_ESTIMACION_PCT > 1.0


# ---------------------------------------------------------------------------
# Cuando NO hay que avisar
# ---------------------------------------------------------------------------
def test_a_clear_gain_is_not_warned_about(warehouse, monkeypatch):
    """La regla es solo para perdidas. Avisar en cada venta con ganancia
    convertiria el aviso en ruido, y un aviso que siempre sale no se lee."""
    sembrar(dias_atras=10, avg_cost=100.0, cierre=140.0)
    lista, _ = ventas(monkeypatch)
    assert lista == []


def test_an_old_sale_does_not_reach_the_rule(warehouse, monkeypatch):
    """Pasados los dos meses ya se puede recomprar sin perder la compensacion.

    El filtro de los 60 dias lo hace `costs.comprobar_dos_meses`; aqui la venta
    se lee igualmente, y esta es la comprobacion de que la cadena entera no
    avisa por algo de hace ano y medio.
    """
    from stocks_tracker.core import costs

    sembrar(dias_atras=200, avg_cost=100.0, cierre=80.0)
    lista, _ = ventas(monkeypatch)
    assert not costs.comprobar_dos_meses("AAA", lista).bloquea


def test_no_positions_at_all_is_not_an_error(warehouse, monkeypatch):
    assert ventas(monkeypatch) == ([], {})


def test_an_open_position_is_not_a_sale(warehouse, monkeypatch):
    """Sin `closed_at` no se ha vendido nada: la regla no tiene nada que mirar."""
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO positions VALUES "
            "('abierta', 'AAA', 10, 100.0, 'EUR', ?, NULL, '', NULL)",
            [date.today() - timedelta(days=5)],
        )
    assert ventas(monkeypatch) == ([], {})


def test_another_ticker_does_not_leak_into_this_one(warehouse, monkeypatch):
    """La regla es por valores homogeneos: haber vendido BBB en perdidas no
    impide nada al comprar AAA."""
    vendida = date.today() - timedelta(days=10)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO positions VALUES "
            "('otra', 'BBB', 10, 100.0, 'EUR', ?, ?, '', NULL)",
            [vendida - timedelta(days=30), vendida],
        )
        conn.execute(
            "INSERT INTO prices_daily (ticker, date, close, adj_close) "
            "VALUES ('BBB', ?, 50.0, 50.0)", [vendida],
        )
    assert ventas(monkeypatch) == ([], {})


# ---------------------------------------------------------------------------
# El formato que espera `costs`
# ---------------------------------------------------------------------------
def test_the_dates_come_out_as_dates_and_not_as_timestamps(warehouse, monkeypatch):
    """DuckDB devuelve fechas como `datetime64` al pasar por pandas.
    `comprobar_dos_meses` resta `hoy - closed_at` esperando dias enteros, y con
    un `Timestamp` la resta da un `Timedelta` cuyo `.days` sigue funcionando,
    pero la clave del diccionario de estimaciones tiene que casar con la que
    guarda el aviso o el porcentaje no se ensena nunca.
    """
    from stocks_tracker.core import costs

    sembrar(dias_atras=10, avg_cost=100.0, cierre=80.0)
    lista, estimado = ventas(monkeypatch)
    regla = costs.comprobar_dos_meses("AAA", lista)
    assert regla.bloquea
    assert estimado.get(regla.avisos[0].vendido_el) is not None, (
        "la clave de la estimacion no casa con la fecha del aviso"
    )


def test_the_estimate_is_a_number_not_a_pandas_null(warehouse, monkeypatch):
    """`pd.NA` formateado con `:+.1f` revienta la pantalla entera."""
    sembrar(dias_atras=10, avg_cost=100.0, cierre=80.0)
    _, estimado = ventas(monkeypatch)
    for valor in estimado.values():
        assert valor is None or (isinstance(valor, float) and not pd.isna(valor))
