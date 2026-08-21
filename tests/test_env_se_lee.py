"""El `.env` se lee, y lo ve TODO el programa.

EL FALLO, REPORTADO POR EL USUARIO

Puso la clave de Twelve Data en el `.env` y ejecuto la auditoria. Salio:

    Auditando 76 valores ... contra stooq

Twelve Data ni aparecia. La clave estaba escrita, el fichero estaba donde el
instalador lo deja, y el programa se comportaba como si no hubiera configurado
nada. Sin ningun error: solo una fuente menos, en silencio.

LA CAUSA

`secrets.load_env()` existia desde el principio y lo llamaba UN modulo: el de
alertas. Ni la ingesta, ni la auditoria, ni el calculo, ni el bot. Cualquier
credencial que no fuera la de Telegram era invisible fuera de esa rama.

Es el mismo patron que este proyecto persigue en `test_codigo_muerto.py`: una
funcion correcta que casi nadie llama. Aqui no estaba muerta del todo —tenia un
llamante— y por eso el detector no la cazaba: la cobertura era parcial, que es
mas dificil de ver que la ausencia total.

LA REGLA

Se carga en `stocks_tracker/__init__.py`. Cualquier cosa que use el programa
importa el paquete, asi que no hay ningun punto de entrada que recordar.
"""

from __future__ import annotations

import subprocess
import sys

from stocks_tracker.core.config import project_root
from stocks_tracker.core.secrets import CREDENTIALS


def _en_un_proceso_limpio(codigo: str, entorno: dict) -> str:
    """Ejecuta codigo en un interprete nuevo, con su propio `.env`.

    Hace falta un proceso aparte: `load_env` esta cacheada con `lru_cache` y el
    paquete ya esta importado en el proceso de los tests, asi que aqui no se
    puede observar el efecto del arranque.
    """
    import os

    salida = subprocess.run(
        [sys.executable, "-c", codigo],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, **entorno},
    )
    assert salida.returncode == 0, salida.stderr
    return salida.stdout.strip()


def test_una_clave_del_env_la_ve_la_auditoria(tmp_path):
    """EL CASO DEL USUARIO. Clave en el fichero, y que la vea quien la necesita."""
    (tmp_path / ".env").write_text(
        "TWELVE_DATA_API_KEY=clave-de-prueba\n", encoding="utf-8")

    visto = _en_un_proceso_limpio(
        "from stocks_tracker.providers import twelve_data_provider as td;"
        "print(td.api_key())",
        {"STOCKS_TRACKER_ROOT": str(tmp_path)},
    )

    assert visto == "clave-de-prueba"


def test_con_clave_la_auditoria_anade_el_tercer_proveedor(tmp_path):
    """La consecuencia observable: "contra stooq" pasa a "contra stooq y
    twelve_data"."""
    (tmp_path / ".env").write_text(
        "TWELVE_DATA_API_KEY=clave-de-prueba\n", encoding="utf-8")

    visto = _en_un_proceso_limpio(
        "from stocks_tracker.ingest.run_audit import _contrastes_disponibles;"
        "print(','.join(_contrastes_disponibles({'providers': ['stooq']})))",
        {"STOCKS_TRACKER_ROOT": str(tmp_path)},
    )

    assert visto == "stooq,twelve_data"


def test_el_entorno_gana_sobre_el_fichero(tmp_path):
    """Permite ejecutar algo puntualmente con otra credencial sin editar el
    fichero ni arriesgarse a dejarla escrita."""
    (tmp_path / ".env").write_text(
        "TWELVE_DATA_API_KEY=la-del-fichero\n", encoding="utf-8")

    visto = _en_un_proceso_limpio(
        "from stocks_tracker.providers import twelve_data_provider as td;"
        "print(td.api_key())",
        {"STOCKS_TRACKER_ROOT": str(tmp_path),
         "TWELVE_DATA_API_KEY": "la-del-entorno"},
    )

    assert visto == "la-del-entorno"


def test_sin_env_el_programa_arranca_igual(tmp_path):
    """Un `.env` que no existe no puede impedir arrancar: el programa funciona
    entero sin ninguna clave, solo con menos fuentes."""
    visto = _en_un_proceso_limpio(
        "import stocks_tracker; print(stocks_tracker.__version__)",
        {"STOCKS_TRACKER_ROOT": str(tmp_path)},
    )

    assert visto


def test_todas_las_credenciales_registradas_se_leen_del_env(tmp_path):
    """Guardarrail sobre las credenciales presentes y futuras.

    Con un test solo para Twelve Data, la proxima credencial que se anada al
    registro vuelve a ser invisible fuera de la rama que la cargue a mano, y el
    sintoma es otra vez una fuente que falta sin decir nada.
    """
    lineas = [f"{c.env}=valor-{i}" for i, c in enumerate(CREDENTIALS)]
    (tmp_path / ".env").write_text("\n".join(lineas) + "\n", encoding="utf-8")

    codigo = (
        "import os, stocks_tracker;"
        "print(','.join(k for k in os.environ if k.startswith(("
        "'TWELVE_','KRAKEN_','TELEGRAM_','POLYMARKET_','FRED_')))"
        ")"
    )
    visto = _en_un_proceso_limpio(
        codigo, {"STOCKS_TRACKER_ROOT": str(tmp_path)}).split(",")

    faltan = [c.env for c in CREDENTIALS if c.env not in visto]
    assert not faltan, f"credenciales que el programa no llega a ver: {faltan}"


def test_el_env_de_ejemplo_documenta_todas_las_credenciales():
    """Si una credencial no esta en `.env.example`, el usuario no sabe que
    existe: el instalador copia ese fichero como plantilla."""
    texto = (project_root() / ".env.example").read_text("utf-8")

    faltan = [c.env for c in CREDENTIALS if c.env not in texto]
    assert not faltan, f"credenciales sin documentar en .env.example: {faltan}"
