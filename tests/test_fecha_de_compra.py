"""Cuando la fecha de compra es la de hoy, no hay diagnostico que valga.

EL CASO REAL DEL QUE SALE ESTE FICHERO

El usuario pidio explicar por que MSFT recibia su veredicto y el comando
`run_advice --por-que MSFT` devolvio, en su ordenador y con sus datos:

    drawdown         -0.09533  /  -0.09533
    rs_vs_bench_3m     0.1402  /    0.1402
    realized_vol_20    0.5538  /    0.5538
    Diagnostico: verde (0 puntos)
    Nada ha empeorado desde que la compraste.

Los once numeros identicos en las dos columnas. La union punto-en-el-tiempo
estaba bien: `opened_at` era la fecha en la que importo su cartera, no la de la
compra, y `date <= opened_at` con `opened_at` = hoy devuelve la fila de hoy.

Comparar una fila consigo misma no da cero senales porque no haya deterioro:
las da porque no hay nada que comparar. Y salia en VERDE, con el texto "nada ha
empeorado desde que la compraste", que es exactamente el verde tranquilizador
por falta de datos contra el que avisa la cabecera de `deterioration.py`.

Los tests van del almacen hacia arriba —SQL, diagnostico, veredicto— porque el
fallo no estaba en ninguna de las tres capas por separado: estaba en que
ninguna comprobaba que las dos fotos fueran distintas.
"""

from __future__ import annotations

import datetime as dt
import pathlib

import duckdb
import pytest

from stocks_tracker.core import advice
from stocks_tracker.core import deterioration as det

HOY = dt.date(2026, 8, 24)
_ESQUEMA = (pathlib.Path(__file__).resolve().parents[1]
            / "src/stocks_tracker/core/schema.sql")


def _almacen(opened_at: dt.date, destino=None) -> duckdb.DuckDBPyConnection:
    """Un almacen con dos anos de indicadores y UNA posicion abierta.

    El pasado esta sano y el presente esta roto: quien compre hace 400 sesiones
    tiene deterioro que ver, y quien "compre" hoy no tiene nada con lo que
    comparar. La diferencia entre los dos casos es lo unico que se prueba.
    """
    conn = duckdb.connect(str(destino) if destino else ":memory:")
    conn.execute(_ESQUEMA.read_text())
    conn.execute("INSERT INTO instruments (ticker, name, asset_class) "
                 "VALUES ('AAA', 'Ejemplo', 'equity')")
    for i in range(500):
        dia = HOY - dt.timedelta(days=500 - i)
        roto = i > 480
        conn.execute(
            "INSERT INTO indicators_daily (ticker, date, above_sma200, "
            "death_cross, drawdown, rs_vs_bench_3m, realized_vol_20, "
            "realized_vol_252) VALUES ('AAA', ?, ?, ?, ?, ?, ?, 0.30)",
            [dia, 0 if roto else 1, 1 if roto else 0,
             -0.45 if roto else -0.02, -0.30 if roto else 0.15,
             0.60 if roto else 0.20])
    conn.execute(
        "INSERT INTO indicators_daily (ticker, date, above_sma200, death_cross,"
        " drawdown, rs_vs_bench_3m, realized_vol_20, realized_vol_252)"
        " VALUES ('AAA', ?, 0, 1, -0.45, -0.30, 0.60, 0.30)", [HOY])
    conn.execute(
        "INSERT INTO positions (id, ticker, qty, avg_cost, currency, opened_at)"
        " VALUES ('p1', 'AAA', 10, 300, 'USD', ?)", [opened_at])
    return conn


def _diagnostico(opened_at: dt.date) -> det.Diagnostico:
    from stocks_tracker.compute.run_advice import _salud

    conn = _almacen(opened_at)
    try:
        fila = _salud(conn).iloc[0]
    finally:
        conn.close()
    hoy, entonces = det.partir(fila)
    return det.diagnosticar("AAA", fund_hoy=hoy, fund_entonces=entonces,
                            ind_hoy=hoy, ind_entonces=entonces,
                            comparado_con=hoy.get("opened_at"))


# ---------------------------------------------------------------------------
# El caso real, de punta a punta
# ---------------------------------------------------------------------------
def test_comprada_hoy_no_sale_en_verde():
    """EL FALLO EXACTO QUE REPORTO EL USO REAL.

    Con `opened_at` = hoy las dos fotos son la misma fila. Verde ahi significa
    "no he encontrado nada" cuando lo cierto es "no he podido buscar", y es el
    unico color que invita a no volver a mirar.
    """
    d = _diagnostico(HOY)

    assert d.nivel is not det.Nivel.VERDE, (
        "Comparando hoy contra hoy no puede salir verde: no se ha comparado "
        "nada. Es el verde tranquilizador por falta de datos."
    )
    assert d.nivel is det.Nivel.GRIS
    assert d.espejo is True
    assert d.comparado is False


def test_comprada_hace_dos_anos_si_se_diagnostica():
    """El contrapeso. Si el arreglo enmudeciera tambien el caso bueno, habria
    cambiado un fallo por otro peor: uno que calla cuando SI hay deterioro."""
    d = _diagnostico(HOY - dt.timedelta(days=400))

    assert d.espejo is False
    assert d.comparado is True
    assert d.comparadas, "Con foto real del pasado tiene que haber que comparar"
    assert d.nivel in (det.Nivel.AMBAR, det.Nivel.ROJO)


def test_el_almacen_trae_las_fechas_de_las_dos_fotos():
    """Sin las fechas no hay forma de saber que las dos fotos son la misma.

    Es la columna que faltaba: la consulta traia once indicadores y ninguna
    marca temporal, asi que nadie —ni el codigo ni el usuario mirando la
    pantalla— podia distinguir "no ha cambiado" de "es la misma fila".
    """
    from stocks_tracker.compute.run_advice import _salud

    conn = _almacen(HOY - dt.timedelta(days=400))
    try:
        fila = _salud(conn).iloc[0]
    finally:
        conn.close()

    hoy, entonces = det.partir(fila)
    assert hoy.get(det.CLAVE_FECHA_PRECIO) is not None
    assert entonces.get(det.CLAVE_FECHA_PRECIO) is not None
    assert entonces[det.CLAVE_FECHA_PRECIO] < hoy[det.CLAVE_FECHA_PRECIO]


# ---------------------------------------------------------------------------
# El detector, aislado
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("entonces, espera", [
    (dt.date(2026, 8, 24), True),    # la misma fecha: es la misma foto
    (dt.date(2026, 8, 25), True),    # posterior: tampoco es una referencia
    (dt.date(2026, 8, 23), False),   # anterior: si lo es
    (None, False),                   # sin dato no se afirma nada
])
def test_es_espejo(entonces, espera):
    hoy = {det.CLAVE_FECHA_PRECIO: dt.date(2026, 8, 24)}
    assert det._es_espejo(hoy, {det.CLAVE_FECHA_PRECIO: entonces},
                          det.CLAVE_FECHA_PRECIO) is espera


def test_sin_fechas_el_comportamiento_no_cambia():
    """Quien llame sin fechas —tests viejos, ETF sin fundamentales— tiene que
    seguir viendo lo de siempre. Un detector que se activa cuando no sabe
    convertiria en GRIS media cartera."""
    d = det.diagnosticar("AAA", ind_hoy={"drawdown": -0.45},
                         ind_entonces={"drawdown": -0.05})
    assert d.espejo is False
    assert d.comparadas


# ---------------------------------------------------------------------------
# Lo que se ve pero no se ha podido comparar
# ---------------------------------------------------------------------------
def test_una_caida_sin_referencia_no_es_grave():
    """SEGUNDO FALLO DEL MISMO CASO, Y EL QUE MOVIA DINERO.

    Una caida del 60 % entraba como senal GRAVE aunque no hubiera dato del dia
    de la compra. Grave dispara la regla 3 del asesor —REDUCIR—, o sea que se
    recortaba una posicion por algo que nadie habia comprobado: puede que ya
    cayera un 50 % el dia que la compraste, y entonces ha MEJORADO.
    """
    d = det.diagnosticar("AAA", ind_hoy={"drawdown": -0.60})

    assert d.senales, "La caida hay que ensenarla igual: callar seria peor"
    assert d.observaciones, "pero como observacion del presente"
    assert not d.graves, (
        "Sin dato del dia de la compra, una caida no demuestra deterioro y no "
        "puede disparar un REDUCIR."
    )
    assert d.puntos == 0


def test_una_caida_con_referencia_si_es_grave():
    """El contrapeso del anterior: con el antes delante, la caida si cuenta."""
    d = det.diagnosticar("AAA", ind_hoy={"drawdown": -0.60},
                         ind_entonces={"drawdown": -0.05})
    assert d.graves
    assert d.puntos > 0


# ---------------------------------------------------------------------------
# El veredicto que llega al usuario
# ---------------------------------------------------------------------------
def test_el_asesor_no_reduce_por_una_caida_sin_comparar():
    """De punta a punta: el falso REDUCIR que el usuario reporto."""
    d = det.diagnosticar("AAA", ind_hoy={"drawdown": -0.60})
    r = advice.sobre_una_posicion("AAA", diagnostico=d, precio=100.0,
                                  stop=50.0, peso_pct=5.0, titulos=10)

    assert r.veredicto is not advice.Veredicto.REDUCIR
    assert r.veredicto is not advice.Veredicto.VENDER
    assert r.veredicto is advice.Veredicto.SIN_OPINION


def test_el_asesor_dice_que_la_fecha_de_compra_es_de_hoy():
    """Un SIN_OPINION que no dice como arreglarse no sirve de nada.

    Este caso NO se arregla solo con el tiempo —al reves que el de una compra
    anterior al historico—: lo arregla el usuario poniendo la fecha real. Asi
    que el motivo tiene que nombrarlo.
    """
    d = det.diagnosticar(
        "AAA",
        ind_hoy={"drawdown": -0.45, det.CLAVE_FECHA_PRECIO: HOY},
        ind_entonces={"drawdown": -0.45, det.CLAVE_FECHA_PRECIO: HOY})
    r = advice.sobre_una_posicion("AAA", diagnostico=d, precio=100.0,
                                  stop=50.0, peso_pct=5.0, titulos=10)

    assert r.veredicto is advice.Veredicto.SIN_OPINION
    texto = " ".join(r.motivos + r.desmentiria).lower()
    assert "fecha de compra" in texto
    assert "import" in texto, "tiene que decir de donde sale esa fecha"


def test_la_concentracion_sigue_avisando_sin_fecha_de_compra():
    """Pesar de mas no depende de la historia de la posicion.

    Si el silencio por falta de fecha se tragara tambien el aviso de
    concentracion, se estaria ocultando un riesgo de cartera que se puede medir
    hoy y que no necesita ningun pasado.
    """
    d = det.diagnosticar(
        "AAA",
        ind_hoy={"drawdown": -0.45, det.CLAVE_FECHA_PRECIO: HOY},
        ind_entonces={"drawdown": -0.45, det.CLAVE_FECHA_PRECIO: HOY})
    r = advice.sobre_una_posicion("AAA", diagnostico=d, precio=100.0,
                                  stop=50.0, peso_pct=95.0, titulos=10)

    assert r.veredicto is advice.Veredicto.REDUCIR
    assert "cartera" in " ".join(r.motivos).lower()


# ---------------------------------------------------------------------------
# Lo que ve el usuario al preguntar "por que"
# ---------------------------------------------------------------------------
def test_por_que_ensena_las_dos_fechas(tmp_path, monkeypatch, capsys):
    """La salida que reporto el usuario tenia once numeros y ninguna fecha.

    Once numeros identicos en dos columnas se leen como "no ha cambiado nada".
    Con las fechas delante se leen como lo que son: la misma fila dos veces. La
    explicacion tiene que llevar la fecha o no explica.
    """
    from stocks_tracker.compute import run_advice
    from stocks_tracker.core import config, db

    almacen = tmp_path / "w.duckdb"
    _almacen(HOY, destino=almacen).close()

    ajustes = config.get_settings()
    ajustes.raw["paths"] = dict(ajustes.raw["paths"], warehouse=str(almacen))
    monkeypatch.setattr(db, "get_settings", lambda: ajustes)

    assert run_advice.por_que("AAA") == 0
    salida = capsys.readouterr().out

    assert "24/08/2026" in salida, "falta la fecha de las fotos comparadas"
    assert "LA MISMA" in salida, (
        "tiene que decir en voz alta que esta comparando hoy contra hoy"
    )


def test_el_comando_no_arrastra_streamlit():
    """Cincuenta y siete lineas de aviso antes de la primera cifra.

    La salida real que mando el usuario empezaba con 57 avisos de
    "No runtime found, using MemoryCacheStorageManager". Venian de un solo
    import —`from ..app.data_access import _CAMPOS_FUND`— que metia Streamlit
    entero en un comando que se ejecuta desde una tarea programada.

    No es cosmetica: una salida que empieza con cincuenta lineas de ruido es
    una salida que no se lee, y la explicacion de por que se recomienda vender
    algo hay que leerla entera.
    """
    import subprocess
    import sys

    # Se EJECUTA `_salud`, no solo se importa el modulo. El import que metia
    # Streamlit estaba dentro de la funcion, asi que un test que solo importara
    # el modulo pasaria con el fallo puesto: comprobado mutandolo.
    guion = (
        "import sys, duckdb, pathlib;"
        "from stocks_tracker.compute.run_advice import _salud;"
        "c = duckdb.connect(':memory:');"
        f"c.execute(pathlib.Path({str(_ESQUEMA)!r}).read_text());"
        "_salud(c);"
        "sys.exit(0 if 'streamlit' not in sys.modules else 1)"
    )
    hecho = subprocess.run([sys.executable, "-c", guion],
                           capture_output=True, text=True)
    assert hecho.returncode == 0, (
        "run_advice ha vuelto a importar Streamlit: la salida del comando se "
        "llenara de avisos de cache antes de la primera cifra."
    )


# ---------------------------------------------------------------------------
# Poder arreglarlo, que es lo unico que lo arregla de verdad
# ---------------------------------------------------------------------------
def test_solo_se_ofrece_corregir_lo_que_hace_falta():
    """Un aviso que sale siempre deja de leerse.

    Solo entran las posiciones cuya fecha es de hoy o posterior —las que no
    tienen pasado con el que compararse—. Una comprada hace dos anos no tiene
    nada que corregir y meterla en la lista escondería las que si.
    """
    import pandas as pd

    from stocks_tracker.app.components.broker_import import _fechas_a_corregir

    posiciones = pd.DataFrame([
        {"id": "1", "ticker": "HOY", "opened_at": HOY},
        {"id": "2", "ticker": "VIEJA", "opened_at": HOY - dt.timedelta(days=400)},
        {"id": "3", "ticker": "VACIA", "opened_at": None},
    ])

    salen = set(_fechas_a_corregir(posiciones, HOY)["ticker"])
    assert salen == {"HOY", "VACIA"}


def test_corregir_la_fecha_devuelve_el_diagnostico(tmp_path, monkeypatch):
    """De punta a punta: la posicion muda vuelve a hablar.

    Es la comprobacion que cierra el caso. Sin ella, `set_opened_at` podria
    escribir en la columna equivocada —o no escribir— y todo lo demas seguiria
    pasando: el gris se veria igual de honesto y la cartera igual de muda.
    """
    from stocks_tracker.app import data_access
    from stocks_tracker.compute.run_advice import _salud
    from stocks_tracker.core import config, db

    almacen = tmp_path / "w.duckdb"
    _almacen(HOY, destino=almacen).close()

    ajustes = config.get_settings()
    ajustes.raw["paths"] = dict(ajustes.raw["paths"], warehouse=str(almacen))
    monkeypatch.setattr(db, "get_settings", lambda: ajustes)

    def diagnostico():
        with db.connect(read_only=True) as conn:
            hoy, entonces = det.partir(_salud(conn).iloc[0])
        return det.diagnosticar("AAA", fund_hoy=hoy, fund_entonces=entonces,
                                ind_hoy=hoy, ind_entonces=entonces)

    assert diagnostico().nivel is det.Nivel.GRIS

    assert data_access.set_opened_at({"p1": HOY - dt.timedelta(days=400)}) == 1

    despues = diagnostico()
    assert despues.espejo is False
    assert despues.comparadas, "con la fecha buena ya hay algo que comparar"
    assert despues.nivel in (det.Nivel.AMBAR, det.Nivel.ROJO)
