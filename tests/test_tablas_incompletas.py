"""Columnas que salen vacias en pantalla sin que nada falle.

Averia real, vista en la primera instalacion: en "Que se mueve hoy", la
pestana de rupturas anuales mostraba la columna "Percentil" completamente en
blanco, en las dos tablas, siempre. No era un problema de datos: la consulta
`get_breakouts_52w` no traia `composite_pctile`, y `movers_table` lo pedia con
`df.get(...)`, que devuelve None sin quejarse. Resultado: una columna fantasma
que el usuario lee como "faltan datos".

Lo mismo en volumen inusual.

Y dos cosas mas que se leen igual de mal: la celda en blanco cuando el nombre
o el sector no se han descargado todavia (el presupuesto de peticiones corta a
los 400 por pasada), y las filas vacias de relleno debajo de una tabla corta
con alto fijo.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest
import streamlit as st

from stocks_tracker.app.components import common
from stocks_tracker.core import db
from stocks_tracker.core.scoring import preset_hash

AYER, HOY = date(2026, 8, 18), date(2026, 8, 19)


@pytest.fixture(autouse=True)
def sin_cache(monkeypatch):
    from stocks_tracker.app import data_access as da

    for nombre in ("get_breakouts_52w", "get_volume_spikes", "get_movers",
                   "available_presets"):
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


def sembrar_ruptura() -> None:
    """Dos valores que hoy rompen maximos. Solo uno tiene score de factores.

    Uno de ellos, ademas, sin nombre ni sector: es el caso del ticker recien
    incorporado al universo cuyo detalle todavia no se ha descargado.
    """
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO instruments (ticker, asset_class, name, gics_sector, "
            "is_active) VALUES ('AAA', 'equity', 'Alfa', 'Tecnologia', TRUE)"
        )
        conn.execute(
            "INSERT INTO instruments (ticker, asset_class, name, gics_sector, "
            "is_active) VALUES ('BBB', 'equity', NULL, NULL, TRUE)"
        )
        filas = []
        for ticker in ("AAA", "BBB"):
            # Ayer lejos del maximo, hoy pegado: eso es una ruptura.
            filas.append((ticker, AYER, -0.05, 0.05))
            filas.append((ticker, HOY, 0.0, 0.30))
        frame = pd.DataFrame(
            filas, columns=["ticker", "date", "dist_52w_high", "dist_52w_low"]
        )
        frame["close"] = 100.0
        frame["ret_1d"] = 0.03
        frame["rel_volume_20"] = 3.0
        frame["rsi14"] = 60.0
        db.upsert_df(conn, "indicators_daily", frame, keys=["ticker", "date"])

        conn.execute(
            "INSERT INTO prices_daily (ticker, date, close, adj_close, volume) "
            "VALUES ('AAA', ?, 100.0, 100.0, 5000000)", [HOY],
        )
        conn.execute(
            "INSERT INTO factor_scores (ticker, date, weights_hash, composite, "
            "composite_pctile) VALUES ('AAA', ?, ?, 1.2, 0.87)",
            [HOY, preset_hash("balanced")],
        )


# ---------------------------------------------------------------------------
# La consulta trae el percentil
# ---------------------------------------------------------------------------

def test_las_rupturas_traen_el_percentil(warehouse):
    """Sin esto la columna "Percentil" de la pestana de rupturas esta vacia
    para todo el mundo, tenga scores o no."""
    sembrar_ruptura()
    from stocks_tracker.app import data_access as da

    tabla = da.get_breakouts_52w("TODOS", high=True).set_index("ticker")

    assert "composite_pctile" in tabla.columns, (
        "la consulta de rupturas no trae el percentil: la columna de la tabla "
        "se dibujara vacia siempre"
    )
    assert tabla.loc["AAA", "composite_pctile"] == pytest.approx(0.87)
    assert pd.isna(tabla.loc["BBB", "composite_pctile"]), (
        "el LEFT JOIN se ha vuelto un JOIN y pierde los valores sin score"
    )


def test_el_volumen_inusual_trae_el_percentil(warehouse):
    sembrar_ruptura()
    from stocks_tracker.app import data_access as da

    tabla = da.get_volume_spikes("TODOS", threshold=2.0).set_index("ticker")

    assert "composite_pctile" in tabla.columns
    assert tabla.loc["AAA", "composite_pctile"] == pytest.approx(0.87)
    assert len(tabla) == 2, (
        "el JOIN con factor_scores ha filtrado filas o las ha duplicado"
    )


def test_el_percentil_no_se_duplica_por_perfil(warehouse):
    """Guardarrail. `factor_scores` guarda un score por perfil de pesos. Sin el
    filtro por `weights_hash`, cada valor sale una vez por perfil calculado y
    la tabla enseña el mismo ticker repetido."""
    sembrar_ruptura()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO factor_scores (ticker, date, weights_hash, composite, "
            "composite_pctile) VALUES ('AAA', ?, ?, 0.4, 0.11)",
            [HOY, preset_hash("value")],
        )
    from stocks_tracker.app import data_access as da

    tabla = da.get_breakouts_52w("TODOS", high=True)

    assert list(tabla["ticker"]).count("AAA") == 1, (
        "AAA sale una vez por perfil de pesos: falta el filtro weights_hash"
    )


# ---------------------------------------------------------------------------
# La tabla no dibuja columnas fantasma
# ---------------------------------------------------------------------------

class TablaFalsa:
    """Recoge lo que `movers_table` le manda a Streamlit."""

    # El formato de columnas se construye con el Streamlit de verdad: lo que se
    # esta probando es que columnas llegan, no como se pintan.
    column_config = st.column_config

    def __init__(self) -> None:
        self.frames: list[pd.DataFrame] = []
        self.alturas: list[int] = []
        self.captions: list[str] = []

    def dataframe(self, frame, *, hide_index=True, height=0, column_config=None):
        self.frames.append(frame)
        self.alturas.append(height)

    def caption(self, texto: str) -> None:
        self.captions.append(texto)


def pintar(monkeypatch, df: pd.DataFrame, height: int = 320) -> TablaFalsa:
    falsa = TablaFalsa()
    monkeypatch.setattr(common, "st", falsa)
    common.movers_table(df, height=height)
    return falsa


def datos(**extra) -> pd.DataFrame:
    base = {
        "ticker": ["AAA", "BBB"],
        "name": ["Alfa", None],
        "gics_sector": ["Tecnologia", None],
        "close": [100.0, 50.0],
        "ret_1d": [0.03, -0.01],
        "rel_volume_20": [3.0, 1.1],
    }
    base.update(extra)
    return pd.DataFrame(base)


def test_sin_ningun_percentil_la_columna_desaparece(monkeypatch):
    """Una barra de progreso vacia en todas las filas no dice nada; una
    columna ausente con su explicacion, si."""
    falsa = pintar(monkeypatch, datos())

    assert "Percentil" not in falsa.frames[0].columns
    assert falsa.captions, "se quita la columna y no se explica por que"
    assert "percentil" in falsa.captions[0].lower()


def test_con_percentiles_la_columna_se_queda(monkeypatch):
    falsa = pintar(monkeypatch, datos(composite_pctile=[0.87, 0.42]))

    assert list(falsa.frames[0]["Percentil"]) == pytest.approx([87.0, 42.0]), (
        "el percentil se pasa en fraccion: la barra marcaria 0,87% en vez de 87%"
    )
    assert not falsa.captions, "sobra el aviso: los percentiles estan"


def test_un_percentil_suelto_basta_para_mantener_la_columna(monkeypatch):
    falsa = pintar(monkeypatch, datos(composite_pctile=[0.87, None]))

    assert "Percentil" in falsa.frames[0].columns
    assert not falsa.captions


def test_el_nombre_y_el_sector_que_faltan_se_ven(monkeypatch):
    """La celda en blanco parece un fallo de la tabla. La raya dice que ese
    dato aun no se ha descargado."""
    vista = pintar(monkeypatch, datos()).frames[0]

    assert list(vista["Nombre"]) == ["Alfa", "—"]
    assert list(vista["Sector"]) == ["Tecnologia", "—"]


def test_el_nombre_vacio_cuenta_como_ausente(monkeypatch):
    """yfinance devuelve cadena vacia tan a menudo como NULL."""
    vista = pintar(monkeypatch, datos(name=["Alfa", ""], gics_sector=["", ""])).frames[0]

    assert list(vista["Nombre"]) == ["Alfa", "—"]
    assert list(vista["Sector"]) == ["—", "—"]


# ---------------------------------------------------------------------------
# El alto de la tabla
# ---------------------------------------------------------------------------

def test_una_tabla_corta_no_se_dibuja_con_huecos(monkeypatch):
    """Con alto fijo de 320, dos filas dejan ocho huecos vacios debajo."""
    alto = pintar(monkeypatch, datos(), height=320).alturas[0]

    assert alto < 320, "la tabla de dos filas sigue reservando el alto entero"
    assert alto == common.ALTO_DE_CABECERA + 2 * common.ALTO_DE_FILA


def test_una_tabla_larga_no_pasa_del_maximo():
    """Y al reves: veinte filas no pueden estirar la pagina sin fin."""
    assert common.alto_ajustado(20, 320) == 320


def test_una_sola_fila_deja_sitio_para_dibujarse():
    assert common.alto_ajustado(1, 320) == common.ALTO_DE_CABECERA + common.ALTO_DE_FILA
    assert common.alto_ajustado(0, 320) == common.alto_ajustado(1, 320)
