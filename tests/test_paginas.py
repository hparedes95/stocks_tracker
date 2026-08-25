"""Que las once paginas se pinten sin reventar.

POR QUE HACIA FALTA

Hasta ahora esto se comprobaba abriendo el navegador y mirando. Funciona
mientras alguien se acuerde de mirar las once, y deja de funcionar exactamente
el dia que se toca algo pequeno en la pagina que no se mira.

Y hay una comprobacion que a mano no se hace nunca: **el almacen recien
creado**. Quien instala el programa por primera vez abre el dashboard antes de
haber descargado nada, y esa es la unica vez que muchas consultas se ejecutan
sobre tablas vacias. Es el estado menos probado y el primero que ve un usuario
nuevo.

QUE ENCONTRO LA PRIMERA VEZ QUE SE EJECUTO

`data_access.data_freshness` reventaba con "cannot convert float NaN to
integer" sobre un almacen vacio, y con ella la pagina de Estado entera. La
causa es una trampa de SQL que se repite: un `SELECT SUM(...)` sin `GROUP BY`
SIEMPRE devuelve una fila —sobre una tabla vacia, una fila con NULL—, asi que
la guarda `if not df.empty` no protegia de nada. Y el `or 0` tampoco, porque
NaN es verdadero.

Un fallo asi no se ve en ningun test de logica: la funcion es correcta en
cuanto hay una sola fila de datos.

QUE NO COMPRUEBA

Que las paginas digan cosas ciertas. Solo que se ejecuten enteras sin
excepciones. Es poco, y es justo lo que a mano no se hace de forma fiable.
"""

from __future__ import annotations

import pathlib
from datetime import date, timedelta

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from stocks_tracker.core import db
from stocks_tracker.core.config import project_root

PAGINAS = sorted(
    p for p in (project_root() / "src/stocks_tracker/app/pages").glob("*.py")
    if p.name != "__init__.py"
)

# Si esto deja de cuadrar es que se ha anadido o quitado una pagina, y hay que
# mirar si la nueva entra aqui. Un descubrimiento por glob que se queda a cero
# pasaria en verde sin comprobar nada.
ESPERADAS = 12

HOY = date(2026, 8, 20)


@pytest.fixture
def almacen(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}
        ui: dict = {"data_freshness_warn_hours": 30}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    # Las paginas cachean con `st.cache_data`. Sin limpiar, la segunda pagina de
    # la misma sesion leeria lo que dejo la primera y estaria mirando un almacen
    # que ya no existe.
    st.cache_data.clear()
    st.cache_resource.clear()
    yield Stub
    st.cache_data.clear()
    st.cache_resource.clear()


def _pintar(ruta: pathlib.Path):
    prueba = AppTest.from_file(str(ruta), default_timeout=120)
    prueba.run()
    return prueba


def test_estan_las_doce_paginas():
    assert len(PAGINAS) == ESPERADAS, [p.name for p in PAGINAS]


@pytest.mark.parametrize("pagina", PAGINAS, ids=lambda p: p.stem)
def test_una_instalacion_recien_hecha_no_rompe_ninguna_pagina(pagina, almacen):
    """El almacen vacio: el estado que ve todo el mundo el primer dia."""
    prueba = _pintar(pagina)

    assert not prueba.exception, (
        f"{pagina.name} revienta sobre un almacen vacio: "
        f"{prueba.exception[0].message}"
    )


def _sembrar() -> None:
    """Lo minimo para que las paginas entren por la rama de "hay datos"."""
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "AAA", "name": "Alfa", "asset_class": "equity",
             "exchange": "NASDAQ", "currency": "USD", "country": "US",
             "gics_sector": "Tecnologia", "is_active": True},
        ]), keys=["ticker"])
        db.upsert_df(conn, "prices_daily", pd.DataFrame([
            {"ticker": "AAA", "date": HOY - timedelta(days=d),
             "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
             "adj_close": 100.0, "volume": 5_000_000, "source": "yfinance"}
            for d in range(260)
        ]), keys=["ticker", "date"])
        conn.execute(
            "INSERT INTO ingest_log VALUES (?, ?, ?, 'prices', 'all', 'OK', "
            "260, 1, '')",
            ["r1", pd.Timestamp(HOY), pd.Timestamp(HOY)],
        )


@pytest.mark.parametrize("pagina", PAGINAS, ids=lambda p: p.stem)
def test_con_datos_tampoco(pagina, almacen):
    """La otra mitad. Con el almacen vacio muchas consultas se cortan antes de
    llegar a la parte que formatea; con una serie dentro, se recorren enteras."""
    _sembrar()
    prueba = _pintar(pagina)

    assert not prueba.exception, (
        f"{pagina.name} revienta con datos dentro: "
        f"{prueba.exception[0].message}"
    )


def test_la_prueba_veria_una_pagina_rota(tmp_path, almacen):
    """Contraprueba. Once paginas en verde no significan nada si el metodo no
    distingue una pagina rota de una que funciona."""
    rota = tmp_path / "rota.py"
    rota.write_text(
        "import streamlit as st\n"
        "st.title('prueba')\n"
        "raise RuntimeError('esto tiene que verse')\n",
        "utf-8",
    )

    prueba = _pintar(rota)

    assert prueba.exception, "una pagina que lanza una excepcion pasa como buena"
    assert "esto tiene que verse" in prueba.exception[0].message
