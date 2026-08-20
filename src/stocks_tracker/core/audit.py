"""Registro de calculos: que entro, que salio, con que codigo.

QUE PREGUNTA CONTESTA, Y POR QUE NO LA CONTESTABA NADIE

El proyecto ya tenia cuatro registros: `ingest_log` dice que se descargo,
`data_quality` que se comprobo, `bot_decisions` que decidio el bot,
`price_consensus` que dijo cada proveedor. Cada uno contesta bien a lo suyo.

Ninguno contesta a esta: *"el score de AAPL salio 82,4 el martes. ¿Como?"*.

Para responder hacen falta tres cosas a la vez, y estaban en tres sitios o en
ninguno: que datos habia cuando se calculo, que version del codigo lo calculo, y
con que configuracion.

QUE SE GUARDA, Y QUE NO

Una fila por EJECUCION de cada paso, no por dato calculado. Una fila por dato
serian millones y nadie las leeria, que es la forma habitual de tener un audit
log que no se audita: se convierte en un coste de escritura y en nada mas.

Cada fila lleva el resumen de lo que entro —cuantas filas, de que fechas, de que
fuentes—, el de lo que salio, y el sello de trazabilidad (`core/lineage`): el
commit de git y el hash de la configuracion.

Con eso, reproducir un numero es: volver a ese commit, poner esa configuracion,
y comprobar que los datos de entrada eran los que dice la fila. Si algo no
cuadra, el numero no era reproducible y ahora se sabe.

EL FALLO NO SE TRAGA

Un paso que revienta escribe su fila con `estado='error'` y el mensaje. Un
registro que solo guarda los exitos convierte un fallo en un hueco, y un hueco
se lee igual que "ese dia no se ejecuto", que es una cosa completamente
distinta.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from . import lineage
from .db import connect
from .ids import ulid
from .timeutils import utcnow

OK = "ok"
ERROR = "error"
RECHAZADO = "rechazado"   # se ejecuto y se nego a seguir: la puerta de calidad


def _json(valor: Any) -> str:
    return json.dumps(valor or {}, ensure_ascii=False, default=str, sort_keys=True)


@dataclass
class Registro:
    """Lo que se va anotando durante un paso, para escribirlo al terminar."""

    paso: str
    run_id: str
    empezado: Any
    entrada: dict = field(default_factory=dict)
    salida: dict = field(default_factory=dict)
    estado: str = OK
    detalle: str = ""

    def leido(self, **datos) -> None:
        """Que habia de entrada. Cuantas filas, de que fechas, de que fuentes."""
        self.entrada.update(datos)

    def escrito(self, **datos) -> None:
        self.salida.update(datos)

    def rechazado(self, motivo: str) -> None:
        """Se ejecuto y se nego a seguir. NO es un error.

        La puerta de calidad negandose a calcular es el sistema funcionando, y
        registrarlo como error lo mezclaria con las averias de verdad: en un mes
        nadie distinguiria un dia con datos malos de un dia con el disco lleno.
        """
        self.estado = RECHAZADO
        self.detalle = motivo


@contextmanager
def paso(nombre: str, run_id: str | None = None, config: dict | None = None):
    """Envuelve un paso del pipeline y deja su rastro pase lo que pase.

    Se escribe en el `finally`. Si el paso revienta, la fila se escribe igual
    con `estado='error'` y el mensaje, y la excepcion sigue su camino: tragarsela
    aqui convertiria el registro en la causa de que un fallo pase inadvertido,
    que seria justo lo contrario de para lo que existe.
    """
    registro = Registro(paso=nombre, run_id=run_id or ulid(), empezado=utcnow())
    try:
        yield registro
    except Exception as exc:  # noqa: BLE001
        registro.estado = ERROR
        registro.detalle = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            guardar(registro, config)
        except Exception:  # noqa: BLE001
            # Que falle el registro NO puede tumbar el paso que se estaba
            # registrando. Un audit log que rompe la ingesta es peor que no
            # tener audit log.
            pass


def guardar(registro: Registro, config: dict | None = None) -> None:
    sello = lineage.sellar(config or {})
    with connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (run_id, paso, empezado, terminado, entrada, "
            "salida, git_commit, config_hash, estado, detalle) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [registro.run_id, registro.paso, registro.empezado, utcnow(),
             _json(registro.entrada), _json(registro.salida),
             sello.git_commit, sello.config_hash,
             registro.estado, registro.detalle],
        )


def historial(conn, paso: str | None = None, limite: int = 100):
    if paso:
        return conn.execute(
            "SELECT * FROM audit_log WHERE paso = ? ORDER BY empezado DESC LIMIT ?",
            [paso, limite],
        ).fetchdf()
    return conn.execute(
        "SELECT * FROM audit_log ORDER BY empezado DESC LIMIT ?", [limite]
    ).fetchdf()


def reproducible(conn, paso: str) -> tuple[bool, str]:
    """Si la ultima ejecucion de un paso se puede reproducir hoy.

    Dos cosas lo impiden, y las dos son informacion util:

    - Que se calculara con el repositorio SUCIO. `lineage` marca el commit con
      `-sucio` cuando hay cambios sin commitear: ese codigo no existe en ningun
      sitio y el numero no se puede volver a obtener.
    - Que el commit no se sepa. Sin el, no hay a donde volver.
    """
    fila = conn.execute(
        "SELECT git_commit, estado FROM audit_log WHERE paso = ? "
        "ORDER BY empezado DESC LIMIT 1", [paso],
    ).fetchone()
    if fila is None:
        return False, f"No hay ninguna ejecucion registrada de '{paso}'."

    commit, estado = fila
    if not commit or commit == "desconocido":
        return False, "No se sabe con que version del codigo se calculo."
    if str(commit).endswith("-sucio"):
        return False, (
            f"Se calculo con el repositorio sin commitear ({commit}). Ese codigo "
            "no existe en ningun sitio: el resultado no se puede volver a obtener."
        )
    if estado != OK:
        return False, f"La ultima ejecucion termino en '{estado}'."
    return True, f"Reproducible desde el commit {commit}."
