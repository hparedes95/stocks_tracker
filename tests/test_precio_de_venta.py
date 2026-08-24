"""Cerrar una posicion no guardaba a que precio se vendio.

EL FALLO, ENCONTRADO EN LA AUDITORIA FINANCIERA

`close_position` marcaba `closed_at` y nada mas. El resultado de la venta se
ESTIMABA despues, en `get_closed_sales`, con el cierre de ese dia.

El cierre del dia no es el precio de ejecucion. Un valor que abre en 100, toca
104 y cierra en 99 pudo venderse en cualquiera de los tres. Y esa estimacion no
es decorativa: alimenta la regla de los dos meses (`cost_panel`), que avisa de
recomprar un valor vendido con perdida antes de dos meses.

EL CASO QUE HACE DANO, CON NUMEROS

  Compra a 100, venta real a 99 -> perdida del 1 %.
  Cierre de ese dia: 104        -> el sistema estima +4 % de GANANCIA.

Con +4 % la regla no avisa, se recompra dentro de los dos meses y la minusvalia
no se puede compensar. (Punto a confirmar con un asesor fiscal; lo que aqui se
arregla es que el aviso llegue.)

Y al reves tambien: venta real a 104 con cierre en 99 da un aviso de perdida
sobre una operacion que fue ganancia, y los avisos falsos se acaban ignorando
igual que los que faltan.

ARREGLO

- `positions.close_price`, y `close_position(id, close_price=...)` lo guarda.
- `get_closed_sales` usa el precio real si lo hay y estima si no, y devuelve
  `precio_real` para que se sepa cual de las dos cosas es.
- La regla de los dos meses aplica su margen de seguridad solo a lo estimado.

`close_price` NULL sigue siendo un caso valido —lo que pasa al reimportar el
extracto, donde el precio de venta no viene— y ahi se sigue estimando. Lo que
ya no se puede es confundir una estimacion con un dato.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from stocks_tracker.core import db

COMPRA = 100.0
AYER = date.today() - timedelta(days=1)


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


def _compra_aapl() -> str:
    """Una compra de AAPL a 100, y el cierre de hoy en 104."""
    from stocks_tracker.app import data_access as da

    da.add_position("AAPL", 10, COMPRA, "EUR")
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "AAPL", "asset_class": "equity", "is_active": True},
        ]), keys=["ticker"])
        db.upsert_df(conn, "prices_daily", pd.DataFrame([
            {"ticker": "AAPL", "date": date.today(), "open": 100.0, "high": 104.0,
             "low": 99.0, "close": 104.0, "adj_close": 104.0, "volume": 1_000,
             "source": "yfinance"},
        ]), keys=["ticker", "date"])
        return conn.execute("SELECT id FROM positions").fetchone()[0]


# ---------------------------------------------------------------------------
# El fallo
# ---------------------------------------------------------------------------
def test_el_precio_de_venta_registrado_manda_sobre_el_cierre(almacen):
    """EL CASO EXACTO. Vendida a 99 con el cierre en 104: es una PERDIDA del
    1 %, no una ganancia del 4 %."""
    from stocks_tracker.app import data_access as da

    pos = _compra_aapl()
    da.close_position(pos, close_price=99.0)

    venta = da.get_closed_sales("AAPL").iloc[0]

    assert venta["resultado_pct"] == pytest.approx(-1.0), (
        "el resultado se sigue calculando con el cierre y no con lo que se vendio"
    )
    assert venta["precio_estimado"] == pytest.approx(99.0)


def test_una_venta_registrada_se_marca_como_real(almacen):
    from stocks_tracker.app import data_access as da

    pos = _compra_aapl()
    da.close_position(pos, close_price=99.0)

    assert bool(da.get_closed_sales("AAPL").iloc[0]["precio_real"]) is True


def test_sin_precio_se_estima_y_se_dice_que_es_estimado(almacen):
    """El caso valido: se cierra sin precio y se estima con el cierre. Lo que
    no puede pasar es que se presente como si fuera un dato."""
    from stocks_tracker.app import data_access as da

    pos = _compra_aapl()
    da.close_position(pos)

    venta = da.get_closed_sales("AAPL").iloc[0]

    assert venta["resultado_pct"] == pytest.approx(4.0)
    assert bool(venta["precio_real"]) is False, (
        "una estimacion se esta presentando como precio real"
    )


def test_un_precio_de_venta_de_cero_no_cuenta_como_dato(almacen):
    """0.0 no es un precio de venta: es el campo vacio del formulario. Tomarlo
    al pie de la letra daria una perdida del 100 % inventada."""
    from stocks_tracker.app import data_access as da

    pos = _compra_aapl()
    da.close_position(pos, close_price=0.0)

    venta = da.get_closed_sales("AAPL").iloc[0]

    assert bool(venta["precio_real"]) is False
    assert venta["resultado_pct"] == pytest.approx(4.0)


def test_se_puede_registrar_una_venta_de_un_dia_anterior(almacen):
    from stocks_tracker.app import data_access as da

    pos = _compra_aapl()
    da.close_position(pos, close_price=99.0, closed_at=AYER)

    venta = da.get_closed_sales("AAPL").iloc[0]
    cuando = venta["closed_at"]
    assert (cuando.date() if hasattr(cuando, "date") else cuando) == AYER
    assert venta["resultado_pct"] == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# La consecuencia: la regla de los dos meses
# ---------------------------------------------------------------------------
def test_la_regla_de_los_dos_meses_ve_la_perdida_que_el_cierre_ocultaba(almacen):
    """Lo que de verdad importa del arreglo. Con la estimacion (+4 %) la venta
    ni siquiera llegaba a la regla."""
    from stocks_tracker.app.components import cost_panel

    pos = _compra_aapl()
    from stocks_tracker.app import data_access as da
    da.close_position(pos, close_price=99.0)

    ventas, _ = cost_panel._ventas_recientes("AAPL")

    assert len(ventas) == 1, "la venta con perdida no llega a la regla"
    assert ventas[0]["perdida_eur"] == pytest.approx(10.0)  # (100 - 99) * 10


def test_una_ganancia_real_pequena_no_dispara_un_aviso_falso(almacen):
    """El margen del 2 % existe porque la ESTIMACION puede equivocarse de
    signo. Con un precio real de venta no hay nada que estimar: un +1 % es un
    +1 % y avisar de una perdida inexistente solo ensena a ignorar el aviso."""
    from stocks_tracker.app import data_access as da
    from stocks_tracker.app.components import cost_panel

    pos = _compra_aapl()
    da.close_position(pos, close_price=101.0)

    ventas, _ = cost_panel._ventas_recientes("AAPL")

    assert ventas == [], "una ganancia real del 1 % esta generando un aviso"


def test_una_ganancia_estimada_pequena_si_avisa(almacen):
    """Y el margen se conserva donde hace falta: sin precio real, un +1 %
    estimado pudo ser una perdida."""
    from stocks_tracker.app import data_access as da
    from stocks_tracker.app.components import cost_panel

    da.add_position("AAPL", 10, 103.0, "EUR")   # cierre 104 -> +0,97 % estimado
    with db.connect() as conn:
        db.upsert_df(conn, "prices_daily", pd.DataFrame([
            {"ticker": "AAPL", "date": date.today(), "open": 104.0, "high": 104.0,
             "low": 104.0, "close": 104.0, "adj_close": 104.0, "volume": 1_000,
             "source": "yfinance"},
        ]), keys=["ticker", "date"])
        pos = conn.execute("SELECT id FROM positions").fetchone()[0]
    da.close_position(pos)

    ventas, _ = cost_panel._ventas_recientes("AAPL")

    assert len(ventas) == 1, "se ha perdido el margen de seguridad de lo estimado"
