"""Unica puerta de acceso a DuckDB.

Patron: el ETL es el unico escritor; la UI abre en solo lectura. DuckDB admite
un escritor, asi que separar los roles evita bloqueos.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from pathlib import Path

import duckdb
import pandas as pd

from .config import get_settings, project_root
from .observability import emit
from .timeutils import utcnow

_SCHEMA_VERSIONS = "_schema_versions"
_BACKUPS_A_CONSERVAR = 5


def schema_path() -> Path:
    return Path(__file__).with_name("schema.sql")


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


class AlmacenOcupado(RuntimeError):
    """El almacen ya lo tiene abierto otro proceso.

    DuckDB admite UN SOLO escritor. El caso normal no es una carrera rara: es
    tener el dashboard abierto y lanzar la descarga o el calculo en una
    consola, que es lo que hace cualquiera.

    Existe como excepcion propia porque el mensaje de DuckDB —"IO Error: ...
    El proceso no tiene acceso al archivo porque esta siendo utilizado por otro
    proceso"— llega envuelto en una traza de veinte lineas y no dice ni cual es
    el otro proceso ni que hacer. El usuario lanzo `stocks.ps1 daily` con el
    dashboard abierto y recibio CINCO trazas identicas, una por paso, sin una
    sola frase que dijera "cierra el dashboard".
    """


def _abrir(path: Path, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Abre el almacen, o explica por que no se puede en una frase.

    Solo se traduce el bloqueo. Cualquier otro fallo de E/S —disco lleno,
    permisos, fichero corrupto— se deja pasar tal cual: convertirlos todos en
    "cierra el dashboard" mandaria a la gente a cerrar ventanas cuando el
    problema es otro.
    """
    try:
        return duckdb.connect(str(path), read_only=read_only)
    except duckdb.IOException as exc:
        if not _es_bloqueo(path, exc):
            raise
        raise AlmacenOcupado(
            "El almacen de datos ya esta abierto por otro proceso, y DuckDB "
            "solo admite uno a la vez.\n"
            "  Lo habitual: tienes el DASHBOARD abierto. Cierralo y repite "
            "esto mismo.\n"
            "  Si no lo tienes abierto, puede haber quedado un proceso "
            "colgado: cierra las ventanas de Stocks Tracker, o reinicia.\n"
            f"  Detalle de DuckDB: {exc}"
        ) from exc


EXIT_OCUPADO = 75

# Como dice DuckDB que el fichero esta cogido. Son DOS mensajes distintos
# segun el sistema:
#
#   Windows  IO Error: Cannot open file "...": <mensaje del sistema>
#            File is already open in python.exe (PID 8)
#   Linux    IO Error: Could not set lock on file "...":
#            Conflicting lock is held in /usr/bin/python3 (PID 1670)
#
# Y NO se busca por el mensaje del sistema operativo aunque sea el que mas se
# lee. El del usuario decia "El proceso no tiene acceso al archivo porque esta
# siendo utilizado por otro proceso": esa parte viene traducida al idioma de
# Windows. Las marcas inglesas cubren DuckDB hasta 1.4 y Linux; desde DuckDB
# 1.5, Windows puede omitirlas y se comprueba el codigo del sistema mas abajo.
_MARCAS_DE_BLOQUEO = (
    "already open in",
    "conflicting lock is held",
    "could not set lock",
)


def _es_bloqueo(path: Path, exc: duckdb.IOException) -> bool:
    """Distingue un fichero ocupado de otros fallos de E/S.

    DuckDB 1.5 dejo de incluir en algunos Windows la frase inglesa que
    identificaba al proceso dueño del bloqueo. En ese caso solo queda el
    mensaje del sistema operativo, que llega traducido y no se puede comparar
    de forma fiable. Una apertura de control permite usar el codigo estable de
    Windows (32/33) sin confundir un disco lleno o un fichero corrupto con el
    dashboard abierto.
    """
    texto = str(exc).lower()
    if any(marca in texto for marca in _MARCAS_DE_BLOQUEO):
        return True
    if os.name != "nt" or not path.exists():
        return False

    return _bloqueado_por_otro_proceso(path)


def _bloqueado_por_otro_proceso(path: Path) -> bool:
    """Pregunta a Windows sin depender del idioma de su mensaje de error."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    # Se pide lectura sin compartir el handle. Si otro proceso ya tiene el
    # fichero, Windows responde 32 (sharing violation) o 33 (lock violation).
    handle = create_file(str(path), 0x80000000, 0, None, 3, 0x80, None)
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        return ctypes.get_last_error() in {32, 33}

    close_handle(handle)
    return False


def arrancar(main) -> None:
    """Ejecuta un `main()` de consola traduciendo el almacen ocupado.

    POR QUE ESTO NO ES COSMETICA

    El usuario lanzo `stocks.ps1 daily` con el dashboard abierto y recibio
    CINCO trazas de Python identicas —una por paso— de veinte lineas cada una,
    sin una sola frase que dijera que hacer. La causa era trivial y el remedio
    tambien: cerrar una ventana. Una traza obliga a leer codigo para averiguar
    eso, y quien usa el programa para decidir inversiones no tiene por que.

    Se traduce SOLO el bloqueo. Cualquier otro fallo sigue saliendo con su
    traza entera: esconder un error que no se entiende es peor que ensenarlo.

    Codigo 75 (EX_TEMPFAIL) para que un script pueda distinguir "no se ha
    podido ahora" de un fallo de verdad.
    """
    import sys

    try:
        salida = main()
    except AlmacenOcupado as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        raise SystemExit(EXIT_OCUPADO) from None
    if salida is not None:
        raise SystemExit(salida)


@contextmanager
def connect(read_only: bool = False):
    """Conexion a DuckDB. Usar siempre como context manager."""
    path = get_settings().warehouse_path
    _ensure_parent(path)
    if read_only and not path.exists():
        migrate()
    conn = _abrir(path, read_only=read_only)
    try:
        yield conn
    finally:
        conn.close()


def _schema_aplicado(path: Path, schema_hash: str) -> bool:
    """Si este esquema exacto ya se aplico al almacen."""
    if not path.exists() or path.stat().st_size == 0:
        return False
    conn = _abrir(path, read_only=True)
    try:
        existe = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
            [_SCHEMA_VERSIONS],
        ).fetchone()[0]
        if not existe:
            return False
        return bool(conn.execute(
            f"SELECT COUNT(*) FROM {_SCHEMA_VERSIONS} WHERE schema_hash = ?",
            [schema_hash],
        ).fetchone()[0])
    finally:
        conn.close()


def _crear_backup_de_migracion(path: Path, schema_hash: str) -> Path | None:
    """Copia atomica antes de aplicar un esquema nuevo; conserva las ultimas."""
    if not path.exists() or path.stat().st_size == 0:
        return None

    carpeta = path.parent / "backups"
    carpeta.mkdir(parents=True, exist_ok=True)
    sufijo = schema_hash[:12]
    existentes = sorted(carpeta.glob(f"{path.stem}-*-{sufijo}{path.suffix}"))
    if existentes:
        return existentes[-1]

    momento = utcnow().strftime("%Y%m%dT%H%M%SZ")
    destino = carpeta / f"{path.stem}-{momento}-{sufijo}{path.suffix}"
    temporal = destino.with_suffix(destino.suffix + ".tmp")
    try:
        shutil.copy2(path, temporal)
        os.replace(temporal, destino)
    finally:
        temporal.unlink(missing_ok=True)

    copias = sorted(
        carpeta.glob(f"{path.stem}-????????T??????Z-????????????{path.suffix}"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for antigua in copias[_BACKUPS_A_CONSERVAR:]:
        antigua.unlink()
    return destino


def backups_disponibles() -> list[Path]:
    """Copias del almacen, de la mas reciente a la mas antigua."""
    path = get_settings().warehouse_path
    carpeta = path.parent / "backups"
    if not carpeta.exists():
        return []
    return sorted(
        carpeta.glob(f"{path.stem}-*{path.suffix}"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )


def verificar_backup(backup: Path) -> dict[str, int | str]:
    """Ensaya una restauracion aislada y lee todas sus tablas.

    Abrir el original no basta: una copia puede existir pero no ser copiable o
    depender accidentalmente de otro fichero. El ensayo trabaja sobre un clon
    temporal, igual que una restauracion real, sin tocar el almacen activo.
    """
    origen = Path(backup).resolve()
    carpeta = (get_settings().warehouse_path.parent / "backups").resolve()
    if origen.parent != carpeta:
        raise ValueError("la copia debe estar en la carpeta de backups del almacen")
    if not origen.is_file():
        raise FileNotFoundError(origen)

    carpeta.mkdir(parents=True, exist_ok=True)
    fd, nombre = tempfile.mkstemp(prefix="restore-drill-", suffix=".duckdb", dir=carpeta)
    os.close(fd)
    temporal = Path(nombre)
    try:
        shutil.copy2(origen, temporal)
        conn = _abrir(temporal, read_only=True)
        try:
            tablas = [row[0] for row in conn.execute("SHOW TABLES").fetchall()]
            filas = 0
            for tabla in tablas:
                escaped = tabla.replace('"', '""')
                filas += int(conn.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0])
        finally:
            conn.close()
    finally:
        temporal.unlink(missing_ok=True)

    resultado: dict[str, int | str] = {
        "backup": str(origen),
        "bytes": origen.stat().st_size,
        "tables": len(tablas),
        "rows": filas,
    }
    emit("backup.verified", **resultado)
    return resultado


def verificar_backups() -> list[dict[str, int | str]]:
    """Ejecuta el simulacro sobre todas las copias conservadas."""
    return [verificar_backup(path) for path in backups_disponibles()]


def restaurar_backup(backup: Path) -> Path | None:
    """Restaura atomicamente una copia validada y guarda el estado anterior.

    Solo acepta ficheros de la carpeta de backups del propio almacen. Antes de
    sustituir nada abre la copia con DuckDB; un fichero corrupto no toca la base
    activa. Si habia una base, queda una copia `pre-restore` recuperable.
    """
    path = get_settings().warehouse_path
    carpeta = (path.parent / "backups").resolve()
    origen = Path(backup).resolve()
    if origen.parent != carpeta:
        raise ValueError("la copia debe estar en la carpeta de backups del almacen")
    if not origen.is_file():
        raise FileNotFoundError(origen)

    temporal = path.with_suffix(path.suffix + ".restore.tmp")
    seguridad: Path | None = None
    try:
        shutil.copy2(origen, temporal)
        prueba = _abrir(temporal, read_only=True)
        prueba.close()

        if path.exists() and path.stat().st_size:
            carpeta.mkdir(parents=True, exist_ok=True)
            momento = utcnow().strftime("%Y%m%dT%H%M%SZ")
            seguridad = carpeta / f"{path.stem}-{momento}-pre-restore{path.suffix}"
            shutil.copy2(path, seguridad)
        os.replace(temporal, path)
    finally:
        temporal.unlink(missing_ok=True)
    emit("backup.restored", backup=origen, safety_copy=seguridad)
    return seguridad


def migrate() -> None:
    """Crea o actualiza tablas, con backup previo si cambia el esquema."""
    path = get_settings().warehouse_path
    _ensure_parent(path)
    sql = schema_path().read_text(encoding="utf-8")
    schema_hash = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    if not _schema_aplicado(path, schema_hash):
        _crear_backup_de_migracion(path, schema_hash)

    conn = _abrir(path)
    try:
        conn.execute("BEGIN TRANSACTION")
        conn.execute(sql)
        conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {_SCHEMA_VERSIONS} (
                schema_hash VARCHAR PRIMARY KEY,
                applied_at TIMESTAMP NOT NULL
            )
        """)
        conn.execute(
            f"INSERT INTO {_SCHEMA_VERSIONS} VALUES (?, ?) ON CONFLICT DO NOTHING",
            [schema_hash, utcnow()],
        )
        conn.execute("COMMIT")
    except Exception:  # noqa: BLE001 — toda averia debe revertir la migracion
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def upsert_df(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    df: pd.DataFrame,
    keys: Sequence[str],
) -> int:
    """Inserta reemplazando las filas cuya clave ya existe.

    DuckDB no tiene un UPSERT generico sobre DataFrames, asi que se hace
    DELETE + INSERT dentro de una transaccion. El payload debe ser unico por
    clave; aceptar duplicados internos haria el resultado dependiente del
    orden de las filas y puede romper constraints de tablas concretas.
    """
    if df is None or df.empty:
        return 0

    cols_info = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    table_cols = [c[1] for c in cols_info]
    if not table_cols:
        raise ValueError(f"La tabla '{table}' no existe")

    missing_keys = [k for k in keys if k not in df.columns]
    if missing_keys:
        raise ValueError(f"Faltan columnas clave {missing_keys} para '{table}'")
    if not keys:
        raise ValueError("El UPSERT necesita al menos una columna clave")

    duplicates = df.duplicated(subset=list(keys), keep=False)
    if duplicates.any():
        sample = df.loc[duplicates, list(keys)].head(5).to_dict("records")
        raise ValueError(
            f"El payload contiene claves duplicadas para '{table}': {sample}"
        )

    payload = df.copy()
    for col in table_cols:
        if col not in payload.columns:
            payload[col] = None
    payload = payload[table_cols]

    conn.register("_payload", payload)
    try:
        conn.execute("BEGIN TRANSACTION")
        join = " AND ".join(f"t.{k} = s.{k}" for k in keys)
        conn.execute(
            f"DELETE FROM {table} AS t WHERE EXISTS "
            f"(SELECT 1 FROM _payload AS s WHERE {join})"
        )
        conn.execute(f"INSERT INTO {table} SELECT * FROM _payload")
        conn.execute("COMMIT")
    except Exception:  # noqa: BLE001 — toda averia debe revertir el upsert
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.unregister("_payload")
    return len(payload)


def query(sql: str, params: Iterable | None = None, read_only: bool = True) -> pd.DataFrame:
    """Atajo para lecturas puntuales fuera de la UI."""
    with connect(read_only=read_only) as conn:
        return conn.execute(sql, list(params) if params else None).fetchdf()


def table_counts() -> pd.DataFrame:
    """Numero de filas por tabla. Util para diagnostico."""
    with connect(read_only=True) as conn:
        names = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        rows = [
            {"tabla": n, "filas": conn.execute(f"SELECT COUNT(*) FROM {n}").fetchone()[0]}
            for n in names
        ]
    return pd.DataFrame(rows).sort_values("tabla").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gestion del almacen DuckDB")
    parser.add_argument("--migrate", action="store_true", help="crea o actualiza las tablas")
    parser.add_argument("--counts", action="store_true", help="muestra filas por tabla")
    parser.add_argument("--list-backups", action="store_true", help="enumera copias disponibles")
    parser.add_argument("--restore-backup", type=Path, help="restaura una copia de backups/")
    parser.add_argument(
        "--verify-backups", action="store_true",
        help="simula la restauracion y lectura de todas las copias",
    )
    args = parser.parse_args()
    if args.migrate:
        migrate()
        print(f"Almacen listo en {get_settings().warehouse_path.relative_to(project_root())}")
    if args.counts:
        print(table_counts().to_string(index=False))
    if args.list_backups:
        for backup in backups_disponibles():
            print(backup)
    if args.restore_backup:
        seguridad = restaurar_backup(args.restore_backup)
        print(f"Copia restaurada: {args.restore_backup}")
        if seguridad:
            print(f"Estado anterior conservado en: {seguridad}")
    if args.verify_backups:
        copias = verificar_backups()
        if not copias:
            print("No hay copias que verificar.")
        for copia in copias:
            print(
                f"OK  {copia['backup']} · {copia['tables']} tablas · "
                f"{copia['rows']} filas · {copia['bytes']} bytes"
            )
    if not (
        args.migrate or args.counts or args.list_backups
        or args.restore_backup or args.verify_backups
    ):
        parser.print_help()


if __name__ == "__main__":
    main()
