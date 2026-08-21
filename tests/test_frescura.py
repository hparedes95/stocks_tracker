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
        # Con su ficha: "sesion completa" se mide sobre acciones y ETF, asi que
        # unos precios sin instrumento no cuentan como sesion ninguna.
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "AAA", "asset_class": "equity", "is_active": True},
        ]), keys=["ticker"])
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


def con_indices(hasta: date) -> None:
    """Los indices, que son el oraculo de festivos.

    Se descargan en cada ejecucion y tardan segundos. Si ^GSPC tiene barra de un
    dia, ese dia hubo sesion; si no la tiene, fue festivo. Es lo que permite
    contestar "¿hasta donde llego el mercado?" sin un calendario de festivos de
    cinco mercados en cuatro paises.
    """
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "^GSPC", "asset_class": "index", "is_active": True},
        ]), keys=["ticker"])
        db.upsert_df(conn, "prices_daily", pd.DataFrame([
            {"ticker": "^GSPC", "date": hasta, "open": 4000.0, "high": 4010.0,
             "low": 3990.0, "close": 4000.0, "adj_close": 4000.0,
             "volume": 1_000, "source": "yfinance"},
        ]), keys=["ticker", "date"])


def test_un_festivo_no_dispara_una_descarga_en_cada_arranque(almacen):
    """SIN ESTO EL ARREGLO SERIA PEOR QUE EL FALLO.

    Si el miercoles fue festivo, no hay cierre del miercoles y no lo va a haber
    nunca. Mirando solo el calendario de lunes a viernes, el programa
    descargaria en cada arranque, para siempre, contra un proveedor gratuito que
    bloquea por abuso.

    Lo que lo evita ya no es una guarda temporal —esa fue el fallo— sino los
    INDICES: si ^GSPC tampoco tiene el miercoles, el miercoles no hubo mercado.
    """
    sembrar(ultimo_precio=MARTES,
            ultima_descarga=datetime(2026, 8, 19, 21, 30))
    con_indices(hasta=MARTES)              # los indices tampoco tienen el 19

    hace_falta, motivo = run_ingest.needs_update(ahora=datetime(2026, 8, 20, 20, 0))

    assert not hace_falta, motivo


def test_los_indices_por_delante_de_las_acciones_piden_descarga(almacen):
    """EL FALLO QUE COSTO TRES DIAS, EN UNA LINEA.

    El instalador descarga QUINCE indices y tarda segundos. Eso ponia
    `ultima descarga = hace 0 h`, y la guarda temporal —"¿he intentado
    descargar desde que cerro esa sesion?"— daba por intentado el universo
    entero. Seiscientas acciones sin bajar detras de una descarga que si se hizo
    y si funciono.

    `last_run` no distingue QUE se descargo. Los indices si.
    """
    sembrar(ultimo_precio=MARTES,
            # Descarga de hace un minuto: la guarda vieja decia "al dia".
            ultima_descarga=datetime(2026, 8, 20, 17, 55))
    con_indices(hasta=JUEVES)              # el mercado llego al jueves

    hace_falta, motivo = run_ingest.needs_update(ahora=datetime(2026, 8, 20, 18, 0))

    assert hace_falta, (
        f"una descarga de quince indices tapa que faltan 600 acciones: {motivo}"
    )
    # 19/08 y no 20/08: a las 18:00 del jueves la sesion del jueves aun no ha
    # cerrado, asi que el tope sigue siendo el miercoles aunque el indice ya
    # traiga la barra provisional del jueves.
    assert "19/08" in motivo and "18/08" in motivo, motivo


def test_no_se_pide_la_sesion_de_hoy_antes_de_que_cierre(almacen):
    """Un indice puede traer la barra PROVISIONAL de hoy. Pedir la sesion de hoy
    a media tarde solo gasta peticiones para recibir la de ayer."""
    sembrar(ultimo_precio=MIERCOLES,
            ultima_descarga=datetime(2026, 8, 20, 10, 0))
    con_indices(hasta=JUEVES)              # provisional del propio jueves

    hace_falta, motivo = run_ingest.needs_update(
        ahora=datetime(2026, 8, 20, 18, 0))          # jueves por la tarde

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
    assert "sesion completa" in motivo or "descarga" in motivo


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
# DESCARGADO NO ES CALCULADO
#
# El segundo fallo del mismo caso, y el que de verdad tenia parado al usuario.
# Arreglado lo de "no se intenta descargar", seguia sin actualizarse: los
# precios SI estaban, y lo que no se hacia era el calculo.
#
# El lanzador preguntaba una sola cosa —"¿hace falta descargar?"— y si la
# respuesta era no, se iba sin calcular. Un unico "estas al dia" contestando a
# dos preguntas distintas. Las dos se separan solas en cuanto el calculo falla
# una noche: la descarga sigue estando reciente, asi que el lanzador se sigue
# yendo por la puerta de atras, y el dashboard ensena el martes para siempre.
# ---------------------------------------------------------------------------

def con_indicadores(hasta: date, tickers: int = 10) -> None:
    """Precios e indicadores de varios valores, para que `current_session` los
    considere una sesion valida (exige el 60 % del dia mas poblado)."""
    nombres = [f"T{i:02d}" for i in range(tickers)]
    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": t, "asset_class": "equity", "is_active": True}
            for t in nombres
        ]), keys=["ticker"])
        db.upsert_df(conn, "indicators_daily", pd.DataFrame([
            {"ticker": t, "date": hasta, "close": 100.0} for t in nombres
        ]), keys=["ticker", "date"])
        db.upsert_df(conn, "prices_daily", pd.DataFrame([
            {"ticker": t, "date": hasta, "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0, "adj_close": 100.0, "volume": 1_000,
             "source": "yfinance"}
            for t in nombres
        ]), keys=["ticker", "date"])


def con_precios_mas_nuevos(hasta: date, tickers: int = 10) -> None:
    nombres = [f"T{i:02d}" for i in range(tickers)]
    with db.connect() as conn:
        # Los instrumentos tambien: la cuenta de precios pendientes se limita a
        # acciones y ETF, asi que unos precios sin ficha no cuentan como nada.
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": t, "asset_class": "equity", "is_active": True}
            for t in nombres
        ]), keys=["ticker"])
        db.upsert_df(conn, "prices_daily", pd.DataFrame([
            {"ticker": t, "date": hasta, "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0, "adj_close": 100.0, "volume": 1_000,
             "source": "yfinance"}
            for t in nombres
        ]), keys=["ticker", "date"])


def test_precios_por_delante_de_los_indicadores_son_trabajo_pendiente(almacen):
    """EL CASO DEL USUARIO. Precios hasta el jueves, indicadores hasta el martes.

    Descargas fallidas: cero. Ultima descarga: reciente. Y el dashboard llevaba
    dos dias ensenando el martes.
    """
    from stocks_tracker.compute.run_compute import sesiones_sin_calcular

    con_indicadores(MARTES)
    con_precios_mas_nuevos(MIERCOLES)
    con_precios_mas_nuevos(JUEVES)

    pendientes, motivo = sesiones_sin_calcular()

    assert pendientes == 2, motivo
    assert "18" in motivo and "20" in motivo, motivo


def test_con_todo_calculado_no_hay_nada_pendiente(almacen):
    from stocks_tracker.compute.run_compute import sesiones_sin_calcular

    con_indicadores(JUEVES)

    pendientes, motivo = sesiones_sin_calcular()

    assert pendientes == 0, motivo


def test_el_fin_de_semana_no_cuenta_como_calculo_pendiente(almacen):
    """Del viernes al lunes hay tres dias naturales y UNA sesion.

    Escrito porque faltaba: mutando la cuenta para que sumara dias naturales en
    vez de dias de mercado, los tests de aqui seguian en verde —el caso que
    habia iba de martes a jueves, donde los dos numeros coinciden—. El aviso
    diria "3 sesiones sin calcular" cada lunes.
    """
    from stocks_tracker.compute.run_compute import sesiones_sin_calcular

    con_indicadores(date(2026, 8, 21))            # viernes
    con_precios_mas_nuevos(date(2026, 8, 24))     # lunes

    pendientes, motivo = sesiones_sin_calcular()

    assert pendientes == 1, motivo


def test_precios_sin_ningun_indicador_es_trabajo_pendiente(almacen):
    """Instalacion a medias: se descargo y no se llego a calcular. Cero
    indicadores no es "nada que hacer", es "todo por hacer"."""
    from stocks_tracker.compute.run_compute import sesiones_sin_calcular

    con_precios_mas_nuevos(JUEVES)

    pendientes, _ = sesiones_sin_calcular()

    assert pendientes >= 1


def test_un_almacen_sin_precios_no_tiene_calculo_pendiente(almacen):
    from stocks_tracker.compute.run_compute import sesiones_sin_calcular

    pendientes, motivo = sesiones_sin_calcular()

    assert pendientes == 0
    assert "no hay precios" in motivo


def test_el_lanzador_calcula_aunque_no_haya_que_descargar():
    """EL FALLO, EN EL SITIO DONDE ESTABA.

    `if ($LASTEXITCODE -eq 0) { return }` justo despues de preguntar por la
    descarga: con los precios al dia, el bloque se iba sin calcular. Se
    comprueba que la unica salida temprana que queda es la del calculo, no la
    de la descarga.
    """
    from stocks_tracker.core.config import project_root

    src = (project_root() / "scripts/windows/stocks.ps1").read_text("utf-8")
    bloque = src[src.index("'update' {"):]
    bloque = bloque[:bloque.index("'autostart' {")]

    assert "run_compute --check-stale" in bloque, (
        "el lanzador no pregunta si hay algo que calcular"
    )
    antes_del_calculo = bloque[:bloque.index("run_compute --check-stale")]
    assert "return }" not in antes_del_calculo.replace(
        "Write-Host \"Ya hay otra actualizacion en marcha; se abre con lo que hay.\" -ForegroundColor Yellow\n                return", ""
    ) or "$descargar" in antes_del_calculo, (
        "sigue habiendo una salida temprana que se lleva el calculo por delante"
    )


def test_el_lanzador_avisa_cuando_la_puerta_de_calidad_para_el_calculo():
    """Sin esto, "no se calcula por datos malos" se ve exactamente igual que
    "todo bien": el dashboard abre con datos viejos y ningun motivo."""
    from stocks_tracker.core.config import project_root

    src = (project_root() / "scripts/windows/stocks.ps1").read_text("utf-8")
    bloque = src[src.index("'update' {"):]
    bloque = bloque[:bloque.index("'autostart' {")]

    assert "77" in bloque, (
        "el codigo 77 —la puerta de calidad negandose— pasa desapercibido"
    )


# ---------------------------------------------------------------------------
# Que el dashboard distinga las dos averias
# ---------------------------------------------------------------------------

def test_el_dashboard_cuenta_las_sesiones_sin_descargar(monkeypatch, almacen):
    from stocks_tracker.app import data_access as da

    monkeypatch.setattr(run_ingest, "ultima_sesion_cerrada",
                        lambda ahora=None: JUEVES)
    con_indicadores(MARTES)          # precios e indicadores hasta el martes

    assert da.sesiones_sin_descargar() == 2       # miercoles y jueves


def test_lo_descargado_no_cuenta_como_sin_descargar(monkeypatch, almacen):
    """LA CONFUSION QUE HUBO QUE CORREGIR.

    La primera version contaba contra la sesion VIGENTE, que sale de los
    indicadores. Con el calculo parado, acusaba de "no descargado" lo que si
    estaba descargado y mandaba al usuario a repetir una descarga que ya se
    habia hecho.
    """
    from stocks_tracker.app import data_access as da

    monkeypatch.setattr(run_ingest, "ultima_sesion_cerrada",
                        lambda ahora=None: JUEVES)
    con_indicadores(MARTES)
    con_precios_mas_nuevos(MIERCOLES)
    con_precios_mas_nuevos(JUEVES)

    assert da.sesiones_sin_descargar() == 0, (
        "dice que faltan precios que estan descargados"
    )


def test_el_fin_de_semana_no_cuenta_como_sesiones_sin_descargar(monkeypatch, almacen):
    """Del viernes al lunes hay tres dias y una sola sesion. Contando dias
    naturales, el aviso saltaria todos los domingos."""
    from stocks_tracker.app import data_access as da

    monkeypatch.setattr(run_ingest, "ultima_sesion_cerrada",
                        lambda ahora=None: date(2026, 8, 24))     # lunes
    con_indicadores(date(2026, 8, 21))                            # viernes

    assert da.sesiones_sin_descargar() == 1


def test_sin_datos_no_se_inventan_sesiones(almacen):
    from stocks_tracker.app import data_access as da

    assert da.sesiones_sin_descargar() == 0


def _pintar_cabecera(tmp_path):
    import streamlit as st
    from streamlit.testing.v1 import AppTest

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
    return " ".join(w.value for w in prueba.warning)


def test_el_aviso_manda_a_calcular_y_no_a_descargar(tmp_path, almacen, monkeypatch):
    """Se PINTA la cabecera y se lee lo que sale.

    Con los precios descargados y el calculo parado, el aviso decia "faltan 2
    sesiones de mercado por descargar", que era falso y ademas mandaba a hacer
    lo que ya estaba hecho.
    """
    monkeypatch.setattr(run_ingest, "ultima_sesion_cerrada",
                        lambda ahora=None: JUEVES)
    con_indicadores(MARTES)
    con_precios_mas_nuevos(MIERCOLES)
    con_precios_mas_nuevos(JUEVES)
    sembrar(ultimo_precio=JUEVES, ultima_descarga=datetime(2026, 8, 20, 21, 30))

    avisos = _pintar_cabecera(tmp_path)

    assert "calcular" in avisos.lower(), (
        f"la cabecera no dice que lo que falta es calcular. Avisos: {avisos!r}"
    )
    assert "por descargar" not in avisos.lower(), (
        f"sigue mandando a descargar lo que ya esta descargado: {avisos!r}"
    )


def test_el_aviso_de_descarga_sigue_saliendo_cuando_toca(tmp_path, almacen, monkeypatch):
    """La contraprueba: si de verdad faltan precios, hay que decirlo."""
    monkeypatch.setattr(run_ingest, "ultima_sesion_cerrada",
                        lambda ahora=None: JUEVES)
    con_indicadores(MARTES)
    sembrar(ultimo_precio=MARTES, ultima_descarga=datetime(2026, 8, 20, 21, 30))

    avisos = _pintar_cabecera(tmp_path)

    assert "descargar" in avisos.lower(), avisos


def test_el_plural_de_sesion_no_lleva_acento():
    """En pantalla salia "2 sesiónes". El acento de "sesión" desaparece al pasar
    al plural, y pegarle la terminacion a la forma acentuada da una palabra que
    no existe."""
    from stocks_tracker.app.components.common import _sesiones

    assert _sesiones(1) == "1 sesión"
    assert _sesiones(2) == "2 sesiones"


# ---------------------------------------------------------------------------
# LA SESION A MEDIAS: la tercera averia, y la que de verdad tenia parado esto
#
# Sale del log de la maquina del usuario:
#
#   El almacen llega al 20/08/2026, pero la ultima sesion completa es el
#   18/08/2026 (601 valores). Se puntua esa.
#
# La descarga de precios reventaba a mitad (un DataFrame en `df.attrs`, ver
# test_attrs_no_tumban_la_descarga.py). Entraban unos pocos valores del 19 y del
# 20, se calculaban, y el dashboard no los ensenaba porque no llegan al 60 % de
# cobertura que exige `current_session`.
#
# Desde fuera eso se ve exactamente igual que "no se ha descargado" y que "no se
# ha calculado", y no lo reportaba nadie.
# ---------------------------------------------------------------------------

def con_sesion_a_medias(completa: date, medias: date, cuantos: int = 3) -> None:
    """Una sesion entera y otra con cuatro gatos, como la deja un crash."""
    with db.connect() as conn:
        nombres = [f"T{i:02d}" for i in range(20)]
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": t, "asset_class": "equity", "is_active": True}
            for t in nombres
        ]), keys=["ticker"])
        filas = [
            {"ticker": t, "date": completa, "close": 100.0} for t in nombres
        ] + [
            {"ticker": t, "date": medias, "close": 100.0}
            for t in nombres[:cuantos]
        ]
        db.upsert_df(conn, "indicators_daily", pd.DataFrame(filas),
                     keys=["ticker", "date"])
        db.upsert_df(conn, "prices_daily", pd.DataFrame([
            {"ticker": f["ticker"], "date": f["date"], "open": 100.0,
             "high": 101.0, "low": 99.0, "close": 100.0, "adj_close": 100.0,
             "volume": 1_000, "source": "yfinance"}
            for f in filas
        ]), keys=["ticker", "date"])


def test_una_sesion_a_medias_se_nombra(almacen):
    """Existe, esta calculada, y el dashboard no la ensena. Sin nombrarla, el
    usuario no tiene forma de saber por que no avanza."""
    from stocks_tracker.app import data_access as da

    con_sesion_a_medias(completa=MARTES, medias=MIERCOLES, cuantos=3)

    medias = da.sesiones_a_medias()

    assert len(medias) == 1
    fecha, valores, minimo = medias[0]
    assert fecha == MIERCOLES
    assert valores == 3
    assert minimo == pytest.approx(20 * 0.6)


def test_una_sesion_completa_no_se_nombra(almacen):
    from stocks_tracker.app import data_access as da

    con_sesion_a_medias(completa=MARTES, medias=MIERCOLES, cuantos=20)

    assert da.sesiones_a_medias() == []


def test_una_sesion_a_medias_no_cuenta_como_descargada(almacen, monkeypatch):
    """EL FALLO QUE DEJO AL USUARIO SIN DESCARGAR DURANTE DIAS.

    `MAX(date)` se satisface con UN ticker. Con tres indices dentro del 20/08 y
    ni una accion, el maximo decia "20/08", el lanzador respondia "al dia" y no
    se volvia a descargar nunca. Nada fallaba a la vista.
    """
    from stocks_tracker.app import data_access as da

    monkeypatch.setattr(run_ingest, "ultima_sesion_cerrada",
                        lambda ahora=None: MIERCOLES)
    con_sesion_a_medias(completa=MARTES, medias=MIERCOLES, cuantos=3)

    assert da.sesiones_sin_descargar() == 1, (
        "una sesion con 3 valores de 20 cuenta como descargada"
    )


def test_el_contador_de_sin_calcular_se_puede_satisfacer(almacen):
    """REGRESION MIA, Y DE LAS MALAS.

    La primera version comparaba los precios con la sesion VIGENTE, que exige el
    60 % de cobertura. Con una sesion a medias, el calculo se ejecutaba, hacia
    su trabajo, y el contador seguia diciendo "1 pendiente": el lanzador
    recalculaba el universo entero en cada arranque, para siempre, sin que nada
    cambiara nunca.

    Un "hay trabajo pendiente" que ninguna cantidad de trabajo puede satisfacer
    no es un aviso, es un bucle.
    """
    from stocks_tracker.compute.run_compute import sesiones_sin_calcular

    con_sesion_a_medias(completa=MARTES, medias=MIERCOLES, cuantos=3)

    pendientes, motivo = sesiones_sin_calcular()

    assert pendientes == 0, (
        f"con todo calculado sigue pidiendo calcular: {motivo}. El lanzador "
        "recalcularia el universo entero en cada arranque."
    )


def test_el_aviso_de_sesion_a_medias_sale_antes_que_los_otros(tmp_path, almacen,
                                                              monkeypatch):
    """Se PINTA y se lee. Es la averia que explica las otras dos, asi que es la
    que hay que nombrar; mandar a calcular lo que ya esta calculado fue lo que
    hizo perder dos dias."""
    monkeypatch.setattr(run_ingest, "ultima_sesion_cerrada",
                        lambda ahora=None: MIERCOLES)
    con_sesion_a_medias(completa=MARTES, medias=MIERCOLES, cuantos=3)
    sembrar(ultimo_precio=MIERCOLES, ultima_descarga=datetime(2026, 8, 19, 21, 30))

    avisos = _pintar_cabecera(tmp_path)

    assert "medias" in avisos.lower(), avisos
    assert "3 valores" in avisos, avisos


def test_una_sesion_a_medias_no_deja_al_dia_a_la_ingesta(almacen):
    """EL ESTADO EXACTO EN QUE QUEDO LA MAQUINA DEL USUARIO.

    La descarga reventaba a mitad. Entraban unos pocos valores del miercoles y
    ninguno mas. `MAX(date)` decia "miercoles", `needs_update` respondia "al
    dia", y el lanzador no volvia a descargar NUNCA: ni ese dia ni los
    siguientes. Cero descargas fallidas en pantalla, porque el proceso moria
    antes de escribir la fila de fallo.

    Escrito porque faltaba: mutando `ultima_completa` de vuelta a `MAX(date)`,
    los 34 tests de este fichero seguian en verde.
    """
    con_sesion_a_medias(completa=MARTES, medias=MIERCOLES, cuantos=3)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO ingest_log VALUES ('r1', ?, ?, 'prices', 'all', 'OK', 1, 1, '')",
            [pd.Timestamp(datetime(2026, 8, 19, 15, 0)),
             pd.Timestamp(datetime(2026, 8, 19, 15, 0))],
        )

    hace_falta, motivo = run_ingest.needs_update(
        ahora=datetime(2026, 8, 20, 20, 0))

    assert hace_falta, (
        f"tres valores de veinte cuentan como sesion descargada: {motivo}. "
        "Asi es como el programa dejo de descargar durante dias."
    )


def test_el_umbral_de_python_y_el_del_esquema_no_se_separan():
    """La misma regla escrita en dos sitios: la vista `current_ession` la lleva
    en SQL y `core/sesiones` en Python.

    Estan duplicadas a proposito —la vista tiene que poder consultarse sin pasar
    por Python— pero si se separan, el dashboard ensena una sesion y el lanzador
    decide sobre otra. Es el mismo fallo que ya hubo con el ranking puntuando un
    dia y las pantallas leyendo otro.
    """
    from stocks_tracker.core import sesiones as ses
    from stocks_tracker.core.config import project_root

    esquema = (project_root() / "src/stocks_tracker/core/schema.sql").read_text("utf-8")
    vista = esquema[esquema.index("CREATE OR REPLACE VIEW current_session"):]
    vista = vista[:vista.index(";")]

    assert f"* {ses.UMBRAL_COMPLETA}" in vista, (
        f"la vista no usa {ses.UMBRAL_COMPLETA}: {vista}"
    )
    assert f"LIMIT {ses.VENTANA}" in vista, (
        f"la vista mira una ventana distinta de {ses.VENTANA} sesiones"
    )


def test_la_vista_y_python_dan_la_misma_sesion(almacen):
    """Y la prueba de verdad de lo anterior: los dos caminos sobre los mismos
    datos tienen que devolver la misma fecha."""
    from stocks_tracker.core import sesiones as ses

    con_sesion_a_medias(completa=MARTES, medias=MIERCOLES, cuantos=3)

    with db.connect(read_only=True) as conn:
        de_la_vista = conn.execute("SELECT date FROM current_session").fetchone()[0]
        de_python = ses.ultima_completa(conn, "indicators_daily")

    assert pd.Timestamp(de_la_vista).date() == de_python
