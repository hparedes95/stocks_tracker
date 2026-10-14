"""Tres puntos locales de recuperación; jamás reemplazan la cartera personal."""
from __future__ import annotations

import io
import uuid
import zipfile

import pandas as pd

from .db import schema_path
from .market_bundle import MAX_BYTES, MAX_EXPANDED, TABLES, digest
from .timeutils import utcnow

RECOVERABLE = (*TABLES, 'market_bundle_state')


def schema_hash():
    return digest(schema_path().read_text(encoding='utf-8').replace('\r\n', '\n').encode())


def save_point(conn):
    """Llamar dentro de la transacción que reemplaza mercado, antes del DELETE."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for table in RECOVERABLE:
            frame = conn.execute(f'SELECT * FROM "{table}" ORDER BY ALL').fetchdf()
            archive.writestr(table + '.parquet', frame.to_parquet(index=False, compression='zstd'))
    raw = output.getvalue()
    if len(raw) > MAX_BYTES:
        raise ValueError('La copia de seguridad supera 512 MB; se cancela el reemplazo de mercado.')
    point = uuid.uuid4().hex
    conn.execute('INSERT INTO market_restore_points VALUES (?, ?, ?, ?, ?)',
                 [point, utcnow(), schema_hash(), digest(raw), raw])
    conn.execute('DELETE FROM market_restore_points WHERE point_id NOT IN '
                 '(SELECT point_id FROM market_restore_points ORDER BY created_at DESC, point_id DESC LIMIT 3)')
    return point


def restore_point(conn, point_id):
    """Restaura tablas permitidas y guarda el mercado saliente en la misma transacción."""
    row = conn.execute('SELECT schema_hash, checksum, payload FROM market_restore_points '
                       'WHERE point_id=?', [point_id]).fetchone()
    if not row or row[0] != schema_hash():
        raise ValueError('Copia local ausente o de otro esquema; no se han modificado datos.')
    raw = bytes(row[2])
    if len(raw) > MAX_BYTES or digest(raw) != row[1]:
        raise ValueError('Copia local dañada; no se han modificado datos.')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        expected = {t + '.parquet' for t in RECOVERABLE}
        if (set(archive.namelist()) != expected or len(archive.infolist()) != len(expected) or
                sum(e.file_size for e in archive.infolist()) > MAX_EXPANDED):
            raise ValueError('Contenido de recuperación inválido.')
        frames = {t: pd.read_parquet(io.BytesIO(archive.read(t + '.parquet'))) for t in RECOVERABLE}
    conn.execute('BEGIN TRANSACTION')
    try:
        safety = save_point(conn)
        for table, frame in frames.items():
            conn.register('_market_restore', frame)
            try:
                conn.execute(f'DELETE FROM "{table}"')
                conn.execute(f'INSERT INTO "{table}" BY NAME SELECT * FROM _market_restore')
            finally:
                conn.unregister('_market_restore')
        conn.execute('DELETE FROM advisor_runs')
        conn.execute('DELETE FROM advisor_diagnostics')
        conn.execute('COMMIT')
    except Exception:  # noqa: BLE001 — la restauración es indivisible
        conn.execute('ROLLBACK')
        raise
    return safety
