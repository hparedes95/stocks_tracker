"""Cuando hay que volver a descargar, y por que la respuesta de antes estaba mal.

EL FALLO, TAL Y COMO LO VIO EL USUARIO

"Acabo de actualizar el programa y no se estan cargando los datos nuevos. En
otro ordenador ayer me paso lo mismo." Ultima cotizacion del 18, ultima
actualizacion hace 21 h, CERO descargas fallidas.

Cero fallidas es la parte que lo delata: no es que la descarga fallara, es que
NO SE INTENTO.

EL MECANISMO

El lanzador, en cada arranque, pregunta `run_ingest --check-stale` y solo
descarga si la respuesta es "hacen falta datos". Esa respuesta la daba
`needs_update()`, y `needs_update()` medía **cuanto hacia de la ultima
descarga**, con un limite de 30 h.

Con eso, una descarga a las cinco de la tarde del miercoles —cuando Wall Street
aun no ha cerrado, asi que trae hasta el martes— deja al programa creyendose al
dia hasta las once de la noche del jueves. Se abra las veces que se abra.

Y en un ordenador que se apaga por la noche, la tarea programada de las 23:15 no
llega a correr nunca, asi que esa descarga de las cinco de la tarde es la unica
que hay. El dashboard se queda congelado.

Lo peor es que era exactamente lo contrario de lo que su propio docstring decia
que hacia: *"en un ordenador personal, la tarea programada de la noche se pierde
cada vez que el equipo esta apagado, y si nadie compensa eso el dashboard acaba
mostrando la semana pasada como si fuera hoy."*

POR QUE NO LO COGIO NINGUN TEST

Porque no habia ninguno. `test_windows_scripts` comprobaba que el TEXTO de la
funcion no contuviera `migrate()`, y que el `.ps1` mencionara `--check-stale`.
Las dos cosas eran ciertas y ninguna miraba lo que la funcion responde.

LA REGLA NUEVA

Se compara el ultimo precio del almacen con la ultima SESION DE MERCADO que ya
ha cerrado. Si falta una sesion Y no se ha intentado descargar desde que esa
sesion cerro, hacen falta datos.

Las dos mitades hacen falta. Sin la primera, se descarga por reloj y no por
mercado, que es el fallo de arriba. Sin la segunda, un dia festivo —cuando no
hay sesion y por tanto no hay precio nuevo que traer— dispararia una descarga en
cada arranque, para siempre.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from stocks_tracker.core import db
from stocks_tracker.ingest import run_ingest

# LAS DOS HORAS DE ESTE FICHERO NO ESTAN EN LA MISMA ZONA, y confundirlas es
# facil, asi que se dice aqui:
#
#   - `ahora` va en HORA LOCAL (Madrid). Es la que decide si una sesion de
#     mercado ha cerrado, y las sesiones son un hecho local.
#   - `ultima_descarga` va en UTC, porque es lo que hay escrito en `ingest_log`:
#     DuckDB guarda TIMESTAMP sin zona y todo lo persistido es UTC naive.
#
# En agosto Madrid va dos horas por delante de UTC, asi que las 23:30 de Madrid
# son las 21:30 UTC. Escribir las dos igual haria pasar los tests con la
# conversion mal puesta, que es justo lo que no puede pasar aqui.

# 2026: el 18 es martes, el 19 miercoles, el 20 jueves, el 21 viernes.
MARTES = date(2026, 8, 18)
MIERCOLES = date(2026, 8, 19)
JUEVES = date(2026, 8, 20)


@pytest.fixture
def almacen(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        ui: dict = {"data_freshness_warn_hours": 30}
        ingest: dict = {}
        compute: dict = {}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    monkeypatch.setattr(run_ingest, "get_settings", lambda: Stub())
    db.migrate()
    return Stub


def sembrar(ultimo_precio: date, ultima_descarga: datetime) -> None:
    with db.connect() as conn:
        db.upsert_df(conn, "prices_daily", pd.DataFrame([
            {"ticker": "AAA", "date": ultimo_precio, "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0, "adj_close": 100.0, "volume": 1_000,
             "source": "yfinance"},
        ]), keys=["ticker", "date"])
        conn.execute(
            "INSERT INTO ingest_log VALUES ('r1', ?, ?, 'prices', 'all', 'OK', 1, 1, '')",
            [pd.Timestamp(ultima_descarga), pd.Timestamp(ultima_descarga)],
        )


# ---------------------------------------------------------------------------
# El fallo que reporto el usuario
# ---------------------------------------------------------------------------
def test_una_descarga_reciente_pero_anterior_al_cierre_no_deja_al_dia(almacen):
    """EL CASO EXACTO DE LA CAPTURA: ultimo precio del 18, descarga hace 21 h.

    La descarga corrio el miercoles a las cinco de la tarde, antes de que
    cerrara Nueva York, asi que solo pudo traer hasta el martes. El miercoles
    cerro despues y ese cierre no lo tiene nadie.

    Con la regla vieja —"hace 21 h, menos de 30, estas al dia"— el programa se
    negaba a descargar el jueves entero.
    """
    sembrar(ultimo_precio=MARTES,
            ultima_descarga=datetime(2026, 8, 19, 15, 0))   # UTC: 17:00 en Madrid

    hace_falta, motivo = run_ingest.needs_update(
        ahora=datetime(2026, 8, 20, 20, 0))                 # local: jueves por la tarde

    assert hace_falta, (
        f"el programa se cree al dia con el cierre del miercoles sin descargar: {motivo}"
    )
    assert "19" in motivo or "miercoles" in motivo.lower(), motivo


def test_el_cierre_de_ayer_lo_tenemos_y_hoy_aun_no_ha_cerrado(almacen):
    """La contraprueba. A media tarde del jueves, con el cierre del miercoles ya
    guardado, no hay nada nuevo que traer: la sesion del jueves todavia no ha
    terminado."""
    sembrar(ultimo_precio=MIERCOLES,
            ultima_descarga=datetime(2026, 8, 19, 21, 30))

    hace_falta, motivo = run_ingest.needs_update(ahora=datetime(2026, 8, 20, 20, 0))

    assert not hace_falta, motivo


def test_pasado_el_cierre_de_hoy_ya_hacen_falta_los_datos_de_hoy(almacen):
    """Y a las once y media de la noche del jueves, si."""
    sembrar(ultimo_precio=MIERCOLES,
            ultima_descarga=datetime(2026, 8, 19, 21, 30))   # UTC: 23:30 en Madrid

    hace_falta, _ = run_ingest.needs_update(ahora=datetime(2026, 8, 20, 23, 30))

    assert hace_falta


def test_el_fin_de_semana_no_pide_datos_que_no_existen(almacen):
    """El sabado no hay sesion del sabado. La ultima cerrada es la del viernes."""
    sembrar(ultimo_precio=date(2026, 8, 21),                # viernes
            ultima_descarga=datetime(2026, 8, 21, 21, 30))

    hace_falta, motivo = run_ingest.needs_update(
        ahora=datetime(2026, 8, 22, 12, 0))                 # local: sabado

    assert not hace_falta, motivo


def test_un_festivo_no_dispara_una_descarga_en_cada_arranque(almacen):
    """LA OTRA MITAD DE LA REGLA, y sin ella el arreglo seria peor que el fallo.

    Si el miercoles fue festivo, no hay cierre del miercoles y no lo va a haber
    nunca. Mirando solo "me falta la sesion del miercoles", el programa
    descargaria en cada arranque, para siempre, contra un proveedor gratuito que
    bloquea por abuso.

    Lo que lo evita es la segunda condicion: ya se intento DESPUES de que esa
    sesion cerrara, y no habia nada. No se vuelve a intentar por lo mismo.
    """
    sembrar(ultimo_precio=MARTES,
            ultima_descarga=datetime(2026, 8, 19, 21, 30))  # 23:30 en Madrid

    hace_falta, motivo = run_ingest.needs_update(ahora=datetime(2026, 8, 20, 20, 0))

    assert not hace_falta, motivo


# ---------------------------------------------------------------------------
# Lo que ya funcionaba y tiene que seguir funcionando
# ---------------------------------------------------------------------------
def test_sin_almacen_hacen_falta_datos(almacen, monkeypatch):
    almacen.warehouse_path.unlink()

    hace_falta, _ = run_ingest.needs_update(ahora=datetime(2026, 8, 20, 20, 0))

    assert hace_falta


def test_los_datos_de_prueba_siempre_piden_descarga(almacen):
    """Un precio inventado no describe el mercado, tenga la fecha que tenga."""
    sembrar(ultimo_precio=JUEVES, ultima_descarga=datetime(2026, 8, 20, 21, 30))
    with db.connect() as conn:
        conn.execute("UPDATE prices_daily SET source = 'synthetic'")

    hace_falta, motivo = run_ingest.needs_update(ahora=datetime(2026, 8, 20, 23, 30))

    assert hace_falta
    assert "prueba" in motivo


def test_una_semana_sin_descargar_pide_datos_pase_lo_que_pase(almacen):
    """El techo de las 30 h se conserva como red de seguridad: si algo raro
    hiciera que la comparacion por sesiones dijera que todo va bien, una semana
    sin bajar nada tiene que disparar una descarga igualmente."""
    sembrar(ultimo_precio=JUEVES,
            ultima_descarga=datetime(2026, 8, 13, 21, 30))

    hace_falta, motivo = run_ingest.needs_update(ahora=datetime(2026, 8, 20, 20, 0))

    assert hace_falta
    assert "h" in motivo


def test_un_almacen_vacio_pide_datos(almacen):
    hace_falta, motivo = run_ingest.needs_update(ahora=datetime(2026, 8, 20, 20, 0))

    assert hace_falta
    assert "vacio" in motivo or "descarga" in motivo


# ---------------------------------------------------------------------------
# La sesion de mercado, aislada
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ahora, esperada", [
    # Jueves a media tarde: la del jueves aun no ha cerrado.
    (datetime(2026, 8, 20, 20, 0), date(2026, 8, 19)),
    # Jueves de madrugada: idem.
    (datetime(2026, 8, 20, 2, 0), date(2026, 8, 19)),
    # Jueves a las 23:30, ya con el cierre americano consolidado.
    (datetime(2026, 8, 20, 23, 30), date(2026, 8, 20)),
    # Sabado y domingo: la del viernes.
    (datetime(2026, 8, 22, 12, 0), date(2026, 8, 21)),
    (datetime(2026, 8, 23, 12, 0), date(2026, 8, 21)),
    # Lunes por la manana: tambien la del viernes.
    (datetime(2026, 8, 24, 9, 0), date(2026, 8, 21)),
])
def test_cual_es_la_ultima_sesion_cerrada(ahora, esperada):
    assert run_ingest.ultima_sesion_cerrada(ahora) == esperada


# ---------------------------------------------------------------------------
# La otra mitad: que el dashboard lo DIGA
# ---------------------------------------------------------------------------

def test_el_dashboard_cuenta_las_sesiones_que_le_faltan(monkeypatch):
    """El mismo reloj roto estaba en la interfaz.

    "Última actualización hace 21 h" suena a recién hecho, y con `is_stale`
    calculado por horas el aviso callaba justo cuando hacía falta: cero
    descargas fallidas, un número reciente y dos sesiones sin traer.
    """
    from stocks_tracker.app import data_access as da

    monkeypatch.setattr(run_ingest, "ultima_sesion_cerrada",
                        lambda ahora=None: MIERCOLES)

    assert da.sesiones_pendientes(MARTES) == 1
    assert da.sesiones_pendientes(MIERCOLES) == 0
    # Y nunca negativo: el almacen puede ir por delante si el proveedor sirve
    # una vela antes de tiempo, y eso no son "menos uno" sesiones pendientes.
    assert da.sesiones_pendientes(JUEVES) == 0


def test_el_fin_de_semana_no_cuenta_como_sesiones_pendientes(monkeypatch):
    """Del viernes al lunes hay tres dias y una sola sesion. Contando dias
    naturales, el aviso saltaria todos los domingos."""
    from stocks_tracker.app import data_access as da

    monkeypatch.setattr(run_ingest, "ultima_sesion_cerrada",
                        lambda ahora=None: date(2026, 8, 24))     # lunes

    assert da.sesiones_pendientes(date(2026, 8, 21)) == 1         # viernes


def test_sin_datos_no_se_inventan_sesiones_pendientes(monkeypatch):
    from stocks_tracker.app import data_access as da

    assert da.sesiones_pendientes(None) == 0


def test_el_aviso_dice_cuantas_sesiones_faltan(tmp_path, almacen, monkeypatch):
    """Se PINTA la cabecera y se lee lo que sale.

    La primera version de este test comprobaba que el fichero contuviera la
    cadena "sesiones_pendientes". Mutando el `if` que la usa a `if False`, el
    test seguia en verde: la cadena estaba en el fichero y el aviso no salia.
    Otra vez lo mismo, comprobar el texto del codigo en vez de su efecto.
    """
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    sembrar(ultimo_precio=MARTES, ultima_descarga=datetime(2026, 8, 19, 15, 0))
    monkeypatch.setattr(run_ingest, "ultima_sesion_cerrada",
                        lambda ahora=None: MIERCOLES)
    st.cache_data.clear()

    pagina = tmp_path / "cabecera.py"
    pagina.write_text(
        "from stocks_tracker.app.components.common import render_freshness_badge\n"
        "render_freshness_badge()\n",
        "utf-8",
    )
    prueba = AppTest.from_file(str(pagina), default_timeout=60)
    prueba.run()

    assert not prueba.exception, prueba.exception[0].message
    avisos = " ".join(w.value for w in prueba.warning)
    assert "sesi" in avisos.lower(), (
        f"la cabecera no avisa de las sesiones que faltan. Avisos: {avisos!r}"
    )
    assert "1" in avisos
