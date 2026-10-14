"""Copias portables de mercado: lista blanca, integridad y sustitución atómica.

Nunca se exporta el almacén entero: contiene cartera, diario y credenciales
indirectas. Los archivos se transportan manualmente para uso privado; no se
publican en GitHub ni se ejecuta SQL procedente del archivo importado.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow.parquet as pq

from .config import project_root
from .db import schema_path
from .timeutils import utcnow

FORMAT = 1
MAX_BYTES = 512 * 1024 * 1024
MAX_EXPANDED = 2 * 1024 * 1024 * 1024
MAX_ROWS_PER_TABLE = 10_000_000
# Excluye deliberadamente recomendaciones, posiciones, diario, watchlist,
# estrategias de usuario, logs, órdenes, notificaciones y tablas futuras.
TABLES = (
    "instruments", "universe_membership", "prices_daily", "corporate_actions",
    "fundamentals_snapshot", "earnings_events", "macro_series", "indicators_daily",
    "factor_scores", "factor_contributions", "signals", "signal_evidence",
    "breadth_daily", "sector_rotation", "regime_daily", "data_quality",
    "price_consensus", "prices_quarantine", "scoring_runs", "sec_facts", "prediction_plans",
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def engine_fingerprint() -> str:
    """Código y configuración del análisis; independiente de Git y del PC."""
    root = Path(__file__).resolve().parents[1]
    h = hashlib.sha256()
    for folder in ("core", "compute", "backtest", "providers"):
        for path in sorted((root / folder).rglob("*.py")):
            h.update(path.relative_to(root).as_posix().encode())
            h.update(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode())
    for name in ("settings.yaml", "factors.yaml", "universe.yaml", "macro.yaml", "stress.yaml"):
        path = project_root() / "config" / name
        h.update(name.encode())
        h.update(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode())
    return h.hexdigest()


def state(conn) -> dict:
    try:
        row = conn.execute("SELECT manifest, frozen FROM market_bundle_state WHERE singleton=1").fetchone()
    except duckdb.CatalogException:  # tabla aún no migrada: versiones antiguas
        return {}
    return {**json.loads(row[0]), "frozen": bool(row[1])} if row else {}


def frozen_market() -> bool:
    from .db import connect
    try:
        with connect(read_only=True) as conn:
            return bool(state(conn).get("frozen"))
    except (OSError, RuntimeError, duckdb.IOException):
        return False


def unlock(conn) -> None:
    """Se llama solo tras confirmar que el usuario quiere abandonar la copia."""
    conn.execute("UPDATE market_bundle_state SET frozen=FALSE WHERE singleton=1")


def export_bundle(conn) -> bytes:
    buffers, tables = {}, {}
    conn.execute("BEGIN TRANSACTION")
    try:
        for name in TABLES:
            frame = conn.execute(f'SELECT * FROM "{name}" ORDER BY ALL').fetchdf()
            stream = io.BytesIO()
            frame.to_parquet(stream, index=False, compression="zstd")
            data = stream.getvalue()
            buffers[f"{name}.parquet"] = data
            tables[name] = {"sha256": digest(data), "rows": len(frame),
                            "columns": list(frame.columns)}
        session = conn.execute("SELECT date FROM current_session").fetchone()
        conn.execute("COMMIT")
    except Exception:  # noqa: BLE001 — revertir la lectura transaccional ante cualquier fallo
        conn.execute("ROLLBACK")
        raise
    if not session:
        raise ValueError("No hay una sesión de análisis completa para exportar.")
    if not tables["prices_daily"]["rows"] or not tables["factor_scores"]["rows"]:
        raise ValueError("Faltan precios o ranking. Completa el análisis antes de exportar.")
    manifest = {"format": FORMAT, "as_of": str(session[0]), "engine": engine_fingerprint(),
                "schema": digest(schema_path().read_text(encoding="utf-8").replace("\r\n", "\n").encode()),
                "tables": tables}
    manifest["bundle_id"] = digest(json.dumps(manifest, sort_keys=True).encode())
    manifest["created_at"] = utcnow().isoformat()
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        for name, data in buffers.items():
            archive.writestr(name, data)
    if result.tell() > MAX_BYTES:
        raise ValueError("La copia supera 512 MB; no se exporta un archivo que no pueda importarse.")
    return result.getvalue()


def inspect_bundle(data: bytes) -> tuple[dict, dict[str, pd.DataFrame]]:
    if len(data) > MAX_BYTES:
        raise ValueError("Archivo demasiado grande (máximo 512 MB).")
    expected = {"manifest.json", *(f"{name}.parquet" for name in TABLES)}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) != len(expected) or set(archive.namelist()) != expected:
            raise ValueError("Contenido no permitido: se esperaba solo la base de mercado.")
        if sum(e.file_size for e in entries) > MAX_EXPANDED:
            raise ValueError("Archivo descomprimido demasiado grande.")
        if archive.getinfo("manifest.json").file_size > 1024 * 1024:
            raise ValueError("Manifiesto demasiado grande.")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != FORMAT or set(manifest.get("tables", {})) != set(TABLES):
            raise ValueError("Formato de copia no compatible.")
        identity = {k: v for k, v in manifest.items() if k not in ("bundle_id", "created_at")}
        if digest(json.dumps(identity, sort_keys=True).encode()) != manifest.get("bundle_id"):
            raise ValueError("Manifiesto alterado o corrupto.")
        if manifest.get("engine") != engine_fingerprint():
            raise ValueError("Los equipos no tienen el mismo código/configuración de análisis. "
                             "Actualiza ambos antes de importar; no se han modificado datos.")
        schema = digest(schema_path().read_text(encoding="utf-8").replace("\r\n", "\n").encode())
        if manifest.get("schema") != schema:
            raise ValueError("Esquema diferente: instala la misma versión en ambos PC.")
        frames = {}
        for name in TABLES:
            raw = archive.read(f"{name}.parquet")
            info = manifest["tables"][name]
            if digest(raw) != info["sha256"]:
                raise ValueError(f"Datos dañados: {name}.")
            metadata = pq.read_metadata(io.BytesIO(raw))
            if metadata.num_rows > MAX_ROWS_PER_TABLE or metadata.num_columns > 200:
                raise ValueError(f"Tabla demasiado grande: {name}.")
            frame = pd.read_parquet(io.BytesIO(raw))
            if len(frame) != info["rows"] or list(frame.columns) != info["columns"]:
                raise ValueError(f"Contenido incoherente: {name}.")
            frames[name] = frame
        prices = frames["prices_daily"]
        if (prices.empty or prices["source"].isna().any() or
                prices["source"].astype(str).str.strip().isin(['synthetic', '']).any()):
            raise ValueError("No se importa mercado sintético o de procedencia desconocida.")
    return manifest, frames


def import_bundle(conn, data: bytes) -> dict:
    """Valida todo antes de cambiar nada; transacción revierte cualquier fallo."""
    manifest, frames = inspect_bundle(data)
    for name, frame in frames.items():
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{name}')").fetchall()]
        if list(frame.columns) != cols:
            raise ValueError(f"Columnas no compatibles: {name}.")
    conn.execute("BEGIN TRANSACTION")
    try:
        from .market_recovery import save_point
        save_point(conn)
        for name, frame in frames.items():
            conn.register("_market_bundle_import", frame)
            conn.execute(f'DELETE FROM "{name}"')
            conn.execute(f'INSERT INTO "{name}" BY NAME SELECT * FROM _market_bundle_import')
            conn.unregister("_market_bundle_import")
        conn.execute("INSERT OR REPLACE INTO market_bundle_state VALUES (1, ?, ?, TRUE)",
                     [json.dumps(manifest), utcnow()])
        # Invalidar tarjetas calculadas con otro mercado; conservar historial privado.
        conn.execute("DELETE FROM advisor_runs")
        conn.execute("DELETE FROM advisor_diagnostics")
        actual_session = conn.execute("SELECT date FROM current_session").fetchone()
        if not actual_session or str(actual_session[0]) != manifest['as_of']:
            raise ValueError("La fecha declarada no coincide con los datos de la copia.")
        conn.execute("COMMIT")
    except Exception:  # noqa: BLE001 — rollback obligatorio de toda la importación
        conn.execute("ROLLBACK")
        raise
    return manifest


def preview(conn, manifest):
    """Comparación sin escrituras; el reemplazo incluye también filas eliminadas."""
    return pd.DataFrame([{'tabla': name,
                          'filas actuales': conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0],
                          'filas de la copia': manifest['tables'][name]['rows']}
                         for name in TABLES])


def main():
    import argparse

    from .db import connect, migrate
    parser = argparse.ArgumentParser(description="Importar una copia privada de mercado")
    parser.add_argument('--import-file', required=True, type=Path)
    args = parser.parse_args()
    if args.import_file.stat().st_size > MAX_BYTES:
        raise ValueError("La copia supera 512 MB.")
    data = args.import_file.read_bytes()
    inspect_bundle(data)
    migrate()
    with connect() as conn:
        imported = import_bundle(conn, data)
    print(f"Base compartida importada: {imported['bundle_id'][:16]} · {imported['as_of']}")


if __name__ == '__main__':
    main()
