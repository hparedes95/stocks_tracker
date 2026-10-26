"""Short-lived UI reads, invalidated by database writes and file replacement.

No persistent connection or disk cache: writers can still acquire DuckDB.
The decision/ingestion engines never use these cached display results.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from ..core import db, holding_analysis, integrity


@st.cache_resource(max_entries=4)
def _prepare_schema(path):
    # Exceptions are deliberately not returned/cached: retry temporary locks
    # on the next interaction. Only successful migrations are memoized.
    db.migrate()


def prepare_schema():
    _prepare_schema(str(db.get_settings().warehouse_path.resolve()))


def _revision():
    path = db.get_settings().warehouse_path.resolve()
    stamps = []
    # DuckDB may commit into its WAL before checkpointing the main file.
    for item in (path, Path(str(path) + '.wal')):
        try:
            info = item.stat()
        except FileNotFoundError:
            stamps.append(None)
        else:
            stamps.append((info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
    return str(path), tuple(stamps)


@st.cache_data(ttl=60, max_entries=16, show_spinner=False)
def _integrity(revision):
    with db.connect(read_only=True) as conn:
        return integrity.revisar(conn)


def get_integrity():
    return _integrity(_revision())


@st.cache_data(ttl=60, max_entries=16, show_spinner=False)
def _holdings(revision, horizon, as_of):
    with db.connect(read_only=True) as conn:
        return holding_analysis.analyse(conn, horizon=horizon, as_of=as_of)


def get_holding_analysis(*, horizon=126):
    return _holdings(_revision(), horizon, holding_analysis.date.today())
