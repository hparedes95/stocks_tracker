"""Unica puerta de acceso a DuckDB.

Patron: el ETL es el unico escritor; la UI abre en solo lectura. DuckDB admite
un escritor, asi que separar los roles evita bloqueos.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from pathlib import Path

import duckdb
import pandas as pd

from .config import get_settings, project_root


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
        texto = str(exc).lower()
        if not any(marca in texto for marca in _MARCAS_DE_BLOQUEO):
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
# Windows, asi que buscar el texto en ingles habria fallado justo en la maquina
# donde aparecio el problema. Comprobado al reproducirlo. Lo que escribe DuckDB
# —lo de abajo— esta siempre en ingles.
_MARCAS_DE_BLOQUEO = (
    "already open in",
    "conflicting lock is held",
    "could not set lock",
)


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


def migrate() -> None:
    """Crea las tablas que falten. Idempotente."""
    path = get_settings().warehouse_path
    _ensure_parent(path)
    sql = schema_path().read_text(encoding="utf-8")
    conn = _abrir(path)
    try:
        conn.execute(sql)
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
    except Exception:
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
    args = parser.parse_args()
    if args.migrate:
        migrate()
        print(f"Almacen listo en {get_settings().warehouse_path.relative_to(project_root())}")
    if args.counts:
        print(table_counts().to_string(index=False))
    if not (args.migrate or args.counts):
        parser.print_help()


if __name__ == "__main__":
    main()
