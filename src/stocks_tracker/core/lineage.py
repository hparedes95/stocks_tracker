"""De donde salio un numero: codigo, configuracion y datos que lo produjeron.

El problema que resuelve. Dentro de tres meses la pantalla dira que
PULLBACK_IN_UPTREND esta validada con un exceso del +1,73 %. Para saber si te
lo puedes creer hace falta saber tres cosas que hoy no se guardan en ninguna
parte:

  - con QUE CODIGO se calculo (el estadistico cambio la semana pasada),
  - con QUE CONFIGURACION (los pesos de los factores, el coste asumido),
  - y sobre QUE DATOS (el proveedor pudo reescribir la serie desde entonces).

Sin eso, un numero guardado es una afirmacion sin autor. Y no se puede
reconstruir despues: el commit de hoy se sabe hoy, y el rango de datos de hoy
tambien.

QUE NO ES

No es una firma ni una garantia de integridad. Cualquiera con acceso al
almacen puede escribir lo que quiera en estas columnas. Sirve para responder
"¿de donde salio esto?" cuando la respuesta se ha olvidado, que es el caso
real, no para defenderse de nadie.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

SIN_GIT = "sin-git"


@lru_cache(maxsize=1)
def git_commit() -> str:
    """El commit del codigo que se esta ejecutando, con un sufijo si hay cambios.

    `-sucio` cuando el arbol de trabajo tiene modificaciones sin confirmar: el
    identificador no describe entonces el codigo que corrio, y decirlo es mas
    util que dar un commit que no corresponde. Es el caso normal mientras se
    desarrolla, asi que conviene que se distinga a simple vista.

    Si no hay git —una instalacion desde un zip— devuelve "sin-git" en vez de
    fallar. Un programa que no arranca porque no puede saber su version es peor
    que uno que no sabe su version.
    """
    raiz = Path(__file__).resolve().parents[3]
    try:
        commit = subprocess.run(
            ["git", "-C", str(raiz), "rev-parse", "--short=12", "HEAD"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        if not commit:
            return SIN_GIT
        sucio = subprocess.run(
            ["git", "-C", str(raiz), "status", "--porcelain"],
            capture_output=True, text=True, timeout=5, check=True,
        ).stdout.strip()
        return f"{commit}-sucio" if sucio else commit
    except (subprocess.SubprocessError, OSError):
        return SIN_GIT


def config_hash(payload: dict) -> str:
    """Huella estable de una configuracion.

    `sort_keys` porque un diccionario reordenado es la misma configuracion:
    sin eso, el hash cambiaria al reordenar un YAML y pareceria que algo se
    modifico. Los flotantes se redondean por el mismo motivo —0,1 + 0,2 no da
    exactamente 0,3 y el hash bailaria solo—.
    """
    return hashlib.blake2s(
        json.dumps(_normalizar(payload), sort_keys=True, default=str).encode(),
        digest_size=8,
    ).hexdigest()


def _normalizar(valor):
    """Deja la configuracion en una forma que se pueda comparar.

    No hace falta un caso especial para `bool` aunque `isinstance(True, int)`
    sea cierto: el unico caso numerico de aqui es `float`, y `isinstance(True,
    float)` es False, asi que los booleanos caen al final sin tocarse y
    `json.dumps` los escribe como `true`, distinto de `1`. Una version anterior
    llevaba esa guarda y era codigo muerto —al quitarla no cambiaba ningun
    test—, asi que se documenta en vez de mantenerla.
    """
    if isinstance(valor, dict):
        return {k: _normalizar(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_normalizar(v) for v in valor]
    if isinstance(valor, float):
        return round(valor, 9)
    return valor


@dataclass(frozen=True)
class Sello:
    """Lo que hace falta para reproducir un numero."""

    git_commit: str
    config_hash: str
    data_from: str | None
    data_to: str | None
    n_rows: int

    def as_dict(self) -> dict:
        return asdict(self)


def sellar(config: dict, data_from=None, data_to=None, n_rows: int = 0) -> Sello:
    """Construye el sello del momento.

    Las fechas se guardan como texto ISO y no como objetos: el sello acaba en
    una columna VARCHAR junto al resto, y una fecha convertida por tres capas
    distintas acaba con tres formatos distintos en la misma columna.
    """
    return Sello(
        git_commit=git_commit(),
        config_hash=config_hash(config),
        data_from=str(data_from) if data_from is not None else None,
        data_to=str(data_to) if data_to is not None else None,
        n_rows=int(n_rows),
    )


def describir(sello: Sello) -> str:
    """Una linea legible, para la consola y para la pantalla."""
    periodo = (f"{sello.data_from} a {sello.data_to}"
               if sello.data_from and sello.data_to else "sin periodo")
    return (f"codigo {sello.git_commit} · configuracion {sello.config_hash} · "
            f"datos {periodo} ({sello.n_rows:,} filas)".replace(",", "."))
