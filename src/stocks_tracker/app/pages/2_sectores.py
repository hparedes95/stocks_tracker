"""Pagina 2 — Sectores y rotacion."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from stocks_tracker.app import data_access as da
from stocks_tracker.app.components import charts, tv_widgets
from stocks_tracker.app.components.common import render_disclaimer

st.title("Sectores y rotacion")

sectors = da.get_sector_performance()
if sectors.empty:
    st.warning("Sin datos sectoriales. Ejecuta `make compute`.")
    st.stop()

st.caption(
    "Todas las cifras son la **mediana** del sector. La media se dispara con un "
    "solo valor en operacion corporativa y deja de describir al grupo."
)

# ---------------------------------------------------------------------------
# Rotacion
# ---------------------------------------------------------------------------
rotation = da.get_rotation()

st.subheader("Mapa de rotacion")
if rotation.empty:
    st.caption(
        "Sin datos de rotacion. Requiere los ETFs sectoriales del universo "
        "`ETF_CORE`; ejecuta `make compute`."
    )
else:
    trails = st.multiselect(
        "Mostrar la estela de",
        options=rotation["etf"].tolist(),
        default=[],
        format_func=lambda e: f"{e} · {rotation.loc[rotation['etf'] == e, 'sector'].iloc[0]}",
        help=(
            "Las once estelas a la vez se cruzan y no se distingue ninguna. "
            "Elige uno o dos sectores para ver su recorrido."
        ),
    )

    rot_left, rot_right = st.columns([3, 2])
    with rot_left:
        st.plotly_chart(
            charts.rotation_chart(rotation, height=520, trails_for=trails),
            width="stretch", config={"displayModeBar": False},
        )
    with rot_right:
        st.markdown("**Como se lee**")
        st.caption(
            "El eje horizontal mide si un sector lo hace mejor o peor que el "
            "indice. El vertical, si esa ventaja se acelera o se agota. La "
            "estela de puntos marca por donde ha pasado en las ultimas semanas.\n\n"
            "Los cuadrantes describen **donde esta** cada sector ahora mismo. "
            "No indican hacia donde ira: un sector que lidera puede dejar de "
            "hacerlo en cualquier momento."
        )
        counts = rotation["cuadrante"].value_counts()
        for name in ["Lidera", "Se debilita", "Rezagado", "Mejora"]:
            members = rotation[rotation["cuadrante"] == name]["sector"].tolist()
            if members:
                st.markdown(f"**{name}** ({counts.get(name, 0)})")
                st.caption(" · ".join(members))

# ---------------------------------------------------------------------------
# Mapa sector x horizonte
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Rendimiento por sector y horizonte")
st.plotly_chart(
    charts.heatmap_sector_horizon(sectors, height=420),
    width="stretch", config={"displayModeBar": False},
)

# ---------------------------------------------------------------------------
# Mapa de superficie
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Mapa de superficie")
group_choice = st.radio(
    "Agrupar por",
    options=["gics_sector", "investment_type"],
    format_func=lambda c: {"gics_sector": "Sector GICS",
                           "investment_type": "Tipo de inversion"}[c],
    horizontal=True,
)
treemap_data = da.get_treemap_data("TODOS", group_choice)
if treemap_data.empty:
    st.caption("Sin datos de capitalizacion suficientes.")
else:
    st.plotly_chart(
        charts.sector_treemap(treemap_data, height=470),
        width="stretch", config={"displayModeBar": False},
    )
    st.caption(
        "Superficie proporcional a la capitalizacion, color segun la variacion "
        "del dia. Agrupar por **tipo de inversion** es algo que el mapa de "
        "TradingView no permite."
    )

# ---------------------------------------------------------------------------
# Tabla
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Detalle por sector")
table = pd.DataFrame(
    {
        "Sector": sectors["sector"],
        "Valores": sectors["n_valores"],
        "Dia": sectors["ret_1d"] * 100,
        "Semana": sectors["ret_5d"] * 100,
        "Mes": sectors["ret_1m"] * 100,
        "Trimestre": sectors["ret_3m"] * 100,
        "12 meses": sectors["ret_12m"] * 100,
        "Sobre MM200": sectors["pct_sobre_mm200"],
    }
)
st.dataframe(
    table, hide_index=True, height=440,
    column_config={
        **{
            col: st.column_config.NumberColumn(format="%+.2f%%")
            for col in ["Dia", "Semana", "Mes", "Trimestre", "12 meses"]
        },
        "Sobre MM200": st.column_config.ProgressColumn(
            min_value=0.0, max_value=100.0, format="%.0f%%"
        ),
    },
)
st.caption(
    "La columna **Sobre MM200** es la amplitud interna del sector: uno que sube "
    "con solo el 30% de sus valores en tendencia alcista se apoya en pocos nombres."
)

# ---------------------------------------------------------------------------
# Amplitud interna
# ---------------------------------------------------------------------------
st.divider()
st.subheader("Amplitud interna de un sector")
chosen = st.selectbox("Sector", options=sectors["sector"].tolist())
breadth = da.get_breadth(f"GICS:{chosen}")
if breadth.empty:
    st.caption("Sin serie de amplitud para este sector todavia.")
else:
    st.plotly_chart(
        charts.breadth_lines(breadth, height=280),
        width="stretch", config={"displayModeBar": False},
    )

# ---------------------------------------------------------------------------
# Complementos de TradingView
# ---------------------------------------------------------------------------
if tv_widgets.enabled():
    st.divider()
    tab_map, tab_crypto = st.tabs(["Mapa de TradingView", "Cripto"])
    with tab_map:
        source = st.selectbox(
            "Universo", options=["SPX500", "NASDAQ100", "IBC", "SX5E"],
            format_func=lambda s: {
                "SPX500": "S&P 500", "NASDAQ100": "Nasdaq 100",
                "IBC": "IBEX 35", "SX5E": "Euro Stoxx 50",
            }[s],
        )
        tv_widgets.stock_heatmap(source, height=560)
    with tab_crypto:
        st.caption(
            "La cripto funciona como termometro de apetito por riesgo: cuando "
            "sube con fuerza, suele haber tolerancia al riesgo en el resto del mercado."
        )
        tv_widgets.crypto_heatmap(height=520)

st.divider()
render_disclaimer()
