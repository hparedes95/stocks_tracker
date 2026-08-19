"""Página 8 — Estado de los datos.

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
    "Última cotización",
    info["last_price_date"].strftime("%d/%m/%Y") if info["last_price_date"] else "—",
)
cols[1].metric(
    "Última actualización",
    f"hace {info['hours_since_run']:.0f} h" if info["hours_since_run"] is not None else "—",
)
cols[2].metric("Descargas fallidas", info["failures"])
cols[3].metric("Instrumentos", len(da.instruments()))

if info["is_stale"]:
    st.warning(
        "Los datos llevan más tiempo del previsto sin actualizarse. "
        "Ejecuta `make ingest && make compute`.",
        icon=":material/schedule:",
    )

st.divider()
st.subheader("Contenido del almacen")
st.dataframe(table_counts(), hide_index=True, height=420)

st.subheader("Cobertura de símbolos de TradingView")
instruments = da.instruments()
if not instruments.empty:
    mapped = instruments["tv_symbol"].notna().sum()
    total = len(instruments)
    st.progress(mapped / total if total else 0.0,
                text=f"{mapped} de {total} instrumentos con equivalencia")
    unmapped = instruments[instruments["tv_symbol"].isna()]
    if not unmapped.empty:
        st.caption(
            "Estos valores usan nuestro propio gráfico en lugar del widget. "
            "No es un fallo: es la degradación prevista."
        )
        st.dataframe(
            unmapped[["ticker", "name", "asset_class"]],
            hide_index=True, height=200,
        )

st.divider()
st.subheader("Calidad de los datos por universo")
st.caption(
    "El score penaliza la falta de datos, pero esa penalización no se ve. "
    "Aquí si: un universo con cobertura baja compite en desventaja, y conviene "
    "saberlo antes de extrañarse de que apenas aparezcan sus valores."
)
coverage = da.coverage_by_universe()
if coverage.empty:
    st.caption("Sin universos registrados.")
else:
    view = coverage.copy()
    view["cobertura_media"] = view["cobertura_media"].fillna(0) * 100
    view["puntuables"] = view["puntuables"].astype(int)
    view = view[
        ["universe", "instrumentos", "puntuables", "con_fundamentales",
         "cobertura_media", "sin_sector", "sin_precio"]
    ]
    st.dataframe(
        view.rename(
            columns={
                "universe": "Universo", "instrumentos": "Instrumentos",
                "puntuables": "Puntuables",
                "con_fundamentales": "Con fundamentales",
                "cobertura_media": "Cobertura media",
                "sin_sector": "Sin sector", "sin_precio": "Sin precio hoy",
            }
        ),
        hide_index=True, height=min(320, 42 + 35 * len(view)),
        column_config={
            "Puntuables": st.column_config.NumberColumn(
                help="Acciones y ETF. Solo estos entran en el ranking.",
            ),
            "Cobertura media": st.column_config.ProgressColumn(
                min_value=0.0, max_value=100.0, format="%.0f%%",
                help="Fracción media de campos fundamentales disponibles.",
            ),
        },
    )

    # Un universo de indices sin fundamentales no esta en desventaja: es que no
    # se puntua. Avisar de el seria una falsa alarma cada vez que se abre la
    # pagina, y las alarmas que siempre saltan dejan de leerse.
    scoreable = coverage[coverage["puntuables"] > 0]
    if not scoreable.empty:
        worst = scoreable.iloc[0]
        if pd.notna(worst["cobertura_media"]) and float(worst["cobertura_media"]) < 0.5:
            st.warning(
                f"**{worst['universe']}** tiene una cobertura media del "
                f"{float(worst['cobertura_media']):.0%}. Sus valores apareceran "
                "poco en el ranking, y no porque sean peores: es que no hay "
                "datos con los que puntuarlos.",
                icon=":material/warning:",
            )

st.subheader("Procedencia de los precios")
sources = da.price_sources()
if not sources.empty:
    st.dataframe(
        sources.rename(
            columns={"fuente": "Fuente", "instrumentos": "Instrumentos",
                     "filas": "Filas", "hasta": "Hasta"}
        ),
        hide_index=True, height=min(200, 42 + 35 * len(sources)),
    )

mixed = da.mixed_source_series()
if not mixed.empty:
    st.warning(
        f"{len(mixed)} series mezclan varias fuentes de precios. Yahoo ajusta "
        "el cierre por dividendos y Stooq no, así que en el día del relevo hay "
        "un salto que no es un movimiento real del mercado. Reconstruyelas con "
        "`make repair`.",
        icon=":material/call_split:",
    )
    st.dataframe(
        mixed.rename(columns={"ticker": "Valor", "fuentes": "Fuentes",
                              "n_fuentes": "Cuántas"}),
        hide_index=True, height=min(240, 42 + 35 * len(mixed)),
    )
else:
    st.caption(
        ":grey[Ninguna serie mezcla fuentes: todas tienen la misma convención "
        "de ajuste.]"
    )

st.divider()
st.subheader("Fundamentales que se contradicen")
st.caption(
    "Los ratios vienen de un **único proveedor gratuito**, y un proveedor "
    "gratuito se equivoca. Ninguno de esos errores da un fallo: entran en el "
    "ranking, suben al valor a los primeros puestos y ahí se quedan con la "
    "misma pinta que los datos buenos. Aquí se contrastan de tres formas: "
    "contra nuestros propios precios, contra las identidades contables que "
    "tienen que cumplir entre si, y contra la descarga anterior."
)

sospechosos = da.review_all_fundamentals()
if sospechosos.empty:
    st.success(
        "Ningún valor tiene fundamentales que se contradigan. No garantiza que "
        "sean correctos: garantiza que no se ha encontrado nada que los "
        "contradiga, que es otra cosa.",
        icon=":material/check_circle:",
    )
else:
    rotos = int((sospechosos["rotos"] > 0).sum())
    resumen_cols = st.columns(2)
    resumen_cols[0].metric("Valores con algun dato imposible", rotos)
    resumen_cols[1].metric("Valores con datos que no cuadran",
                           len(sospechosos) - rotos)
    st.dataframe(
        sospechosos[["ticker", "rotos", "avisos", "campos"]],
        hide_index=True, height=min(420, 42 + 35 * len(sospechosos)),
        column_config={
            "ticker": "Valor",
            "rotos": st.column_config.NumberColumn("Imposibles", format="%d"),
            "avisos": st.column_config.NumberColumn("Avisos", format="%d"),
            "campos": st.column_config.TextColumn("Campos", width="large"),
        },
    )
    st.caption(
        "**No se corrige nada automáticamente.** Cuando dos datos se "
        "contradicen no se sabe cual es el equivocado, y elegir uno sería peor "
        "que avisar de los dos. El detalle de cada uno está en la pestaña "
        "Fundamentales de su ficha."
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
    "make ingest           # datos reales (Yahoo, con Stooq de respaldo)\n"
    "make ingest-demo      # datos sintéticos, sin red\n"
    "make compute          # indicadores, señales, factores y amplitud\n"
    "make compute-presets  # puntua con todos los estilos de inversión\n"
    "make repair           # reconstruye series con fuentes mezcladas",
    language="bash",
)
st.caption(
    "La descarga se hace por lotes y sin hilos a propósito: la concurrencia es "
    "lo que dispara el bloqueo de Yahoo. Un universo de 750 valores son unas 16 "
    "peticiones, no 750. Si Yahoo deja de responder para algunos valores, se "
    "le piden a Stooq: el relevo queda anotado en el registro de arriba."
)

render_disclaimer()
