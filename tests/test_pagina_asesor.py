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
    cayendo del 24 % al 8 % y la deuda pasando de 0,9x a 5x.

    Se ejecuta el paso REAL que calcula y guarda, no un doble: es la unica
    forma de comprobar que la cadena entera —almacen, motor, escritura,
    pantalla— esta conectada.
    """
    from stocks_tracker.compute.run_advice import calcular_y_guardar

    _sembrar_cartera()
    calcular_y_guardar()
    st.cache_data.clear()

    prueba = _pintar()
    texto = " ".join(str(m.value) for m in prueba.markdown)

    assert "AAA" in texto
    assert ("Vender" in texto or "Reducir" in texto), (
        "una posicion con la tesis rota no esta recibiendo veredicto"
    )


def test_lo_que_se_ensena_es_lo_que_quedo_guardado(almacen):
    """LA PROPIEDAD QUE HACE QUE EL MARCADOR SIGNIFIQUE ALGO.

    La pantalla lee lo escrito; no recalcula. Si recalculara al vuelo y el
    marcador puntuara lo guardado, bastaria un cambio de precio entre una cosa
    y otra para que el marcador estuviera puntuando consejos que nadie vio.
    """
    from stocks_tracker.compute.run_advice import calcular_y_guardar

    _sembrar_cartera()
    calcular_y_guardar()
    st.cache_data.clear()

    with db.connect(read_only=True) as conn:
        guardados = {f[0] for f in conn.execute(
            "SELECT ticker FROM recommendations").fetchall()}

    prueba = _pintar()
    texto = " ".join(str(m.value) for m in prueba.markdown)

    assert guardados, "el paso de calculo no ha guardado nada"
    for ticker in guardados:
        assert ticker in texto, (
            f"{ticker} esta guardado pero la pantalla no lo ensena"
        )


def test_sin_calcular_la_pagina_dice_que_hacer(almacen):
    """El estado del primer dia. Una pagina vacia sin explicacion parece rota,
    y la explicacion tiene que decir el comando exacto."""
    prueba = _pintar()

    texto = " ".join(str(e.value) for e in prueba.info)
    assert "run_advice" in texto or "consejo" in texto


def test_el_efectivo_se_pide_al_calcular_y_no_se_inventa():
    """El programa no habla con el banco ni con el broker, y el extracto solo
    trae posiciones. Suponer que hay caja produciria compras que no se pueden
    ejecutar, que es la forma mas rapida de que una pantalla deje de usarse.

    El dato entra por el comando (`--caja`) y no por la pantalla: la pantalla
    solo lee lo ya calculado, asi que un efectivo tecleado alli no cambiaria
    nada de lo guardado.
    """
    import inspect

    from stocks_tracker.compute import run_advice

    firma = inspect.signature(run_advice.calcular_y_guardar)

    assert "caja" in firma.parameters
    assert firma.parameters["caja"].default == 0.0, (
        "el efectivo por defecto no puede ser mayor que cero: seria inventarlo"
    )
    assert "--caja" in inspect.getsource(run_advice.main)


def test_sin_efectivo_declarado_no_se_recomienda_comprar_nada(almacen):
    """El caso por defecto. Con caja a cero, `size_by_atr` no puede dimensionar
    y las compras salen vetadas: es lo correcto, y lo contrario seria
    recomendar comprar con dinero que no existe."""
    from stocks_tracker.compute.run_advice import calcular_y_guardar

    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "BBB", "name": "Beta", "asset_class": "equity",
             "currency": "EUR", "gics_sector": "Banca", "is_active": True},
        ]), keys=["ticker"])
        db.upsert_df(conn, "indicators_daily", pd.DataFrame([
            {"ticker": "BBB", "date": HOY, "close": 50.0, "atr_pct": 2.0},
        ]), keys=["ticker", "date"])
        from stocks_tracker.core.scoring import preset_hash
        db.upsert_df(conn, "factor_scores", pd.DataFrame([
            {"ticker": "BBB", "date": HOY, "weights_hash": preset_hash("balanced"),
             "composite": 2.0, "composite_pctile": 0.98, "coverage": 0.9},
        ]), keys=["ticker", "date", "weights_hash"])

    calcular_y_guardar()

    with db.connect(read_only=True) as conn:
        veredictos = [f[0] for f in conn.execute(
            "SELECT veredicto FROM recommendations WHERE ticker = 'BBB'"
        ).fetchall()]

    assert "comprar" not in veredictos, (
        "se esta recomendando comprar sin efectivo declarado"
    )


def test_con_efectivo_declarado_si_se_puede_comprar(almacen):
    """Contraprueba: el veto tiene que ser por falta de caja y no porque el
    camino de compra este roto."""
    from stocks_tracker.compute.run_advice import calcular_y_guardar

    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "BBB", "name": "Beta", "asset_class": "equity",
             "currency": "EUR", "gics_sector": "Banca", "is_active": True},
        ]), keys=["ticker"])
        db.upsert_df(conn, "indicators_daily", pd.DataFrame([
            {"ticker": "BBB", "date": HOY, "close": 50.0, "atr_pct": 2.0},
        ]), keys=["ticker", "date"])
        from stocks_tracker.core.scoring import preset_hash
        db.upsert_df(conn, "factor_scores", pd.DataFrame([
            {"ticker": "BBB", "date": HOY, "weights_hash": preset_hash("balanced"),
             "composite": 2.0, "composite_pctile": 0.98, "coverage": 0.9},
        ]), keys=["ticker", "date", "weights_hash"])

    calcular_y_guardar(caja=10_000.0)

    with db.connect(read_only=True) as conn:
        fila = conn.execute(
            "SELECT veredicto, importe_eur, stop FROM recommendations "
            "WHERE ticker = 'BBB'").fetchone()

    assert fila and fila[0] == "comprar"
    assert fila[1] > 0, "una compra sin importe no es accionable"
    assert fila[2] == pytest.approx(50.0 - 2.5 * 1.0), "el stop no cuadra"


def test_la_calibracion_sale_en_pantalla_y_no_promete_de_mas(almacen):
    """La calibracion mide hacia ATRAS y solo la mitad de precio; el marcador
    mide hacia DELANTE y el asesor entero. Van en bloques separados porque no
    son la misma clase de prueba, y juntarlas las haria parecerlo.

    Sin ranking historico —el caso del primer dia— la pantalla tiene que decir
    como generarlo, no quedarse en blanco.
    """
    prueba = _pintar()

    texto = " ".join(str(m.value) for m in prueba.markdown)
    assert "--history" in texto or "historico" in texto


def test_la_calibracion_se_niega_con_un_perfil_de_fundamentales(almacen):
    """LA NEGATIVA QUE MAS IMPORTA, comprobada desde el comando real.

    `balanced` es el perfil por defecto y lleva valor, crecimiento, calidad y
    dividendo. De esos no hay serie punto-en-el-tiempo, asi que un t-stat sobre
    ellos mide supervivencia, no la estrategia.
    """
    from stocks_tracker.compute.run_advice import calibrar

    resultado = calibrar(preset="balanced")

    assert not resultado.solo_precio
    assert not resultado.concluyente


def test_se_puede_preguntar_por_que_sin_abrir_la_base_de_datos(almacen, capsys):
    """POR QUE EXISTE ESTE COMANDO.

    Cuando el usuario reporto que MSFT y NVDA le salian como REDUCIR, la unica
    forma de diagnosticarlo era teclear SQL con comillas anidadas dentro de
    `cmd`. No funciono, y se perdio un intercambio entero peleando con el
    escapado en vez de con el problema.

    Una pantalla que da consejos tiene que poder explicar cualquiera de ellos.
    """
    from stocks_tracker.compute.run_advice import por_que

    _sembrar_cartera()

    assert por_que("aaa") == 0, "no encuentra la posicion (¿distingue mayusculas?)"

    salida = capsys.readouterr().out
    assert "profit_margin" in salida, "no ensena los numeros crudos"
    assert "8" in salida and "24" in salida, "no ensena el antes y el despues"
    assert "margen" in salida.lower(), "no ensena el motivo"


def test_preguntar_por_algo_que_no_tienes_lo_dice(almacen, capsys):
    from stocks_tracker.compute.run_advice import por_que

    assert por_que("ZZZZ") == 1
    assert "no esta en tu cartera" in capsys.readouterr().out
