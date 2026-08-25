"""La pagina del asesor, pintada con una cartera que de verdad dispara consejos.

El smoke test de `test_paginas.py` comprueba que las doce paginas no revientan,
pero no siembra cartera: la mitad de esta pagina —la que decide sobre lo que ya
tienes— no llegaba a ejecutarse.

Y hay una comprobacion que solo se puede hacer aqui: que el marcador diga en voz
alta que esta vacio. Una pantalla de consejos que empieza sin marcador y no lo
explica parece rota o, peor, parece validada.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from stocks_tracker.core import db
from stocks_tracker.core.config import project_root

HOY = date(2026, 8, 20)
RUTA = project_root() / "src/stocks_tracker/app/pages/12_asesor.py"


@pytest.fixture
def almacen(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}
        ui: dict = {"data_freshness_warn_hours": 30}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    st.cache_data.clear()
    st.cache_resource.clear()
    yield Stub
    st.cache_data.clear()
    st.cache_resource.clear()


def _sembrar_cartera() -> None:
    """Una posicion con el margen desplomado y la deuda disparada.

    Es el caso que tiene que producir un veredicto, no un silencio: si la
    pagina lo pinta como "mantener", el asesor esta callando justo cuando hay
    algo que decir.
    """
    from stocks_tracker.app import data_access as da

    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "AAA", "name": "Alfa", "asset_class": "equity",
             "currency": "EUR", "gics_sector": "Tecnologia", "is_active": True},
        ]), keys=["ticker"])
        for tabla, extra in (("prices_daily", {"open": 100.0, "high": 100.0,
                                               "low": 100.0, "adj_close": 100.0,
                                               "volume": 1_000,
                                               "source": "yfinance"}),
                             ("indicators_daily", {"atr_pct": 2.0})):
            db.upsert_df(conn, tabla, pd.DataFrame([
                {"ticker": "AAA", "date": HOY, "close": 100.0, **extra},
            ]), keys=["ticker", "date"])
        # La foto de fundamentales de hoy y la del dia de la compra.
        db.upsert_df(conn, "fundamentals_snapshot", pd.DataFrame([
            {"ticker": "AAA", "as_of": HOY, "profit_margin": 8.0,
             "net_debt_to_ebitda": 5.0},
            {"ticker": "AAA", "as_of": HOY - timedelta(days=200),
             "profit_margin": 24.0, "net_debt_to_ebitda": 0.9},
        ]), keys=["ticker", "as_of"])

    da.add_position("AAA", 10, 90.0, "EUR")
    with db.connect() as conn:
        conn.execute("UPDATE positions SET opened_at = ?",
                     [HOY - timedelta(days=180)])
    st.cache_data.clear()


def _pintar() -> AppTest:
    prueba = AppTest.from_file(str(RUTA), default_timeout=120)
    prueba.run()
    assert not prueba.exception, prueba.exception[0].message
    return prueba


def test_el_marcador_vacio_se_explica_en_vez_de_parecer_roto(almacen):
    """LO PRIMERO QUE VA A VER EL USUARIO, y va a durar meses.

    Sin esta frase, una pantalla sin marcador parece que no funciona; o peor,
    parece que el asesor esta validado y simplemente no ensena numeros.
    """
    prueba = _pintar()

    texto = " ".join(str(e.value) for e in prueba.info)
    assert "empieza vacio" in texto
    assert "hacia delante" in texto


def test_una_posicion_deteriorada_recibe_un_veredicto(almacen):
    """Si esto sale como MANTENER, el asesor esta callando con el margen
    cayendo del 24 % al 8 % y la deuda pasando de 0,9x a 5x."""
    _sembrar_cartera()

    prueba = _pintar()
    texto = " ".join(str(m.value) for m in prueba.markdown)

    assert "AAA" in texto
    assert ("Vender" in texto or "Reducir" in texto), (
        "una posicion con la tesis rota no esta recibiendo veredicto"
    )


def test_la_pagina_pide_el_efectivo_en_vez_de_inventarlo(almacen):
    """El programa no habla con el banco. Suponer que hay caja produciria
    compras que no se pueden ejecutar."""
    prueba = _pintar()

    etiquetas = [str(n.label) for n in prueba.number_input]
    assert any("Efectivo" in e for e in etiquetas)


def test_sin_efectivo_declarado_no_se_recomienda_comprar_nada(almacen):
    """El caso por defecto al abrir la pagina. Con caja a cero, `size_by_atr`
    no puede dimensionar y las compras salen vetadas: es lo correcto, y lo
    contrario seria recomendar comprar con dinero que no existe."""
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "BBB", "name": "Beta", "asset_class": "equity",
             "currency": "EUR", "gics_sector": "Banca", "is_active": True},
        ]), keys=["ticker"])
        db.upsert_df(conn, "indicators_daily", pd.DataFrame([
            {"ticker": "BBB", "date": HOY, "close": 50.0, "atr_pct": 2.0},
        ]), keys=["ticker", "date"])
        # El hash REAL del perfil: `get_candidates` filtra por el, y con uno
        # inventado la consulta devuelve vacio y el test pasaria sin probar nada.
        from stocks_tracker.core.scoring import preset_hash
        db.upsert_df(conn, "factor_scores", pd.DataFrame([
            {"ticker": "BBB", "date": HOY, "weights_hash": preset_hash("balanced"),
             "composite": 2.0, "composite_pctile": 0.98, "coverage": 0.9},
        ]), keys=["ticker", "date", "weights_hash"])
    st.cache_data.clear()

    prueba = _pintar()
    # Sobre las CABECERAS de las tarjetas, no sobre el texto de la pagina: la
    # palabra "Comprar" tambien sale en la explicacion del marcador, y buscarla
    # suelta hacia que este test pasara siempre.
    cabeceras = [str(m.value) for m in prueba.markdown
                 if "**BBB**" in str(m.value)]

    assert cabeceras, "el candidato no llega a la pagina"
    assert not any("— Comprar" in c for c in cabeceras), (
        "se esta recomendando comprar sin efectivo declarado"
    )
