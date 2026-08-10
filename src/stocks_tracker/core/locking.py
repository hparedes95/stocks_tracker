"""Exclusion mutua entre procesos que escriben en el almacen.

DuckDB admite un unico escritor. En Linux eso lo resolvia `flock` dentro de
`daily_update.sh`, pero en Windows no hay equivalente y ahora hay tres cosas
que pueden querer escribir a la vez:

- la tarea programada de la noche,
- el lanzador, que se pone al dia al abrir el programa,
- el usuario ejecutando algo a mano.

Y coinciden justo en el peor momento: al encender el ordenador por la manana,
la tarea que se perdio anoche arranca por `StartWhenAvailable` mientras el
usuario abre el dashboard. Sin esto, uno de los dos muere con un error de
bloqueo que el script de arriba interpreta como "hacen falta datos" y relanza
una descarga completa.

Se usa un fichero con creacion atomica (`O_EXCL`) en lugar de una libreria de
bloqueos porque tiene que funcionar igual en Windows y en Unix sin
dependencias.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from .config import get_settings
from .timeutils import utcnow

# Un proceso que muere sin limpiar deja el fichero ahi para siempre. Pasado
# este tiempo se considera abandonado: mas vale arriesgarse a un solapamiento
# improbable que dejar el sistema sin actualizarse nunca.
STALE_AFTER_HOURS = 3.0


class AlreadyRunning(RuntimeError):
    """Otro proceso tiene el almacen tomado."""


def lock_path() -> Path:
    return get_settings().logs_dir / "writer.lock"


def _is_stale(path: Path) -> bool:
    try:
        age_hours = (utcnow().timestamp() - path.stat().st_mtime) / 3600.0
    except OSError:
        return True
    return age_hours > STALE_AFTER_HOURS


@contextmanager
def single_writer(task: str = ""):
    """Toma el bloqueo de escritura o lanza `AlreadyRunning`.

    No espera: con procesos que se ejecutan cada dia, encolarse no aporta
    nada. Si otro esta trabajando, este se retira y ya se actualizara luego.
    """
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists() and _is_stale(path):
        path.unlink(missing_ok=True)

    try:
        # O_EXCL falla si el fichero ya existe, y esa comprobacion la hace el
        # sistema de ficheros de forma atomica: dos procesos simultaneos no
        # pueden creerse ambos duenos del bloqueo.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        holder = ""
        try:
            holder = path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        raise AlreadyRunning(
            f"Ya hay otro proceso actualizando los datos{f' ({holder})' if holder else ''}."
        ) from None

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"pid={os.getpid()} tarea={task} desde={utcnow():%Y-%m-%d %H:%M:%S}")
        yield
    finally:
        path.unlink(missing_ok=True)
