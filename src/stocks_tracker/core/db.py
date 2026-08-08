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


@contextmanager
def connect(read_only: bool = False):
    """Conexion a DuckDB. Usar siempre como context manager."""
    path = get_settings().warehouse_path
    _ensure_parent(path)
    if read_only and not path.exists():
        # DuckDB falla al abrir en solo lectura un fichero inexistente.
        migrate()
    conn = duckdb.connect(str(path), read_only=read_only)
    try:
        yield conn
    finally:
        conn.close()


def migrate() -> None:
    """Crea las tablas que falten. Idempotente."""
    path = get_settings().warehouse_path
    _ensure_parent(path)
    sql = schema_path().read_text(encoding="utf-8")
    conn = duckdb.connect(str(path))
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
    DELETE + INSERT dentro de una transaccion. Idempotente por construccion:
    ejecutar el mismo lote dos veces no duplica ni cambia recuentos.
    """
    if df is None or df.empty:
        return 0

    # Alinear columnas con la tabla destino, en su orden.
    cols_info = conn.execute(f"PRAGMA table_info('{table}')").fetchall()
    table_cols = [c[1] for c in cols_info]
    if not table_cols:
        raise ValueError(f"La tabla '{table}' no existe")

    missing_keys = [k for k in keys if k not in df.columns]
    if missing_keys:
        raise ValueError(f"Faltan columnas clave {missing_keys} para '{table}'")

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
