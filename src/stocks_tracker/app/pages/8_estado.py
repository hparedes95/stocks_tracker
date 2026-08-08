"""Pagina 8 — Estado de los datos.

Sirve para responder a "por que el dashboard muestra esto": que se descargo,
cuando, que fallo y que valores tienen datos viejos.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from stocks_tracker.app import data_access as da
from stocks_tracker.app.components.common import render_disclaimer
from stocks_tracker.core.db import connect, table_counts

st.title("Estado de los datos")

info = da.data_freshness()

cols = st.columns(4)
cols[0].metric(
    "Ultima cotizacion",
    info["last_price_date"].strftime("%d/%m/%Y") if info["last_price_date"] else "—",
)
cols[1].metric(
    "Ultima actualizacion",
    f"hace {info['hours_since_run']:.0f} h" if info["hours_since_run"] is not None else "—",
)
cols[2].metric("Descargas fallidas", info["failures"])
cols[3].metric("Instrumentos", len(da.instruments()))

if info["is_stale"]:
    st.warning(
        "Los datos llevan mas tiempo del previsto sin actualizarse. "
        "Ejecuta `make ingest && make compute`.",
        icon=":material/schedule:",
    )

st.divider()
st.subheader("Contenido del almacen")
st.dataframe(table_counts(), hide_index=True, height=420)

st.subheader("Cobertura de simbolos de TradingView")
instruments = da.instruments()
if not instruments.empty:
    mapped = instruments["tv_symbol"].notna().sum()
    total = len(instruments)
    st.progress(mapped / total if total else 0.0,
                text=f"{mapped} de {total} instrumentos con equivalencia")
    unmapped = instruments[instruments["tv_symbol"].isna()]
    if not unmapped.empty:
        st.caption(
            "Estos valores usan nuestro propio grafico en lugar del widget. "
            "No es un fallo: es la degradacion prevista."
        )
        st.dataframe(
            unmapped[["ticker", "name", "asset_class"]],
            hide_index=True, height=200,
        )

st.divider()
st.subheader("Registro de ingesta")
with connect(read_only=True) as conn:
    log = conn.execute(
        """
        SELECT started_at, task, target, status, rows_written, requests_used, error
        FROM ingest_log ORDER BY started_at DESC LIMIT 60
        """
    ).fetchdf()

if log.empty:
    st.caption("Sin ejecuciones registradas.")
else:
    log["started_at"] = pd.to_datetime(log["started_at"]).dt.strftime("%d/%m %H:%M")
    st.dataframe(
        log.rename(
            columns={
                "started_at": "Cuando", "task": "Tarea", "target": "Objetivo",
                "status": "Estado", "rows_written": "Filas",
                "requests_used": "Peticiones", "error": "Detalle",
            }
        ),
        hide_index=True, height=420,
    )

st.divider()
st.subheader("Como actualizar")
st.code(
    "make ingest     # datos reales de Yahoo Finance\n"
    "make ingest-demo # datos sinteticos, sin red\n"
    "make compute    # indicadores, senales, factores y amplitud",
    language="bash",
)
st.caption(
    "La descarga se hace por lotes y sin hilos a proposito: la concurrencia es "
    "lo que dispara el bloqueo de Yahoo. Un universo de 750 valores son unas 16 "
    "peticiones, no 750."
)

render_disclaimer()
