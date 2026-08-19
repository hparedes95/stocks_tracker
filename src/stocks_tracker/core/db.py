"""Unica puerta de acceso a DuckDB."""

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
    path = get_settings().warehouse_path
    _ensure_parent(path)
    if read_only and not path.exists():
        migrate()
    conn = duckdb.connect(str(path), read_only=read_only)
    try:
        yield conn
    finally:
        conn.close()


def migrate() -> None:
    """Crea tablas y aplica migraciones idempotentes del esquema."""
    path = get_settings().warehouse_path
    _ensure_parent(path)
    sql = schema_path().read_text(encoding="utf-8")
    conn = duckdb.connect(str(path))
    try:
        conn.execute(sql)
        migrations = [
            "ALTER TABLE signal_evidence ADD COLUMN IF NOT EXISTS p_value DOUBLE",
            "ALTER TABLE signal_evidence ADD COLUMN IF NOT EXISTS adjusted_p_value DOUBLE",
            "ALTER TABLE signal_evidence ADD COLUMN IF NOT EXISTS multiple_testing_method VARCHAR",
            "ALTER TABLE signal_evidence ADD COLUMN IF NOT EXISTS data_quality_status VARCHAR",
            "ALTER TABLE signal_evidence ADD COLUMN IF NOT EXISTS fundamentals_point_in_time BOOLEAN",
            "ALTER TABLE signal_evidence ADD COLUMN IF NOT EXISTS git_commit VARCHAR",
            "ALTER TABLE signal_evidence ADD COLUMN IF NOT EXISTS config_hash VARCHAR",
            "ALTER TABLE signal_evidence ADD COLUMN IF NOT EXISTS data_from DATE",
            "ALTER TABLE signal_evidence ADD COLUMN IF NOT EXISTS data_to DATE",
        ]
        for statement in migrations:
            conn.execute(statement)
    finally:
        conn.close()


def upsert_df(conn: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame,
              keys: Sequence[str]) -> int:
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
        raise ValueError(f"El payload contiene claves duplicadas para '{table}': {sample}")
    payload = df.copy()
    for col in table_cols:
        if col not in payload.columns:
            payload[col] = None
    payload = payload[table_cols]
    conn.register("_payload", payload)
    try:
        conn.execute("BEGIN TRANSACTION")
        join = " AND ".join(f"t.{k} = s.{k}" for k in keys)
        conn.execute(f"DELETE FROM {table} AS t WHERE EXISTS (SELECT 1 FROM _payload AS s WHERE {join})")
        conn.execute(f"INSERT INTO {table} SELECT * FROM _payload")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.unregister("_payload")
    return len(payload)


def query(sql: str, params: Iterable | None = None, read_only: bool = True) -> pd.DataFrame:
    with connect(read_only=read_only) as conn:
        return conn.execute(sql, list(params) if params else None).fetchdf()


def table_counts() -> pd.DataFrame:
    with connect(read_only=True) as conn:
        names = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]
        rows = [{"tabla": n, "filas": conn.execute(f"SELECT COUNT(*) FROM {n}").fetchone()[0]} for n in names]
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
