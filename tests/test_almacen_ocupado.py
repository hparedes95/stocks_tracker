"""Cuando el almacen ya lo tiene abierto el dashboard.

EL CASO REAL DEL QUE SALE ESTE FICHERO

El usuario ejecuto `stocks.ps1 daily` con el dashboard abierto y recibio esto,
CINCO veces seguidas —una por paso— con veinte lineas de traza cada una:

    _duckdb.IOException: IO Error: Cannot open file "...warehouse.duckdb":
    El proceso no tiene acceso al archivo porque esta siendo utilizado por
    otro proceso.
    File is already open in ...python.exe (PID 8)

Ni una sola frase del programa. Y despues `stocks.ps1 huella` le enseno un
resultado impecable —del calculo anterior, del que no se dijo nada— que
parecia confirmar que el ciclo habia ido bien.

DuckDB admite un solo escritor. La causa era trivial y el remedio tambien:
cerrar una ventana. Pero para saberlo habia que leer el codigo.
"""

from __future__ import annotations

import subprocess
import sys
from contextlib import contextmanager

import duckdb
import pytest

from stocks_tracker.core import db


@pytest.fixture
def almacen(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "w.duckdb"
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub.warehouse_path


@contextmanager
def _otro_proceso_lo_tiene(path):
    """Sujeta el almacen desde OTRO proceso, que es el caso real.

    Tiene que ser otro proceso: DuckDB deja abrir dos veces el mismo fichero
    dentro del mismo interprete —reutiliza la instancia— y el bloqueo solo
    aparece entre procesos distintos, que es justo lo que pasa cuando el
    dashboard corre en una ventana y la descarga en otra.
    """
    guion = (
        "import duckdb, sys, time\n"
        "c = duckdb.connect(sys.argv[1])\n"
        "print('listo', flush=True)\n"
        "time.sleep(30)\n"
    )
    hijo = subprocess.Popen([sys.executable, "-c", guion, str(path)],
                            stdout=subprocess.PIPE, text=True)
    try:
        hijo.stdout.readline()          # espera a que lo tenga cogido
        yield
    finally:
        hijo.kill()
        hijo.wait()


def test_el_bloqueo_se_traduce_a_una_frase(almacen):
    """Se bloquea el almacen DE VERDAD y se comprueba el mensaje.

    No se simula la excepcion a proposito. Un `raise` inventado habria pasado
    igual con el detector mal escrito: la primera version buscaba el texto del
    sistema operativo, y ese llega TRADUCIDO al idioma de Windows —el del
    usuario decia "esta siendo utilizado por otro proceso"—, asi que habria
    fallado justo en la maquina donde aparecio el problema. Se vio al
    reproducirlo, no al razonarlo.
    """
    with _otro_proceso_lo_tiene(almacen):
        with pytest.raises(db.AlmacenOcupado) as fallo:
            db.migrate()

    texto = str(fallo.value).lower()
    assert "dashboard" in texto, "tiene que decir QUE cerrar"
    assert "cierra" in texto


def test_connect_tambien_lo_traduce(almacen):
    """`migrate` no es la unica puerta: media aplicacion entra por `connect`."""
    with _otro_proceso_lo_tiene(almacen), pytest.raises(db.AlmacenOcupado):
        with db.connect() as conn:
            conn.execute("SELECT 1")


def test_los_demas_fallos_de_disco_no_se_disfrazan(almacen, monkeypatch):
    """Un disco lleno o un fichero corrupto NO son el dashboard abierto.

    Traducirlo todo a "cierra el dashboard" mandaria a la gente a cerrar
    ventanas cuando el problema es otro, y ese consejo no arregla nada.
    """
    def revienta(*a, **k):
        raise duckdb.IOException("disk is full")

    monkeypatch.setattr(db.duckdb, "connect", revienta)
    with pytest.raises(duckdb.IOException) as fallo:
        db.migrate()
    assert not isinstance(fallo.value, db.AlmacenOcupado)


def test_arrancar_no_ensena_la_traza_y_devuelve_75(capsys):
    """Lo que ve el usuario en la consola.

    El codigo 75 (EX_TEMPFAIL) no es decoracion: es lo que permite que
    `stocks.ps1 daily` distinga "no se ha podido AHORA" de un fallo de verdad y
    pare el ciclo en vez de repetir cinco veces el mismo error.
    """
    def main():
        raise db.AlmacenOcupado("El almacen ya esta abierto. Cierra el dashboard.")

    with pytest.raises(SystemExit) as salida:
        db.arrancar(main)

    assert salida.value.code == db.EXIT_OCUPADO == 75
    capturado = capsys.readouterr()
    assert "Cierra el dashboard" in capturado.err
    assert "Traceback" not in capturado.err + capturado.out


def test_arrancar_respeta_el_codigo_de_salida_de_siempre():
    """Los comandos que ya devolvian un codigo —78 sin fuentes, 76 sin nada que
    puntuar— tienen que seguir devolviendolo. Envolverlos no puede cambiar lo
    que un script que ya existe interpreta."""
    with pytest.raises(SystemExit) as salida:
        db.arrancar(lambda: 78)
    assert salida.value.code == 78


def test_arrancar_no_sale_cuando_main_no_devuelve_nada():
    """La mayoria de los `main()` no devuelven nada. Convertir ese None en un
    SystemExit(None) funciona por casualidad; hacerlo explicito evita que el
    dia que alguien mire el codigo de salida se encuentre una sorpresa."""
    db.arrancar(lambda: None)


def test_el_mensaje_de_windows_en_espanol_tambien_se_reconoce():
    """EL FALLO DE MI PRIMERA VERSION DEL DETECTOR.

    Buscaba "being used by another process". El mensaje que recibio el usuario
    decia "El proceso no tiene acceso al archivo porque esta siendo utilizado
    por otro proceso": Windows traduce SU parte del error al idioma del
    sistema, asi que el detector habria fallado exactamente en la maquina donde
    aparecio el problema.

    Lo que no se traduce nunca es lo que escribe DuckDB, y es por ahi por donde
    se reconoce. Aqui va el texto literal que llego del uso real.
    """
    real = (
        'IO Error: Cannot open file "C:\\\\Users\\\\x\\\\data\\\\warehouse.duckdb": '
        "El proceso no tiene acceso al archivo porque está siendo utilizado "
        "por otro proceso.\n\nFile is already open in "
        "C:\\\\Users\\\\x\\\\python.exe (PID 8)"
    )
    assert any(m in real.lower() for m in db._MARCAS_DE_BLOQUEO)


def test_el_mensaje_de_linux_tambien_se_reconoce():
    """El otro formato, que es distinto y no comparte ni una palabra clave con
    el de Windows. Salio al reproducir el fallo aqui."""
    real = ('IO Error: Could not set lock on file "/tmp/w.duckdb": '
            "Conflicting lock is held in /usr/bin/python3.11 (PID 1670).")
    assert any(m in real.lower() for m in db._MARCAS_DE_BLOQUEO)
